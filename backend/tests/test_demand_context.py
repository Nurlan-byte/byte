"""Checks against deliberately synthetic weather-sensitive shops, never club impact claims."""

from copy import deepcopy
from datetime import date, timedelta
import random

import pytest
from pydantic import ValidationError

from backend import ml_core, ml_service
from backend.demand_context import (
    CONTEXT_FEATURE_NAMES,
    ContextIndex,
    DemandContext,
    with_demand_context,
)
from backend.tests.test_ml_core import START, fixture


def day(index):
    return (START + timedelta(days=index)).isoformat()


def weather(index, issue, temperature, kind="forecast", rain=None):
    return {
        "date": day(index),
        "available_on": day(issue),
        "kind": kind,
        "temperature_c": temperature,
        "precipitation_mm": rain,
    }


def club(sign=1, kind="production"):
    data = fixture(150)
    if kind == "procurement":
        data["planning_kind"] = "procurement"
    if kind == "pos":
        data["schema_version"] = "pos-units-1"
    rng = random.Random(12345)
    readings = []
    for index, row in enumerate(data["rows"]):
        temp = rng.choice([-12, -8, 5, 13, 22])
        readings.append(weather(index, index - 2, temp))
        row["sold"] = 65 + sign * temp * 2
        if kind in {"pos", "procurement"}:
            row.pop("is_open")
            row.pop("opening")
            row.pop("closing")
            row.pop("stockout_status")
    data["demand_context"] = {"weather": readings}
    return data


def missing_finite(values):
    return {
        name: value
        for name, value in zip(CONTEXT_FEATURE_NAMES, values, strict=True)
        if value == value
    }


