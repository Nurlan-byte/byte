"""Freeze a receipt-count estimator on development dates and evaluate it on untouched cohorts."""
import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import sys

from evaluate_cafe import load_csv
from receipt_candidates import estimate

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend import chronos_order  # noqa: E402

BENCH = Path(__file__).resolve().parents[1]
SPEC = BENCH / "receipt_quality_protocol.json"
LEGACY = BENCH / "results" / "cafe" / "forecasts.csv"


def day(text):
    return date.fromisoformat(text).toordinal()


def iso(ordinal):
    return date.fromordinal(ordinal).isoformat()


def fingerprints():
    paths = [SPEC, Path(__file__), LEGACY, Path(__file__).with_name("receipt_candidates.py"), ROOT / "backend/chronos_order.py",
             ROOT / "backend/chronos_assets.py", ROOT / "backend/forecasting.py", ROOT / "backend/demand_context.py"]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in paths}


def load_history(folder, spec):
    for name, digest in spec["source_sha256"].items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Wrong raw source: {name}")
    categories = {}
    for row in load_csv(folder / "Products.csv"):
        sku = row["ProductName"] if row["Variant"] == "-" else f"{row['ProductName']} ({row['Variant']})"
        categories[sku] = row["Category"]
    active, counts, ranking = defaultdict(set), defaultdict(Counter), defaultdict(Counter)
    last = day(spec["assortment_cutoff"])
    for receipt in load_csv(folder / "Transactions.csv"):
        site, when = receipt["Outlet"], day(receipt["Date"])
        labels = receipt["Items"].split(", ")
        if len(set(labels)) != int(receipt["TotalItem"]) or len(labels) != len(set(labels)):
            raise ValueError("Receipt incidence requires distinct named items")
        active[site].add(when)
        for sku in labels:
            counts[site, sku][when] += 1
            if when <= last:
                ranking[site][sku] += 1
    if set(active) != set(spec["outlets"]):
        raise ValueError("Raw source has different outlets")
    cohorts = {"cafe": {}, "snacks": {}}
    histories = {}
    for site in spec["outlets"]:
        ordered = sorted(ranking[site], key=lambda sku: (-ranking[site][sku], sku))
        cohorts["cafe"][site] = [s for s in ordered if categories[s] in {"Tea", "Coffee"}][:6]
        cohorts["snacks"][site] = sorted(s for s in ordered if categories[s] == "Snacks")
        for sku in cohorts["cafe"][site] + cohorts["snacks"][site]:
            observed = counts[site, sku]
            first = min(observed)
            histories[site, sku] = {d: observed[d] for d in sorted(active[site]) if d >= first}
    return cohorts, histories


def paired_rows(history, assortment, window, candidates, horizon, *, pos_history=False):
    rows = []
    start, end = map(day, window)
    for site, skus in assortment.items():
        for target in range(start, end + 1):
            origin = target - horizon
            available = {}
            for sku in skus:
                full = history[site, sku]
                if target not in full:
                    continue  # No observed store-day: not a known zero.
                known_end = origin
                if pos_history:
                    known_end = max((d for d, n in full.items() if d <= origin and n > 0), default=-1)
                available[sku] = {d: n for d, n in full.items() if d <= known_end}
            if not available:
                continue
            batch = {"target": "item_line_count", "unit": "item_line", "rows": [
                {"date": iso(d), "product_id": sku, "item_line_count": n}
                for sku, known in available.items() for d, n in known.items()
            ]}
            incumbent = chronos_order.forecast_order(batch, date.fromordinal(target),
                                                     date.fromordinal(origin), sorted(available))
            for sku, known in sorted(available.items()):
                base = incumbent[sku]["forecast_mean"]
                forecasts = {name: estimate(name, base, known, origin, target) for name in candidates}
                rows.append({"site": site, "sku": sku, "date": iso(target), "origin": iso(origin),
                             "actual": history[site, sku][target], "baseline": base, **forecasts})
    return rows


def summarize(rows, candidate):
    scored = [r for r in rows if r["baseline"] is not None and r[candidate] is not None]
    if not scored or any(r["actual"] < 0 for r in rows):
        raise ValueError("Expected paired, nonnegative receipt counts")
    actual = sum(r["actual"] for r in scored)
    result = {"expected": len(rows), "n": len(scored), "actual_sum": actual,
              "zero_actuals": sum(r["actual"] == 0 for r in scored), "negative_actuals": 0,
              "start": min(r["date"] for r in rows), "end": max(r["date"] for r in rows)}
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([row[k] for k in ("site", "sku", "date", "origin", "actual")]).encode() + b"\n")
    result["data_sha256"] = digest.hexdigest()
    for point, label in [("baseline", "baseline"), (candidate, "candidate")]:
        values = [r[point] for r in scored]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError(f"Invalid forecast: {point}")
        err = math.fsum(abs(r[point] - r["actual"]) for r in scored)
        result[f"{label}_covered"] = sum(r[point] is not None for r in rows)
        result[f"{label}_ae_sum"] = err
        result[f"{label}_mae"] = err / len(scored)
        result[f"{label}_bias_pct"] = 100 * (math.fsum(values) - actual) / actual if actual else None
    return result


