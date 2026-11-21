"""Walk-forward comparison of sparse retail forecasts with the shipping order engine."""
import argparse
import csv
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

BENCHMARK = Path(__file__).resolve().parents[1]
ROOT = BENCHMARK.parents[1]
sys.path.insert(0, str(ROOT))
PROTOCOL = BENCHMARK / "sparse_tsb_protocol.json"
SPEC = json.loads(PROTOCOL.read_text(encoding="utf-8"))
GROUPS = ("m5_CA_1", "m5_TX_1", "m5_WI_1", "vending")
GAP = timedelta(days=SPEC["horizon_days"])
MODELS = ("baseline", "b1", "candidate")


def fingerprints():
    files = (PROTOCOL, Path(__file__), ROOT / "backend/chronos_order.py", ROOT / "backend/forecasting.py",
             ROOT / "backend/chronos_assets.py")
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
            for path in files}


def read_panel(path, vending=False, *, expected_digest=None):
    """Missing item-dates stay missing. The source preparer fixes the roster before development."""
    digest = hashlib.sha256(path.read_text(encoding="utf-8-sig").encode()).hexdigest()
    if expected_digest is None:
        expected_digest = SPEC["panel_lf_sha256"]["vending" if vending else "m5"]
    if digest != expected_digest:
        raise ValueError("Source panel differs from the frozen input")
    stores = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            site = row["machine"] if vending else row["site"]
            if vending and site != "GuttenPlans x1367":
                raise ValueError("Unexpected vending machine")
            group = "vending" if vending else "m5_" + site
            product = row["product"]
            day = date.fromisoformat(row["date"])
            units = int(row["observed_units"] if vending else row["sold"])
            history = stores.setdefault(group, {}).setdefault(product, {})
            if not product or units < 0 or day in history:
                raise ValueError(f"Invalid or duplicate observation for {group}, {product}, {day}")
            history[day] = units
    if any(len(products) != 6 for products in stores.values()):
        raise ValueError("Expected six preselected products per store")
    return stores


def split_dates(days):
    days = sorted(set(days))
    holdout = days[-SPEC["holdout_days"]:]
    if len(holdout) != SPEC["holdout_days"]:
        raise ValueError("Too few holdout dates")
    development = [d for d in days if d <= holdout[0] - GAP][-SPEC["development_days"]:]
    if len(development) != SPEC["development_days"]:
        raise ValueError("Too few development dates before the holdout cutoff")
    return development, holdout


def recent_values(history, cutoff):
    return [history[day] for day in sorted(history) if cutoff - timedelta(days=56) < day <= cutoff]


def eligible(values):
    return len(values) >= 28 and 2 * values.count(0) > len(values)


def point(candidate, baseline, tsb, recent):
    if tsb is None:
        return baseline
    if candidate == "tsb":
        return tsb
    if candidate == "half_cap_above_recent_mean":
        tsb = min(baseline, max(tsb, math.fsum(recent) / len(recent)))
    elif candidate == "half_cap_tsb":
        tsb = min(baseline, tsb)
    elif candidate != "half_tsb":
        raise ValueError("Unknown candidate")
    return (baseline + tsb) / 2


def tsb_worker():
    """The benchmark environment owns StatsForecast; the production engine keeps its locked dependencies."""
    from importlib.metadata import version
    import numpy as np
    from statsforecast.models import TSB

    model = TSB(**SPEC["tsb"])
    means = []
    for values in json.load(sys.stdin):
        forecast = model.forecast(y=np.asarray(values, dtype=np.float64), h=SPEC["horizon_days"])["mean"]
        value = float(forecast[-1])
        if not math.isfinite(value) or value < 0:
            raise ValueError("Invalid StatsForecast output")
        means.append(value)
    json.dump({"statsforecast_version": version("statsforecast"), "forecasts": means}, sys.stdout, allow_nan=False)


