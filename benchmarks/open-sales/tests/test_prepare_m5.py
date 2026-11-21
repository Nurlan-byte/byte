"""Small independent fixtures for M5 label identity and pre-period assortment selection."""
from pathlib import Path
import sys

import duckdb
import pandas as pd
import pandera.pandas as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_m5 import SELECTION_END, SELECTION_START, STORES, classify, prepare, select_series, validate_frame


def test_store_activity_listing_and_real_zero():
    with duckdb.connect() as db:
        db.execute("""
            CREATE TABLE food AS SELECT * FROM (VALUES
              (1,0,true,true), (2,NULL,true,true), (3,0,NULL,true),
              (4,0,false,true), (5,0,true,false), (6,3,true,true)
            ) f(day_no,sold,is_open,listed)
        """)
        classify(db)
        assert db.execute("SELECT state FROM classified ORDER BY day_no").fetchall() == [
            ("observed",), ("unknown",), ("unknown",), ("closed",), ("unknown",), ("observed",),
        ]
        db.execute("DROP VIEW classified")
        db.execute("INSERT INTO food VALUES (7,2,false,true)")
        with pytest.raises(ValueError, match="Positive sales"):
            classify(db)


def test_panel_requires_unique_dates_integers_and_known_observations():
    frame = pd.DataFrame({
        "site": ["CA_1", "CA_1"], "product": ["FOODS_1_001"] * 2,
        "date": pd.to_datetime(["2015-01-01", "2015-01-02"]).astype("datetime64[ns]"),
        "sold": pd.array([0, pd.NA], dtype="Int64"), "state": ["observed", "unknown"],
    })
    validate_frame(frame)
    for invalid in (pd.concat([frame, frame]), frame.assign(sold=pd.array([0, 0], dtype="Int64")),
                    frame.assign(sold=pd.array([-1, pd.NA], dtype="Int64")),
                    frame.assign(sold=[1.5, float("nan")])):
        with pytest.raises(pa.errors.SchemaError):
            validate_frame(invalid)


def test_selection_uses_only_earlier_known_rows():
    with duckdb.connect() as db:
        db.execute("CREATE TABLE classified(site VARCHAR,product VARCHAR,day_no INTEGER,sold INTEGER,state VARCHAR)")
        for store in STORES:
            rows = [(store, f"FOODS_{interval}_{sku}", day, int(day % interval == 0), "observed")
                    for interval in (1, 2, 8) for sku in range(3)
                    for day in range(SELECTION_START, SELECTION_END + 1)]
            db.executemany("INSERT INTO classified VALUES (?,?,?,?,?)", rows)
        select_series(db)
        before = db.execute("SELECT * FROM selected ORDER BY ALL").fetchall()
        assert len(before) == 18
        db.execute("DROP TABLE selected")
        # Make all formerly rare SKUs look frequent after the decision.
        db.execute("""
            INSERT INTO classified SELECT site,product,day_no+112,1,'observed' FROM classified
        """)
        select_series(db)
        assert db.execute("SELECT * FROM selected ORDER BY ALL").fetchall() == before


def test_changed_source_cannot_write_panel(tmp_path):
    source, panel, manifest = [tmp_path / name for name in ("source.zip", "panel.csv", "metadata.json")]
    source.write_bytes(b"different release")
    with pytest.raises(ValueError, match="SHA-256"):
        prepare(source, panel, manifest)
    assert not panel.exists() and not manifest.exists()
