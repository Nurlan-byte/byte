"""Freeze a demand estimator on development dates, then test it against the current order policy."""
import argparse
from collections import defaultdict
from datetime import date, timedelta
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

from evaluate_bakery import get_data
from evaluate_chronos_order import daily_history
from export_results import FORMATS, replay_rows
from verify_results import RESULTS, verify_sample

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend import chronos_order as engine
from volume_candidates import PairedPoints

PROTOCOL_FILE = Path(__file__).resolve().parents[1] / "quality_protocol.json"
ROOT = PROTOCOL_FILE.parents[2]


def fingerprints(spec):
    files = [*spec["baseline_files_sha256"], PROTOCOL_FILE.relative_to(ROOT).as_posix()]
    scripts = Path(__file__).resolve().parent.relative_to(ROOT)
    files.extend((scripts / name).as_posix() for name in (
        "evaluate_volume_quality.py", "volume_candidates.py", "evaluate_chronos_order.py",
        "evaluate_bakery.py", "export_results.py", "verify_results.py",
    ))
    return {path: hashlib.sha256((ROOT / path).read_text(encoding="utf-8").encode()).hexdigest()
            for path in files}


def verify_report(report):
    spec = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
    hashes = fingerprints(spec)
    if report["schema_version"] != 1 or report["protocol"] != spec["id"] or (
        report["baseline_commit"] != spec["baseline_commit"] or report["baseline_protocol"] != engine.PROTOCOL_VERSION
    ):
        raise ValueError("Wrong experiment or baseline protocol")
    if hashes != report["fingerprints"] or any(hashes[p] != v for p, v in spec["baseline_files_sha256"].items()):
        raise ValueError("Stale benchmark: source or protocol fingerprint changed")
    dev = report["development_selection_scores_pct"]
    if set(dev) != set(spec["candidates"]) or min(spec["candidates"], key=dev.get) != report["candidate"]:
        raise ValueError("Stored winner differs from the selection on development")
    for partition, required, days in [
        ("development", set(FORMATS), spec["development_first_scored_dates"]),
        ("holdout", set(FORMATS) | {"bakery_new_skus"}, spec["holdout_last_scored_dates"]),
    ]:
        sources = report["partitions"][partition]["sources"]
        if set(sources) != required:
            raise ValueError("Incomplete paired sources")
        for source in sources.values():
            dates = source["dates"]
            if (len(set(dates)) != days or dates != sorted(set(dates)) or
                [source["start"], source["end"]] != [dates[0], dates[-1]] or
                source["n"] < days or source["actual_sum"] <= 0 or
                len(bytes.fromhex(source["data_sha256"])) != 32 or
                not all(math.isfinite(source[k]) for k in ("baseline_mae", "candidate_mae", "baseline_bias_pct", "candidate_bias_pct"))):
                raise ValueError("Invalid source metrics or dates")
    development = report["partitions"]["development"]["sources"]
    holdout = report["partitions"]["holdout"]["sources"]
    if any(development[s]["end"] >= holdout[s]["start"] for s in development):
        raise ValueError("Development overlaps the holdout")
    if not math.isclose(aggregate_change(development), dev[report["candidate"]], abs_tol=1e-10):
        raise ValueError("Stored development score does not match source metrics")
    if gate(holdout, spec["gate"]) != report["gate"]:
        raise ValueError("Stored gate differs from paired holdout evidence")


def metrics(pairs):
    actual = sum(p["actual"] for p in pairs)
    if not pairs or actual <= 0:
        raise ValueError("A scored source needs observations and positive total sales")
    n = len(pairs)
    result = {"n": n, "actual_sum": actual}
    for name in ("baseline", "candidate"):
        result[name + "_mae"] = math.fsum(abs(p[name] - p["actual"]) for p in pairs) / n
        result[name + "_bias_pct"] = 100 * (math.fsum(p[name] for p in pairs) - actual) / actual
    if result["baseline_mae"] == 0:
        raise ValueError("A relative MAE comparison needs a non-zero baseline MAE")
    return result


