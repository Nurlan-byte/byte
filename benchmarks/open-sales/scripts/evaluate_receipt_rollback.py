"""Check the fixed legacy Ridge/B1 receipt policy against the shipping Chronos-2/B1 policy."""
import argparse
import csv
from datetime import date, timedelta
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path

from evaluate_receipts import BENCH, LEGACY, ROOT, SPEC, iso, load_history, paired_rows, summarize, verify_report
from uci_receipt_source import load_uci
from backend.ml_core import prepare_forecast, train_model

POLICY = BENCH / "receipt_rollback_protocol.json"


def fingerprints():
    files = [POLICY, SPEC, LEGACY, Path(__file__), Path(__file__).with_name("evaluate_receipts.py"),
             Path(__file__).with_name("uci_receipt_source.py")]
    files += [ROOT / "backend" / name for name in
              ("ml_core.py", "chronos_order.py", "forecasting.py", "demand_context.py", "chronos_assets.py")]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in files}


def add_legacy_forecasts(history, assortment, rows):
    snapshots = {}
    for (site, target, origin), group in groupby(rows, key=lambda r: (r["site"], r["date"], r["origin"])):
        cutoff = date.fromisoformat(origin)
        monday = cutoff - timedelta(days=cutoff.weekday())
        dataset = {"id": "receipts-" + site, "version": "1", "bakery_id": site, "target": "item_line_count",
                   "unit": "item_line", "planning_kind": "procurement", "rows": [
                       {"date": iso(d), "product_id": sku, "item_line_count": n, "is_open": True,
                        "stockout_status": "unknown"}
                       for sku in assortment[site] for d, n in history[site, sku].items()
                       if d <= cutoff.toordinal()
                   ]}
        if (site, monday) not in snapshots:
            fit = {**dataset, "rows": [r for r in dataset["rows"] if r["date"] <= monday.isoformat()]}
            snapshots[site, monday] = train_model(fit, monday)
        batch = prepare_forecast(snapshots[site, monday], dataset, date.fromisoformat(target), cutoff)
        for row in group:
            point = batch.predict(row["sku"])
            row.update(rollback=point["forecast_mean"], fitted_through=monday.isoformat(), method=point["method"])


def historical_week(rows, spec):
    with LEGACY.open(encoding="utf-8-sig", newline="") as source:
        archived = {(r["site"], r["product"], r["date"]): r for r in csv.DictReader(source)}
    start, end = spec["historical_week"]
    week = [r for r in rows if start <= r["date"] <= end]
    differences = []
    legacy_errors = []
    for row in week:
        old = archived[row["site"], row["sku"], row["date"]]
        if (old["cutoff"], old["training_cutoff"]) != (row["origin"], row["fitted_through"]):
            raise ValueError("Historical fit and issue dates differ")
        point = float(old["bakeplan"])
        differences.append(abs(row["rollback"] - point))
        legacy_errors.append(abs(row["actual"] - point))
    return {**summarize(week, "rollback"), "published_legacy_mae": math.fsum(legacy_errors)/len(week),
            "maximum_abs_prediction_difference": max(differences)}


def decision(week, cohorts, limits):
    failures = []
    if week["maximum_abs_prediction_difference"] > limits["max_historical_prediction_difference"]:
        failures.append("original_week:reproduction")
    if week["candidate_mae"] > week["published_legacy_mae"] + limits["max_historical_prediction_difference"]:
        failures.append("original_week:mae")
    if week["expected"] != 126 or week["n"] != week["expected"]:
        failures.append("original_week:coverage")
    for name in limits["all_required"]:
        part = cohorts[name]
        if part["n"] != part["expected"]:
            failures.append(name + ":coverage")
        if 100 * (1 - part["candidate_mae"] / part["baseline_mae"]) < limits["min_mae_improvement_pct"]:
            failures.append(name + ":mae")
        if abs(part["candidate_bias_pct"]) - abs(part["baseline_bias_pct"]) > limits["max_absolute_bias_increase_pp"]:
            failures.append(name + ":volume_bias")
    return {"passed": not failures, "reasons": failures}


