"""Issued (including unconfirmed) automatic sales forecasts versus closed-day observations."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import sqlite3

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.branch_accounts import set_account
from backend.config import business_today
from backend import storage
from scripts.sqlite_snapshot import inspect_database


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    path = tmp_path / "issued.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(path))
    monkeypatch.delenv("BAKEPLAN_LOCAL_DEMO", raising=False)
    storage.initialize_database()
    for branch in ("north", "south"):
        hashed = bcrypt.hashpw(b"fixture", bcrypt.gensalt(rounds=4)).decode()
        set_account(branch, branch, hashed)
    asof = business_today() - timedelta(days=3)
    monkeypatch.setattr("backend.app.business_today", lambda: asof)
    monkeypatch.setattr("backend.context_routes.business_today", lambda: asof)
    monkeypatch.setattr("backend.chronos_order._predict", lambda entries, horizon: [6.0] * len(entries))
    with TestClient(app) as client:
        client.auth = ("north", "fixture")
        yield client, asof


def import_shop(client, dates, *, branch="north", stockout="none", quantity=4, provenance="test", is_open=True):
    rows = ["date,product_id,opening,received,sold,waste,closing,is_open,stockout_status"]
    for day in dates:
        rows.append(f"{day},tea,0,{quantity},{quantity},0,0,{str(is_open).lower()},{stockout}")
    request = {"schema_version": "procurement-1", "target": "sold_units", "unit": "physical_unit",
               "source": "synthetic", "bakery_id": branch, "provenance": provenance,
               "catalog": [{"product_id": "tea", "name": "Tea", "pack_size": 1}], "csv_text": "\n".join(rows)}
    response = client.post("/api/datasets", json=request, auth=(branch, "fixture"))
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def test_unconfirmed_forecasts_survive_retry_and_restart(workspace, monkeypatch):
    client, asof = workspace
    target = asof + timedelta(days=1)
    dataset_id = import_shop(client, [asof - timedelta(days=i) for i in range(14, 0, -1)])
    request = dict(dataset_id=dataset_id, target_date=str(target), cutoff=str(asof - timedelta(days=1)), method="AUTO")
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(r.status_code == 200 for r in pool.map(lambda _: client.post("/api/forecasts", json=request), range(3)))
    assert client.post(f"/api/datasets/{dataset_id}/first-forecast").is_success
    assert client.post("/api/plans", json={**request, "lead_time_days": 1, "mode": "operational"}).status_code == 201
    query = {"bakery_id": "north", "start": str(target), "end": str(target), "source": "synthetic"}
    url = "/api/reports/issued-forecasts"
    initial = client.get(url, params=query)
    assert initial.status_code == 200, initial.text
    assert initial.json()["issued_predictions"] == initial.json()["issued_product_days"] == 1
    assert initial.json()["excluded"]["missing_actual"] == 1
    # A different import reissues the same product/day. The first decision remains the evaluation sample.
    revised = import_shop(client, [asof - timedelta(days=i) for i in range(14, 0, -1)], quantity=12)
    assert client.post("/api/forecasts", json={**request, "dataset_id": revised}).is_success
    monkeypatch.setattr("backend.app.business_today", business_today)
    actual = import_shop(client, [target], quantity=5)
    with TestClient(app) as restarted:
        restarted.auth = client.auth
        report = restarted.get(url, params=query).json()
        assert report["issued_predictions"] == 2 and report["issued_product_days"] == 1
        assert report["actual_dataset_id"] == actual and report["matched_product_days"] == 1
        assert report["model_mae"] == report["b1_mae"] == 1
        assert report["model_bias"] == 1 and report["b1_bias"] == -1
        assert report["overforecast_total"] == 1 and report["underforecast_total"] == 0
        assert report["by_product"]["tea"] == report["by_frequency"]["regular"]
        assert report["coverage"] == 1 and report["availability_unknown_days"] == 0
        assert restarted.get(url, params=query, auth=("south", "fixture")).status_code == 404
        foreign = import_shop(client, [target], branch="south", quantity=500)
        assert restarted.get(url, params={**query, "actual_dataset_id": foreign}).status_code == 404
        stockout = import_shop(client, [target], stockout="occurred")
        censored = restarted.get(url, params=query).json()
        assert censored["actual_dataset_id"] == stockout and censored["excluded"]["stockout"] == 1
        assert censored["model_mae"] is None and censored["coverage"] == 0
        assert restarted.get(url, params={**query, "actual_dataset_id": actual}).json()["model_mae"] == 1


@pytest.mark.parametrize("entry", ["forecast", "first", "plan"])
def test_each_live_entry_publishes_but_replay_and_diagnostics_do_not(workspace, entry):
    client, asof = workspace
    dataset = import_shop(client, [asof - timedelta(days=i) for i in range(14, 0, -1)])
    target = asof + timedelta(days=1)
    request = {"dataset_id": dataset, "target_date": str(target), "cutoff": str(asof - timedelta(days=1))}
    query = {"bakery_id": "north", "source": "synthetic", "start": str(target), "end": str(target)}
    url = "/api/reports/issued-forecasts"
    assert client.post("/api/forecasts", json={**request, "method": "B1"}).is_success
    assert client.post("/api/forecasts", json={**request, "target_date": str(asof), "method": "AUTO"}).is_success
    assert client.post("/api/plans", json={**request, "mode": "replay", "lead_time_days": 1}).is_success
    assert client.get(url, params={**query, "start": str(asof)}).json()["issued_predictions"] == 0
    if entry == "forecast":
        response = client.post("/api/forecasts", json=request)
    elif entry == "first":
        response = client.post(f"/api/datasets/{dataset}/first-forecast")
    else:
        response = client.post("/api/plans", json={**request, "lead_time_days": 1})
    assert response.is_success, response.text
    assert client.get(url, params=query).json()["issued_product_days"] == 1
    for bad_query in ({"horizon_days": 1}, {"horizon_days": 1025}, {"end": str(asof)}):
        assert client.get(url, params={**query, **bad_query}).status_code == 422


def test_only_closed_day_imports_match_and_old_retries_preserve_corrections(workspace, monkeypatch):
    client, asof = workspace
    target = asof + timedelta(days=1)
    origin = import_shop(client, [asof - timedelta(days=i) for i in range(14, 0, -1)])
    assert client.post(f"/api/datasets/{origin}/first-forecast").is_success
    query = {"bakery_id": "north", "source": "synthetic", "start": str(target), "end": str(target)}
    url = "/api/reports/issued-forecasts"
    monkeypatch.setattr("backend.app.business_today", lambda: target)
    monkeypatch.setattr("backend.issued_report.business_today", lambda: target)
    early = import_shop(client, [target], quantity=0)
    assert client.get(url, params=query).json()["excluded"]["pending"] == 1
    monkeypatch.setattr("backend.app.business_today", business_today)
    monkeypatch.setattr("backend.issued_report.business_today", business_today)
    assert client.get(url, params=query).json()["excluded"]["missing_actual"] == 1
    assert import_shop(client, [target], quantity=0) == early
    zero = client.get(url, params=query).json()
    assert zero["model_mae"] == 6 and zero["actual_dataset_id"] == early
    closed = import_shop(client, [target], quantity=0, is_open=False)
    assert import_shop(client, [target], quantity=0) == early
    assert client.get(url, params={**query, "unit": "item_line", "actual_dataset_id": closed}).status_code == 422

    @contextmanager
    def indexed_read():
        with storage.connection() as db:
            def authorize(action, table, column, *_):
                if action == sqlite3.SQLITE_READ and table == "datasets" and column == "payload":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            yield db

    # Daily auto selection must not decode old dataset JSON, even to discover which snapshot to use.
    monkeypatch.setattr("backend.issued_report.connection", indexed_read)
    corrected = client.get(url, params=query).json()
    assert corrected["actual_dataset_id"] == closed and corrected["excluded"]["closed"] == 1
    assert corrected["matched_product_days"] == 0 and corrected["b1_mae"] is None


def test_item_lines_compare_each_product_only_to_matching_units_and_source(workspace, monkeypatch):
    client, asof = workspace
    target = asof + timedelta(days=1)

    def import_lines(rows, source="synthetic"):
        csv = "date,product_id,item_line_count\n" + "\n".join(f"{d},{product},{count}" for d, product, count in rows)
        response = client.post("/api/datasets", json={"schema_version": "sales-1", "target": "item_line_count",
            "unit": "item_line", "source": source, "bakery_id": "north", "provenance": "register", "csv_text": csv})
        assert response.is_success, response.text
        return response.json()["id"]

    history = [(asof - timedelta(days=i), product, count)
               for i in range(14, 0, -1) for product, count in (("tea", 4), ("coffee", int(i == 14)))]
    initial = import_lines(history)
    issued = client.post(f"/api/datasets/{initial}/first-forecast").json()["result"]["items"]
    by_sku = {item["product_id"]: item for item in issued}
    monkeypatch.setattr("backend.app.business_today", business_today)
    partial = import_lines([(target, "coffee", 1)])
    import_lines([(target, "tea", 91)], source="external")
    import_shop(client, [target], quantity=90)
    query = {"source": "synthetic", "unit": "item_line", "bakery_id": "north", "start": str(target), "end": str(target)}
    url = "/api/reports/issued-forecasts"
    uncovered = client.get(url, params=query).json()
    assert uncovered["coverage"] == 0.5 and uncovered["excluded"]["missing_actual"] == 1
    assert uncovered["by_product"]["tea"]["model_mae"] is None
    import_lines([(target, "tea", 3)])
    full = client.get(url, params=query).json()
    assert full["actual_dataset_count"] == 2 and full["actual_dataset_id"] is None
    assert full["matched_product_days"] == full["availability_unknown_days"] == 2
    assert full["by_frequency"]["sparse"] == full["by_product"]["coffee"]
    assert full["by_frequency"]["regular"] == full["by_product"]["tea"]
    for sku, volume in (("coffee", 1), ("tea", 3)):
        assert full["by_product"][sku]["b1_mae"] == abs(by_sku[sku]["b1_mean"] - volume)
        assert full["by_product"][sku]["model_mae"] == abs(by_sku[sku]["forecast_mean"] - volume)
    limited = client.get(url, params={**query, "actual_dataset_id": partial}).json()
    assert limited["coverage"] == 0.5 and limited["actual_selection"] == "selected_dataset"
    counts = inspect_database(storage.database_path())["table_counts"]
    assert counts["issued_forecasts"] == counts["forecast_actuals"] == 2


def test_missing_baseline_is_reported_without_discarding_the_issued_product(workspace, monkeypatch):
    client, asof = workspace
    day = asof - timedelta(days=1)
    target = asof + timedelta(days=1)
    dataset = import_shop(client, [day])
    first = client.post(f"/api/datasets/{dataset}/first-forecast").json()["result"]["items"]
    assert first[0]["forecast_mean"] == 6 and first[0]["b1_mean"] is None
    monkeypatch.setattr("backend.app.business_today", business_today)
    import_shop(client, [target])
    report = client.get("/api/reports/issued-forecasts", params={"source": "synthetic", "bakery_id": "north",
                        "start": str(target), "end": str(target)}).json()
    assert report["issued_predictions"] == report["excluded"]["missing_pair"] == 1
    assert report["coverage"] == 0 and report["model_mae"] is None and report["b1_mae"] is None


def test_automatic_order_and_issued_forecast_commit_together(workspace, monkeypatch):
    client, asof = workspace
    dataset = import_shop(client, [asof - timedelta(days=i) for i in range(14, 0, -1)])
    target = asof + timedelta(days=1)
    request = {"dataset_id": dataset, "target_date": str(target), "cutoff": str(asof - timedelta(days=1)),
               "lead_time_days": 1}
    with storage.connection() as db:
        db.execute("""CREATE TRIGGER simulate_write_failure BEFORE INSERT ON issued_forecasts BEGIN
                   SELECT RAISE(ABORT, 'simulated journal error'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated journal error"):
        client.post("/api/plans", json=request)
    with storage.connection() as db:
        for table in ("plans", "revisions", "issued_forecasts"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        db.execute("DROP TRIGGER simulate_write_failure")
    assert client.post("/api/plans", json=request).status_code == 201
    with storage.connection() as db:
        for table in ("plans", "revisions", "issued_forecasts"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
