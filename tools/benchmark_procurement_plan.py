"""Compare B1 and ML order creation across two BakePlan checkouts.

Run with the project's Python: python tools/benchmark_procurement_plan.py BEFORE_CHECKOUT
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta
from functools import partial
import importlib
import json
import os
from pathlib import Path
import sqlite3
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.benchmark_batch_forecast import CUTOFF, fixture, paired_runs


TODAY = CUTOFF + timedelta(days=1)
TARGET = TODAY + timedelta(days=1)


def seed():
    from backend import deliveries, ml_core, model_storage, storage

    data = fixture()
    data["schema_version"] = "procurement-1"
    data["catalog"] = [
        {"product_id": f"sku-{i:02d}", "name": f"Item {i}", "pack_size": 6, "max_order_qty": None}
        for i in range(50)
    ]
    for row in data["rows"]:
        row.update(opening=0, received=row["sold"], waste=0, closing=0, stockout_status="unknown")
    storage.initialize_database()
    storage.save_dataset(data)
    model = ml_core.train_model(data, CUTOFF - timedelta(days=7))
    model.update(id="plan-benchmark-ridge", source=data["source"], provenance=data["provenance"])
    model_storage.save_training(model, "plan-benchmark", "{}")
    delivery, _ = deliveries.create_delivery(data["id"], deliveries.DeliveryRequest(
        number="benchmark-incoming", product_id="sku-00", arrival_date=TARGET, ordered_qty=12,
    ))
    deliveries.receive_delivery(data["id"], delivery["id"], deliveries.ReceiptRequest(
        quantity=5, idempotency_key="first-receipt",
    ))


def measure(request, directory):
    from backend import services, storage

    write_times = []

    class TimedConnection(sqlite3.Connection):
        write_start = None

        def execute(self, sql, *args):
            result = super().execute(sql, *args)
            if sql == "BEGIN IMMEDIATE":
                self.write_start = perf_counter()
            return result

        def __exit__(self, *args):
            result = super().__exit__(*args)
            if self.write_start is not None:
                write_times.append(perf_counter() - self.write_start)
            return result

    # Time from successful writer acquisition through the real SQLite commit.
    with patch.object(sqlite3, "connect", partial(sqlite3.connect, factory=TimedConnection)):
        start = perf_counter()
        plan = services.create_plan(request, TODAY)
        elapsed = perf_counter() - start
    if len(write_times) != 1 or storage.get_plan(plan["id"], 0) != plan:
        raise ValueError("Expected one writer transaction and an identical saved revision")
    if UUID(plan["id"]).version != 4 or datetime.fromisoformat(plan["created_at"]).utcoffset() != timedelta(0):
        raise ValueError("Expected a generated plan UUID and a UTC timestamp")
    if len(plan["items"]) != 50 or plan["items"][0]["incoming_units"] != 7:
        raise ValueError("Expected 50 items and a partially received delivery")
    methods = Counter(item["method"] for item in plan["items"])
    if request.method == "ML" and set(methods) != {"ML", "B1"}:
        raise ValueError(f"Expected ML and B1 in the ML order: {methods}")
    stable = {k: v for k, v in plan.items() if k not in {"id", "created_at"}}
    encoded = json.dumps(stable, sort_keys=True, allow_nan=False).encode()
    reference = directory / (request.method + ".json")
    if reference.exists():
        if encoded != reference.read_bytes():
            raise ValueError(f"{request.method} plan changed beyond its generated ID and timestamp")
    else:
        reference.write_bytes(encoded)
    return {"total_ms": elapsed * 1000, "writer_ms": write_times[0] * 1000, "methods": methods}


def worker(checkout, directory, action):
    sys.path.insert(0, str(checkout))
    os.environ["BAKEPLAN_DB"] = str(directory / "orders.sqlite3")
    if action == "seed":
        seed()
        return
    from backend.schemas import PlanRequest

    importlib.import_module("backend.ml_service")  # Measure a running application.
    results = {}
    for method in ("B1", "ML"):
        request = PlanRequest(
            dataset_id="benchmark", target_date=TARGET, cutoff=CUTOFF, method=method,
            lead_time_days=1, usable_stock_assumption={f"sku-{i:02d}": 3 for i in range(50)},
        )
        results[method] = measure(request, directory)
    print(json.dumps(results))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("before_checkout", type=Path)
        parser.add_argument("--repeats", type=int, default=7)
        options = parser.parse_args()
        runs = paired_runs(options.before_checkout.resolve(), options.repeats, __file__, "plan")
        print(f"Verified both saved orders and first revisions in {options.repeats} measured pairs plus warmup.")
        for method in ("B1", "ML"):
            print(f"{method} item methods: {runs['before'][0][method]['methods']}")
            for metric in ("total_ms", "writer_ms"):
                before, after = (
                    median(run[method][metric] for run in runs[side]) for side in ("before", "after")
                )
                print(f"{method} {metric}: {before:.3f} -> {after:.3f} ({before / after:.2f}x)")
