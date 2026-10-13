"""Generated synthetic fixtures only; no bakery/customer source data."""

from copy import deepcopy
from datetime import date, timedelta
import json
import math

import pytest

from backend import ml_core as core
from backend.forecasting import baseline

START = date(2026, 1, 1)


def fixture(days=90, target="sold_units", products=("p1",)):
    rows = []
    for offset in range(days):
        day = START + timedelta(days=offset)
        for product_no, product in enumerate(products):
            # Nonconstant deterministic counts with both weekly pattern and drift.
            value = 25 + product_no * 10 + day.weekday() * 3 + offset // 9
            row = {"date": day.isoformat(), "product_id": product}
            if target == "sold_units":
                row.update(
                    sold=value,
                    is_open=True,
                    stockout_status="none",
                    opening=0,
                    closing=0,
                )
            else:
                row["item_line_count"] = value
            rows.append(row)
    return {
        "id": "dataset-a",
        "version": "v1",
        "bakery_id": "bakery-a",
        "target": target,
        "unit": core.TARGET_UNITS[target],
        "source": "synthetic",
        "provenance": "generated-in-test",
        "rows": rows,
    }


def train(data, days=90):
    return core.train_model(data, START + timedelta(days=days - 1))


def forecast(model, data, product="p1", offset=90, cutoff_offset=89):
    return core.forecast_model(
        model,
        data,
        product,
        START + timedelta(days=offset),
        START + timedelta(days=cutoff_offset),
    )


@pytest.mark.parametrize("target", list(core.TARGET_UNITS))
def test_safe_json_deterministic_artifact_and_prediction(target):
    data = fixture(target=target)
    model = train(data)
    assert model == train(deepcopy(data))
    assert model["status"] == "ready"
    assert model["protocol_version"] == core.PROTOCOL_VERSION
    encoded = json.dumps(model, allow_nan=False, sort_keys=True)
    decoded = json.loads(encoded)
    assert forecast(model, data) == forecast(decoded, data)
    result = forecast(decoded, data)
    assert result["method"] == "ML"
    assert result["fallback_reason"] is None
    assert 0 <= result["forecast_mean"] <= core.MAX_COUNT
    assert result["n"] == 4
    assert decoded["products"]["p1"]["parameters"]["alpha"] == 100
    assert "pickle" not in encoded


def test_rows_after_cutoff_and_future_products_do_not_affect_fit_or_prediction():
    data = fixture()
    original = train(data)
    changed = deepcopy(data)
    changed["rows"] += fixture(150)["rows"][90:]
    changed["rows"][-1]["sold"] = 900_000
    changed["rows"][-1]["product_id"] = "only-in-future"
    assert train(changed) == original
    assert forecast(original, data) == forecast(original, changed)


def test_features_ignore_target_truth_and_future_values():
    history = {START.toordinal() + offset: offset + 10 for offset in range(120)}
    target = START + timedelta(days=80)
    cutoff = target - timedelta(days=core.FORECAST_HORIZON_DAYS)
    original = core._features(history, target, cutoff)
    changed = dict(history)
    for key in changed:
        if key > cutoff.toordinal():
            changed[key] = 1_000_000
    assert core._features(changed, target, cutoff) == original
    changed[cutoff.toordinal()] += 5
    assert core._features(changed, target, cutoff) != original


def test_holdout_transform_and_fit_are_frozen_then_deployment_refits():
    data = fixture()
    first = train(data)
    holdout = first["evaluation"]["holdout_start"]
    changed = deepcopy(data)
    for row in changed["rows"]:
        if row["date"] >= holdout:
            row["sold"] += 300
    second = train(changed)
    p1, p2 = first["products"]["p1"], second["products"]["p1"]
    assert p1["evaluation_parameters_hash"] == p2["evaluation_parameters_hash"]
    assert p1["parameters_hash"] != p2["parameters_hash"]
    assert first["evaluation"]["fit_end"] < holdout
    assert p1["deployment_fit_end"] == first["training_cutoff"]
    assert (
        p1["counts"]["deployment_training_rows"]
        > p1["counts"]["evaluation_training_rows"]
    )
    assert first["evaluation"]["ml_mae"] != second["evaluation"]["ml_mae"]


