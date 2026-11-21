from datetime import date

import pytest
from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from backend import chronos_order, ml_service
from backend.app import app
from backend.schemas import ForecastRequest
from backend.tests.test_ml_api import forecast_body, import_dataset, synthetic_csv, train


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "forecast-contract.sqlite3"))
    monkeypatch.setattr("backend.app.business_today", lambda: date(2026, 10, 13))
    with TestClient(app) as client:
        yield client


def test_success_response_schema_and_required_prediction(client, monkeypatch):
    schemas = client.get("/openapi.json").json()
    success = schemas["paths"]["/api/forecasts"]["post"]["responses"]["200"]
    contract = success["content"]["application/json"]["schema"]
    assert contract.get("$ref") == "#/components/schemas/ForecastResponse"
    dataset = import_dataset(client, synthetic_csv(days=1, products=["p1"]))
    request = forecast_body(dataset, method="B1")
    result = client.post("/api/forecasts", json=request).json()
    del result["items"][0]["forecast_mean"]
    monkeypatch.setattr("backend.app.create_forecast", lambda *_: result)
    with pytest.raises(ResponseValidationError) as error:
        client.post("/api/forecasts", json=request)
    assert error.value.errors()[0]["loc"] == ("response", "items", 0, "forecast_mean")


@pytest.mark.parametrize(("method", "days", "expected_method"), [
    ("B1", 1, "manual"), ("B1", 60, "B1"), ("ML", 90, "ML"), ("AUTO", 60, "Chronos-2"),
])
def test_forecast_response_preserves_service_payload(client, monkeypatch, method, days, expected_method):
    receipts = method == "AUTO"
    dataset = import_dataset(client, synthetic_csv(days=days, sales_only=receipts), sales_only=receipts)
    if method == "ML":
        response = train(client, dataset)
        assert response.status_code == 201 and response.json()["status"] == "ready"
    if receipts:
        monkeypatch.setattr(chronos_order, "_predict", lambda entries, horizon: [8.25] * len(entries))
    request = forecast_body(dataset, method=method)
    expected = ml_service.create_forecast(ForecastRequest(**request), date(2026, 10, 13))
    response = client.post("/api/forecasts", json=request)
    assert response.status_code == 200, response.text
    assert response.json() == expected
    item = response.json()["items"][0]
    assert item["method"] == expected_method
    if receipts:
        assert item["reason"] is None and item["selection"]["b1_mae"] is None
        assert "evaluation_applicability" not in response.json()
    else:
        assert "protocol_version" not in response.json() and "selection" not in item
        assert item["fallback_reason"] is None
        if expected_method == "manual":
            assert item["forecast_mean"] is None and item["used_dates"] == []
