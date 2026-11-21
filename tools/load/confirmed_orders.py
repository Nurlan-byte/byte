"""Measure confirmed-order reads while four synthetic outlets refresh on a disposable local API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event
from time import sleep, time
from unittest.mock import patch

import httpx
import uvicorn

# The same harness can measure an older checkout by running it from that checkout's root.
sys.path.insert(0, str(Path.cwd()))
from backend import chronos_order, context_sources, pos_providers, pos_sync, pos_vault
from backend import app as web

START = datetime(2026, 10, 12, 2, tzinfo=UTC)
NEXT = START + timedelta(days=1)
OUTLETS = [pos_providers.Location(str(i), f"Test shop {i}", "synthetic-load") for i in range(1, 5)]


def prepare_orders(client):
    """Create and confirm through the same API used by the operator."""
    orders, connections = [], []
    for outlet in OUTLETS:
        result = client.post("/api/pos/connections", json={
            "provider": "poster", "location_id": outlet.id, "days": 28,
            "api_key": "synthetic-load-key", "city": "none",
        }).raise_for_status().json()
        conn = result["connection"]
        assert conn["status"] == "ready", conn
        dataset = result["dataset"]
        forecast = result["forecast"]
        catalog = client.post(f"/api/datasets/{dataset['id']}/order-catalog",
                              json={"packs": {"tea": 2}}).raise_for_status().json()
        draft = client.post("/api/plans", json={
            "dataset_id": catalog["id"], "cutoff": forecast["cutoff"], "target_date": forecast["targetDate"],
            "lead_time_days": 1, "usable_stock_assumption": {"tea": 0},
        }).raise_for_status().json()
        qty = draft["items"][0]["recommended_qty"]
        assert type(qty) is int, draft
        saved = client.post(f"/api/plans/{draft['id']}/confirm", json={
            "revision": 0, "idempotency_key": "fixture-confirm", "quantities": {"tea": qty},
        }).raise_for_status().json()
        orders.append({"id": saved["id"], "branch": dataset["bakery_id"], "dataset": catalog["id"],
                       "revision": 1, "quantities": {"tea": qty}})
        connections.append((outlet.id, conn["id"], dataset))
    assert len({order["branch"] for order in orders}) == len(OUTLETS)
    return orders, connections


@contextmanager
def server():
    config = uvicorn.Config(web.app, host="127.0.0.1", port=0, log_level="warning")
    with config.bind_socket() as sock, ThreadPoolExecutor(max_workers=1) as pool:
        uv = uvicorn.Server(config)
        process = pool.submit(uv.run, sockets=[sock])
        try:
            for _ in range(300):
                if uv.started:
                    break
                if process.done():
                    process.result()
                    raise RuntimeError("Fixture failed to start")
                sleep(.05)
            else:
                raise RuntimeError("Fixture startup timed out")
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            uv.should_exit = True


def checked_refresh(client, outlet, ident, previous):
    saved = client.get(f"/api/pos/connections/{ident}/latest").raise_for_status().json()
    conn, dataset, forecast = saved["connection"], saved["dataset"], saved["forecast"]
    assert conn["location"]["id"] == outlet and conn["status"] == "ready", "wrong outlet or refresh status"
    assert dataset["id"] != previous["id"] and dataset["bakery_id"] == previous["bakery_id"], "wrong dataset"
    assert forecast["observed_through"] == conn["last_observed_day"] == str(START.date()), "wrong sales date"
    assert conn["last_success_day"] == str(NEXT.date()), "wrong refresh date"
    assert forecast["targetDate"] == str(NEXT.date() + timedelta(days=1)), "wrong forecast date"
    assert forecast["result"]["dataset_id"] == dataset["id"], "forecast does not reference the new dataset"
    items = forecast["result"]["items"]
    assert len(items) == 1 and items[0]["method"] == "Chronos-2" and math.isfinite(items[0]["forecast_mean"]), "invalid prediction"
    return {"outlet": outlet, "observed": conn["last_observed_day"],
            "target": forecast["targetDate"], "forecast_mean": items[0]["forecast_mean"]}


def read_metrics(metrics):
    reads, counts = [], {"verified_orders": 0, "finished_workers": 0, "dropped_iterations": 0}
    for line in metrics.read_text(encoding="utf-8").splitlines():
        point = json.loads(line)
        if point["type"] != "Point":
            continue
        data = point["data"]
        if point["metric"] in counts:
            counts[point["metric"]] += int(data["value"])
        elif point["metric"] == "http_req_duration":
            if data["tags"]["name"] == "read-confirmed-order":
                end = datetime.fromisoformat(data["time"]).timestamp()
                reads.append((end - data["value"] / 1000, end, data["value"]))
    return reads, counts


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(values) - 1) * fraction
    a, b = math.floor(index), math.ceil(index)
    return ordered[a] + (ordered[b] - ordered[a]) * (index - a)


def measure(args, directory):
    db = directory / "orders.sqlite3"
    key = directory / "test.key"
    pos_vault.init_key(key)
    os.environ.update(BAKEPLAN_DB=str(db), BAKEPLAN_LOCAL_DEMO="1", BAKEPLAN_POS_MASTER_KEY_FILE=str(key), BAKEPLAN_POS_ENABLED="1",
                      HF_HUB_OFFLINE="1", K6_NO_USAGE_REPORT="true")
    for name in ("BAKEPLAN_HEALTHCHECKS_FILE", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        os.environ.pop(name, None)
    arrivals, completions, inference = {}, [], []
    joined = Barrier(4)
    measuring = Event()

    def sales(reader, location, observations):
        if measuring.is_set():
            arrivals[location.id] = time()
            joined.wait(timeout=15)
            sleep(4)  # Simulated vendor latency only.
        for i in range((observations.end - observations.start).days + 1):
            day = observations.start + timedelta(days=i)
            observations.add(day, "tea", "Test tea", 1 + int(location.id) + day.toordinal() % 11, True)

    @web.app.post("/fixture/run-due")
    def refresh():
        pos_sync.run_due(NEXT)
        completions.append(time())
        return True

    with (
        patch.object(pos_providers, "locations", return_value=OUTLETS),
        patch.object(pos_providers, "poster_sales", sales),
        patch.object(context_sources, "load_demand_context", return_value=({}, [])),
        patch.object(web, "business_today", return_value=START.date()),
        patch.object(pos_sync, "now_utc", return_value=START),
        patch.object(pos_sync, "POLL_SECONDS", 3600),
        server() as origin,
        httpx.Client(base_url=origin, timeout=90, trust_env=False) as client,
    ):
        orders, connections = prepare_orders(client)
        model = chronos_order._pipeline()  # Warmed by the seed imports.
        predict = model.predict_quantiles

        def timed_predict(*a, **kw):
            start = time()
            try:
                return predict(*a, **kw)
            finally:
                inference.append((start, time()))

        script = Path(__file__).with_name("orders.js").resolve()
        metrics = directory / "samples.json"
        measuring.set()
        with patch.object(model, "predict_quantiles", timed_predict):
            completed = subprocess.run([args.k6, "run", "--quiet", "--out", "json=samples.json", str(script)],
                                       cwd=directory, env={**os.environ, "LOAD_ORIGIN": origin,
                                                           "LOAD_ORDERS": json.dumps(orders)}, timeout=100)
        if not metrics.exists():
            raise RuntimeError(f"k6 did not produce measurements (exit {completed.returncode})")
        proofs, branch_errors = [], []
        for branch in connections:
            try:
                proofs.append(checked_refresh(client, *branch))
            except (AssertionError, KeyError, TypeError, httpx.HTTPError) as error:
                branch_errors.append({"outlet": branch[0], "error": str(error) or "unexpected saved forecast"})
    reads, counts = read_metrics(metrics)
    # All four workers have reached their branch, and none has returned from publication yet.
    concurrent = []
    if len(arrivals) == len(completions) == 4:
        start, end = max(arrivals.values()), min(completions)
        concurrent = [ms for begin, finish, ms in reads if start <= begin and finish <= end]
    cpu_reads = [ms for begin, finish, ms in reads if any(begin < b and finish > a for a, b in inference)]
    duration = [ms for _, _, ms in reads]
    concurrent_p95 = percentile(concurrent, .95)
    passed = (completed.returncode == 0 and len(duration) == 60 and counts["verified_orders"] == 60
              and len(proofs) == 4 and bool(inference) and concurrent_p95 is not None and concurrent_p95 <= 500)
    return {"passed": passed, "k6_exit": completed.returncode, "reads": len(duration), **counts,
            "verified_branches": proofs, "branch_errors": branch_errors,
            "read_p50_ms": percentile(duration, .5), "read_max_ms": max(duration, default=None),
            "read_p95_ms": percentile(duration, .95), "concurrent_reads": len(concurrent),
            "concurrent_p95_ms": concurrent_p95, "inference_overlapping_reads": len(cpu_reads),
            "model_calls": len(inference), "inference_ms": round(sum(b-a for a,b in inference)*1000, 2),
            "synthetic_vendor_wait_seconds": 4}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k6", default="k6", help="Path to the Grafana k6 executable or command on PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if Path(args.k6).is_file():
        args.k6 = str(Path(args.k6).resolve())
    with TemporaryDirectory(prefix="bakeplan-load-") as tmp:
        result = measure(args, Path(tmp))
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    if not result["passed"]:
        raise SystemExit("Saved-order latency, correctness or overlap missed its test threshold")


if __name__ == "__main__":
    main()
