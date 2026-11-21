"""One preregistered, bias-limited TSB candidate against seven real store/machine cohorts."""
import argparse
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import evaluate_sparse_tsb as reference

PROTOCOL = reference.BENCHMARK / "sparse_bias_protocol.json"
SPEC = json.loads(PROTOCOL.read_text(encoding="utf-8"))
PANELS = {p: SPEC[p + "_groups"] for p in ("regression", "confirmation")}
DIGESTS = {**reference.SPEC["panel_lf_sha256"],
           "m5_confirmation": "e34ed6d1771a07824494c6919480b759473c0082f456eb58159ae6f7da71ad77"}
DAY = timedelta(days=1)


def fingerprints():
    files = [PROTOCOL, Path(__file__), PROTOCOL.parent / "scripts/prepare_m5.py"]
    return reference.fingerprints() | {
        p.relative_to(reference.ROOT).as_posix(): hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
        for p in files}


def bias_days(target):
    cutoff = target - reference.GAP
    return [cutoff - i * DAY for i in range(SPEC["bias_window_calendar_days"])]


def bias_cap(days, sales, predictions):
    residuals = [predictions[t] - sales[t] for t in days if t in sales and predictions.get(t) is not None]
    if len(residuals) < SPEC["minimum_bias_pairs"]:
        return 0.0
    return max(0.0, math.fsum(residuals) / len(residuals))


def guarded_point(shipping, tsb, cap):
    half_cap = reference.point("half_cap_tsb", shipping, tsb, [])
    return max(half_cap, shipping - cap)


def replay(products, targets, forecast):
    """Cache one shipping run for all SKUs per historical target. Each run receives only its own prefix."""
    wanted = set(targets)
    for target in targets:
        wanted.update(bias_days(target))
    sales_days = {day for history in products.values() for day in history}
    rows = sorted((day, sku, qty) for sku, history in products.items() for day, qty in history.items())
    skus = sorted(products)
    predictions = {sku: {} for sku in skus}
    pairs = []
    for target in sorted(wanted & sales_days):
        cutoff = target - reference.GAP
        dataset = {"unit": "physical_unit", "target": "sold_units",
                   "rows": [{"date": day.isoformat(), "product_id": sku, "sold": qty}
                            for day, sku, qty in rows if day <= cutoff]}
        decisions = forecast(dataset, target, cutoff, skus)
        for sku, history in sorted(products.items()):
            baseline = decisions[sku]["forecast_mean"]
            predictions[sku][target] = baseline
            b1 = decisions[sku]["b1_mean"]
            if target not in targets or target not in history or baseline is None or b1 is None:
                continue
            recent = reference.recent_values(history, cutoff)
            pairs.append({"actual": history[target], "baseline": baseline, "b1": b1, "tsb": None,
                          "recent": recent if reference.eligible(recent) else [],
                          "cap": bias_cap(bias_days(target), history, predictions[sku])})
    digest = hashlib.sha256()
    for sku in skus:
        for day, units in sorted(products[sku].items()):
            if day <= targets[-1]:
                digest.update(json.dumps([sku, day.isoformat(), units]).encode() + b"\n")
    return pairs, digest.hexdigest()


