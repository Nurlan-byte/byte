"""Ordering expectations shared with the browser, independent of forecast source."""

import json
from math import ceil, floor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app


CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "procurement_order_cases.json")
    .read_text(encoding="utf-8")
)


def import_sales(client, inputs):
    mean = inputs["forecast_mean"]
    if mean is None:
        # Saturday sales provide no Sunday history.
        observations = [("2026-01-24", 10)]
    else:
        observations = [("2026-01-18", floor(mean)), ("2026-01-25", ceil(mean))]
    rows = [f"{day},water,0,{sold},{sold},0,0" for day, sold in observations]
    result = client.post("/api/datasets", json={
        "schema_version": "procurement-1", "source": "synthetic",
        "bakery_id": "contract-shop", "target": "sold_units", "unit": "physical_unit",
        "provenance": "Synthetic order policy examples",
        "catalog": [{
            "product_id": "water", "name": "Water",
            "pack_size": inputs["pack_size"], "max_order_qty": inputs["max_order_qty"],
        }],
        "csv_text": "\n".join(["date,product_id,opening,received,sold,waste,closing", *rows]),
    })
    assert result.status_code == 201, result.text
    return result.json()["id"]


def create_order(client, request, method):
    response = client.post("/api/plans", json={**request, "method": method})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_same_forecast_has_one_order_decision(case, monkeypatch, tmp_path):
    inputs = case["input"]
    expected = case["expected"]
    stock = inputs["usable_stock_assumption"]
    incoming = inputs["incoming_units"]
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "orders.sqlite3"))
    with TestClient(app) as client:
        request = {
            "dataset_id": import_sales(client, inputs),
            "target_date": "2026-02-01", "cutoff": "2026-01-30",
            "target_is_open": inputs["target_is_open"], "mode": "replay", "lead_time_days": 1,
            "usable_stock_assumption": {} if stock is None else {"water": stock},
            "expected_deliveries": [
                {"product_id": "water", "quantity": incoming, "arrival_date": "2026-02-01"}
            ] if incoming else [],
        }
        plan = create_order(client, request, "B1")
        baseline, = plan["items"]
        fallback, = create_order(client, request, "ML")["items"]  # No model has been trained.
        assert fallback["fallback_reason"]
        orders = [baseline, fallback]
        if inputs["forecast_mean"] is not None:
            # Hold the numerical forecast fixed while switching its source to ML.
            model = {"id": "controlled-model", "protocol_version": "test"}
            prediction = {
                "product_id": "water", "method": "ML", "forecast_mean": inputs["forecast_mean"],
                "used_dates": baseline["used_dates"], "n": baseline["n"],
                "evaluation_applicability": {"applicable": True}, "fallback_reason": None,
            }
            monkeypatch.setattr("backend.ml_service.forecast_items", lambda *args, **kwargs: {
                "model": model, "model_id": model["id"], "items": [prediction],
            })
            ml, = create_order(client, request, "ML")["items"]
            assert ml["method"] == ("ML" if expected["automatic_eligible"] else "manual")
            orders.append(ml)
        for item in orders:
            assert item["forecast_mean"] == inputs["forecast_mean"]
            assert item["incoming_units"] == incoming
            assert item["available_stock_units"] == (None if stock is None else stock + incoming)
            assert {key: item[key] for key in expected} == expected
            assert item["max_constraint_active"] is (expected["constraint_reason"] is not None)
        assert baseline["method"] == fallback["method"] == ("B1" if expected["automatic_eligible"] else "manual")
        if edit := case.get("edit"):
            response = client.post(f"/api/plans/{plan['id']}/confirm", json={
                "revision": plan["revision"], "idempotency_key": "manual-edit",
                "quantities": {"water": edit["quantity"]},
            })
            assert response.status_code == 200, response.text
            confirmed, = response.json()["items"]
            assert confirmed["confirmed_packs"] == edit["quantity"] // inputs["pack_size"]
            assert confirmed["confirmed_shortage_units"] == edit["shortage_units"]
            assert confirmed["confirmed_constraint_reason"] == edit["constraint_reason"]
            assert confirmed["shortage_units"] == expected["shortage_units"]
            assert confirmed["constraint_reason"] == expected["constraint_reason"]