@pytest.mark.parametrize("kind", ["production", "procurement", "pos"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_same_forecast_can_learn_opposite_temperature_response(sign, kind):
    data = club(sign, kind)
    cutoff = date.fromisoformat(day(149))
    target = date.fromisoformat(day(151))
    model = ml_core.train_model(data, cutoff)
    assert model["status"] == "ready"
    forecasts = []
    for temperature in (-12, 22):
        candidate = deepcopy(data)
        candidate["demand_context"]["weather"].append(weather(151, 149, temperature))
        item = ml_core.forecast_model(model, candidate, "p1", target, cutoff)
        assert item["method"] == "ML"
        assert item["demand_features"]["forecast_temperature_c"] == temperature
        forecasts.append(item["forecast_mean"])
    assert sign * (forecasts[1] - forecasts[0]) > 5


def test_as_of_records_revisions_and_lagged_weather():
    # START is Thursday. The target Saturday was originally a day off,
    # then declared a working day after the simulated decision.
    ctx = ContextIndex(
        {
            "calendar": [
                {"date": day(2), "available_on": day(-9), "day_type": "holiday"},
                {"date": day(2), "available_on": day(1), "day_type": "workday"},
            ],
            "weather": [
                weather(2, 0, 8, rain=0),
                weather(2, 1, 19, rain=9),
                weather(2, 2, 50, kind="observation"),
                weather(-1, 0, 12, kind="observation"),
                weather(-2, -1, 20, kind="observation"),
            ],
        }
    )
    target = date.fromisoformat(day(2))
    features = missing_finite(ctx.features(target, date.fromisoformat(day(0))))
    assert features == {
        "weekend": 1,
        "day_off": 1,
        "public_holiday": 1,
        "latest_temperature_c": 12,
        "recent_temperature_change_c": -8,
        "forecast_temperature_c": 8,
        "forecast_temperature_change_c": -4,
        "forecast_precipitation_mm": 0,
    }
    later = missing_finite(ctx.features(target, date.fromisoformat(day(1))))
    assert later["weekend"] == 1 and later["day_off"] == later["public_holiday"] == 0
    assert later["forecast_temperature_c"] == 19
    assert missing_finite(ctx.features(target, date.fromisoformat(day(-16)))) == {
        "weekend": 1,
        "day_off": 1,
    }


def test_observations_arriving_after_decision_cannot_change_training_or_prediction():
    data = club()
    cutoff, target = date.fromisoformat(day(149)), date.fromisoformat(day(151))
    original = ml_core.train_model(data, cutoff)
    poison = deepcopy(data)
    poison["demand_context"]["weather"] += [
        weather(i, 151, 61, "observation") for i in range(152)
    ]
    poison["demand_context"]["calendar"] = [
        {"date": day(i), "available_on": day(151), "day_type": "holiday"}
        for i in range(152)
    ]
    assert ml_core.train_model(poison, cutoff) == original
    assert ml_core.forecast_model(
        original, poison, "p1", target, cutoff
    ) == ml_core.forecast_model(original, data, "p1", target, cutoff)
    poison["demand_context"]["weather"][61]["temperature_c"] += 3
    assert (
        ml_core.forecast_model(original, poison, "p1", target, cutoff)[
            "fallback_reason"
        ]
        == "snapshot_mismatch"
    )


def test_context_is_opt_in_and_missing_weather_is_not_zero():
    data = fixture()
    cutoff, target = date.fromisoformat(day(89)), date.fromisoformat(day(91))
    legacy = ml_core.train_model(data, cutoff)
    artifact = ml_core.forecast_model(legacy, data, "p1", target, cutoff)
    assert artifact["method"] == "ML" and "demand_features" not in artifact
    enriched = {**data, "demand_context": {}}
    current = ml_core.train_model(enriched, cutoff)
    item = ml_core.forecast_model(current, enriched, "p1", target, cutoff)
    assert item["method"] == "ML"
    assert item["demand_features"] == {"weekend": 0, "day_off": 0}
    assert (
        ml_core.forecast_model(legacy, enriched, "p1", target, cutoff)[
            "fallback_reason"
        ]
        == "unsupported_protocol"
    )
    assert ml_core.forecast_model(current, data, "p1", target, cutoff)["method"] == "B1"


def test_forecast_only_physical_sales_without_stock_can_use_b1():
    data = club(kind="pos")
    item = ml_service.forecast_items(
        data, date.fromisoformat(day(151)), date.fromisoformat(day(149)), method="B1"
    )["items"][0]
    assert item["method"] == "B1" and item["n"] == 4 and item["forecast_mean"] > 0


def test_immutable_context_dedup_is_order_independent_and_replacing_does_not_chain_versions():
    data = fixture(2)
    a, b = weather(1, -1, 11), weather(2, 0, 9)
    first = with_demand_context(data, {"weather": [b, a]})
    second = with_demand_context(first, {"weather": [a, b]})
    assert first == second
    assert first["version"] == first["content_hash"] != data["version"]
    assert first["rows"] == data["rows"] and "demand_context" not in data
    replacement = {"weather": [b]}
    assert with_demand_context(first, replacement) == with_demand_context(
        data, replacement
    )


@pytest.mark.parametrize(
    "rows",
    [
        [weather(2, 1, 0, "observation")],
        [weather(2, 3, 0, "forecast")],
        [weather(2, 0, None)],
        [weather(2, 0, True)],
        [weather(2, 0, float("inf"))],
        [weather(2, 0, 12), weather(2, 0, 15)],
        [weather(2, 0, None, rain=-2)],
    ],
)
def test_invalid_vintages_rejected(rows):
    with pytest.raises(ValidationError):
        DemandContext.model_validate({"weather": rows})


def test_context_future_edition_changes_immutable_dataset_but_not_trained_evidence():
    data = club()
    context = data.pop("demand_context")
    first = with_demand_context(data, context)
    next_edition = {**context, "weather": [*context["weather"], weather(151, 150, 27)]}
    revised = {
        **with_demand_context(first, next_edition),
        "id": "new-immutable-revision",
    }
    cutoff = date.fromisoformat(day(149))
    left, right = (ml_core.train_model(version, cutoff) for version in [first, revised])
    assert left["dataset_version"] != right["dataset_version"]
    assert left["snapshot_hash"] == right["snapshot_hash"]
    assert left["products"] == right["products"]
    with pytest.raises(ValueError, match="identity mismatch"):
        ml_core.forecast_model(
            left, revised, "p1", date.fromisoformat(day(151)), cutoff
        )


@pytest.mark.parametrize("kind", ["production", "procurement"])
def test_full_ridge_forecast_context_is_saved_in_operational_workflows(
    monkeypatch, tmp_path, kind
):
    from fastapi.testclient import TestClient
    from backend.app import app

    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "context.sqlite3"))
    data = club(-1, kind)
    context = data["demand_context"]
    context.update(city="Шымкент", weather_source="Synthetic weather archive")
    context["weather"].append(weather(151, 149, -12))
    if kind == "production":
        header = "date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status"
        lines = [
            f"{r['date']},p1,true,0,{r['sold']},{r['sold']},0,0,0,none"
            for r in data["rows"]
        ]
        extra = {}
    else:
        header = "date,product_id,opening,received,sold,waste,closing"
        lines = [f"{r['date']},p1,0,{r['sold']},{r['sold']},0,0" for r in data["rows"]]
        extra = {
            "schema_version": "procurement-1",
            "catalog": [{"product_id": "p1", "name": "Tea", "pack_size": 1}],
        }
    body = {
        "source": "synthetic",
        "target": "sold_units",
        "unit": "physical_unit",
        "bakery_id": "weather-test",
        "provenance": "Synthetic weather and sales",
        "csv_text": "\n".join([header, *lines]),
        "demand_context": context,
        **extra,
    }
    with TestClient(app) as client:
        created = client.post("/api/datasets", json=body)
        assert created.status_code == 201, created.text
        ident = created.json()["id"]
        saved = client.get(f"/api/datasets/{ident}").json()
        assert saved["demand_context"]["city"] == "Шымкент"
        assert saved["demand_context"]["weather"][-1]["temperature_c"] == -12
        body["demand_context"]["weather"].reverse()
        dedup = client.post("/api/datasets", json=body)
        assert dedup.status_code == 200 and dedup.json()["id"] == ident
        train = client.post(
            f"/api/datasets/{ident}/train",
            json={"cutoff": day(149), "idempotency_key": "train"},
        )
        assert train.status_code == 201, train.text
        req = {
            "dataset_id": ident,
            "cutoff": day(149),
            "target_date": day(151),
            "method": "ML",
        }
        forecast = client.post("/api/forecasts", json=req)
        assert forecast.status_code == 200, forecast.text
        prediction = forecast.json()["items"][0]
        assert prediction["method"] == "ML"
        assert prediction["demand_features"]["forecast_temperature_c"] == -12
        if kind == "procurement":
            req.update(lead_time_days=1, usable_stock_assumption={"p1": 0})
        plan = client.post("/api/plans", json={**req, "mode": "replay"})
        assert plan.status_code == 201, plan.text
        item = client.get(f"/api/plans/{plan.json()['id']}").json()["items"][0]
        assert item["forecast_mean"] == prediction["forecast_mean"]
        assert item["demand_features"] == prediction["demand_features"]


