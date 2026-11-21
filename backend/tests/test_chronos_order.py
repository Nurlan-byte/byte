from datetime import date, timedelta

import numpy as np
import pytest

from backend import chronos_order as engine


START = date(2025, 1, 1)


def day(n):
    return START + timedelta(days=n)


def history(values):
    return {"target": "sold_units", "unit": "physical_unit",
            "rows": [{"product_id": sku, "date": day(i).isoformat(), "sold": v,
                      "stockout_status": "unknown"}
                     for sku, series in values.items() for i, v in enumerate(series) if v is not None]}


def constant(monkeypatch, value):
    monkeypatch.setattr(engine, "_predict", lambda inputs, horizon: [float(value)] * len(inputs))


def test_expected_order_volume_uses_all_quantiles_nonnegative_with_flat_tails():
    levels = np.array([0.1, 0.5, 0.9])
    assert engine._expected_units(levels, [10, -3, 0]) == pytest.approx(3)
    with pytest.raises(ValueError):
        engine._expected_units(levels, [0, np.nan, 4])


def test_only_sparse_with_better_paired_mae_can_switch(monkeypatch):
    constant(monkeypatch, 10)
    ds = history({"rare": [0] * 40, "regular": [2] * 40,
                  "half": [0, 1] * 20, "short": [0] * 10, "unknown": []})
    result = engine.forecast_order(ds, day(41), day(39), ["rare", "regular", "half", "short", "unknown"])
    assert result["rare"]["method"] == "B1" and result["rare"]["forecast_mean"] == 0
    assert result["rare"]["selection"] == {
        "observed_days": 40, "zero_days": 40, "validation_pairs": 14,
        "b1_mae": 0, "chronos_mae": 10,
    }
    for product in ("regular", "half", "short"):
        assert result[product]["method"] == "Chronos-2"
        assert result[product]["selection"]["validation_pairs"] == 0
    assert result["unknown"]["forecast_mean"] is None
    constant(monkeypatch, 0)
    tied = engine.forecast_order(ds, day(41), day(39), ["rare"])["rare"]
    assert tied["method"] == "Chronos-2"
    assert tied["selection"]["b1_mae"] == tied["selection"]["chronos_mae"]


def test_lead_time_and_causal_validation_ignore_future_and_closed_observations(monkeypatch):
    seen = []

    def predict(inputs, horizon):
        assert horizon == 3
        seen.extend([entry["target"].copy() for entry in inputs])
        return [7.] * len(inputs)

    monkeypatch.setattr(engine, "_predict", predict)
    ds = history({"sku": [0] * 45 + [500, 900]})
    ds["rows"][5]["is_open"] = False
    before = engine.forecast_order(ds, day(47), day(44), ["sku"])
    assert len(seen) == 15  # the live prediction and 14 paired historical targets
    assert len(seen[0]) == 45 and len(seen[1]) == 42  # same 3-day lead in holdout
    assert np.isnan(seen[0][5]) and before["sku"]["selection"]["observed_days"] == 44
    assert all(np.nanmax(v) == 0 for v in seen)
    ds["rows"][-2]["sold"] = 700000
    ds["rows"][5]["sold"] = 300000
    assert before == engine.forecast_order(ds, day(47), day(44), ["sku"])


def test_cold_start_and_missing_sales_days_do_not_require_a_weekday_match(monkeypatch):
    ds = history({"x": [5, None, 7]})
    def predict(inputs, horizon):
        assert len(inputs) == 1 and horizon == 1
        assert np.array_equal(inputs[0]["target"], [5, np.nan, 7], equal_nan=True)
        return [4.]
    monkeypatch.setattr(engine, "_predict", predict)
    result = engine.forecast_order(ds, day(3), day(2), ["x", "fresh"])
    assert (result["x"]["method"], result["x"]["n"], result["x"]["eligible_weekday_n"]) == ("Chronos-2", 2, 0)
    assert result["fresh"]["method"] == "manual"
    assert engine.forecast_order(ds, day(3), day(2), {}) == {}


def test_context_uses_only_vintages_known_at_issue(monkeypatch):
    ds = history({"x": [1, 2, 3, 4, 5]})
    def vintage(day_id, issue, temperature):
        return {"date": day(day_id).isoformat(), "available_on": day(issue).isoformat(),
                "temperature_c": float(temperature), "kind": "forecast"}
    ds["demand_context"] = {"weather": [vintage(3, 1, 10), vintage(3, 2, 20),
                                        vintage(6, 4, 8), vintage(6, 5, 30)]}
    def predict(inputs, horizon):
        row = inputs[0]
        assert horizon == 2
        assert row["past_covariates"]["forecast_temperature_c"][3] == 10
        assert row["future_covariates"]["forecast_temperature_c"][-1] == 8
        return [8.]
    monkeypatch.setattr(engine, "_predict", predict)
    assert engine.forecast_order(ds, day(6), day(4), ["x"])["x"]["forecast_mean"] == 8


def test_receipt_count_forecast_and_provider_failure(monkeypatch):
    ds = {"target": "item_line_count", "unit": "item_line",
          "rows": [{"date": day(0).isoformat(), "product_id": "s", "item_line_count": 5}]}
    def broken():
        raise OSError("missing weights")
    monkeypatch.setattr(engine, "_pipeline", broken)
    monkeypatch.setattr(engine, "_POINT_CACHE", engine.OrderedDict())
    with pytest.raises(engine.ChronosUnavailable):
        engine.forecast_order(ds, day(2), day(0), ["s"])
    # Missing observations do not touch the provider even when it is unavailable.
    assert engine.forecast_order(ds, day(2), day(0), ["new"])["new"]["method"] == "manual"


def test_observation_far_outside_checkpoint_context_is_never_sent_as_zero(monkeypatch):
    ds = history({"x": [0], "stale": [1]})
    ds["rows"].append({"product_id": "x", "date": day(9000).isoformat(), "sold": 0})
    def predict(inputs, horizon):
        assert len(inputs) == 1 and horizon == 2
        values = inputs[0]["target"]
        assert len(values) == engine.CONTEXT_DAYS and np.isnan(values[:-1]).all() and values[-1] == 0
        return [0.]
    monkeypatch.setattr(engine, "_predict", predict)
    forecast = engine.forecast_order(ds, day(9002), day(9000), ["x", "stale"])
    assert forecast["stale"]["method"] == "manual"
    assert forecast["x"]["method"] == "Chronos-2"
    assert forecast["x"]["selection"]["validation_pairs"] == 0


def test_cache_reuses_only_identical_inputs_and_horizon(monkeypatch):
    class Pipeline:
        quantiles = [0.1, 0.5, 0.9]
        calls = []
        def predict_quantiles(self, inputs, *, prediction_length, cross_learning, **kwargs):
            assert cross_learning is False
            self.calls.append((len(inputs), prediction_length))
            return [np.ones((1, prediction_length, 3)) for _ in inputs], None
    model = Pipeline()
    monkeypatch.setattr(engine, "_pipeline", lambda: model)
    monkeypatch.setattr(engine, "_POINT_CACHE", engine.OrderedDict())
    row = {"target": np.array([2., 4.], dtype=np.float32)}
    engine._predict([row, row], 2)
    engine._predict([row], 2)
    engine._predict([row], 3)
    engine._predict([{**row, "future_covariates": {"temp": np.array([28., 25., 22.])}}], 3)
    row["target"][0] = 0
    engine._predict([row], 3)
    assert model.calls == [(1, 2), (1, 3), (1, 3), (1, 3)]
