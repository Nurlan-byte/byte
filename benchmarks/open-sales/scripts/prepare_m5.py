"""Prepare a fixed real M5 unit-sales panel for a two-day-ahead evaluation."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from zipfile import ZipFile

import duckdb
import pandera.pandas as pa
import pandas as pd

ARCHIVE_SHA256 = "0349ba38a2efd30d0f5acc6394c1110e140e1a990c650d7b5ca44c5b25dd12f5"
ARCHIVE_URL = "https://zenodo.org/records/12636070/files/m5-forecasting-accuracy.zip"
STORES = ("CA_1", "TX_1", "WI_1")
CONFIRMATION_STORES = ("CA_2", "TX_2", "WI_2")
FIRST_DAY, LAST_DAY = 1606, 1941
SELECTION_START, SELECTION_END = 1634, 1745
MEMBERS = ("calendar.csv", "sales_train_evaluation.csv", "sell_prices.csv")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_tables(db, raw, stores):
    """Retain missing cells and calculate store activity across the entire assortment."""
    days = ",".join(f"d_{i}" for i in range(FIRST_DAY, LAST_DAY + 1))
    db.execute("""
        CREATE TEMP TABLE calendar AS
        SELECT d, CAST(substr(d, 3) AS INTEGER) day_no, CAST(date AS DATE) date, wm_yr_wk
        FROM read_csv(?) WHERE CAST(substr(d, 3) AS INTEGER) BETWEEN ? AND ?
    """, [str(raw / MEMBERS[0]), FIRST_DAY, LAST_DAY])
    db.execute(f"""
        CREATE TEMP TABLE daily AS
        SELECT store_id site, item_id product, cat_id, day_id,
            CASE WHEN raw_units IS NULL THEN NULL
                 WHEN regexp_full_match(raw_units, '[0-9]+') THEN CAST(raw_units AS INTEGER)
                 ELSE error('Invalid M5 physical unit count') END sold
        FROM (SELECT store_id, item_id, cat_id, {days} FROM read_csv(?, all_varchar=true)
              WHERE store_id IN (?, ?, ?))
        UNPIVOT INCLUDE NULLS (raw_units FOR day_id IN ({days}))
    """, [str(raw / MEMBERS[1]), *stores])
    db.execute("""
        CREATE TEMP TABLE food AS
        WITH activity AS (
            SELECT site, day_id, CASE WHEN max(sold) > 0 THEN true ELSE NULL END is_open
            FROM daily GROUP BY site, day_id
        ), prices AS (
            SELECT store_id site, item_id product, wm_yr_wk, sell_price FROM read_csv(?)
            WHERE store_id IN (?, ?, ?) AND wm_yr_wk IN (SELECT wm_yr_wk FROM calendar)
        )
        SELECT daily.site, daily.product, c.day_no, c.date, daily.sold, a.is_open,
               coalesce(p.sell_price > 0, false) listed
        FROM daily JOIN calendar c ON c.d=daily.day_id JOIN activity a USING(site,day_id)
        LEFT JOIN prices p ON p.site=daily.site AND p.product=daily.product AND p.wm_yr_wk=c.wm_yr_wk
        WHERE daily.cat_id='FOODS'
    """, [str(raw / MEMBERS[2]), *stores])


def classify(db):
    # M5 only establishes recorded store activity. An all-zero store-day has unknown opening status.
    db.execute("""
        CREATE TEMP VIEW classified AS SELECT *,
            CASE WHEN is_open=false THEN 'closed'
                 WHEN is_open IS NULL OR NOT listed OR sold IS NULL THEN 'unknown'
                 ELSE 'observed' END AS state FROM food
    """)
    if db.execute("""
        SELECT count(*) FROM classified WHERE sold<0 OR (sold>0 AND state!='observed')
    """).fetchone()[0]:
        raise ValueError("Positive sales on a closed/unlisted day or invalid quantity")


def select_series(db, stores=STORES):
    """Two SKUs per frequency stratum per store, fixed before either scoring period."""
    db.execute("""
        CREATE TEMP TABLE selected AS
        WITH history AS (
            SELECT site, product, avg(CAST(sold>0 AS INTEGER)) AS frequency
            FROM classified
            WHERE day_no BETWEEN ? AND ? AND state='observed' GROUP BY site, product
            HAVING count(*)>=100 AND count(*) FILTER (WHERE sold>0)>=4
               AND count(*) FILTER (WHERE day_no<?+28 AND sold>0)>0
               AND count(*) FILTER (WHERE day_no>?-28 AND sold>0)>0
        ), strata AS (
            SELECT site, product, CASE WHEN frequency<=1.0/3 THEN 'rare'
                                       WHEN frequency<=2.0/3 THEN 'medium' ELSE 'frequent' END stratum
            FROM history
        )
        SELECT site,product,stratum FROM strata
        QUALIFY row_number() OVER (PARTITION BY site,stratum ORDER BY md5(product),product)<=2
    """, [SELECTION_START, SELECTION_END, SELECTION_START, SELECTION_END])
    if db.execute("SELECT count(*) FROM selected").fetchone()[0] != len(stores) * 6:
        raise ValueError("Expected two products per frequency stratum per store")


def validate_frame(frame, stores=STORES):
    """Validate the scored-panel contract before writing any labels."""
    schema = pa.DataFrameSchema({
        "site": pa.Column(str, pa.Check.isin(stores)),
        "product": pa.Column(str, pa.Check.str_matches(r"^FOODS_[A-Za-z0-9_]+$")),
        "date": pa.Column("datetime64[ns]"),
        "sold": pa.Column(pd.Int64Dtype(), pa.Check.ge(0), nullable=True),
        "state": pa.Column(str, pa.Check.isin(["observed", "unknown", "closed"])),
    }, unique=["site", "product", "date"], strict=True, checks=[
        pa.Check(lambda df: df["state"].eq("observed").eq(df["sold"].notna()).all(),
                 name="only observed records carry a sales label"),
    ])
    # Pandera reports duplicate composite keys by DataFrame index. Normalize caller
    # indexes so concatenated batches can report the offending store, SKU and date.
    schema.validate(frame.reset_index(drop=True))


def prepare(archive, output, metadata, stores=STORES):
    if len({p.resolve() for p in (archive, output, metadata)}) != 3:
        raise ValueError("Use distinct paths for input archive, panel and metadata")
    archive_digest = sha256(archive)
    if archive_digest != ARCHIVE_SHA256:
        raise ValueError(f"Unexpected M5 archive SHA-256: {archive_digest}")
    with tempfile.TemporaryDirectory(prefix="bakeplan-m5-") as folder, duckdb.connect() as db:
        raw = Path(folder)
        db.execute("SET threads=2")
        with ZipFile(archive) as source:
            for member in MEMBERS:
                with source.open(member) as stream, (raw / member).open("wb") as extracted:
                    shutil.copyfileobj(stream, extracted, length=1024 * 1024)
        prepare_tables(db, raw, stores)
        classify(db)
        select_series(db, stores)
        frame = db.execute("""
            SELECT site, product, date, CASE WHEN state='observed' THEN sold END sold, state
            FROM classified JOIN selected USING(site,product) ORDER BY site,product,date
        """).df()
        frame["date"] = frame["date"].astype("datetime64[ns]")
        frame["sold"] = frame["sold"].astype("Int64")
        validate_frame(frame, stores)
        if len(frame) != 6 * len(stores) * (LAST_DAY - FIRST_DAY + 1):
            raise ValueError("Incomplete product-day grid")
        series = [dict(zip(("site", "product", "stratum"), row)) for row in db.execute(
            "SELECT site,product,stratum FROM selected ORDER BY site,product"
        ).fetchall()]
    observed = frame.loc[frame["state"] == "observed", ["site", "product", "date", "sold"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    counts = {k: int(frame["state"].eq(k).sum()) for k in ("observed", "closed", "unknown")}
    with (tempfile.TemporaryDirectory(dir=output.parent) as csv_dir,
          tempfile.TemporaryDirectory(dir=metadata.parent) as manifest_dir):
        staged_csv, staged_manifest = Path(csv_dir) / "panel.csv", Path(manifest_dir) / "metadata.json"
        observed.to_csv(staged_csv, index=False, date_format="%Y-%m-%d", lineterminator="\n", encoding="utf-8")
        manifest = {
            "dataset": "M5 FOODS", "doi": "10.5281/zenodo.12636070", "source_url": ARCHIVE_URL,
            "archive_sha256": archive_digest, "panel_sha256": sha256(staged_csv),
            "selection_start": "2015-07-20", "selection_end": "2015-11-08",
            "panel_start": str(frame["date"].min().date()), "panel_end": str(frame["date"].max().date()),
            "unit": "recorded physical units", "series": series, "series_count": len(series),
            "records_by_state": counts, "observed_zeros": int(observed["sold"].eq(0).sum()),
        }
        staged_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        staged_csv.replace(output)
        staged_manifest.replace(metadata)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--confirmation", action="store_true", help="Use the preregistered CA_2/TX_2/WI_2 panel")
    args = parser.parse_args()
    result = prepare(args.archive, args.output, args.metadata, CONFIRMATION_STORES if args.confirmation else STORES)
    print(json.dumps({key: result[key] for key in ("panel_sha256", "series_count", "records_by_state", "observed_zeros")}))


if __name__ == "__main__":
    main()
