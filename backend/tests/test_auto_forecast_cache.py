from copy import deepcopy
from datetime import date

import pytest

from backend import chronos_order, forecast_cache
from backend.errors import ServiceError
from backend.ml_service import automatic_forecasts


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.setattr(forecast_cache, "_results", forecast_cache.OrderedDict())
    dataset = {"id": "sample", "version": "v1", "bakery_id": "A", "schema_version": "sales-1",
               "rows": [{"date": "2025-01-01", "product_id": "x", "sold": 2}],
               "demand_context": {"weather": [{"temperature_c": 3}]}}
    return dataset, date(2025, 1, 4), date(2025, 1, 2), ["x"]


def test_identical_reloaded_inputs_share_immutable_result(monkeypatch, inputs):
    calls = []

    def compute(*args):
        calls.append(args)
        return {"x": {"forecast_mean": 2.4, "used_dates": ["2025-01-01"]}}

    monkeypatch.setattr(chronos_order, "forecast_order", compute)
    first = automatic_forecasts(*inputs)
    first["x"]["used_dates"].clear()
    for _ in range(2):
        fresh = automatic_forecasts(*deepcopy(inputs))
        assert fresh["x"]["used_dates"] == ["2025-01-01"]
        fresh["x"].clear()
    assert len(calls) == 1


def test_corrections_horizon_products_and_branch_invalidate(monkeypatch, inputs):
    dataset, target, cutoff, products = inputs
    calls = []

    def compute(*args):
        calls.append(True)
        return {"x": {"forecast_mean": len(calls)}}

    monkeypatch.setattr(chronos_order, "forecast_order", compute)
    automatic_forecasts(*inputs)
    for field, value in (("sold", 4), ("stockout_status", "unknown"), ("is_open", False)):
        dataset["rows"][0][field] = value
        automatic_forecasts(*inputs)
    dataset["demand_context"]["weather"][0]["temperature_c"] = 7
    automatic_forecasts(*inputs)
    dataset["bakery_id"] = "B"
    automatic_forecasts(*inputs)
    automatic_forecasts(dataset, date(2025, 1, 5), cutoff, products)
    automatic_forecasts(dataset, target, date(2025, 1, 1), products)
    automatic_forecasts(dataset, target, cutoff, ["x", "y"])
    assert len(calls) == 9


def test_failure_is_retryable_and_cache_is_bounded(monkeypatch, inputs):
    def unavailable(*_):
        raise chronos_order.ChronosUnavailable("offline")

    monkeypatch.setattr(chronos_order, "forecast_order", unavailable)
    with pytest.raises(ServiceError):
        automatic_forecasts(*inputs)
    assert not forecast_cache._results
    monkeypatch.setattr(chronos_order, "forecast_order", lambda *_: {"x": {"forecast_mean": 1.2}})
    assert automatic_forecasts(*inputs)["x"]["forecast_mean"] == 1.2
    monkeypatch.setattr(forecast_cache, "MAX_ENTRIES", 2)
    for i in range(5):
        forecast_cache.remember(str(i), {"x": i})
    assert list(forecast_cache._results) == ["3", "4"]
    monkeypatch.setattr(forecast_cache, "MAX_RESULT_BYTES", 5)
    forecast_cache.remember("large", {"x": "larger than five bytes"})
    assert forecast_cache.lookup("large") is None


def test_large_panels_bypass_serialization_and_still_forecast(monkeypatch, inputs):
    dataset, *_ = inputs
    monkeypatch.setattr(forecast_cache, "MAX_INPUT_ROWS", 1)
    monkeypatch.setattr(forecast_cache, "MAX_CONTEXT_ROWS", 1)
    monkeypatch.setattr(chronos_order, "forecast_order", lambda *_: {"x": {"forecast_mean": 1.2}})

    def unexpected_encode(*args, **kwargs):
        pytest.fail("Oversized histories and contexts must bypass cache encoding")

    monkeypatch.setattr(forecast_cache.json, "dumps", unexpected_encode)
    dataset["rows"].append(dataset["rows"][0])
    assert automatic_forecasts(*inputs)["x"]["forecast_mean"] == 1.2
    dataset["rows"].pop()
    dataset["demand_context"]["weather"].append({"temperature_c": 8})
    assert automatic_forecasts(*inputs)["x"]["forecast_mean"] == 1.2