def mae_change(source):
    return 100 * (source["candidate_mae"] / source["baseline_mae"] - 1)


def aggregate_change(sources):
    return math.fsum(mae_change(source) for source in sources.values()) / len(sources)


def gate(sources, limits):
    failures = []
    if set(sources) != set(FORMATS) | {"bakery_new_skus"}:
        raise ValueError("Holdout gate requires every original source and unseen bakery products")
    improvement = -aggregate_change(sources)
    if improvement < limits["mean_relative_mae_improvement_min_pct"]:
        failures.append("mean_relative_mae")
    for name, source in sources.items():
        if mae_change(source) > limits["per_source_mae_increase_max_pct"] + 1e-9:
            failures.append(name + ":mae")
        bias_increase = abs(source["candidate_bias_pct"]) - abs(source["baseline_bias_pct"])
        if bias_increase > limits["per_source_absolute_volume_bias_increase_max_pp"] + 1e-9:
            failures.append(name + ":volume_bias")
    return {"passed": not failures, "reasons": failures, "thresholds": limits,
            "mean_relative_mae_improvement_pct": improvement}


def sources(bench, bakery):
    for name in FORMATS:
        refs = replay_rows(name, bench / name)
        verify_sample(name, refs, RESULTS / name)
        history = daily_history(name, bench / name, bakery, refs[0]["site"])
        for r in refs:
            if r["actual"] != history[r["site"]][r["date"], r["product"]]:
                raise ValueError(f"Unpaired outcome for {name}")
        yield name, history, refs


def extra_products(bakery, scoring_dates):
    _, rows, _ = get_data(bakery, ranks=slice(6, 12))
    history = {(r["date"], r["product_id"]): r["sold"] for r in rows}
    site = "French bakery"
    refs = [
        {"date": day, "product": sku, "site": site, "actual": sold,
         "cutoff": (date.fromisoformat(day) - timedelta(days=2)).isoformat()}
        for (day, sku), sold in history.items() if day in scoring_dates
    ]
    return {site: history}, refs


def compare(name, history, refs, chosen_dates, candidates, points):
    field = "item_line_count" if name == "cafe" else "sold"
    grouped = defaultdict(dict)
    for row in refs:
        if row["date"] in chosen_dates:
            batch = grouped[row["site"], row["date"], row["cutoff"]]
            sku = row["product"]
            if sku in batch:
                raise ValueError("Duplicate product-day reference")
            batch[sku] = row
    digest = hashlib.sha256()
    for site, values in sorted(history.items()):
        for (day, sku), count in sorted(values.items()):
            if day <= max(chosen_dates):
                digest.update(json.dumps([site, day, sku, count], ensure_ascii=True).encode() + b"\n")
    pairs = {c: [] for c in candidates}
    for (site, when, asof), labels in sorted(grouped.items()):
        day, cutoff = date.fromisoformat(when), date.fromisoformat(asof)
        if (day - cutoff).days != 2:
            raise ValueError("Expected two-day issue lead")
        data = {"target": "item_line_count" if name == "cafe" else "sold_units",
                "unit": "item_line" if name == "cafe" else "physical_unit",
                "rows": [{"date": d, "product_id": sku, field: v}
                         for (d, sku), v in history[site].items() if d <= asof]}
        skus = sorted(labels)
        result = {}
        for mode in ["baseline", *candidates]:
            with patch.object(engine, "_predict", partial(points.predict, candidate=mode)):
                result[mode] = engine.forecast_order(data, day, cutoff, skus)
        # Cross-check actual, unmodified shipping inference at the first origin.
        if not pairs[candidates[0]]:
            direct = engine.forecast_order(data, day, cutoff, skus)
            if not all(direct[k]["method"] == result["baseline"][k]["method"] and math.isclose(
                direct[k]["forecast_mean"], result["baseline"][k]["forecast_mean"],
                rel_tol=1e-5, abs_tol=1e-5) for k in skus):
                raise AssertionError("Benchmark baseline diverged from the shipping engine")
        for sku in skus:
            actual = labels[sku]["actual"]
            baseline = result["baseline"][sku]["forecast_mean"]
            digest.update(json.dumps([site, when, asof, sku, actual], ensure_ascii=True).encode() + b"\n")
            for candidate in candidates:
                point = result[candidate][sku]["forecast_mean"]
                if not all(math.isfinite(p) and p >= 0 for p in (baseline, point)):
                    raise ValueError("Missing or invalid paired point forecast")
                pairs[candidate].append({"actual": actual, "baseline": baseline, "candidate": point})
    expected_count = sum(len(labels) for labels in grouped.values())
    if not expected_count or any(len(p) != expected_count for p in pairs.values()):
        raise ValueError("The evaluation must cover every paired product-day")
    return {c: {**metrics(p), "start": min(chosen_dates), "end": max(chosen_dates),
                "dates": sorted(chosen_dates), "data_sha256": digest.hexdigest()} for c, p in pairs.items()}