def apply_tsb(all_pairs, python):
    sparse = [p for pairs in all_pairs for p in pairs if p["recent"]]
    result = subprocess.run([str(python), str(Path(reference.__file__).resolve()), "--tsb-worker"],
                            input=json.dumps([p["recent"] for p in sparse]), capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    if output["statsforecast_version"] != "2.0.3":
        raise ValueError("Unexpected TSB version")
    for pair, tsb in zip(sparse, output["forecasts"], strict=True):
        # summarize(..., "tsb") scores the supplied point directly for eligible pairs.
        pair["tsb"] = guarded_point(pair["baseline"], tsb, pair["cap"])
    return output["statsforecast_version"]


def decision(partitions):
    reasons = [f"{panel}/{reason}" for panel, data in partitions.items() for reason in data["gate"]["reasons"]]
    return {"passed": not reasons, "reasons": reasons}


def evaluate(m5, vending, confirmation, python):
    from backend.chronos_order import forecast_order, PROTOCOL_VERSION

    data = (reference.read_panel(m5) | reference.read_panel(vending, vending=True)
            | reference.read_panel(confirmation, expected_digest=DIGESTS["m5_confirmation"]))
    if set(data) != {name for names in PANELS.values() for name in names}:
        raise ValueError("Incomplete preregistered panels")
    raw = {}
    for group, products in data.items():
        _, dates = reference.split_dates(t for history in products.values() for t in history)
        pairs, digest = replay(products, dates, forecast_order)
        raw[group] = pairs, dates, digest
        print(f"{group}: {len(pairs)} paired forecasts", file=sys.stderr, flush=True)
    version = apply_tsb((values[0] for values in raw.values()), python)
    partitions = {}
    for panel, groups in PANELS.items():
        sources = {}
        for group in groups:
            pairs, dates, sha = raw[group]
            changed = [p for p in pairs if p["tsb"] is not None and p["tsb"] < p["baseline"]]
            sources[group] = {
                **reference.summarize(pairs, "tsb", dates, len(data[group]), sha),
                "changed_pairs": len(changed), "changed_positive_days": sum(p["actual"] > 0 for p in changed),
                "total_downward_units": math.fsum(p["baseline"] - p["tsb"] for p in changed),
            }
        partitions[panel] = {"sources": sources, "gate": reference.decision(sources, groups)}
    return {"schema_version": 1, "protocol": SPEC["id"], "candidate": SPEC["candidate"],
            "baseline_commit": SPEC["baseline_commit"], "baseline_protocol": PROTOCOL_VERSION, "target": "sold_units",
            "statsforecast_version": version, "fingerprints": fingerprints(), "panel_lf_sha256": DIGESTS,
            "partitions": partitions, "gate": decision(partitions)}


def verify(report):
    old = json.loads((reference.BENCHMARK / "results/sparse_tsb.json").read_text(encoding="utf-8"))
    reference.verify(old)
    fixed = {"schema_version": 1, "protocol": SPEC["id"], "candidate": SPEC["candidate"],
             "statsforecast_version": old["statsforecast_version"], "baseline_protocol": old["baseline_protocol"],
             "baseline_commit": SPEC["baseline_commit"], "target": "sold_units", "fingerprints": fingerprints(),
             "panel_lf_sha256": DIGESTS}
    if any(report[k] != v for k, v in fixed.items()) or set(report["partitions"]) != set(PANELS):
        raise ValueError("Changed experiment identity or partition")
    for panel, groups in PANELS.items():
        part = report["partitions"][panel]
        if set(part["sources"]) != set(groups):
            raise ValueError("Changed store roster")
        for name, r in part["sources"].items():
            reference.verify_scores(r)
            reference.verify_scores(r["eligible"])
            dates = [date.fromisoformat(v) for v in r["dates"]]
            n = len(dates)
            if not (dates == sorted(set(dates)) and n == reference.SPEC["holdout_days"]
                    and (r["start"], r["end"]) == (dates[0].isoformat(), dates[-1].isoformat())
                    and r["planned"] == 6 * n and 0 < r["n"] <= r["planned"]
                    and math.isclose(r["coverage_pct"], r["n"] * 100 / r["planned"])
                    and len(bytes.fromhex(r["data_sha256"])) == 32):
                raise ValueError("Invalid observations or coverage")
            sparse = r["eligible"]
            if not (0 <= r["changed_positive_days"] <= min(r["changed_pairs"], sparse["positive_days"])
                    and 0 <= r["changed_pairs"] <= sparse["n"] <= r["n"]
                    and 0 <= sparse["actual_sum"] <= r["actual_sum"] and r["total_downward_units"] >= 0
                    and math.isclose(r["baseline_sum"] - r["candidate_sum"], r["total_downward_units"], abs_tol=1e-8)
                    and math.isclose(sparse["baseline_sum"] - sparse["candidate_sum"], r["total_downward_units"], abs_tol=1e-8)):
                raise ValueError("Invalid adjustment totals")
            if panel == "regression":
                before = old["partitions"]["holdout"]["sources"][name]
                for field in ("data_sha256", "dates", "n", "actual_sum", "baseline_abs_error_sum", "b1_abs_error_sum"):
                    same = math.isclose(r[field], before[field], rel_tol=1e-7) if isinstance(r[field], float) else r[field] == before[field]
                    if not same:
                        raise ValueError(f"Regression reference changed: {name}/{field}")
        if reference.decision(part["sources"], groups) != part["gate"]:
            raise ValueError("Partition gate differs from scores")
    if decision(report["partitions"]) != report["gate"]:
        raise ValueError("Final gate differs from both panel decisions")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("m5-panel", "vending-daily", "confirmation", "tsb-python", "output", "verify-report"):
        parser.add_argument("--" + key, type=Path)
    args = parser.parse_args()
    if args.verify_report:
        verify(json.loads(args.verify_report.read_text(encoding="utf-8")))
        print("Verified bias-limited sparse forecast report")
        return
    if not all((args.m5_panel, args.vending_daily, args.confirmation, args.tsb_python, args.output)):
        parser.error("Provide the three input panels, --tsb-python and --output")
    if args.output.resolve() in {p.resolve() for p in (args.m5_panel, args.vending_daily, args.confirmation)}:
        parser.error("Use a separate output path for the report")
    report = evaluate(args.m5_panel, args.vending_daily, args.confirmation, args.tsb_python.resolve())
    verify(report)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    verify(json.loads(args.output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
