"""Contract and transactional checks for the independent procurement path."""

import csv
import importlib
import io
from copy import deepcopy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.ml_core import forecast_model, train_model


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "procurement.sqlite3"))
    with TestClient(app) as active:
        yield active


def payload(days=70):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "product_id", "opening", "received", "sold", "waste", "closing"])
    for n in range(days):
        day = date(2026, 8, 1) + timedelta(days=n)
        writer.writerow([day, "tea", 0, 10 + n % 7, 10 + n % 7, 0, 0])
    return {
        "schema_version": "procurement-1", "source": "synthetic",
        "bakery_id": "test-shop", "target": "sold_units", "unit": "physical_unit",
        "provenance": "Generated test fixture", "csv_text": out.getvalue(),
        "catalog": [{"product_id": "tea", "name": "Tea", "pack_size": 6, "max_order_qty": 36}],
    }


def imported(client):
    response = client.post("/api/datasets", json=payload())
    assert response.status_code == 201, response.text
    return response.json()


def test_import_contract_balance_and_identity(client):
    dataset = imported(client)
    assert dataset["planning_kind"] == "procurement"
    assert dataset["rows"][0]["is_open"] is None
    assert dataset["rows"][0]["stockout_status"] == "unknown"
    assert dataset["catalog"][0]["pack_size"] == 6
    duplicate = client.post("/api/datasets", json=payload())
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == dataset["id"]
    bad = payload()
    bad["csv_text"] = bad["csv_text"].replace(",10,10,0,0", ",10,11,0,0", 1)
    invalid = client.post("/api/datasets", json=bad)
    assert invalid.status_code == 422
    assert "balance" in invalid.text or "equal" in invalid.text
    bad = payload()
    bad["catalog"][0]["product_id"] = "other"
    assert client.post("/api/datasets", json=bad).status_code == 422


def test_plan_confirm_export_and_restart(client):
    dataset = imported(client)
    request = {
        "dataset_id": dataset["id"], "target_date": "2026-10-11",
        "cutoff": "2026-10-09", "mode": "replay", "method": "B1",
        "lead_time_days": 1, "usable_stock_assumption": {"tea": 0},
    }
    response = client.post("/api/plans", json=request)
    assert response.status_code == 201, response.text
    plan = response.json()
    item = plan["items"][0]
    assert plan["planning_kind"] == "procurement"
    assert plan["order_date"] == "2026-10-10"
    assert item["forecast_mean"] is not None
    assert item["recommended_qty"] % 6 == 0
    assert item["confirmed_qty"] is None
    assert item["unknown_open_n"] == 70
    assert client.post(f"/api/plans/{plan['id']}/confirm", json={
        "revision": 0, "idempotency_key": "bad", "quantities": {"tea": 7},
    }).status_code == 422
    confirm = {"revision": 0, "idempotency_key": "good", "quantities": {"tea": 18}}
    saved = client.post(f"/api/plans/{plan['id']}/confirm", json=confirm)
    assert saved.status_code == 200, saved.text
    assert saved.json()["items"][0]["confirmed_qty"] == 18
    assert client.post(f"/api/plans/{plan['id']}/confirm", json=confirm).json() == saved.json()
    assert client.get(f"/api/plans/{plan['id']}").json()["revision"] == 1
    export = client.get(f"/api/plans/{plan['id']}/export")
    assert export.status_code == 200
    assert "forecast_sales_units" in export.text
    assert "confirmed_order_units" in export.text


def test_unknown_stock_is_not_zero(client):
    dataset = imported(client)
    request = {
        "dataset_id": dataset["id"], "target_date": "2026-10-11",
        "cutoff": "2026-10-09", "mode": "replay", "method": "B1",
        "lead_time_days": 1,
    }
    response = client.post("/api/plans", json=request)
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["usable_stock_assumption"] is None
    assert item["automatic_eligible"] is False
    assert item["method"] == "manual"
    assert item["forecast_mean"] is not None
    assert item["recommended_qty"] == 0
    request["target_date"] = "2026-10-10"
    assert client.post("/api/plans", json=request).status_code == 422


def test_training_and_forecast_observed_sales(client):
    dataset = imported(client)
    response = client.post(f"/api/datasets/{dataset['id']}/train", json={
        "cutoff": "2026-10-09", "idempotency_key": "fit",
    })
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["protocol_version"].startswith("procurement-")
    assert model["counts"]["eligible_labels"] == 70
    assert model["evaluation"]["selection_performed"] is True
    assert model["evaluation"]["evaluation_role"] == "internal_selection_validation_not_independent_accuracy_estimate"
    forecast = client.post("/api/forecasts", json={
        "dataset_id": dataset["id"], "target_date": "2026-10-11",
        "cutoff": "2026-10-09", "method": "ML", "model_id": model["id"],
    })
    assert forecast.status_code == 200, forecast.text
    assert forecast.json()["items"][0]["forecast_mean"] is not None
    plan = client.post("/api/plans", json={
        "dataset_id": dataset["id"], "target_date": "2026-10-11",
        "cutoff": "2026-10-09", "mode": "replay", "method": "ML",
        "model_id": model["id"], "lead_time_days": 1,
        "usable_stock_assumption": {"tea": 0},
    })
    assert plan.status_code == 201, plan.text
    assert plan.json()["model_id"] == model["id"]
    assert plan.json()["items"][0]["recommended_qty"] % 6 == 0


def test_future_rows_cannot_change_frozen_model_or_first_forecast():
    with io.StringIO(payload(80)["csv_text"]) as source:
        rows = list(csv.DictReader(source))
    for row in rows:
        row["sold"] = int(row["sold"])
        row["is_open"] = None
        row["stockout_status"] = "unknown"
    dataset = {
        "id": "isolated", "version": "v1", "bakery_id": "test-shop",
        "target": "sold_units", "unit": "physical_unit",
        "planning_kind": "procurement", "rows": rows,
    }
    cutoff = date(2026, 10, 1)
    model = train_model(dataset, cutoff)
    first = forecast_model(model, dataset, "tea", date(2026, 10, 3), cutoff)
    changed = deepcopy(dataset)
    for row in changed["rows"]:
        if row["date"] > cutoff.isoformat():
            row["sold"] = 999999
    assert train_model(changed, cutoff) == model
    assert forecast_model(model, changed, "tea", date(2026, 10, 3), cutoff) == first
    farther = forecast_model(model, dataset, "tea", date(2026, 10, 4), cutoff)
    assert farther["method"] == "B1"
    assert farther["fallback_reason"] == "forecast_horizon_mismatch"


def test_operational_order_date_expires_before_late_confirmation(client, monkeypatch):
    api = importlib.import_module("backend.app")
    dataset = imported(client)
    monkeypatch.setattr(api, "business_today", lambda: date(2026, 11, 7))
    response = client.post("/api/plans", json={
        "dataset_id": dataset["id"], "target_date": "2026-11-10",
        "cutoff": "2026-10-09", "mode": "operational", "method": "B1",
        "lead_time_days": 2, "usable_stock_assumption": {"tea": 0},
    })
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["order_date"] == "2026-11-07"
    monkeypatch.setattr(api, "business_today", lambda: date(2026, 11, 8))
    confirmation = client.post(f"/api/plans/{plan['id']}/confirm", json={
        "revision": 0, "idempotency_key": "late", "quantities": {"tea": 12},
    })
    assert confirmation.status_code == 409
    assert client.get(f"/api/plans/{plan['id']}").json()["revision"] == 0