def evaluate(cafe_folder, archive, spec):
    cafe_spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if cafe_spec["id"] != spec["cafe_protocol"]:
        raise ValueError("Unknown cafe source protocol")
    groups, history = load_history(cafe_folder, cafe_spec)
    uci_group, uci_history = load_uci(archive, spec["uci"])
    datasets = {"cafe": (history, groups["cafe"], spec["cafe_holdout"]),
                "snacks": (history, groups["snacks"], spec["cafe_holdout"]),
                "uci": (uci_history, uci_group, spec["uci_holdout"])}
    report = {"protocol": spec["id"], "fingerprints": fingerprints(), "holdout": {}}
    for name, (records, assortment, window) in datasets.items():
        rows = paired_rows(records, assortment, window, [], spec["horizon"])
        add_legacy_forecasts(records, assortment, rows)
        report["holdout"][name] = summarize(rows, "rollback")
        if name == "cafe":
            report["original_week"] = historical_week(rows, spec)
    report["gate"] = decision(report["original_week"], report["holdout"], spec["gate"])
    return report


def verify(report, spec):
    if report["protocol"] != spec["id"] or report["fingerprints"] != fingerprints():
        raise ValueError("Rollback report does not match current protocol and source fingerprints")
    if set(report["holdout"]) != set(spec["gate"]["all_required"]):
        raise ValueError("Missing or unexpected evaluation cohort")
    reference = json.loads((BENCH / "results/receipt_quality.json").read_text(encoding="utf-8"))
    verify_report(reference, json.loads(SPEC.read_text(encoding="utf-8")))
    for name, source in reference["partitions"]["holdout"]["sources"].items():
        scores = report["holdout"][name]
        check_observations(name, scores, spec["cafe_holdout"], source["expected"], source)
        for metric in ("baseline_mae", "baseline_bias_pct"):
            if not math.isclose(scores[metric], source[metric], abs_tol=1e-5):
                raise ValueError(f"{name}: the incumbent differs from the reference replay")
    uci = spec["uci"]["outcomes"]
    check_observations("uci", report["holdout"]["uci"], spec["uci_holdout"], uci["trading_days"] * spec["uci"]["top_n"], uci)
    week = reference["original_week"]
    check_observations("original_week", report["original_week"], spec["historical_week"], week["all"]["n"],
                       {**spec["historical_outcomes"], "zero_actuals": week["zero"]["n"]})
    if not math.isclose(report["original_week"]["published_legacy_mae"] * week["all"]["n"], week["all"]["legacy_ae_sum"]):
        raise ValueError("original_week: legacy error differs from the archived comparison")
    for scores in [*report["holdout"].values(), report["original_week"]]:
        for model in ("baseline", "candidate"):
            if scores["n"] < 1 or not math.isclose(scores[model+"_mae"], scores[model+"_ae_sum"] / scores["n"]):
                raise ValueError("Stored error does not match paired sum")
    if report["gate"] != decision(report["original_week"], report["holdout"], spec["gate"]):
        raise ValueError("Stored release decision does not match the fixed gate")


def check_observations(name, scores, window, count, source):
    if ([scores["start"], scores["end"]] != window or scores["negative_actuals"] != 0
            or any(scores[key] != count for key in ("expected", "n", "baseline_covered", "candidate_covered"))
            or any(scores[key] != source[key] for key in ("actual_sum", "zero_actuals", "data_sha256"))):
        raise ValueError(f"{name}: dates, coverage or observed counts differ from the reference source")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="Directory with the two original Mendeley CSVs")
    parser.add_argument("--uci-archive", type=Path, help="Unmodified online+retail.zip from UCI")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path)
    args = parser.parse_args()
    spec = json.loads(POLICY.read_text(encoding="utf-8"))
    path = args.verify_report or args.output
    if not args.verify_report:
        if not (args.data and args.uci_archive and args.output):
            parser.error("Pass --data, --uci-archive and --output for a fresh replay")
        result = evaluate(args.data, args.uci_archive, spec)
        verify(result, spec)
        path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))
    verify(saved, spec)
    print(json.dumps(saved["gate"]))


if __name__ == "__main__":
    main()