def by_site(rows, candidate):
    return {"all": summarize(rows, candidate),
            "outlets": {s: summarize([r for r in rows if r["site"] == s], candidate)
                        for s in sorted({r["site"] for r in rows})}}


def bias_increase(score):
    return abs(score["candidate_bias_pct"]) - abs(score["baseline_bias_pct"])


def choose(dev, spec):
    allowed = [name for name in spec["candidates"] if bias_increase(dev[name]) <=
               spec["gate"]["max_absolute_bias_increase_pp"] and dev[name]["candidate_covered"] >= dev[name]["baseline_covered"]]
    return min(allowed, key=lambda name: dev[name]["candidate_mae"]) if allowed else "baseline"


def gate(scores, winner, spec):
    thresholds = spec["gate"]
    reasons = ["no_eligible_development_candidate"] if winner == "baseline" else []
    improvements = []
    for name, row in scores.items():
        improvement = 100 * (1 - row["candidate_mae"] / row["baseline_mae"])
        improvements.append(improvement)
        if improvement < thresholds["min_mae_improvement_pct"]:
            reasons.append(name + ":mae")
        if bias_increase(row) > thresholds["max_absolute_bias_increase_pp"]:
            reasons.append(name + ":volume_bias")
        if row["candidate_covered"] < row["baseline_covered"]:
            reasons.append(name + ":coverage")
    return {"passed": not reasons, "reasons": reasons, "thresholds": thresholds,
            "mean_relative_mae_improvement_pct": math.fsum(improvements) / len(improvements)}


def original_week(rows, winner, spec):
    legacy = {(r["site"], r["product"], r["date"]): r for r in load_csv(LEGACY)}
    start, end = spec["historical_diagnostic"]
    week = [r for r in rows if start <= r["date"] <= end]
    for r in week:
        old = legacy[r["site"], r["sku"], r["date"]]
        if old["cutoff"] != r["origin"]:
            raise ValueError("Legacy forecast has different cutoff")
        r["legacy"] = float(old["bakeplan"])

    def totals(group):
        return {"n": len(group), **{label + "_ae_sum": math.fsum(abs(r[key] - r["actual"]) for r in group)
                for key, label in [("baseline", "baseline"), (winner, "candidate"), ("legacy", "legacy")]}}

    return {"start": start, "end": end, "all": totals(week),
            "zero": totals([r for r in week if r["actual"] == 0]),
            "positive": totals([r for r in week if r["actual"] > 0]),
            "by_outlet": {s: totals([r for r in week if r["site"] == s]) for s in spec["outlets"]}}


def evaluate(folder, spec):
    cohorts, history = load_history(folder, spec)
    dev = paired_rows(history, cohorts["cafe"], spec["development"], spec["candidates"], spec["horizon"])
    scores = {name: summarize(dev, name) for name in spec["candidates"]}
    winner = choose(scores, spec)
    print(f"Frozen development choice: {winner}", file=sys.stderr, flush=True)
    tested = [] if winner == "baseline" else [winner]
    holdout, sensitivity = {}, {}
    for name, assortment in cohorts.items():
        print(f"Scoring {name} on held-out dates", file=sys.stderr, flush=True)
        rows = paired_rows(history, assortment, spec["holdout"], tested, spec["horizon"])
        holdout[name] = by_site(rows, winner)
        if name == "cafe":
            diagnostic = original_week(rows, winner, spec)
        sensitivity[name] = by_site(paired_rows(history, assortment, spec["holdout"], tested, spec["horizon"],
                                                pos_history=True), winner)
    checked = {name: part["all"] for name, part in holdout.items()}
    return {"schema_version": 1, "protocol": spec["id"], "target": "item_line_count", "baseline_commit": spec["baseline_commit"],
            "baseline_protocol": chronos_order.PROTOCOL_VERSION, "fingerprints": fingerprints(),
            "source_sha256": spec["source_sha256"], "assortments": cohorts, "candidate": winner,
            "development_scores": scores, "partitions": {
                "development": {"sources": {"cafe": summarize(dev, winner)}, "outlets": by_site(dev, winner)["outlets"]},
                "holdout": {"sources": checked, "outlets": {k: v["outlets"] for k, v in holdout.items()}}},
            "gate": gate(checked, winner, spec), "original_week": diagnostic, "pos_trailing_sensitivity": sensitivity}


