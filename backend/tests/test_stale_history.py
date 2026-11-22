"""Products without records in the recent window require an operator decision."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import chronos_order
from backend.app import app
from backend.ml_service import automatic_forecasts
from backend.tests.test_procurement import payload

CUTOFF = date(2026, 10, 9)
TARGET = CUTOFF + timedelta(days=2)


@pytest.fixture(autouse=True)
def engine(monkeypatch):
    monkeypatch.setattr(chronos_order, "_predict", lambda inputs, horizon: [0.3] * len(inputs))


def selected(rows, schema="pos-units-1"):
    dataset = {"schema_version": schema, "rows": rows, "target": "sold_units"}
    return automatic_forecasts(dataset, TARGET, CUTOFF, ["p"])["p"]


def row(age, sold=2, **extra):
    return {"date": str(CUTOFF - timedelta(days=age)), "product_id": "p", "sold": sold, **extra}


@pytest.mark.parametrize(("age", "method"), [(0, "Chronos-2"), (55, "Chronos-2"), (56, "manual"), (200, "manual")])
def test_boundary(age, method):
    result = selected([row(age)])
    assert result["method"] == method
    if method == "manual":
        assert result["forecast_mean"] is None and result["reason"] == "stale_sales_history"


def test_explicit_zero_is_a_record():
    assert selected([row(200), row(5, 0)])["method"] != "manual"


def test_two_fresh_observations_keep_new_product_eligible():
    result = selected([row(1), row(0)])
    assert result["method"] == "Chronos-2" and result["forecast_mean"] is not None


@pytest.mark.parametrize("extra", [{"stockout_status": "occurred"}, {"is_open": False}, {"sold": None}])
def test_non_observation_does_not_refresh_history(extra):
    assert selected([row(200), {**row(1), **extra}])["reason"] == "stale_sales_history"


def test_record_after_cutoff_does_not_refresh_history():
    assert selected([row(200), row(-1)])["reason"] == "stale_sales_history"


def test_no_history_keeps_its_own_reason():
    assert selected([])["reason"] == "no_sales_history"


def test_stale_product_is_manual_in_preview_and_order(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "stale.sqlite"))
    monkeypatch.setattr("backend.app.business_today", lambda: date(2026, 10, 10))
    monkeypatch.setattr("backend.context_routes.business_today", lambda: date(2026, 10, 10))
    data = payload()
    lines = [data["csv_text"].splitlines()[0], f"{CUTOFF - timedelta(days=200)},old,0,3,3,0,0"]
    lines += [f"{CUTOFF - timedelta(days=n)},live,0,3,3,0,0" for n in range(10, -1, -1)]
    data["csv_text"] = chr(10).join(lines)
    data["catalog"] = [{"product_id": p, "name": p, "pack_size": 24} for p in ("old", "live")]
    with TestClient(app) as client:
        ident = client.post("/api/datasets", json=data).json()["id"]
        preview = client.post(f"/api/datasets/{ident}/first-forecast").json()["result"]["items"]
        response = client.post("/api/plans", json={
            "dataset_id": ident, "target_date": str(TARGET), "cutoff": str(CUTOFF),
            "lead_time_days": 1, "usable_stock_assumption": {"old": 0, "live": 0},
            "expected_deliveries": [],
        })
    assert response.status_code == 201, response.text
    order = {item["product_id"]: item for item in response.json()["items"]}
    shown = {item["product_id"]: item for item in preview}
    assert shown["old"]["method"] == order["old"]["method"] == "manual"
    assert order["old"]["reason"] == shown["old"]["reason"] == "stale_sales_history"
    assert order["old"]["recommended_qty"] == 0 and not order["old"]["automatic_eligible"]
    assert order["live"]["automatic_eligible"] and order["live"]["recommended_qty"] == 24
