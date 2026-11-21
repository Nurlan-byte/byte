"""Compare the shipping order forecast with both published references on paired product-days."""
import argparse
from collections import Counter, defaultdict
from datetime import date
import json
import math
from pathlib import Path
import sys

from evaluate_bakery import get_data
from export_results import FORMATS, load_rows, replay_rows
from verify_results import RESULTS, verify_sample

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.chronos_order import PROTOCOL_VERSION, forecast_order


def daily_history(sample, folder, bakery_source, single_site):
    if sample == "bakery":
        _, rows, _ = get_data(bakery_source)  # Verifies the original source checksum.
        product, quantity, site = "product_id", "sold", None
    else:
        filename, product, quantity, site = {
            "cafe": ("cafe_daily.csv", "product", "receipts_with_product", "outlet"),
            "vending": ("vending_daily.csv", "product", "observed_units", "machine"),
            "grocery": ("completejourney_daily.csv", "product_id", "sold", None),
        }[sample]
        rows = load_rows(folder, filename)
    observations = defaultdict(dict)
    for row in rows:
        branch = row[site] if site else single_site
        key = row["date"], row[product]
        if key in observations[branch]:
            raise ValueError(f"{sample}: duplicate daily observation at {branch}, {key}")
        observations[branch][key] = int(row[quantity])
    return observations


def evaluate(sample, folder, bakery_source, days):
    refs = replay_rows(sample, folder)
    verify_sample(sample, refs, RESULTS / sample)
    history = daily_history(sample, folder, bakery_source, refs[0]["site"])
    target_dates = sorted({row["date"] for row in refs})[-days:]
    batches = defaultdict(list)
    for row in refs:
        if history[row["site"]].get((row["date"], row["product"])) != row["actual"]:
            raise ValueError(f"{sample}: recorded outcome and daily observations disagree at {row['date']}")
        if row["date"] in target_dates:
            batches[row["site"], row["date"], row["cutoff"]].append(row)
    field = "item_line_count" if sample == "cafe" else "sold"
    errors = defaultdict(list)
    methods = Counter()
    for (site, day, cutoff), labels in sorted(batches.items()):
        observed = [{"date": d, "product_id": sku, field: units}
                    for (d, sku), units in history[site].items() if d <= cutoff]
        data = {"target": "item_line_count" if sample == "cafe" else "sold_units",
                "unit": "item_line" if sample == "cafe" else "physical_unit", "rows": observed}
        decisions = forecast_order(data, date.fromisoformat(day), date.fromisoformat(cutoff),
                                   sorted(row["product"] for row in labels))
        for row in labels:
            decision = decisions[row["product"]]
            if decision["b1_mean"] != float(row["b1"]) or decision["forecast_mean"] is None:
                raise ValueError(f"{sample}: unavailable or unpaired forecast at {day} for {row['product']}")
            methods[decision["method"]] += 1
            for name, value in (("b1", row["b1"]), ("prior", row["bakeplan"]), ("chosen", decision["forecast_mean"])):
                errors[name].append(abs(float(value) - row["actual"]))
    mae = {name: math.fsum(values) / len(values) for name, values in errors.items()}
    return {"pairs": methods.total(), "dates": target_dates, "methods": dict(methods), "mae": mae,
            "change_vs_prior_pct": 100 * (mae["chosen"] / mae["prior"] - 1) if mae["prior"] else None,
            "change_vs_b1_pct": 100 * (mae["chosen"] / mae["b1"] - 1) if mae["b1"] else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench", required=True, type=Path, help="Directory containing the four local replay folders")
    parser.add_argument("--bakery-source", required=True, type=Path, help="Original French bakery ZIP (v1)")
    parser.add_argument("--days", default=7, type=int, help="Last scored dates per source (default: 7)")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be positive")
    summaries = {name: evaluate(name, args.bench / name, args.bakery_source, args.days) for name in FORMATS}
    print(json.dumps({"protocol": PROTOCOL_VERSION, "sources": summaries}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
