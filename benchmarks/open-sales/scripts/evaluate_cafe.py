"""Reproduce the receipt-incidence forecast comparison on Mendeley kcgf45y24m v2.

Run with Python 3.14 and NumPy. --data must contain Transactions.csv, Products.csv
from https://doi.org/10.17632/kcgf45y24m.2. --repo is the unpacked Nurlan-byte/byte
snapshot ac3c2af5a4535c6fcd35ccd7ebdd41c4f7bac4cb. --out is the output folder.
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path


TRAIN_END = date(2025, 8, 4)
START = date(2025, 8, 6)
END = date(2025, 9, 30)
ONE_DAY = timedelta(days=1)
MONTH_CODES = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)
HASHES = {
    "Transactions.csv": "36f2412969f8241e72b5f3eb395e5af13a7ba042de1e6ecdbe32bee23723ef90",
    "Products.csv": "09e3d91530797fb0c2c5a214f5660a6e2235521b1cf332382343c75e7708d047",
    "backend/ml_core.py": "855de87f08b533bdade9f96e1b09e371d63da7b8bd5c77a8b3be41ed42edf3d1",
    "backend/forecasting.py": "82854f8841c836d222b9cae07d40c8eba26bcf7b52bdb1ccf1eb90da7ca16794",
}


def load_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def save_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def measure(group):
    valid = [r for r in group if r["b1"] is not None and r["bakeplan"] is not None]
    total = sum(r["actual"] for r in valid)
    errors = {
        m: sum(abs(r[m] - r["actual"]) for r in valid) for m in ["b1", "bakeplan"]
    }
    return {
        "n_expected": len(group),
        "n_scored": len(valid),
        "actual_receipt_incidences": total,
        "n_zero": sum(r["actual"] == 0 for r in valid),
        "n_ridge": sum(r["method"] == "ML" for r in valid),
        "n_baseline_fallback": sum(r["method"] == "B1" for r in valid),
        "mae_b1": errors["b1"] / len(valid),
        "mae_bakeplan": errors["bakeplan"] / len(valid),
        "wape_b1_pct": 100 * errors["b1"] / total if total else None,
        "wape_bakeplan_pct": 100 * errors["bakeplan"] / total if total else None,
        "relative_mae_improvement_pct": 100
        * (errors["b1"] - errors["bakeplan"])
        / errors["b1"]
        if errors["b1"]
        else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "out", "data"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    for filename, digest in HASHES.items():
        if filename.startswith("backend/"):
            content = (args.repo / filename).read_bytes().replace(b"\r\n", b"\n")
        else:
            content = (args.data / filename).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"Unexpected file hash: {filename}")
    args.out.mkdir(exist_ok=True, parents=True)
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.forecasting import baseline
    from backend.ml_core import train_model, forecast_model

    catalog = {}
    for row in load_csv(args.data / "Products.csv"):
        sku = (
            row["ProductName"]
            if row["Variant"] == "-"
            else f"{row['ProductName']} ({row['Variant']})"
        )
        assert sku not in catalog and "," not in sku
        catalog[sku] = row["Category"]
    counts = Counter()
    receipts = Counter()
    seen = set()
    sizes = Counter()
    zero_payment = 0
    train_counts = defaultdict(Counter)
    for row in load_csv(args.data / "Transactions.csv"):
        receipt = row["ReceiptNumber"]
        outlet = row["Outlet"]
        day = date.fromisoformat(row["Date"])
        expected_prefix = f"{outlet}{MONTH_CODES[day.month - 1]}{day.year % 100:02d}"
        assert receipt not in seen and receipt.startswith(expected_prefix)
        assert date(2025, 1, 2) <= day <= END
        seen.add(receipt)
        labels = row["Items"].split(", ")
        assert len(labels) == len(set(labels)) == int(row["TotalItem"])
        sizes[len(labels)] += 1
        receipts[outlet, day] += 1
        zero_payment += float(row["NetSales"]) <= 0
        for label in labels:
            assert label in catalog
            counts[outlet, day, label] += 1
            if day <= TRAIN_END and catalog[label] in {"Coffee", "Tea"}:
                train_counts[outlet][label] += 1
    assert len(seen) == 53820
    forecasts, aggregate, daily = [], [], []
    details = {
        "source": "Anonymous Transactional Dataset v2 (Engel, Gaol, Kurniawan, Budiharto), CC BY 4.0.",
        "source_url": "https://doi.org/10.17632/kcgf45y24m.2",
        "code_commit": "ac3c2af5a4535c6fcd35ccd7ebdd41c4f7bac4cb",
        "input_hashes": HASHES,
        "target": "Number of receipts that list the beverage variant at a given outlet on a date",
        "input_adapter": "Native item_line_count/item_line target with the procurement per-product model gate. Each item is the exact named drink variant recorded once in a receipt.",
        "train_selection": "Six most frequent exact catalog variants per outlet in Coffee or Tea categories through 2025-08-04, tie broken by full variant label.",
        "cutoff_lag_days": 2,
        "refit_every_days": 7,
        "selection_end": TRAIN_END.isoformat(),
        "start": START.isoformat(),
        "end": END.isoformat(),
        "receipts_total": len(seen),
        "receipts_by_number_of_listed_items": dict(sizes),
        "zero_or_negative_net_sales_receipts": zero_payment,
        "days_without_receipts_policy": "omitted",
        "zero_policy": "No receipt for SKU on an observed store day after its first receipt = zero receipt incidences",
        "outlet_names": "Use each raw receipt's Outlet value; File tallies for SHOP002/003 differ from article table 4.",
        "outlets": {},
    }
    for outlet in sorted(train_counts):
        skus = sorted(
            train_counts[outlet], key=lambda s: (-train_counts[outlet][s], s)
        )[:6]
        active_days = sorted(d for shop, d in receipts if shop == outlet)
        rows = []
        for sku in skus:
            first = min(d for d in active_days if counts[outlet, d, sku] > 0)
            for d in active_days:
                if d >= first:
                    rows.append(
                        {
                            "date": d.isoformat(),
                            "product_id": sku,
                            "item_line_count": counts[outlet, d, sku],
                            "is_open": True,
                            "stockout_status": "unknown",
                        }
                    )
                    daily.append(
                        {
                            "date": d.isoformat(),
                            "outlet": outlet,
                            "product": sku,
                            "receipts_with_product": counts[outlet, d, sku],
                            "outlet_receipts": receipts[outlet, d],
                        }
                    )
        ds = {
            "id": "cafe-v2-" + outlet,
            "version": "1",
            "bakery_id": outlet,
            "planning_kind": "procurement",
            "target": "item_line_count",
            "unit": "item_line",
            "rows": rows,
        }
        models = {}
        group = []
        for d in active_days:
            if not START <= d <= END:
                continue
            cutoff = d - 2 * ONE_DAY
            trained = TRAIN_END + ((cutoff - TRAIN_END).days // 7) * 7 * ONE_DAY
            if trained not in models:
                models[trained] = train_model(ds, trained)
            for sku in skus:
                forecast = forecast_model(models[trained], ds, sku, d, cutoff)
                b1 = baseline(
                    rows,
                    product_id=sku,
                    target_date=d,
                    cutoff=cutoff,
                    method="B1",
                    value_key="item_line_count",
                    eligible=lambda r: r.get("is_open") is not False,
                )["forecast_mean"]
                assert b1 == forecast["b1_mean"]
                group.append(
                    {
                        "date": d.isoformat(),
                        "cutoff": cutoff.isoformat(),
                        "training_cutoff": trained.isoformat(),
                        "outlet": outlet,
                        "product": sku,
                        "actual": counts[outlet, d, sku],
                        "b1": b1,
                        "bakeplan": forecast["forecast_mean"],
                        "method": forecast["method"],
                    }
                )
        forecasts.extend(group)
        aggregate.append({"outlet": outlet, "product": "ALL", **measure(group)})
        for sku in skus:
            aggregate.append(
                {
                    "outlet": outlet,
                    "product": sku,
                    **measure([r for r in group if r["product"] == sku]),
                }
            )
        details["outlets"][outlet] = {
            "selected_train_receipt_counts": {
                sku: train_counts[outlet][sku] for sku in skus
            },
            "all_days_with_receipts": len(active_days),
            "all_receipts": sum(receipts[outlet, d] for d in active_days),
            "test_days": len({r["date"] for r in group}),
            "model_snapshots": len(models),
            "missing_test_dates": [
                (START + i * ONE_DAY).isoformat()
                for i in range((END - START).days + 1)
                if (START + i * ONE_DAY) not in active_days
            ],
        }
    aggregate.insert(0, {"outlet": "ALL", "product": "ALL", **measure(forecasts)})
    save_csv(args.out / "cafe_predictions.csv", forecasts)
    save_csv(args.out / "cafe_metrics.csv", aggregate)
    save_csv(
        args.out / "cafe_daily.csv",
        sorted(daily, key=lambda r: (r["date"], r["outlet"], r["product"])),
    )
    details["metrics"] = aggregate
    (args.out / "cafe_results.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps([r for r in aggregate if r["product"] == "ALL"], indent=2))


if __name__ == "__main__":
    main()
