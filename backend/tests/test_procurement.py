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
from backend.storage import save_plan


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
    row, = csv.DictReader(io.StringIO(export.text.lstrip("\ufeff")))
    assert row["plan_id"] == plan["id"]
    assert row["revision"] == "1"
    assert row["confirmed_order_units"] == "18"


@pytest.mark.parametrize(("maximum", "ml_forecast", "capped"), [(6, 5, False), (36, 40, True)])
def test_final_ml_order_and_warning_use_the_same_forecast(client, monkeypatch, maximum, ml_forecast, capped):
    history = payload()
    history["catalog"][0]["max_order_qty"] = maximum
    dataset = client.post("/api/datasets", json=history).json()

    def forecast_items(*args):
        return {
            "model_id": "test-model", "model": {"id": "test-model", "protocol_version": "test"},
            "items": [{
                "product_id": "tea", "method": "ML", "forecast_mean": ml_forecast,
                "used_dates": [], "n": 4,
            }],
        }

    monkeypatch.setattr("backend.ml_service.forecast_items", forecast_items)
    response = client.post("/api/plans", json={
        "dataset_id": dataset["id"], "target_date": "2026-10-11",
        "cutoff": "2026-10-09", "mode": "replay", "method": "ML",
        "lead_time_days": 1, "usable_stock_assumption": {"tea": 0},
    })
    assert response.status_code == 201
    item = response.json()["items"][0]
    assert item["method"] == "ML"
    assert item["max_constraint_active"] is capped
    assert any("Supplier maximum" in warning for warning in item["warnings"]) is capped


def test_saved_csv_distinguishes_suggested_order_from_manual_quantity(client):
    history = payload()
    history["catalog"][0]["max_order_qty"] = 6
    dataset_id = client.post("/api/datasets", json=history).json()["id"]
    plan = client.post("/api/plans", json={
        "dataset_id": dataset_id, "target_date": "2026-10-11", "cutoff": "2026-10-09",
        "mode": "replay", "lead_time_days": 1, "usable_stock_assumption": {"tea": 0},
    }).json()
    endpoint = f"/api/plans/{plan['id']}"
    assert plan["items"][0]["forecast_mean"] == 11
    draft_export = client.get(endpoint + "/export?revision=0").content
    draft_row, = csv.DictReader(io.StringIO(draft_export.decode("utf-8-sig")))
    assert draft_row["suggested_constraint_reason"] == "supplier_limit"
    assert draft_row["confirmed_shortage_units"] == ""

    confirmed = client.post(endpoint + "/confirm", json={
        "revision": 0, "idempotency_key": "zero", "quantities": {"tea": 0},
    })
    assert confirmed.status_code == 200
    saved = confirmed.json()["items"][0]
    assert (saved["confirmed_packs"], saved["confirmed_shortage_units"], saved["confirmed_constraint_reason"]) == (0, 11, None)
    row, = csv.DictReader(io.StringIO(client.get(endpoint + "/export?revision=1").text.lstrip("\ufeff")))
    assert (row["suggested_order_packs"], row["suggested_shortage_units"], row["suggested_constraint_reason"]) == ("1", "5", "supplier_limit")
    assert (row["confirmed_order_packs"], row["confirmed_shortage_units"], row["confirmed_constraint_reason"]) == ("0", "11", "")
    assert client.get(endpoint + "/export?revision=0").content == draft_export


def test_legacy_order_export_preserves_original_bytes_and_revision(client):
    legacy = {
        "id": "historical-order", "revision": 1, "status": "confirmed", "mode": "replay",
        "config_version": "procurement-2", "planning_kind": "procurement",
        "order_date": "2026-10-10", "target_date": "2026-10-11", "cutoff": "2026-10-09",
        "lead_time_days": 1, "dataset_id": "old-data", "dataset_version": "v1",
        "source": "synthetic", "provenance": "test", "requested_method": "B1",
        "items": [{
            "product_id": "tea", "name": "Tea", "forecast_mean": 11, "usable_stock_assumption": 0,
            "incoming_units": 0, "available_stock_units": 0, "recommended_qty": 6, "confirmed_qty": 6,
            "pack_size": 6, "max_order_qty": 6, "method": "B1", "used_dates": ["2026-10-04"],
        }],
    }
    save_plan(legacy)
    expected = (
        "plan_id,revision,planning_kind,order_date,target_date,cutoff,lead_time_days,dataset_id,dataset_version,"
        "source,provenance,product_id,name,forecast_sales_units,assumed_usable_stock_units,suggested_order_units,"
        "confirmed_order_units,pack_size,max_order_qty,method,requested_method,model_id,protocol_version,used_dates,"
        "fallback_reason,incoming_units,available_stock_units\r\n"
        "historical-order,1,procurement,2026-10-10,2026-10-11,2026-10-09,1,old-data,v1,synthetic,test,tea,"
        "Tea,11,0,6,6,6,6,B1,B1,,,2026-10-04,,0,0\r\n"
    ).encode("utf-8-sig")
    url = "/api/plans/historical-order"
    assert client.get(url + "/export").content == expected
    updated = client.post(url + "/confirm", json={
        "revision": 1, "idempotency_key": "edit", "quantities": {"tea": 0},
    })
    assert updated.status_code == 200
    assert client.get(url + "/export?revision=1").content == expected


def test_scheduled_deliveries_are_counted_once_by_target_and_saved_with_revision(client):
    dataset = imported(client)
    deliveries = [
        {"product_id": "tea", "arrival_date": day, "quantity": qty}
        for day, qty in [("2026-10-10", 5), ("2026-10-11", 4), ("2026-10-12", 120)]
    ]
    body = {
        "dataset_id": dataset["id"], "target_date": "2026-10-11", "cutoff": "2026-10-09",
        "mode": "replay", "lead_time_days": 1, "usable_stock_assumption": {"tea": 1},
        "expected_deliveries": deliveries,
    }
    result = client.post("/api/plans", json=body)
    assert result.status_code == 201, result.text
    plan = result.json()
    item = plan["items"][0]
    assert item["available_stock_units"] == 10
    assert item["incoming_units"] == 9
    assert item["recommended_qty"] == 6
    url = f"/api/plans/{plan['id']}"
    saved = client.post(url + "/confirm", json={
        "revision": 0, "idempotency_key": "save-delivery", "quantities": {"tea": 6},
    })
    assert saved.status_code == 200
    assert client.get(url).json()["expected_deliveries"] == deliveries
    row = next(csv.DictReader(io.StringIO(client.get(url + "/export").text.lstrip("\ufeff"))))
    assert (row["incoming_units"], row["assumed_usable_stock_units"], row["available_stock_units"]) == ("9", "1", "10")
    unknown = client.post("/api/plans", json={**body, "usable_stock_assumption": {}}).json()["items"][0]
    assert unknown["available_stock_units"] is None and unknown["method"] == "manual"
    for change in ({"arrival_date": "2026-10-09"}, {"product_id": "other"}, {"quantity": 0}, {"quantity": True}):
        invalid = client.post("/api/plans", json={**body, "expected_deliveries": [{**deliveries[0], **change}]})
        assert invalid.status_code == 422


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