def evaluate(bench, bakery):
    spec = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
    hashes = fingerprints(spec)
    for file, expected in spec["baseline_files_sha256"].items():
        if hashes[file] != expected:
            raise ValueError(f"Baseline differs from the frozen order protocol: {file}")
    points = PairedPoints()
    samples = {name: (hist, refs) for name, hist, refs in sources(bench, bakery)}
    partitions = {}
    development = {c: {} for c in spec["candidates"]}
    for name, (history, refs) in samples.items():
        days = sorted({r["date"] for r in refs})
        dev, hold = days[:spec["development_first_scored_dates"]], days[-spec["holdout_last_scored_dates"]:]
        if dev[-1] >= hold[0]:
            raise ValueError("Development and holdout overlap")
        partitions[name] = hold
        stats = compare(name, history, refs, dev, spec["candidates"], points)
        for c, s in stats.items():
            development[c][name] = s
        print("development", name, {c: round(mae_change(s), 3) for c,s in stats.items()}, file=sys.stderr, flush=True)
    # Lock the choice before opening holdout predictions or scoring new products.
    candidate = min(spec["candidates"], key=lambda c: aggregate_change(development[c]))
    samples["bakery_new_skus"] = extra_products(bakery, partitions["bakery"])
    partitions["bakery_new_skus"] = partitions["bakery"]
    holdout = {}
    for name, (history, refs) in samples.items():
        holdout[name] = compare(name, history, refs, partitions[name], [candidate], points)[candidate]
        print("holdout", name, round(mae_change(holdout[name]), 3), file=sys.stderr, flush=True)
    return {"schema_version": 1, "protocol": spec["id"], "baseline_commit": spec["baseline_commit"],
            "fingerprints": hashes,
            "baseline_protocol": engine.PROTOCOL_VERSION, "candidate": candidate,
            "gate": gate(holdout, spec["gate"]),
            "development_selection_scores_pct": {c: aggregate_change(s) for c,s in development.items()},
            "partitions": {"development": {"sources": development[candidate]}, "holdout": {"sources": holdout}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench", type=Path)
    parser.add_argument("--bakery-source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path, help="Check code fingerprints and gate without checkpoint or sales files")
    args = parser.parse_args()
    if args.verify_report:
        saved = json.loads(args.verify_report.read_text(encoding="utf-8"))
        verify_report(saved)
        print("Verified paired report; candidate gate:", "passed" if saved["gate"]["passed"] else "rejected")
        return
    if not args.bench or not args.bakery_source:
        parser.error("full evaluation requires --bench and --bakery-source")
    report = evaluate(args.bench, args.bakery_source)
    verify_report(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
        if json.loads(args.output.read_text(encoding="utf-8")) != report:
            raise ValueError("Report verification failed")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