@pytest.mark.parametrize("constant,expected", [(True, "B1"), (False, "history")])
def test_context_can_lose_and_unused_weather_is_not_reported(constant, expected):
    data = fixture(150)
    for i, row in enumerate(data["rows"]):
        row["sold"] = 45 if constant else 20 + i // 2
    # The correlation in early data reverses in validation.
    data["demand_context"] = {
        "weather": [
            weather(i, i - 2, i / 5 if i < 120 else -40 + i / 20) for i in range(152)
        ]
    }
    cutoff, target = date.fromisoformat(day(149)), date.fromisoformat(day(151))
    selected = ml_core.train_model(data, cutoff)
    raw = ml_core.train_model(data, cutoff, select_context=False)
    decision = selected["products"]["p1"]["selection"]
    assert decision["selected"] == expected
    assert decision["mae"][expected] == min(decision["mae"].values())
    assert decision["paired_n"] == 30
    operational = ml_core.forecast_model(selected, data, "p1", target, cutoff)
    assert "demand_features" not in operational
    assert operational["method"] == ("B1" if constant else "ML")
    if not constant:
        original = {**data, "demand_context": None}
        history_only = ml_core.forecast_model(
            ml_core.train_model(original, cutoff), original, "p1", target, cutoff
        )
        assert operational["forecast_mean"] == pytest.approx(
            history_only["forecast_mean"]
        )
    assert "demand_features" in ml_core.forecast_model(raw, data, "p1", target, cutoff)