def replay(products, days, python):
    from backend.chronos_order import forecast_order

    digest = hashlib.sha256()
    for sku, history in sorted(products.items()):
        for day, units in sorted(history.items()):
            if day <= days[-1]:
                digest.update(json.dumps([sku, day.isoformat(), units]).encode() + b"\n")
    pairs = []
    for day in days:
        cutoff = day - GAP
        rows = [{"date": when.isoformat(), "product_id": sku, "sold": amount}
                for sku, history in products.items() for when, amount in history.items() if when <= cutoff]
        decisions = forecast_order({"rows": rows, "target": "sold_units", "unit": "physical_unit"},
                                   day, cutoff, sorted(products))
        for sku, history in sorted(products.items()):
            decision = decisions[sku]
            if day not in history or decision["forecast_mean"] is None or decision["b1_mean"] is None:
                continue
            recent = recent_values(history, cutoff)
            pairs.append({"actual": history[day], "baseline": decision["forecast_mean"], "b1": decision["b1_mean"],
                          "recent": recent if eligible(recent) else [], "tsb": None})
    eligible_pairs = [p for p in pairs if p["recent"]]
    response = subprocess.run([str(python), str(Path(__file__).resolve()), "--tsb-worker"],
                              input=json.dumps([p["recent"] for p in eligible_pairs]), capture_output=True, text=True)
    if response.returncode:
        raise RuntimeError(f"StatsForecast evaluation failed: {response.stderr}")
    result = json.loads(response.stdout)
    for pair, value in zip(eligible_pairs, result["forecasts"], strict=True):
        pair["tsb"] = value
    return pairs, digest.hexdigest(), result["statsforecast_version"]


def scores(pairs, candidate):
    predictions = {
        "candidate": [point(candidate, p["baseline"], p["tsb"], p["recent"]) for p in pairs],
        "baseline": [p["baseline"] for p in pairs],
        "b1": [p["b1"] for p in pairs],
    }
    actuals = [p["actual"] for p in pairs]
    n, actual = len(pairs), sum(actuals)
    result = {"n": n, "actual_sum": actual, "positive_days": sum(a > 0 for a in actuals)}
    for model, values in predictions.items():
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid paired forecast")
        total = math.fsum(values)
        absolute = math.fsum(abs(v - a) for v, a in zip(values, actuals, strict=True))
        result.update({model + "_sum": total, model + "_abs_error_sum": absolute,
                       model + "_mae": absolute / n if n else None, model + "_bias_units": total - actual,
                       model + "_bias_pct": 100 * (total - actual) / actual if actual else None})
    return result


def summarize(pairs, candidate, days, count, digest):
    n_planned = len(days) * count
    return {**scores(pairs, candidate), "start": days[0].isoformat(), "end": days[-1].isoformat(),
            "dates": [d.isoformat() for d in days], "data_sha256": digest,
            "planned": n_planned, "coverage_pct": 100 * len(pairs) / n_planned,
            "eligible": scores([p for p in pairs if p["tsb"] is not None], candidate)}


def improvement(record):
    return 100 * (1 - record["candidate_mae"] / record["baseline_mae"])


def decision(records, groups=GROUPS):
    limits = SPEC["gate"]
    failures = []
    gains, sparse_gains = [], []
    for name in groups:
        group = records[name]
        gains.append(improvement(group))
        if gains[-1] < -limits["per_source_mae_increase_max_pct"] - 1e-9:
            failures.append(name + ":mae")
        if abs(group["candidate_bias_pct"]) - abs(group["baseline_bias_pct"]) > limits["per_source_absolute_volume_bias_increase_max_pp"]:
            failures.append(name + ":bias")
        if group["coverage_pct"] < limits["minimum_paired_coverage_pct"]:
            failures.append(name + ":coverage")
        cohort = group["eligible"]
        if cohort["n"] < limits["minimum_eligible_pairs_per_source"] or cohort["positive_days"] < limits["minimum_eligible_positive_days_per_source"] or not cohort["baseline_mae"]:
            failures.append(name + ":eligible_evidence")
        else:
            sparse_gains.append(improvement(cohort))
    mean = math.fsum(gains) / len(gains)
    if mean < limits["mean_relative_mae_improvement_min_pct"]:
        failures.append("mean_mae")
    if not sparse_gains or math.fsum(sparse_gains) <= 0:
        failures.append("eligible_mae")
    return {"passed": not failures, "reasons": failures, "thresholds": limits, "mean_relative_mae_improvement_pct": mean}


def selection_key(records):
    result = decision(records)
    violations = sum(r.endswith((":mae", ":bias")) for r in result["reasons"])
    return violations, -result["mean_relative_mae_improvement_pct"]