def test_shared_date_split_and_paired_metrics_match_product_cohort():
    data = fixture(products=("p1", "p2"))
    # A sparse product has a different observation count, but the same fit_end.
    data["rows"] = [
        row
        for index, row in enumerate(data["rows"])
        if row["product_id"] == "p1" or index % 6 == 1
    ]
    model = train(data)
    ready = [
        product
        for product in model["products"].values()
        if product["status"] == "ready"
    ]
    assert len(ready) == 2
    assert len({product["evaluation_fit_end"] for product in ready}) == 1
    evaluation = model["evaluation"]
    n = sum(product["evaluation"]["paired_n"] for product in ready)
    assert evaluation["paired_n"] == n
    for metric in ("b1_mae", "ml_mae"):
        expected = (
            sum(
                product["evaluation"][metric] * product["evaluation"]["paired_n"]
                for product in ready
            )
            / n
        )
        assert evaluation[metric] == pytest.approx(expected)
    assert evaluation["coverage"] == n / evaluation["holdout_eligible_n"]
    assert evaluation["experimental"] is True
    assert evaluation["selection_performed"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", "other-dataset"),
        ("version", "other-version"),
        ("bakery_id", "other-bakery"),
    ],
)
def test_identity_mismatch_is_rejected(key, value):
    data = fixture()
    model = train(data)
    changed = deepcopy(data)
    changed[key] = value
    with pytest.raises(ValueError, match="identity mismatch"):
        forecast(model, changed)


def test_cross_target_or_unit_use_rejected():
    model = train(fixture())
    with pytest.raises(ValueError, match="identity mismatch"):
        forecast(model, fixture(target="item_line_count"))
    data = fixture()
    data["unit"] = "item_line"
    with pytest.raises(ValueError, match="target/unit"):
        train(data)
    del data["target"]
    with pytest.raises(ValueError, match="target/unit"):
        train(data)


def test_future_model_and_invalid_temporal_order_are_rejected():
    data = fixture()
    model = train(data)
    with pytest.raises(ValueError, match="training cutoff"):
        forecast(model, data, offset=89, cutoff_offset=88)
    with pytest.raises(ValueError, match="precede"):
        forecast(model, data, offset=89)


def test_insufficient_history_falls_back_to_unchanged_b1():
    data = fixture(34)
    model = train(data, 34)
    assert model["status"] == "not_ready"
    assert model["reason"] == "insufficient_history_dates"
    result = forecast(model, data, offset=34, cutoff_offset=33)
    assert result["method"] == "B1"
    assert result["fallback_reason"] == "model_not_ready"
    reference = baseline(
        data["rows"],
        product_id="p1",
        target_date=START + timedelta(days=34),
        cutoff=START + timedelta(days=33),
        method="B1",
    )
    assert result["forecast_mean"] == reference["forecast_mean"]
    assert result["used_dates"] == reference["used_dates"]


def test_minimum_history_boundary_and_no_history_manual_fallback():
    # The first holdout issue cannot train on the preceding day's label.
    # At 36 dates, ceil(20%) is eight and the embargo leaves only 27 fit dates.
    assert train(fixture(36), 36)["status"] == "not_ready"
    enough = fixture(37)
    assert train(enough, 37)["status"] == "ready"
    sparse = fixture(5)
    model = train(sparse, 5)
    result = forecast(model, sparse, offset=5, cutoff_offset=4)
    assert result["method"] == "manual"
    assert result["forecast_mean"] is None
    assert result["fallback_reason"] == "no_b1_history"
    empty = fixture(0)
    assert train(empty)["products"] == {}
    assert train(empty)["status"] == "not_ready"


def test_open_stockout_carryover_missing_labels_excluded_with_counts():
    data = fixture()
    data["rows"][0]["is_open"] = False
    data["rows"][1]["stockout_status"] = "unknown"
    data["rows"][2]["opening"] = 3
    data["rows"][3]["closing"] = 3
    del data["rows"][4]["opening"]
    del data["rows"][5]["sold"]
    data["rows"][6]["sold"] = 1.5
    model = train(data)
    product = model["products"]["p1"]
    assert product["counts"]["excluded_labels"] == 7
    assert product["counts"]["eligible_labels"] == 83
    assert product["exclusions"] == {
        "closed_or_unknown_open": 1,
        "stockout_or_unknown": 1,
        "carryover": 2,
        "unknown_carryover": 1,
        "missing_label": 1,
        "invalid_or_out_of_bounds_label": 1,
    }


