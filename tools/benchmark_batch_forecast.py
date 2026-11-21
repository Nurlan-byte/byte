"""Compare the complete forecast_items result and latency in two BakePlan checkouts.

Run with the project's Python: python tools/benchmark_batch_forecast.py BEFORE_CHECKOUT
"""

import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
FIRST_DAY = date(2025, 1, 1)
CUTOFF = FIRST_DAY + timedelta(days=199)


def fixture():
    rows = []
    for day in range(200):
        current = FIRST_DAY + timedelta(days=day)
        for product in range(50):
            demand = current.weekday() if product < 25 else day
            rows.append({
                "date": current.isoformat(), "product_id": f"sku-{product:02d}",
                "sold": 10 + product + demand, "is_open": True,
            })
    return {
        "id": "benchmark", "version": "synthetic-v1", "bakery_id": "benchmark",
        "planning_kind": "procurement", "target": "sold_units", "unit": "physical_unit",
        "source": "synthetic", "provenance": "Deterministic batch latency fixture",
        "metadata_declared": True, "rows": rows,
    }


def worker(checkout, directory, action):
    sys.path.insert(0, str(checkout))
    os.environ["BAKEPLAN_DB"] = str(directory / "fixture.sqlite3")
    from backend import ml_core, ml_service, model_storage, storage

    data = fixture()
    if action == "seed":
        storage.initialize_database()
        storage.save_dataset(data)
        model = ml_core.train_model(data, CUTOFF - timedelta(days=7))
        model.update(id="benchmark-ridge", source=data["source"], provenance=data["provenance"])
        model_storage.save_training(model, "benchmark", "{}")
        return
    target = CUTOFF + timedelta(days=2)
    start = perf_counter()
    forecast = ml_service.forecast_items(data, target, CUTOFF)
    elapsed = perf_counter() - start
    encoded = json.dumps(forecast, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    reference = directory / "forecast.json"
    if reference.exists():
        if encoded != reference.read_bytes():
            raise ValueError("The complete forecast response changed")
    else:
        reference.write_bytes(encoded)
    methods = Counter(item["method"] for item in forecast["items"])
    if len(forecast["items"]) != 50 or not methods["ML"] or not methods["B1"]:
        raise ValueError(f"Expected 50 products with both ML and B1 outcomes: {methods}")
    print(json.dumps({"seconds": elapsed, "methods": methods, "sha256": hashlib.sha256(encoded).hexdigest()}))


def paired_runs(before, repeats, script, workload):
    """Run both checkouts against one seeded database, alternating measured order."""
    if repeats < 1:
        raise ValueError("At least one measured run is required")
    with TemporaryDirectory() as temp:
        def run(checkout, action):
            args = [sys.executable, script, "--worker", str(checkout), temp, action]
            env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
            return subprocess.check_output(args, cwd=checkout, env=env, text=True)

        run(before, "seed")
        # Warm the database and file caches for both checkouts before timing.
        run(before, workload)
        run(ROOT, workload)
        checkouts = {"before": before, "after": ROOT}
        results = {"before": [], "after": []}
        for trial in range(repeats):
            order = ("before", "after") if trial % 2 == 0 else ("after", "before")
            for label in order:
                results[label].append(json.loads(run(checkouts[label], workload)))
        return results


def compare(before, repeats):
    results = paired_runs(before, repeats, __file__, "forecast")
    timings = {label: [run["seconds"] for run in runs] for label, runs in results.items()}
    summary = {label: median(values) for label, values in timings.items()}
    first = results["before"][0]
    print(json.dumps({
        "seconds": timings, "median_seconds": summary, "speedup": summary["before"] / summary["after"],
        "methods": first["methods"], "complete_forecast_sha256": first["sha256"],
    }, indent=2))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("before_checkout", type=Path)
        parser.add_argument("--repeats", type=int, default=5)
        options = parser.parse_args()
        compare(options.before_checkout.resolve(), options.repeats)
