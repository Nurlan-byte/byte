"""Test one outlet-level weekday effect on earlier unseen dates and the historical regression."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

from evaluate_receipts import BENCH, SPEC, day, gate, load_history, original_week, paired_rows, summarize

PROTOCOL = BENCH / "receipt_weekday_protocol.json"
MODEL = "pooled_weekday"


def weekday_means(known, origin, target):
    if len(known) < 2:
        return {}
    complete = set(range(origin - 55, origin + 1)).intersection(*(series.keys() for series in known.values()))
    weekdays = [d for d in complete if (target - d) % 7 == 0]
    if len(complete) < 28 or len(weekdays) < 4:
        return {}
    sums = {sku: sum(series[d] for d in complete) for sku, series in known.items()}
    total = sum(sums.values())
    if not total:
        return {}
    rate = total / len(complete)
    weekday_total = sum(series[d] for series in known.values() for d in weekdays)
    multiplier = (weekday_total + rate) / ((len(weekdays) + 1) * rate)
    return {sku: count / len(complete) * multiplier for sku, count in sums.items()}


def run(folder, plan, source):
    results = {}
    for window in ("fresh", "regression"):
        split = plan[window]
        spec = {**source, "assortment_cutoff": split["assortment_cutoff"]}
        cohorts, history = load_history(folder, spec)
        results[window] = {}
        for name, assortment in cohorts.items():
            print(f"Evaluating {window} {name}", flush=True)
            rows = paired_rows(history, assortment, split["dates"], [], source["horizon"])
            groups = defaultdict(list)
            for row in rows:
                groups[row["site"], row["date"]].append(row)
            for (site, target_date), group in groups.items():
                target = day(target_date)
                known = {sku: history[site, sku] for sku in assortment[site]}
                forecasts = weekday_means(known, target - source["horizon"], target)
                for row in group:
                    row[MODEL] = (forecasts.get(row["sku"], row["baseline"])
                                  if row["baseline"] is not None else None)
            results[window][name] = summarize(rows, MODEL)
            if window == "regression" and name == "cafe":
                results["original_week"] = original_week(rows, MODEL, spec)
    return results


def verdict(results, spec):
    failures = []
    for split in ("fresh", "regression"):
        for name, score in results[split].items():
            decision = gate({name: score}, MODEL, spec)
            failures.extend(split + "/" + reason for reason in decision["reasons"])
    week = results["original_week"]["all"]
    if week["n"] != 126 or week["candidate_ae_sum"] > week["legacy_ae_sum"]:
        failures.append("original_week:legacy")
    return {"passed": not failures, "reasons": failures}


def fingerprint(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def verify(results, plan, source):
    if (results["protocol"] != plan["id"] or results["protocol_sha256"] != fingerprint(PROTOCOL)
            or results["script_sha256"] != fingerprint(Path(__file__))
            or results["source_sha256"] != source["source_sha256"]):
        raise ValueError("Weekday experiment provenance differs")
    for split in ("fresh", "regression"):
        scores = results[split]
        if set(scores) != {"cafe", "snacks"}:
            raise ValueError("Both assortments are required")
        # In this pinned source all three outlets have complete trading dates and
        # six selected drinks / nine selected snacks at both assortment cutoffs.
        days = day(plan[split]["dates"][1]) - day(plan[split]["dates"][0]) + 1
        for cohort, row in scores.items():
            expected = len(source["outlets"]) * {"cafe": 6, "snacks": 9}[cohort] * days
            if ([row["start"], row["end"]] != plan[split]["dates"] or row["n"] != expected
                    or not 0 <= row["zero_actuals"] <= row["n"] or row["negative_actuals"] != 0
                    or any(row[key] != row["n"] for key in ("expected", "baseline_covered", "candidate_covered"))):
                raise ValueError("Incomplete paired forecast window")
            for name in ("baseline", "candidate"):
                if not math.isclose(row[name + "_mae"] * row["n"], row[name + "_ae_sum"]):
                    raise ValueError("Inconsistent weekday MAE")
    reference = json.loads((BENCH / "results/receipt_quality.json").read_text(encoding="utf-8"))
    for name, row in results["regression"].items():
        ref = reference["partitions"]["holdout"]["sources"][name]
        if (row["data_sha256"] != ref["data_sha256"] or not math.isclose(
                row["baseline_ae_sum"], ref["baseline_ae_sum"], abs_tol=1e-4)):
            raise ValueError("September product-days or baseline differ")
    week = results["original_week"]
    if (set(week["by_outlet"]) != set(source["outlets"])
            or [week["start"], week["end"]] != source["historical_diagnostic"]):
        raise ValueError("Original window or outlets differ")
    for key in ("n", "legacy_ae_sum", "baseline_ae_sum", "candidate_ae_sum"):
        total = week["all"][key]
        if (not math.isclose(total, week["zero"][key] + week["positive"][key], abs_tol=1e-9)
                or not math.isclose(total, math.fsum(row[key] for row in week["by_outlet"].values()), abs_tol=1e-9)):
            raise ValueError("Original error breakdown differs")
        if key != "candidate_ae_sum" and not math.isclose(total, reference["original_week"]["all"][key], abs_tol=1e-4):
            raise ValueError("Original comparison differs")
    if results["gate"] != verdict(results, source):
        raise ValueError("Decision differs from the paired results")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path)
    args = parser.parse_args()
    plan, source = (json.loads(path.read_text(encoding="utf-8")) for path in (PROTOCOL, SPEC))
    if args.verify_report:
        results = json.loads(args.verify_report.read_text(encoding="utf-8"))
    else:
        if not args.data or not args.output:
            parser.error("--data and --output required for a fresh replay")
        results = run(args.data, plan, source)
        results.update(protocol=plan["id"], protocol_sha256=fingerprint(PROTOCOL),
                       script_sha256=fingerprint(Path(__file__)), source_sha256=source["source_sha256"],
                       gate=verdict(results, source))
        verify(results, plan, source)
        args.output.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        results = json.loads(args.output.read_text(encoding="utf-8"))
    verify(results, plan, source)
    print(json.dumps(results["gate"]))


if __name__ == "__main__":
    main()
