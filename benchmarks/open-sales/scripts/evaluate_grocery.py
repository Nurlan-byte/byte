"""Benchmark BakePlan's frozen procurement model on the Complete Journey retail panel."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

DATA_COMMIT = "5b5d06192b9856edd04e4d405787af2f2e4a1fef"
MODEL_COMMIT = "ac3c2af5a4535c6fcd35ccd7ebdd41c4f7bac4cb"
MODEL_SHA256 = "855de87f08b533bdade9f96e1b09e371d63da7b8bd5c77a8b3be41ed42edf3d1"
DATA_HASHES = {
    "transactions.rds": "1fa0700033f1e5d9bb6b09e2be063d8d68474d346e95c50f2833e09d083e0007",
    "products.rda": "a80c6df33623b4af296ae9f317a6647e369db7e8ce7e7baed0e1bf44b9d979e5",
}
CUTOFF = date(2017, 11, 4)
START = date(2017, 11, 6)
END = date(2017, 12, 31)
CATEGORIES = {
    "BAG SNACKS",
    "CHIPS&SNACKS",
    "CANDY - CHECKLANE",
    "CANDY - PACKAGED",
    "SOFT DRINKS",
    "WATER",
    "WATER - CARBONATED/FLVRD DRINK",
    "SNACK NUTS",
}


def check_data_files(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for filename, expected in DATA_HASHES.items():
        path = directory / filename
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Unexpected SHA-256 in the cached file {path}")
            continue
        url = f"https://raw.githubusercontent.com/bradleyboehmke/completejourney/{DATA_COMMIT}/data/{filename}"
        with urlopen(url, timeout=90) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Unexpected SHA-256 for downloaded {filename}")
        with NamedTemporaryFile(dir=directory, delete=False) as cache_file:
            temporary = Path(cache_file.name)
            try:
                cache_file.write(data)
            except OSError:
                cache_file.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def select_products(products, panel):
    catalog = products.loc[products["product_category"].isin(CATEGORIES)].set_index(
        "product_id"
    )
    early = panel.loc[
        (panel["day"] <= CUTOFF) & panel["product_id"].isin(catalog.index)
    ]
    # The busiest store and products are selected entirely before forecasting.
    store = min(
        early.groupby("store_id").size().items(),
        key=lambda pair: (-pair[1], int(pair[0])),
    )[0]
    store_rows = panel.loc[panel["store_id"] == store]
    eligible = early.loc[early["store_id"] == store]
    units = eligible.groupby(["product_id", "day"])["quantity"].sum()
    popular = units[units > 0].groupby(level="product_id").agg(["count", "sum"])
    ids = sorted(
        popular.index,
        key=lambda p: (-popular.at[p, "count"], -popular.at[p, "sum"], int(p)),
    )[:8]
    selection = [
        {
            "product_id": p,
            "category": str(catalog.at[p, "product_category"]),
            "type": str(catalog.at[p, "product_type"]),
            "pack": str(catalog.at[p, "package_size"]),
            "train_positive_days": int(popular.at[p, "count"]),
            "train_units": int(popular.at[p, "sum"]),
        }
        for p in ids
    ]
    return store, store_rows, ids, selection


def daily_records(store_rows, ids, observation_days):
    selected = store_rows.loc[store_rows["product_id"].isin(ids)]
    totals = selected.groupby(["product_id", "day"])["quantity"].sum()
    if not ((totals >= 0) & (totals % 1 == 0)).all():
        raise ValueError(
            "Expected whole nonnegative daily quantities for the selected products"
        )
    records = []
    for product in ids:
        sales = totals.loc[product]
        first = min(sales.loc[sales > 0].index)
        for day in observation_days:
            if day >= first:
                records.append(
                    {
                        "date": day.isoformat(),
                        "product_id": product,
                        "sold": int(sales.get(day, 0)),
                        "is_open": True,
                        "stockout_status": "unknown",
                    }
                )
    return records


def measurements(frame):
    total = int(frame["actual"].sum())
    result = {
        "predictions": len(frame),
        "actual_units": total,
        "method_counts": dict(Counter(frame["method"])),
    }
    for method in ("b1", "bakeplan"):
        error = abs(frame[method] - frame["actual"])
        result[method] = {
            "mae": float(error.mean()),
            "wape": float(error.sum() / total) if total else None,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        required=True,
        type=Path,
        help="BakePlan repository containing the benchmarked backend",
    )
    parser.add_argument(
        "--data",
        required=True,
        type=Path,
        help="Cache for the two pinned R data files, downloaded if absent",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output directory for the daily history, predictions and metrics",
    )
    args = parser.parse_args()
    model_path = args.repo / "backend" / "ml_core.py"
    normalized_code = model_path.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(normalized_code).hexdigest() != MODEL_SHA256:
        raise ValueError(f"{model_path} does not match BakePlan {MODEL_COMMIT}")
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.ml_core import forecast_model, train_model
    import pandas as pd
    import pyreadr

    check_data_files(args.data)
    products = next(iter(pyreadr.read_r(args.data / "products.rda").values()))
    # The package author groups the source's timestamps by this local timezone.
    transactions = next(
        iter(
            pyreadr.read_r(
                args.data / "transactions.rds", timezone="America/New_York"
            ).values()
        )
    )
    if len(transactions) != 1_469_307:
        raise ValueError("Expected the full 1,469,307-line Complete Journey panel")
    panel = transactions[
        ["store_id", "product_id", "quantity", "transaction_timestamp"]
    ].copy()
    panel["day"] = panel.pop("transaction_timestamp").dt.date
    store, store_rows, ids, selection = select_products(products, panel)
    observation_days = sorted(store_rows["day"].unique())
    records = daily_records(store_rows, ids, observation_days)
    dataset = {
        "id": "completejourney-panel-" + store,
        "version": "1",
        "bakery_id": "store-" + store,
        "planning_kind": "procurement",
        "target": "sold_units",
        "unit": "physical_unit",
        "rows": records,
    }
    model = train_model(dataset, CUTOFF)
    actuals = {(r["date"], r["product_id"]): r["sold"] for r in records}
    observed_test_days = [d for d in observation_days if START <= d <= END]
    forecasts = []
    for day in observed_test_days:
        cutoff = day - timedelta(days=2)
        for product in ids:
            result = forecast_model(model, dataset, product, day, cutoff)
            forecasts.append(
                {
                    "date": day.isoformat(),
                    "cutoff": cutoff.isoformat(),
                    "product_id": product,
                    "actual": actuals[day.isoformat(), product],
                    "b1": result["b1_mean"],
                    "bakeplan": result["forecast_mean"],
                    "method": result["method"],
                }
            )
    frame = pd.DataFrame(forecasts)
    paired = frame.dropna(subset=["b1", "bakeplan"])
    days = (END - START).days + 1
    calendar = (START + timedelta(days=i) for i in range(days))
    summary = {
        "source_commit": DATA_COMMIT,
        "model_commit": MODEL_COMMIT,
        "input_sha256": DATA_HASHES,
        "store_id": store,
        "panel_receipt_lines": len(panel),
        "store_receipt_lines": len(store_rows),
        "selection": selection,
        "training_cutoff": CUTOFF.isoformat(),
        "start": START.isoformat(),
        "end": END.isoformat(),
        "model_snapshot_hash": model["snapshot_hash"],
        "holdout_start": model["evaluation"]["holdout_start"],
        "holdout_end": model["evaluation"]["holdout_end"],
        "calendar_days": days,
        "missing_days": [
            d.isoformat() for d in calendar if d not in observed_test_days
        ],
        "candidates": len(frame),
        "coverage": len(paired) / len(frame),
        "summary": measurements(paired),
        "per_product": {
            p: measurements(group) for p, group in paired.groupby("product_id")
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).sort_values(["date", "product_id"]).to_csv(
        args.out / "completejourney_daily.csv", index=False
    )
    frame.to_csv(args.out / "completejourney_predictions.csv", index=False)
    (args.out / "completejourney_results.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary["summary"], indent=2))


if __name__ == "__main__":
    main()