def test_carryover_diagnostic_b1_preserves_existing_history_policy():
    data = fixture(34)
    for row in data["rows"]:
        row["opening"] = row["closing"] = 5
    model = train(data, 34)
    assert model["counts"]["eligible_labels"] == 0
    result = forecast(model, data, offset=34, cutoff_offset=33)
    reference = baseline(
        data["rows"],
        product_id="p1",
        target_date=START + timedelta(days=34),
        cutoff=START + timedelta(days=33),
        method="B1",
    )
    assert result["method"] == "B1"
    assert result["forecast_mean"] == reference["forecast_mean"]
    assert result["used_dates"] == reference["used_dates"]


def test_missing_sales_dates_not_invented_as_zero_labels():
    data = fixture(target="item_line_count")
    data["rows"] = [row for index, row in enumerate(data["rows"]) if index % 3 != 0]
    del data["rows"][0]["item_line_count"]
    data["rows"][1]["item_line_count"] = 0  # Explicitly observed zero is valid.
    model = train(data)
    assert model["counts"]["rows"] == 60
    assert model["counts"]["eligible_labels"] == 59
    assert model["products"]["p1"]["exclusions"] == {"missing_label": 1}
    assert model["products"]["p1"]["parameters"]["feature_names"] == list(
        core.FEATURE_NAMES
    )
    assert len(model["products"]["p1"]["parameters"]["means"]) == 2 * len(
        core.FEATURE_NAMES
    )


def test_individual_product_not_ready_and_missing_product_fallback():
    data = fixture(products=("p1", "p2"))
    data["rows"] = [
        row
        for row in data["rows"]
        if row["product_id"] == "p1" or row["date"] < "2026-03-10"
    ]
    model = train(data)
    assert model["status"] == "ready"
    assert model["products"]["p2"]["status"] == "not_ready"
    assert model["products"]["p2"]["reason"] == "no_holdout_pairs"
    assert forecast(model, data, "p2")["fallback_reason"] == "product_not_ready"
    del model["products"]["p1"]
    assert forecast(model, data)["fallback_reason"] == "product_not_trained"
    assert forecast(model, data, "unknown")["method"] == "manual"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda model: model["products"]["p1"]["parameters"]["coefficients"].__setitem__(
            0, math.nan
        ),
        lambda model: model["products"]["p1"]["parameters"]["scales"].__setitem__(0, 0),
        lambda model: model["products"]["p1"]["parameters"]["means"].pop(),
        lambda model: model["products"]["p1"]["parameters"].__setitem__(
            "intercept", math.inf
        ),
        lambda model: model["products"]["p1"]["parameters"]["coefficients"].__setitem__(
            0, "execute this"
        ),
        lambda model: model["products"]["p1"]["parameters"]["coefficients"].__setitem__(
            0, 42
        ),
        lambda model: model.__setitem__("products", []),
    ],
)
def test_corrupted_json_artifact_falls_back_safely(mutation):
    data = fixture()
    model = train(data)
    mutation(model)
    result = forecast(model, data)
    assert result["method"] == "B1"
    assert result["fallback_reason"] == "invalid_artifact"
    assert math.isfinite(result["forecast_mean"])


def test_nonfinite_prediction_falls_back(monkeypatch):
    data = fixture()
    model = train(data)
    monkeypatch.setattr(core, "_predict", lambda *args: math.nan)
    result = forecast(model, data)
    assert result["method"] == "B1"
    assert result["fallback_reason"] == "nonfinite_prediction"


def test_changed_same_version_training_snapshot_falls_back():
    data = fixture()
    model = train(data)
    data["rows"][0]["sold"] += 1
    result = forecast(model, data)
    assert result["method"] == "B1"
    assert result["fallback_reason"] == "snapshot_mismatch"


