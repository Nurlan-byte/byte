"""Measure an AUTO service request on three public cafes with real forecast vintages."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REFERENCE = "8012cffd0f7e462c1870539921aa3e7fc08c2993"
TARGET, CUTOFF = date(2025, 9, 30), date(2025, 9, 28)


def encode(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()


def measure(service, panels):
    start = time.perf_counter_ns()
    responses = [service.forecast_from_dataset(panel, TARGET, CUTOFF, "AUTO", None) for panel in panels]
    return (time.perf_counter_ns() - start) / 1e6, encode(responses)


def main():
    from backend import chronos_order, forecast_cache, ml_service
    from backend.demand_context import with_demand_context
    from benchmarks.context.sources import cafe_inputs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cafe", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 3:
        parser.error("Use at least three repeat requests per implementation")

    baseline = ModuleType("baseline_service")
    original = subprocess.check_output(["git", "show", REFERENCE + ":backend/ml_service.py"], cwd=ROOT)
    exec(compile(original, "<reference ml_service.py>", "exec"), baseline.__dict__)
    panels = []
    for data, calendar, weather in cafe_inputs(args.cafe):
        data.update(source="Mendeley cafe POS", schema_version="sales-1")
        panels.append(with_demand_context(data, {"calendar": calendar, "weather": weather}))

    chronos_order._pipeline()  # Load and verify the same checkpoint outside both timers.
    first = {}
    # Each variant starts from an empty scalar inference cache and a ready model.
    for name, service in (("before", baseline), ("after", ml_service)):
        chronos_order._POINT_CACHE.clear()
        forecast_cache._results.clear()
        elapsed, response = measure(service, panels)
        first[name] = (elapsed, response)
    canonical = first["before"][1]
    if canonical != first["after"][1]:
        raise ValueError("First service responses differ")

    timings = {"before": [], "after": []}
    services = (("before", baseline), ("after", ml_service))
    for run in range(args.runs):
        for name, service in (services if run % 2 == 0 else reversed(services)):
            elapsed, output = measure(service, panels)
            if output != canonical:
                raise ValueError("A complete repeated service response differs")
            timings[name].append(elapsed)
    report = {
        "reference_commit": REFERENCE, "protocol": chronos_order.PROTOCOL_VERSION,
        "data": "Surabaya cafe POS receipt item lines, calendar and archived GFS forecast vintages",
        "target": str(TARGET), "cutoff": str(CUTOFF), "cafes": len(panels),
        "products": sum(len({row["product_id"] for row in p["rows"]}) for p in panels),
        "receipt_item_day_rows": sum(len(p["rows"]) for p in panels),
        "input_sha256": hashlib.sha256(encode(panels)).hexdigest(),
        "complete_response_sha256": hashlib.sha256(canonical).hexdigest(),
        "all_responses_identical": True, "repeat_runs": args.runs, "python": sys.version.split()[0],
        "first_three_cafes_ms": {k: round(v[0], 3) for k, v in first.items()},
        "repeat_three_cafes_median_ms": {k: round(statistics.median(v), 3) for k, v in timings.items()},
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if json.loads(args.output.read_text(encoding="utf-8")) != report:
        raise ValueError("Saved benchmark differs")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
