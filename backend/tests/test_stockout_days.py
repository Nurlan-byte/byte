from datetime import date, timedelta

import pytest

from backend import chronos_order, forecast_cache, ml_service
from backend.procurement_planning import procurement_item
from backend.sales_observations import HISTORY_RULE

START = date(2025, 1, 1)
TARGET = START + timedelta(days=30)
CUTOFF = START + timedelta(days=28)
STOCKOUT = START + timedelta(days=23)
PRODUCT = {"product_id": "x", "name": "x", "pack_size": 1, "max_order_qty": None}


def rows(status="occurred"):
    return [
        {"date": str(day), "product_id": "x", "is_open": True,
         "sold": 1 if day == STOCKOUT else 10,
         "stockout_status": status if day == STOCKOUT else "unknown"}
        for day in (START + timedelta(days=offset) for offset in range(29))
    ]


@pytest.mark.parametrize("scope", [
    {"schema_version": "sales-1"},
    {"schema_version": "pos-units-1"},
    {"planning_kind": "procurement", "schema_version": "procurement-1"},
])
@pytest.mark.parametrize("status,known", [("occurred", False), ("unknown", True), ("none", True)])
def test_only_marked_stockout_is_missing_for_automatic_forecast(monkeypatch, scope, status, known):
    seen = {}

    def engine(dataset, target, cutoff, products):
        seen.update(chronos_order._known_sales(dataset, cutoff.toordinal(), products)["x"])
        return {"x": {"forecast_mean": 1}}

    monkeypatch.setattr(forecast_cache, "_results", forecast_cache.OrderedDict())
    monkeypatch.setattr(chronos_order, "forecast_order", engine)
    dataset = {"id": "sample", "version": "v1", "rows": rows(status), **scope}
    ml_service.automatic_forecasts(dataset, TARGET, CUTOFF, ["x"])
    assert (STOCKOUT.toordinal() in seen) is known
    assert len(seen) == (29 if known else 28)
    assert dataset["rows"] == rows(status)


def test_stockout_is_not_low_demand_in_weekday_fallbacks():
    item = procurement_item(rows(), PRODUCT, TARGET, CUTOFF, "B1", True, 0)
    fallback = ml_service._b1(
        {"planning_kind": "procurement"}, rows(), "x", TARGET, CUTOFF, "test")
    assert item["recommended_qty"] == 10
    assert item["forecast_mean"] == fallback["forecast_mean"] == 10
    assert str(STOCKOUT) not in item["used_dates"] + fallback["used_dates"]


def test_history_with_only_stockouts_requires_manual_order():
    history = [{**row, "stockout_status": "occurred"} for row in rows()]
    item = procurement_item(history, PRODUCT, TARGET, CUTOFF, "B1", True, 0)
    assert item["method"] == "manual" and item["forecast_mean"] is None


@pytest.mark.parametrize("status,cutoff,changed", [
    ("occurred", CUTOFF, True),
    ("occurred", STOCKOUT, True),
    ("occurred", STOCKOUT - timedelta(days=1), False),
    ("unknown", CUTOFF, False),
    ("none", CUTOFF, False),
])
def test_only_changed_history_gets_its_own_protocol(status, cutoff, changed):
    suffix = f"-{HISTORY_RULE}" if changed else ""
    assert ml_service.automatic_protocol({"rows": rows(status)}, cutoff) == chronos_order.PROTOCOL_VERSION + suffix


def test_forecast_reports_the_protocol_of_its_history(monkeypatch):
    monkeypatch.setattr(forecast_cache, "_results", forecast_cache.OrderedDict())
    monkeypatch.setattr(chronos_order, "forecast_order", lambda *args: {"x": {"forecast_mean": 1}})
    protocols = []
    for status in ("occurred", "none"):
        dataset = {"id": "sample", "version": status, "rows": rows(status), "schema_version": "pos-units-1",
                   "bakery_id": "x", "source": "synthetic", "provenance": "test",
                   "target": "sold_units", "unit": "physical_unit"}
        protocols.append(ml_service.forecast_from_dataset(dataset, TARGET, CUTOFF, "AUTO", None)["protocol_version"])
    assert protocols == [f"{chronos_order.PROTOCOL_VERSION}-{HISTORY_RULE}", chronos_order.PROTOCOL_VERSION]