def test_bounds_and_duplicate_rows_are_rejected():
    too_many = fixture()
    too_many["rows"] *= 112
    with pytest.raises(ValueError, match="10000"):
        train(too_many)
    too_many_products = fixture(1, products=tuple(f"p{n}" for n in range(51)))
    with pytest.raises(ValueError, match="50 products"):
        train(too_many_products)
    duplicate = fixture()
    duplicate["rows"].append(duplicate["rows"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        train(duplicate)


def test_counts_and_predictions_bounded():
    data = fixture()
    data["rows"][0]["sold"] = core.MAX_COUNT + 1
    assert (
        train(data)["products"]["p1"]["exclusions"]["invalid_or_out_of_bounds_label"]
        == 1
    )
    params = train(fixture())["products"]["p1"]["parameters"]
    params["intercept"] = 1e10
    with pytest.raises(ValueError, match="Prediction exceeds"):
        core._predict(params, [1.0] * len(core.FEATURE_NAMES), 50)
    params["intercept"] = -1e10
    assert core._predict(params, [1.0] * len(core.FEATURE_NAMES), 50) == 0


def test_holdout_label_exclusions_are_visible_separately():
    data = fixture()
    data["rows"][-1]["stockout_status"] = "occurred"
    model = train(data)
    evaluation = model["evaluation"]
    assert evaluation["holdout_excluded_labels"] == 1
    assert evaluation["holdout_label_exclusions"] == {"stockout_or_unknown": 1}
    assert evaluation["holdout_rows"] == evaluation["holdout_eligible_n"] + 1
    assert evaluation["paired_n"] == evaluation["holdout_eligible_n"]


def test_fit_failure_is_explicit_not_ready(monkeypatch):
    def failed_fit(_samples):
        raise ValueError("synthetic fit failure")

    monkeypatch.setattr(core, "_fit", failed_fit)
    data = fixture()
    model = train(data)
    assert model["status"] == "not_ready"
    assert model["products"]["p1"]["reason"] == "estimator_failure"
    assert model["evaluation"]["paired_n"] == 0
    assert model["evaluation"]["ml_mae"] is None
    assert model["evaluation"]["exclusions"]["product_not_ready"] > 0
    assert forecast(model, data)["method"] == "B1"


def test_maximum_accepted_snapshot_uses_fixed_dimensions():
    data = fixture(200, products=tuple(f"p{n}" for n in range(50)))
    assert len(data["rows"]) == core.MAX_ROWS
    model = train(data, 200)
    assert model["counts"]["ready_products"] == core.MAX_PRODUCTS
    assert all(
        len(product["parameters"]["coefficients"]) == 22
        for product in model["products"].values()
    )
    assert len(json.dumps(model, allow_nan=False)) < 500_000


def test_entirely_missing_lag_has_train_only_imputation_and_indicator():
    data = fixture(150, target="item_line_count")
    data["rows"] = [row for index, row in enumerate(data["rows"]) if index % 3 == 0]
    model = train(data, 150)
    assert model["status"] == "ready"
    params = model["products"]["p1"]["parameters"]
    lag_two = core.FEATURE_NAMES.index("lag_2")
    assert params["imputation_medians"][lag_two] > 0
    assert params["means"][len(core.FEATURE_NAMES) + lag_two] == 1.0
    assert model["counts"]["eligible_labels"] == 50


@pytest.mark.parametrize("target", list(core.TARGET_UNITS))
@pytest.mark.parametrize("value", [1_000_001, 1_000_000_000])
def test_high_accepted_counts_preserve_diagnostic_b1(target, value):
    data = fixture(35, target=target)
    key = "sold" if target == "sold_units" else "item_line_count"
    for row in data["rows"]:
        row[key] = value
    model = train(data, 35)
    assert model["status"] == "not_ready"
    assert model["counts"]["eligible_labels"] == 0
    result = forecast(model, data, offset=35, cutoff_offset=34)
    assert result["method"] == "B1"
    assert result["forecast_mean"] == value
    assert result["b1_mean"] == value
    assert result["n"] == 4
    assert result["fallback_reason"] == "model_not_ready"


@pytest.mark.parametrize("target", list(core.TARGET_UNITS))
def test_ready_artifact_with_new_high_lag_falls_back_without_clipping(target):
    data = fixture(target=target)
    model = train(data)
    assert model["status"] == "ready"
    new_row = fixture(91, target=target)["rows"][-1]
    new_row["sold" if target == "sold_units" else "item_line_count"] = 1_000_000_000
    data["rows"].append(new_row)
    result = forecast(model, data, offset=91, cutoff_offset=90)
    reference_kwargs = (
        {"eligible": lambda _: True, "value_key": "item_line_count"}
        if target == "item_line_count"
        else {}
    )
    reference = baseline(
        data["rows"],
        product_id="p1",
        target_date=START + timedelta(days=91),
        cutoff=START + timedelta(days=90),
        method="B1",
        **reference_kwargs,
    )
    assert result["method"] == "B1"
    assert result["forecast_mean"] == reference["forecast_mean"]
    assert result["used_dates"] == reference["used_dates"]
    assert result["fallback_reason"] == "feature_out_of_bounds"


def test_out_of_bounds_training_features_have_explicit_exclusions():
    data = fixture()
    data["rows"][78]["sold"] = 1_000_000_000
    model = train(data)
    product = model["products"]["p1"]
    assert product["counts"]["feature_out_of_bounds"] > 0
    assert model["evaluation"]["exclusions"]["feature_out_of_bounds"] > 0
    assert model["evaluation"]["paired_n"] < model["evaluation"]["holdout_b1_n"]
    assert (
        sum(model["evaluation"]["exclusions"].values())
        + model["evaluation"]["paired_n"]
        == model["evaluation"]["holdout_eligible_n"]
    )


def test_finite_prediction_above_bound_is_explicit_b1_fallback():
    data = fixture()
    model = train(data)
    product = model["products"]["p1"]
    product["parameters"]["intercept"] = 1e10
    product["parameters_hash"] = core._digest(product["parameters"])
    result = forecast(model, data)
    assert result["method"] == "B1"
    assert result["fallback_reason"] == "prediction_out_of_bounds"
    assert result["forecast_mean"] == result["b1_mean"]


@pytest.mark.parametrize("target_name", list(core.TARGET_UNITS))
def test_default_horizon_ignores_all_facts_after_completed_day_cutoff(target_name):
    data = fixture(100, target=target_name)
    model = train(data, 90)
    original = forecast(model, data, offset=91, cutoff_offset=89)
    assert original["method"] == "ML"
    assert original["evaluation_applicability"]["applicable"] is True
    changed = deepcopy(data)
    label = "sold" if target_name == "sold_units" else "item_line_count"
    for row in changed["rows"]:
        if row["date"] > model["training_cutoff"]:
            row[label] = 999_999
            row.update(is_open=False, opening=99, stockout_status="occurred")
    changed["rows"][-1]["product_id"] = "future-product"
    assert train(changed, 90) == model
    assert forecast(model, changed, offset=91, cutoff_offset=89) == original


def test_training_holdout_and_default_forecast_features_have_identical_horizon(
    monkeypatch,
):
    observed = []
    original_features = core._features

    def record_features(history, target, cutoff):
        features, baseline = original_features(history, target, cutoff)
        observed.append((target, cutoff, features))
        return features, baseline

    monkeypatch.setattr(core, "_features", record_features)
    data = fixture()
    model = train(data)
    assert len(observed) == len(data["rows"])
    assert all((target - cutoff).days == 2 for target, cutoff, _ in observed)
    assert all(
        features[core.FEATURE_NAMES.index("lag_2")] == data["rows"][index - 2]["sold"]
        for index, (_, _, features) in enumerate(observed)
        if features is not None
    )
    forecast(model, data, offset=91, cutoff_offset=89)
    target, cutoff, _ = observed[-1]
    assert (target - cutoff).days == model["evaluation"]["horizon_days"] == 2


def test_frozen_fit_excludes_labels_unavailable_at_first_holdout_issue(monkeypatch):
    fits = []
    original_fit = core._fit

    def record_fit(samples):
        fits.append(deepcopy(samples))
        return original_fit(samples)

    monkeypatch.setattr(core, "_fit", record_fit)
    data = fixture()
    first = train(data)
    evaluation = first["evaluation"]
    first_cutoff = date.fromisoformat(evaluation["holdout_start"]) - timedelta(days=2)
    assert evaluation["first_holdout_cutoff"] == first_cutoff.isoformat()
    assert all(sample["date"] <= first_cutoff.isoformat() for sample in fits[0])
    assert evaluation["fit_end"] == fits[0][-1]["date"]
    embargo_day = (first_cutoff + timedelta(days=1)).isoformat()
    assert evaluation["embargoed_dates"] == [embargo_day]
    changed = deepcopy(data)
    for row in changed["rows"]:
        if row["date"] > first_cutoff.isoformat():
            row["sold"] += 500
    second = train(changed)
    assert (
        first["products"]["p1"]["evaluation_parameters_hash"]
        == second["products"]["p1"]["evaluation_parameters_hash"]
    )
    assert (
        first["products"]["p1"]["parameters_hash"]
        != second["products"]["p1"]["parameters_hash"]
    )


def test_paired_scores_recompute_on_identical_explicit_date_product_cohort():
    data = fixture(products=("p1", "p2"))
    # Missing rows and excluded observed labels are different denominator cases.
    data["rows"] = [
        row
        for row in data["rows"]
        if not (
            row["product_id"] == "p2" and row["date"] in {"2026-03-17", "2026-03-21"}
        )
    ]
    for row in data["rows"]:
        if row["product_id"] == "p2" and row["date"] == "2026-03-20":
            row["stockout_status"] = "unknown"
    model = train(data)
    evaluation = model["evaluation"]
    _, records, _ = core._snapshot(data, date.fromisoformat(model["training_cutoff"]))
    histories = core._history(records)
    all_pairs = []
    for product_id, product in model["products"].items():
        prefix, later = [], []
        eligible_n = 0
        for row in records:
            if row["product_id"] != product_id or row["value"] is None:
                continue
            day = date.fromisoformat(row["date"])
            features, baseline = core._features(
                histories[product_id], day, day - timedelta(days=2)
            )
            if row["date"] >= evaluation["holdout_start"]:
                eligible_n += 1
            if features is None or not core._safe_features(features):
                continue
            sample = {
                "date": row["date"],
                "features": features,
                "value": row["value"],
                "b1": baseline["b1_mean"],
            }
            if row["date"] <= evaluation["first_holdout_cutoff"]:
                prefix.append(sample)
            elif row["date"] >= evaluation["holdout_start"]:
                later.append(sample)
        frozen = core._fit(prefix)
        pairs = [
            (
                sample["value"],
                sample["b1"],
                core._predict(frozen, sample["features"], sample["b1"]),
            )
            for sample in later
        ]
        metrics = product["evaluation"]
        assert metrics["paired_dates"] == [sample["date"] for sample in later]
        assert metrics["paired_date_count"] == metrics["paired_n"] == len(pairs)
        assert metrics["paired_start"] == metrics["paired_dates"][0]
        assert metrics["paired_end"] == metrics["paired_dates"][-1]
        assert metrics["coverage_denominator"] == "eligible_observed_holdout_labels"
        assert metrics["coverage_denominator_n"] == eligible_n
        assert metrics["coverage"] == len(pairs) / eligible_n
        for key, value in core._score(pairs).items():
            assert metrics[key] == pytest.approx(value)
        all_pairs.extend(pairs)
    assert evaluation["paired_n"] == len(all_pairs)
    assert evaluation["coverage_denominator_n"] == evaluation["holdout_eligible_n"]
    assert evaluation["paired_dates"] == sorted(
        {
            day
            for product in model["products"].values()
            for day in product["evaluation"]["paired_dates"]
        }
    )
    assert evaluation["paired_date_count"] == len(evaluation["paired_dates"])
    for key, value in core._score(all_pairs).items():
        assert evaluation[key] == pytest.approx(value)


@pytest.mark.parametrize("gap", [1, 2, 3, 7])
def test_forecast_evaluation_applicability_is_explicit_for_arbitrary_horizons(gap):
    data = fixture()
    model = train(data)
    result = forecast(model, data, offset=89 + gap, cutoff_offset=89)
    assert result["method"] == "ML"
    assert result["evaluation_applicability"] == {
        "applicable": gap == 2,
        "reason": None if gap == 2 else "forecast_horizon_mismatch",
        "evaluated_horizon_days": 2,
        "requested_horizon_days": gap,
    }


def test_old_one_day_protocol_cannot_silently_use_new_feature_schema():
    data = fixture()
    model = train(data)
    model["protocol_version"] = "residual-ridge-100-v1-experimental"
    result = forecast(model, data, offset=91, cutoff_offset=89)
    assert result["method"] == "B1"
    assert result["fallback_reason"] == "unsupported_protocol"
    assert result["evaluation_applicability"]["applicable"] is False
    assert result["evaluation_applicability"]["reason"] == "unsupported_protocol"