def evaluate(m5, vending, python):
    from backend.chronos_order import PROTOCOL_VERSION

    panels = read_panel(m5) | read_panel(vending, vending=True)
    if set(panels) != set(GROUPS):
        raise ValueError("Incomplete store panels")
    dates = {key: split_dates(day for h in stores.values() for day in h) for key, stores in panels.items()}
    versions = set()

    def run(index, candidates):
        results = {name: {} for name in candidates}
        for key in GROUPS:
            target_days = dates[key][index]
            pairs, digest, version = replay(panels[key], target_days, python)
            versions.add(version)
            for name in candidates:
                results[name][key] = summarize(pairs, name, target_days, len(panels[key]), digest)
            print(f"{('development','holdout')[index]} {key}: {len(pairs)} paired forecasts", file=sys.stderr, flush=True)
        return results

    development = run(0, SPEC["candidates"])
    winner = min(SPEC["candidates"], key=lambda name: selection_key(development[name]))
    print("Selected on development:", winner, file=sys.stderr, flush=True)
    holdout = run(1, [winner])[winner]
    if len(versions) != 1:
        raise ValueError("StatsForecast version changed during evaluation")
    return {"schema_version": 1, "protocol": SPEC["id"], "baseline_commit": SPEC["baseline_commit"],
            "baseline_protocol": PROTOCOL_VERSION, "target": SPEC["target"], "candidate": winner,
            "statsforecast_version": versions.pop(), "fingerprints": fingerprints(),
            "development_selection": {name: list(selection_key(records)) for name, records in development.items()},
            "partitions": {"development": {"sources": development[winner]}, "holdout": {"sources": holdout}},
            "gate": decision(holdout)}


def verify_scores(record):
    n, actual = record["n"], record["actual_sum"]
    if type(n) is not int or type(actual) is not int or not 0 <= record["positive_days"] <= min(n, actual):
        raise ValueError("Invalid counts")
    for model in MODELS:
        total, ae = (record[model + s] for s in ("_sum", "_abs_error_sum"))
        if not all(math.isfinite(x) and x >= 0 for x in (total, ae)) or ae + 1e-7 < abs(total - actual):
            raise ValueError("Invalid prediction totals")
        for field, expected in (("_mae", ae / n if n else None), ("_bias_units", total - actual),
                                ("_bias_pct", 100 * (total - actual) / actual if actual else None)):
            observed = record[model + field]
            if (expected is None and observed is not None) or (expected is not None and
                    (observed is None or not math.isclose(observed, expected, abs_tol=1e-9))):
                raise ValueError(f"Inconsistent {model}{field}")


def verify(report):
    if (report["fingerprints"] != fingerprints() or report["schema_version"] != 1 or
            report["protocol"] != SPEC["id"] or report["baseline_commit"] != SPEC["baseline_commit"] or
            report["target"] != SPEC["target"] or report["statsforecast_version"] != "2.0.3" or
            report["baseline_protocol"] != "chronos-2-29ec3766d36d-expected-volume-sparse-b1-v1"):
        raise ValueError("Stale sparse experiment or code fingerprint")
    partitions = report["partitions"]
    for split in ("development", "holdout"):
        records = partitions[split]["sources"]
        if set(records) != set(GROUPS):
            raise ValueError("Missing source")
        for key, record in records.items():
            days = [date.fromisoformat(value) for value in record["dates"]]
            if (len(days) != SPEC[split + "_days"] or days != sorted(set(days))
                    or [days[0].isoformat(), days[-1].isoformat()] != [record["start"], record["end"]]
                    or record["planned"] != len(days) * 6 or not 0 < record["n"] <= record["planned"]
                    or len(bytes.fromhex(record["data_sha256"])) != 32
                    or not math.isclose(record["coverage_pct"], record["n"] * 100 / record["planned"])):
                raise ValueError("Invalid dates or coverage")
            if split == "holdout" and date.fromisoformat(partitions["development"]["sources"][key]["end"]) + GAP > days[0]:
                raise ValueError("Training labels overlap the holdout cutoff")
            verify_scores(record)
            verify_scores(record["eligible"])
            if record["eligible"]["n"] > record["n"] or record["eligible"]["actual_sum"] > record["actual_sum"]:
                raise ValueError("Eligible cohort exceeds full source")
    selection = report["development_selection"]
    winner = report["candidate"]
    if (set(selection) != set(SPEC["candidates"]) or min(SPEC["candidates"], key=selection.get) != winner or
            list(selection_key(partitions["development"]["sources"])) != selection[winner] or
            decision(partitions["holdout"]["sources"]) != report["gate"]):
        raise ValueError("Selection or gate differs from scores")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m5-panel", type=Path)
    parser.add_argument("--vending-daily", type=Path)
    parser.add_argument("--tsb-python", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path)
    parser.add_argument("--tsb-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.tsb_worker:
        tsb_worker()
        return
    if args.verify_report:
        verify(json.loads(args.verify_report.read_text(encoding="utf-8")))
        print("Verified sparse forecast report")
        return
    if not all((args.m5_panel, args.vending_daily, args.tsb_python, args.output)):
        parser.error("Provide --m5-panel, --vending-daily, --tsb-python and --output")
    report = evaluate(args.m5_panel, args.vending_daily, args.tsb_python.resolve())
    verify(report)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    verify(json.loads(args.output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
