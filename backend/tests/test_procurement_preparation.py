"""Batch history and concurrent receiving through the order API."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backend import procurement_planning, services
from backend.app import app


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "shop.sqlite3"))
    monkeypatch.setattr("backend.app.business_today", lambda: date(2026, 3, 1))
    csv_text = """date,product_id,opening,received,sold,waste,closing,is_open,stockout_status
2025-12-29,tea,0,1000,1000,0,0,,unknown
2026-01-05,tea,0,8,8,0,0,true,none
2026-01-12,tea,0,60,60,0,0,true,none
2026-01-12,zero,0,0,0,0,0,true,none
2026-01-19,tea,0,0,0,0,0,false,unknown
2026-02-02,tea,0,16,16,0,0,,unknown
2026-02-09,tea,0,0,0,0,0,false,none
2026-02-16,tea,0,12,12,0,0,true,occurred
2026-02-23,tea,0,20,20,0,0,true,none
2026-02-24,tea,0,1000,1000,0,0,,unknown
2026-03-01,water,0,10,10,0,0,,unknown"""
    products = ["zero", "water", "tea"]
    with TestClient(app) as client:
        imported = client.post("/api/datasets", json={
            "schema_version": "procurement-1", "source": "synthetic", "bakery_id": "shop",
            "target": "sold_units", "unit": "physical_unit", "provenance": "Batch order test",
            "catalog": [{"product_id": p, "name": p, "pack_size": 1} for p in products],
            "csv_text": csv_text,
        })
        assert imported.status_code == 201, imported.text
        yield client, {
            "dataset_id": imported.json()["id"], "method": "B1", "target_date": "2026-03-02",
            "cutoff": "2026-02-23", "lead_time_days": 1,
            "usable_stock_assumption": dict.fromkeys(products, 0),
        }


def test_order_scans_sales_once_and_preserves_history_boundaries(shop, monkeypatch):
    client, request = shop
    scans = 0
    load_dataset = services.get_dataset

    class TrackedRows(list):
        def __iter__(self):
            nonlocal scans
            scans += 1
            return super().__iter__()

    def load(ident):
        dataset = load_dataset(ident)
        return {**dataset, "rows": TrackedRows(dataset["rows"])}

    monkeypatch.setattr(services, "get_dataset", load)
    reply = client.post("/api/plans", json=request)
    assert reply.status_code == 201, reply.text
    zero, water, tea = reply.json()["items"]
    assert [zero["product_id"], water["product_id"], tea["product_id"]] == ["zero", "water", "tea"]
    assert (zero["forecast_mean"], zero["method"], zero["recommended_qty"]) == (0, "B1", 0)
    assert (water["forecast_mean"], water["method"], water["unknown_open_n"]) == (None, "manual", 0)
    # 2026-02-16 is a marked stockout: its sales are not a measure of demand.
    assert tea["used_dates"] == ["2026-02-23", "2026-02-02", "2026-01-12", "2026-01-05"]
    assert (tea["eligible_weekday_n"], tea["n"], tea["forecast_mean"], tea["recommended_qty"]) == (4, 4, 26, 26)
    assert (tea["unknown_open_n"], tea["unknown_stockout_n"]) == (2, 3)
    assert scans == 1


def test_receipt_commits_during_order_preparation_and_is_in_the_saved_revision(shop, monkeypatch):
    client, request = shop
    register = f"/api/datasets/{request['dataset_id']}/deliveries"
    delivery = client.post(register, json={
        "number": "PO-12", "product_id": "tea", "ordered_qty": 12,
        "arrival_date": request["target_date"],
    })
    assert delivery.status_code == 201, delivery.text
    delivery_id = delivery.json()["id"]
    calculating, resume = Event(), Event()
    baseline = procurement_planning.baseline

    def paused_baseline(*args, **kwargs):
        calculating.set()
        assert resume.wait(timeout=20)
        return baseline(*args, **kwargs)

    monkeypatch.setattr(procurement_planning, "baseline", paused_baseline)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending_plan = pool.submit(client.post, "/api/plans", json=request)
        try:
            assert calculating.wait(timeout=10)
            pending_receipt = pool.submit(client.post, f"{register}/{delivery_id}/receipts", json={
                "quantity": 5, "idempotency_key": "receive-five",
            })
            receipt = pending_receipt.result(timeout=10)
            assert receipt.status_code == 200, receipt.text
            assert receipt.json()["remaining_qty"] == 7
        finally:
            resume.set()
        response = pending_plan.result(timeout=10)
    assert response.status_code == 201, response.text
    plan = response.json()
    tea = next(item for item in plan["items"] if item["product_id"] == "tea")
    assert (tea["incoming_units"], tea["recommended_qty"]) == (7, 19)
    assert plan["registry_deliveries"] == [{
        "id": delivery_id, "number": "PO-12", "product_id": "tea",
        "arrival_date": request["target_date"], "quantity": 7,
    }]
    assert client.get(f"/api/plans/{plan['id']}/revisions").json() == [plan]