def verify_report(report, spec):
    if report["fingerprints"] != fingerprints() or report["fingerprints"]["backend/chronos_order.py"] != spec["baseline_engine_sha256"]:
        raise ValueError("Stale receipt report: code or protocol changed")
    if (report["schema_version"] != 1 or report["protocol"] != spec["id"] or report["target"] != "item_line_count" or
            report["baseline_protocol"] != chronos_order.PROTOCOL_VERSION or
            report["baseline_commit"] != spec["baseline_commit"] or report["source_sha256"] != spec["source_sha256"]):
        raise ValueError("Unexpected provenance")
    cohorts = report["assortments"]
    outlets = set(spec["outlets"])
    if (set(cohorts) != {"cafe", "snacks"} or any(set(c) != outlets for c in cohorts.values()) or
            any(len(skus) != 6 for skus in cohorts["cafe"].values()) or
            any(not skus or len(skus) != len(set(skus)) for c in cohorts.values() for skus in c.values()) or
            {s for skus in cohorts["cafe"].values() for s in skus} & {s for skus in cohorts["snacks"].values() for s in skus}):
        raise ValueError("Expected disjoint cohorts and all three outlets")
    if set(report["development_scores"]) != set(spec["candidates"]):
        raise ValueError("Missing eligible development comparisons")
    winner = choose(report["development_scores"], spec)
    scores = report["partitions"]["holdout"]["sources"]
    if set(scores) != {"cafe", "snacks"} or report["candidate"] != winner or report["gate"] != gate(scores, winner, spec):
        raise ValueError("Decision differs from recorded comparison")
    for section in ("development", "holdout"):
        partition = report["partitions"][section]
        days = day(spec[section][1]) - day(spec[section][0]) + 1
        for cohort, metrics in partition["sources"].items():
            sites = partition["outlets"] if section == "development" else partition["outlets"][cohort]
            if set(sites) != outlets:
                raise ValueError("Missing outlet metrics")
            for site, part in sites.items():
                if (part["expected"] != days * len(cohorts[cohort][site]) or
                        [part["start"], part["end"]] != spec[section]):
                    raise ValueError("Missing eligible product-days")
            if ([metrics["start"], metrics["end"]] != spec[section] or
                    any(metrics[k] != sum(v[k] for v in sites.values()) for k in (
                        "expected", "n", "actual_sum", "baseline_covered", "candidate_covered", "zero_actuals", "negative_actuals")) or
                    metrics["n"] != metrics["expected"]):
                raise ValueError("Incomplete evaluation window")
            for label in ("baseline", "candidate"):
                ae = metrics[label + "_ae_sum"]
                bias = math.fsum(v[label + "_bias_pct"] * v["actual_sum"] for v in sites.values()) / metrics["actual_sum"]
                if (not math.isclose(metrics[label + "_mae"] * metrics["n"], ae) or
                        not math.isclose(ae, math.fsum(v[label + "_ae_sum"] for v in sites.values())) or
                        not math.isclose(bias, metrics[label + "_bias_pct"], abs_tol=1e-12)):
                    raise ValueError("Inconsistent MAE or volume bias")
    for cohort, part in report["pos_trailing_sensitivity"].items():
        if any(part["all"][k] != scores[cohort][k] for k in ("start", "end", "expected", "actual_sum", "data_sha256")):
            raise ValueError("Trailing-day sensitivity used different outcomes")
    diagnostic = report["original_week"]
    if ([diagnostic["start"], diagnostic["end"]] != spec["historical_diagnostic"] or
            set(diagnostic["by_outlet"]) != outlets or diagnostic["all"]["n"] != 126):
        raise ValueError("Original evaluation no longer contains 126 paired cases")
    for name in ("n", "baseline_ae_sum", "candidate_ae_sum", "legacy_ae_sum"):
        if (not math.isclose(diagnostic["all"][name], diagnostic["zero"][name] + diagnostic["positive"][name]) or
                not math.isclose(diagnostic["all"][name], math.fsum(s[name] for s in diagnostic["by_outlet"].values()))):
            raise ValueError("Original error decomposition does not add up")
    if (not math.isclose(diagnostic["all"]["legacy_ae_sum"], 87.11688965148196, abs_tol=1e-4) or
            not math.isclose(diagnostic["all"]["baseline_ae_sum"], 90.83450826376676, abs_tol=1e-4)):
        raise ValueError("Original seven-day regression was not reproduced")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="Folder with original Mendeley Transactions.csv and Products.csv")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path)
    args = parser.parse_args()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if args.verify_report:
        report = json.loads(args.verify_report.read_text(encoding="utf-8"))
    else:
        if not args.data or not args.output:
            parser.error("--data and --output required for a fresh replay")
        report = evaluate(args.data, spec)
        verify_report(report, spec)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        report = json.loads(args.output.read_text(encoding="utf-8"))
    verify_report(report, spec)
    print(json.dumps(report["gate"], ensure_ascii=False))


if __name__ == "__main__":
    main()
