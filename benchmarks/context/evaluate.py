"""Compare the deployed BakePlan decision (including B1 fallbacks) on identical future dates."""

import argparse
import csv
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean
import sys

from sources import ONE_DAY, cafe_inputs, dates, seoul_inputs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def predict_rows(base, calendar, weather, start, stop):
    from backend.demand_context import with_demand_context
    from backend.ml_core import prepare_forecast, train_model

    scenarios = {
        "prior": base,
        "calendar": with_demand_context(base, {"calendar": calendar}),
        "weather_calendar": with_demand_context(
            base, {"calendar": calendar, "weather": weather}
        ),
    }
    scenarios["auto"] = scenarios["weather_calendar"]
    models = {}
    weekly = None
    value_field = "sold" if base["target"] == "sold_units" else "item_line_count"
    actual = {day: {} for day in dates(start, stop)}
    for row in base["rows"]:
        day = date.fromisoformat(row["date"])
        if day in actual:
            actual[day][row["product_id"]] = row[value_field]
    for day, products in actual.items():
        if not products:
            continue
        cutoff = day - 2 * ONE_DAY
        training_cutoff = cutoff - (day - start).days % 7 * ONE_DAY
        if training_cutoff != weekly:
            weekly = training_cutoff
            models = {
                k: train_model(ds, weekly, select_context=k == "auto")
                for k, ds in scenarios.items()
            }
        batch = {
            k: prepare_forecast(models[k], ds, day, cutoff)
            for k, ds in scenarios.items()
        }
        for sku, label in products.items():
            result = {
                "date": str(day),
                "cutoff": str(cutoff),
                "trained": str(weekly),
                "location": base["bakery_id"],
                "series": sku,
                "actual": label,
            }
            for name, predictor in batch.items():
                forecast = predictor.predict(sku)
                if forecast["forecast_mean"] is None:
                    raise ValueError(
                        f"Expected a paired forecast for {day}, {sku}, {name}"
                    )
                if name == "prior":
                    result["B1"] = forecast["b1_mean"]
                elif forecast["b1_mean"] != result["B1"]:
                    raise ValueError("Comparisons must share B1 and the target history")
                result[name] = forecast["forecast_mean"]
                result[name + "_method"] = forecast["method"]
                if name == "auto":
                    choice = models[name]["products"][sku].get("selection", {})
                    result["auto_candidate"] = choice.get("selected", "unavailable")
            yield result


def metrics(rows):
    predictions = ("B1", "prior", "calendar", "weather_calendar", "auto")
    output = {"pairs": len(rows), "days": len({r["date"] for r in rows})}
    for name in predictions:
        output[name + "_mae"] = round(mean(abs(r[name] - r["actual"]) for r in rows), 6)
        if name != "B1":
            output[name + "_methods"] = dict(
                sorted(Counter(r[name + "_method"] for r in rows).items())
            )
    output["auto_candidate_days"] = dict(
        sorted(Counter(r["auto_candidate"] for r in rows).items())
    )
    return output


def check_published_cafe(rows):
    """Match each legacy prediction against the independently published earlier run."""
    index = {(r["date"], r["location"], r["series"]): r for r in rows}
    path = ROOT / "benchmarks/open-sales/results/cafe/forecasts.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        for old in csv.DictReader(f):
            key = (old["date"], old["site"], old["product"])
            new = index.pop(key)
            if (
                new["prior_method"] != old["method"]
                or new["cutoff"] != old["cutoff"]
                or new["trained"] != old["training_cutoff"]
                or float(old["b1"]) != new["B1"]
                or not math.isclose(
                    float(old["bakeplan"]), new["prior"], rel_tol=0, abs_tol=1e-9
                )
            ):
                raise ValueError(
                    f"Baseline no longer matches the public reference for {key}"
                )
    if index:
        raise ValueError("Missing keys in the legacy cafe reference")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seoul", type=Path, help="UCI Seoul Bike Sharing Demand ZIP")
    parser.add_argument(
        "--cafe",
        type=Path,
        help="Mendeley v2 directory with Transactions.csv and Products.csv",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        help="Optional per-date forecasts for independent inspection",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the computed metrics to the reference",
    )
    args = parser.parse_args()
    if not args.seoul and not args.cafe:
        parser.error("supply --seoul, --cafe, or both")
    groups = {}
    if args.seoul:
        groups["seoul"] = list(
            predict_rows(
                *seoul_inputs(args.seoul), date(2018, 9, 1), date(2018, 11, 30)
            )
        )
    if args.cafe:
        groups["cafe"] = []
        for dataset, calendar, weather in cafe_inputs(args.cafe):
            groups["cafe"].extend(
                predict_rows(
                    dataset, calendar, weather, date(2025, 8, 6), date(2025, 9, 30)
                )
            )
    report = {}
    for name, rows in groups.items():
        report[name] = {"all": metrics(rows)}
        if name == "cafe":
            check_published_cafe(rows)
            for loc in sorted({r["location"] for r in rows}):
                report[name][loc] = metrics([r for r in rows if r["location"] == loc])
    if args.predictions:
        rows = [row for group in groups.values() for row in group]
        with args.predictions.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(report, indent=2))
    if args.check:
        reference = json.loads(
            Path(__file__).with_name("results.json").read_text(encoding="utf-8")
        )
        for name in groups:
            if report[name] != reference[name]:
                raise SystemExit(f"Results differ from the recorded {name} reference")


if __name__ == "__main__":
    main()
