"""Bounded experimental B1 + pooled relative Ridge, with numeric JSON artifacts only.

Protocol v3: one Ridge estimator per dataset, shared by all products. Features
and target are expressed relative to the product's own B1 (difference divided
by max(B1, 1)), so products of different scale pool their evidence and the
estimator cannot memorise an absolute sales level. v2 learned absolute levels
and pulled forecasts back towards the training-period level after a demand
shift. The estimator, alpha, features, bounds and chronological split are fixed
in advance; they were chosen on generated data only, not on any bakery history.
Missing calendar dates are missing observations, never zero-sales labels.
"""

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date

import numpy as np

PROTOCOL_VERSION = "pooled-relative-ridge-200-v3-horizon-2-experimental"
FORECAST_HORIZON_DAYS = 2
ALPHA = 200.0
# Relative correction bounds: ML may move B1 by -80%..+200% of max(B1, 1).
MIN_RELATIVE_CORRECTION = -0.8
MAX_RELATIVE_CORRECTION = 2.0
# Older models still predict, but their holdout metrics are not presented as
# applicable: the estimator has not seen the most recent weeks.
MAX_MODEL_AGE_DAYS = 14
MAX_ROWS = 10_000
MAX_PRODUCTS = 50
MAX_COUNT = 1_000_000
MAX_DIAGNOSTIC_COUNT = 1_000_000_000
MIN_TRAIN_DATES = 28
MIN_TRAIN_ROWS = 14
MIN_HOLDOUT_DATES = 7
TARGET_UNITS = {"sold_units": "physical_unit", "item_line_count": "item_line"}
FEATURE_NAMES = (
    "b1",
    "lag_2",
    "lag_7",
    "lag_14",
    "lag_28",
    "mean_7",
    "mean_28",
    "observed_count_7",
    "observed_count_28",
    "weekday_sin",
    "weekday_cos",
)
# Estimator inputs derived from FEATURE_NAMES; level features become relative.
MODEL_FEATURE_NAMES = (
    "rel_lag_2",
    "rel_lag_7",
    "rel_lag_14",
    "rel_lag_28",
    "rel_mean_7",
    "rel_mean_28",
    "observed_count_7",
    "observed_count_28",
    "weekday_sin",
    "weekday_cos",
)
IDENTITY_KEYS = ("bakery_id", "dataset_id", "dataset_version", "target", "unit")


def _day(value):
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError("Expected an ISO date")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("Expected an ISO date")
    return result


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _identity(dataset):
    target = dataset.get("target")
    if target not in TARGET_UNITS or dataset.get("unit") != TARGET_UNITS[target]:
        raise ValueError("Explicit supported target/unit are required")
    identity = {
        "dataset_id": dataset.get("id"),
        "dataset_version": dataset.get("version"),
        "bakery_id": dataset.get("bakery_id"),
        "target": target,
        "unit": dataset["unit"],
    }
    if any(
        not isinstance(value, str) or not value.strip() for value in identity.values()
    ):
        raise ValueError("Explicit dataset id/version and bakery identity are required")
    return identity


def _label(row, target):
    """Return an observed label or an explicit exclusion reason."""
    if target == "sold_units":
        if row.get("is_open") is not True:
            return None, "closed_or_unknown_open"
        if row.get("stockout_status") != "none":
            return None, "stockout_or_unknown"
        if any(type(row.get(key)) is not int for key in ("opening", "closing")):
            return None, "unknown_carryover"
        if row["opening"] != 0 or row["closing"] != 0:
            return None, "carryover"
        value = row.get("sold")
    else:
        value = row.get("item_line_count")
    if value is None:
        return None, "missing_label"
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        return None, "invalid_or_out_of_bounds_label"
    return value, None


def _snapshot(dataset, cutoff):
    identity = _identity(dataset)
    rows = dataset.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError(f"Dataset must contain at most {MAX_ROWS} rows")
    records, seen, products = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Dataset rows must be objects")
        day = _day(row.get("date"))
        # Future row values/product existence cannot affect this snapshot or fit.
        if day > cutoff:
            continue
        product = row.get("product_id")
        if not isinstance(product, str) or not product or len(product) > 200:
            raise ValueError("A bounded product_id is required")
        key = day, product
        if key in seen:
            raise ValueError("Duplicate date/product row")
        seen.add(key)
        products.add(product)
        if len(products) > MAX_PRODUCTS:
            raise ValueError(f"Dataset must contain at most {MAX_PRODUCTS} products")
        value, reason = _label(row, identity["target"])
        # Preserve the existing diagnostic B1 history policy. Carryover excludes
        # a training/evaluation label, but does not erase an observed sale from B1.
        history_value = row.get(
            "sold" if identity["target"] == "sold_units" else "item_line_count"
        )
        if identity["target"] == "sold_units" and (
            row.get("is_open") is not True or row.get("stockout_status") != "none"
        ):
            history_value = None
        if (
            type(history_value) is not int
            or not 0 <= history_value <= MAX_DIAGNOSTIC_COUNT
        ):
            history_value = None
        records.append(
            {
                "date": day.isoformat(),
                "product_id": product,
                "value": value,
                "history_value": history_value,
                "exclusion": reason,
            }
        )
    records.sort(key=lambda record: (record["date"], record["product_id"]))
    snapshot_hash = _digest({"identity": identity, "rows": records})
    return identity, records, snapshot_hash


def _history(records):
    histories = defaultdict(dict)
    for row in records:
        if row["history_value"] is not None:
            histories[row["product_id"]][_day(row["date"]).toordinal()] = row[
                "history_value"
            ]
    return histories


def _features(history, target, cutoff):
    """Only facts through the inclusive cutoff, strictly before target, are read."""
    ordinal, end = target.toordinal(), min(cutoff.toordinal(), target.toordinal() - 1)
    selected = [
        ordinal - delta
        for delta in range(7, 57, 7)
        if 1 <= ordinal - delta <= end and ordinal - delta in history
    ][:4]
    b1 = sum(history[key] for key in selected) / len(selected) if selected else None
    baseline = {
        "b1_mean": b1,
        "used_dates": [date.fromordinal(key).isoformat() for key in selected],
        "n": len(selected),
    }
    if b1 is None:
        return None, baseline

    def lag(delta):
        return (
            history.get(ordinal - delta, math.nan)
            if ordinal - delta <= end
            else math.nan
        )

    windows = [
        [
            history[key]
            for key in range(max(1, ordinal - days), end + 1)
            if key in history
        ]
        for days in (7, 28)
    ]
    features = [
        b1,
        *[lag(delta) for delta in (2, 7, 14, 28)],
        *[sum(window) / len(window) if window else math.nan for window in windows],
        *[len(window) for window in windows],
        math.sin(2 * math.pi * target.weekday() / 7),
        math.cos(2 * math.pi * target.weekday() / 7),
    ]
    return features, baseline


def _safe_features(features):
    return len(features) == len(FEATURE_NAMES) and all(
        math.isnan(value) or (math.isfinite(value) and abs(value) <= MAX_COUNT)
        for value in features
    )


class _PredictionBoundsError(ValueError):
    """A finite estimator output exceeds the declared experimental bound."""


def _scale(b1):
    """Relative unit for one product-day; >= 1 so near-zero B1 stays bounded."""
    return max(float(b1), 1.0)


def _model_features(features):
    """Level-free estimator inputs: (feature - B1) / max(B1, 1) for levels."""
    b1 = features[0]
    scale = _scale(b1)
    relative = [
        math.nan if math.isnan(value) else (value - b1) / scale
        for value in features[1:7]
    ]
    return [*relative, *features[7:]]


def _relative_target(value, b1):
    return (value - b1) / _scale(b1)


def _fit(samples):
    if any(not _safe_features(sample["features"]) for sample in samples):
        raise ValueError("Features exceed the ML bound")
    raw = np.asarray(
        [_model_features(sample["features"]) for sample in samples], dtype=float
    )
    missing = np.isnan(raw)
    # An entirely unobserved relative feature is imputed as "same as B1" (0);
    # missingness stays explicit through the indicator columns.
    medians = np.asarray(
        [
            np.median(column[~np.isnan(column)]) if np.any(~np.isnan(column)) else 0.0
            for column in raw.T
        ]
    )
    # Numeric imputation never creates a label or an observed calendar date.
    filled = np.where(missing, medians, raw)
    expanded = np.concatenate([filled, missing.astype(float)], axis=1)
    means = expanded.mean(axis=0)
    scales = expanded.std(axis=0)
    scales[scales < 1e-12] = 1.0
    transformed = (expanded - means) / scales
    targets = np.asarray(
        [_relative_target(sample["value"], sample["b1"]) for sample in samples],
        dtype=float,
    )
    intercept = float(targets.mean())
    coefficients = np.linalg.solve(
        transformed.T @ transformed + ALPHA * np.eye(transformed.shape[1]),
        transformed.T @ (targets - intercept),
    )
    params = {
        "alpha": ALPHA,
        "feature_names": list(MODEL_FEATURE_NAMES),
        "imputation_medians": medians.tolist(),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "coefficients": coefficients.tolist(),
        "intercept": intercept,
    }
    _validate_params(params)
    return params


def _validate_params(params):
    if not isinstance(params, dict) or params.get("alpha") != ALPHA:
        raise ValueError("Invalid estimator")
    if set(params) != {
        "alpha",
        "feature_names",
        "imputation_medians",
        "means",
        "scales",
        "coefficients",
        "intercept",
    }:
        raise ValueError("Invalid estimator fields")
    if params.get("feature_names") != list(MODEL_FEATURE_NAMES):
        raise ValueError("Invalid feature schema")
    for key, size in (
        ("imputation_medians", len(MODEL_FEATURE_NAMES)),
        ("means", 2 * len(MODEL_FEATURE_NAMES)),
        ("scales", 2 * len(MODEL_FEATURE_NAMES)),
        ("coefficients", 2 * len(MODEL_FEATURE_NAMES)),
    ):
        values = params.get(key)
        if not isinstance(values, list) or len(values) != size:
            raise ValueError("Invalid estimator dimension")
        if any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or abs(value) > 1e12
            for value in values
        ):
            raise ValueError("Invalid numeric estimator")
    intercept = params.get("intercept")
    if (
        type(intercept) not in (int, float)
        or not math.isfinite(intercept)
        or abs(intercept) > 1e12
    ):
        raise ValueError("Invalid intercept")
    if any(value < 1e-12 for value in params["scales"]):
        raise ValueError("Invalid scale")


def _predict(params, features, b1):
    _validate_params(params)
    raw = np.asarray(features, dtype=float)
    if raw.shape != (len(FEATURE_NAMES),) or not _safe_features(raw):
        raise ValueError("Invalid prediction features")
    if type(b1) not in (int, float) or not math.isfinite(b1) or b1 != raw[0]:
        raise ValueError("B1 does not match the prediction features")
    model_raw = np.asarray(_model_features(raw.tolist()), dtype=float)
    missing = np.isnan(model_raw)
    filled = np.where(missing, params["imputation_medians"], model_raw)
    expanded = np.concatenate([filled, missing.astype(float)])
    transformed = (expanded - np.asarray(params["means"])) / np.asarray(
        params["scales"]
    )
    correction = params["intercept"] + float(
        transformed @ np.asarray(params["coefficients"])
    )
    if not math.isfinite(correction):
        raise ValueError("Nonfinite prediction")
    correction = min(max(correction, MIN_RELATIVE_CORRECTION), MAX_RELATIVE_CORRECTION)
    result = b1 + correction * _scale(b1)
    if not math.isfinite(result):
        raise ValueError("Nonfinite prediction")
    if result > MAX_COUNT:
        raise _PredictionBoundsError("Prediction exceeds the ML bound")
    return max(0.0, result)


def _score(pairs):
    n = len(pairs)
    return {
        "paired_n": n,
        "b1_mae": sum(abs(value - b1) for value, b1, _ in pairs) / n if n else None,
        "ml_mae": sum(abs(value - pred) for value, _, pred in pairs) / n if n else None,
    }


def _paired_evaluation(pairs, paired_dates, eligible_n):
    """Both errors share exactly the same observed date/product cohort."""
    dates = sorted(set(paired_dates))
    return {
        **_score(pairs),
        "paired_dates": dates,
        "paired_date_count": len(dates),
        "paired_start": dates[0] if dates else None,
        "paired_end": dates[-1] if dates else None,
        "coverage": len(pairs) / eligible_n if eligible_n else 0.0,
        "coverage_denominator": "eligible_observed_holdout_labels",
        "coverage_denominator_n": eligible_n,
    }


def _model_age_days(model, cutoff):
    """Days between the training cutoff and the forecast cutoff, or None."""
    try:
        trained = _day(model.get("training_cutoff"))
    except ValueError, TypeError:
        return None
    return (cutoff - trained).days


def evaluation_applicability(model, target, cutoff, product_id=None):
    """Describe horizon compatibility, never promise future forecast accuracy."""
    target, cutoff = _day(target), _day(cutoff)
    evaluation = model.get("evaluation", {}) if isinstance(model, dict) else {}
    evaluated_horizon = (
        evaluation.get("horizon_days") if isinstance(evaluation, dict) else None
    )
    if type(evaluated_horizon) is not int or evaluated_horizon < 1:
        evaluated_horizon = None
    requested_horizon = (target - cutoff).days
    reason = None
    if not isinstance(model, dict):
        reason = "no_model"
    elif model.get("protocol_version") != PROTOCOL_VERSION:
        reason = "unsupported_protocol"
    elif evaluated_horizon != FORECAST_HORIZON_DAYS:
        reason = "unknown_evaluation_horizon"
    elif requested_horizon != evaluated_horizon:
        reason = "forecast_horizon_mismatch"
    elif model.get("status") != "ready":
        reason = "model_not_ready"
    elif _model_age_days(model, cutoff) is None:
        reason = "invalid_training_cutoff"
    elif _model_age_days(model, cutoff) > MAX_MODEL_AGE_DAYS:
        reason = "model_too_old"
    elif type(evaluation.get("paired_n")) is not int or evaluation["paired_n"] < 1:
        reason = "no_paired_evaluation"
    elif product_id is not None:
        products = model.get("products")
        product = products.get(product_id) if isinstance(products, dict) else None
        if not isinstance(product, dict) or product.get("status") != "ready":
            reason = "product_not_ready"
        else:
            product_evaluation = product.get("evaluation")
            if (
                not isinstance(product_evaluation, dict)
                or type(product_evaluation.get("paired_n")) is not int
                or product_evaluation["paired_n"] < 1
            ):
                reason = "no_paired_evaluation"
    return {
        "applicable": reason is None,
        "reason": reason,
        "evaluated_horizon_days": evaluated_horizon,
        "requested_horizon_days": requested_horizon,
    }


def train_model(dataset: dict, cutoff: date) -> dict:
    """Fit an immutable, deterministic JSON object for an explicit as-of snapshot.

    Last max(7, ceil(20%)) distinct dates are the common holdout. Training and
    holdout features both use target minus two calendar days. Frozen estimators
    see labels only through the first holdout issue cutoff. Observations roll
    forward at that same two-day horizon; deployment refits through cutoff.
    No test-based parameter/model selection.
    """
    cutoff = _day(cutoff)
    identity, records, snapshot_hash = _snapshot(dataset, cutoff)
    dates = sorted({row["date"] for row in records})
    holdout_n = max(MIN_HOLDOUT_DATES, math.ceil(len(dates) * 0.2))
    candidate_holdout = dates[-holdout_n:]
    first_issue_cutoff = (
        date.fromordinal(
            max(1, _day(candidate_holdout[0]).toordinal() - FORECAST_HORIZON_DAYS)
        ).isoformat()
        if candidate_holdout
        else None
    )
    # Labels immediately before holdout may not exist at its first issue time.
    # Embargo those dates rather than fitting on unavailable future labels.
    candidate_train = [day for day in dates[:-holdout_n] if day <= first_issue_cutoff]
    split_ok = len(candidate_train) >= MIN_TRAIN_DATES
    train_dates, holdout_dates = (
        (candidate_train, candidate_holdout) if split_ok else ([], [])
    )
    train_end = train_dates[-1] if train_dates else None
    holdout_start = holdout_dates[0] if holdout_dates else None
    first_issue_cutoff = first_issue_cutoff if split_ok else None
    histories = _history(records)
    grouped, samples = defaultdict(list), defaultdict(list)
    feature_exclusions = defaultdict(Counter)
    holdout_feature_exclusions = defaultdict(Counter)
    for row in records:
        product = row["product_id"]
        grouped[product].append(row)
        if row["value"] is None:
            continue
        day = _day(row["date"])
        features, baseline = _features(
            histories[product],
            day,
            date.fromordinal(max(1, day.toordinal() - FORECAST_HORIZON_DAYS)),
        )
        feature_reason = (
            "missing_b1_history"
            if features is None
            else "feature_out_of_bounds"
            if not _safe_features(features)
            else None
        )
        if feature_reason:
            feature_exclusions[product][feature_reason] += 1
            if holdout_start and row["date"] >= holdout_start:
                holdout_feature_exclusions[product][feature_reason] += 1
        else:
            samples[product].append(
                {
                    "date": row["date"],
                    "features": features,
                    "value": row["value"],
                    "b1": baseline["b1_mean"],
                }
            )
    # Protocol v3: one estimator pooled across products. Relative features and
    # targets make products of different scale comparable. The frozen estimator
    # sees only labels through train_end; deployment refits through cutoff.
    pooled_prefix = [
        sample
        for product in sorted(samples)
        for sample in samples[product]
        if train_end and sample["date"] <= train_end
    ]
    pooled_all = [sample for product in sorted(samples) for sample in samples[product]]
    frozen = deployment = None
    pooled_reason = None
    if split_ok and len(pooled_prefix) >= MIN_TRAIN_ROWS:
        try:
            frozen = _fit(pooled_prefix)
            deployment = _fit(pooled_all)
        except ValueError, TypeError, ArithmeticError, np.linalg.LinAlgError:
            frozen = deployment = None
            pooled_reason = "estimator_failure"
    deployment_fit_end = max((sample["date"] for sample in pooled_all), default=None)
    products, all_pairs, all_paired_dates = {}, [], []
    holdout_eligible_n = (
        sum(
            row["value"] is not None and row["date"] >= holdout_start for row in records
        )
        if holdout_start
        else 0
    )
    for product, rows in sorted(grouped.items()):
        eligible_n = sum(row["value"] is not None for row in rows)
        exclusions = dict(
            sorted(
                Counter(row["exclusion"] for row in rows if row["exclusion"]).items()
            )
        )
        prefix = [
            sample
            for sample in samples[product]
            if train_end and sample["date"] <= train_end
        ]
        later = [
            sample
            for sample in samples[product]
            if holdout_start and sample["date"] >= holdout_start
        ]
        product_holdout_n = (
            sum(
                row["value"] is not None and row["date"] >= holdout_start
                for row in rows
            )
            if holdout_start
            else 0
        )
        result = {
            "status": "not_ready",
            "reason": "insufficient_history_dates",
            "counts": {
                "rows": len(rows),
                "eligible_labels": eligible_n,
                "excluded_labels": len(rows) - eligible_n,
                "feature_rows": len(samples[product]),
                "missing_b1_history": feature_exclusions[product]["missing_b1_history"],
                "feature_out_of_bounds": feature_exclusions[product][
                    "feature_out_of_bounds"
                ],
                "evaluation_training_rows": len(prefix),
                "holdout_eligible_rows": product_holdout_n,
                "holdout_b1_rows": product_holdout_n
                - holdout_feature_exclusions[product]["missing_b1_history"],
                "holdout_feature_out_of_bounds": holdout_feature_exclusions[product][
                    "feature_out_of_bounds"
                ],
                "deployment_training_rows": 0,
            },
            "exclusions": exclusions,
            "evaluation": _paired_evaluation([], [], product_holdout_n),
        }
        products[product] = result
        if not split_ok:
            continue
        if len(prefix) < MIN_TRAIN_ROWS:
            result["reason"] = "insufficient_training_feature_rows"
            continue
        if not later:
            result["reason"] = "no_holdout_pairs"
            continue
        if frozen is None or deployment is None:
            result["reason"] = pooled_reason or "insufficient_training_feature_rows"
            continue
        try:
            pairs = [
                (
                    sample["value"],
                    sample["b1"],
                    _predict(frozen, sample["features"], sample["b1"]),
                )
                for sample in later
            ]
        except ValueError, TypeError, ArithmeticError, np.linalg.LinAlgError:
            result["reason"] = "estimator_failure"
            continue
        result.update(
            {
                "status": "ready",
                "reason": None,
                "parameters": deployment,
                "parameters_hash": _digest(deployment),
                "evaluation_fit_end": train_end,
                "evaluation_parameters_hash": _digest(frozen),
                "deployment_fit_end": deployment_fit_end,
                "evaluation": _paired_evaluation(
                    pairs, [sample["date"] for sample in later], product_holdout_n
                ),
            }
        )
        result["counts"]["deployment_training_rows"] = len(samples[product])
        all_pairs.extend(pairs)
        all_paired_dates.extend(sample["date"] for sample in later)
    ready_n = sum(product["status"] == "ready" for product in products.values())
    holdout_rows = [
        row for row in records if holdout_start and row["date"] >= holdout_start
    ]
    evaluation = {
        "holdout_rows": len(holdout_rows),
        "holdout_excluded_labels": len(holdout_rows) - holdout_eligible_n,
        "holdout_label_exclusions": dict(
            sorted(
                Counter(
                    row["exclusion"] for row in holdout_rows if row["exclusion"]
                ).items()
            )
        ),
        "protocol": "chronological_holdout_frozen_estimator_rolling_observations",
        "horizon_days": FORECAST_HORIZON_DAYS,
        "feature_cutoff_policy": "target_minus_horizon_calendar_days",
        "first_holdout_cutoff": first_issue_cutoff,
        "embargoed_dates": (
            [day for day in dates if first_issue_cutoff < day < holdout_start]
            if split_ok
            else []
        ),
        "experimental": True,
        "selection_performed": False,
        "design_selected_on": "generated_synthetic_data_only",
        "estimator_scope": "pooled_all_products",
        "pooled_evaluation_training_rows": len(pooled_prefix) if frozen else 0,
        "pooled_deployment_training_rows": len(pooled_all) if deployment else 0,
        "fit_end": train_end,
        "holdout_start": holdout_start,
        "holdout_end": holdout_dates[-1] if holdout_dates else None,
        "train_dates": len(train_dates),
        "holdout_dates": len(holdout_dates),
        "holdout_eligible_n": holdout_eligible_n,
        "holdout_b1_n": sum(
            product["counts"]["holdout_b1_rows"] for product in products.values()
        ),
        **_paired_evaluation(all_pairs, all_paired_dates, holdout_eligible_n),
        "exclusions": {
            "missing_b1_history": sum(
                product["counts"]["holdout_eligible_rows"]
                - product["counts"]["holdout_b1_rows"]
                for product in products.values()
            ),
            "feature_out_of_bounds": sum(
                product["counts"]["holdout_feature_out_of_bounds"]
                for product in products.values()
            ),
            "product_not_ready": sum(
                product["counts"]["holdout_b1_rows"]
                - product["counts"]["holdout_feature_out_of_bounds"]
                for product in products.values()
                if product["status"] != "ready"
            ),
        },
        "per_product": {
            key: {
                "status": value["status"],
                "reason": value["reason"],
                **value["evaluation"],
                "counts": value["counts"],
                "exclusions": value["exclusions"],
            }
            for key, value in products.items()
        },
    }
    return {
        "protocol_version": PROTOCOL_VERSION,
        **identity,
        "training_cutoff": cutoff.isoformat(),
        "snapshot_hash": snapshot_hash,
        "status": "ready" if ready_n else "not_ready",
        "reason": None
        if ready_n
        else ("insufficient_history_dates" if not split_ok else "no_ready_products"),
        "products": products,
        "evaluation": evaluation,
        "counts": {
            "rows": len(records),
            "products": len(products),
            "ready_products": ready_n,
            "eligible_labels": sum(row["value"] is not None for row in records),
        },
        "bounds": {
            "max_rows": MAX_ROWS,
            "max_products": MAX_PRODUCTS,
            "max_label_and_prediction": MAX_COUNT,
            "max_diagnostic_count": MAX_DIAGNOSTIC_COUNT,
            "min_training_dates": MIN_TRAIN_DATES,
            "min_training_feature_rows": MIN_TRAIN_ROWS,
            "min_holdout_dates": MIN_HOLDOUT_DATES,
            "raw_feature_count": len(FEATURE_NAMES),
            "model_feature_count": len(MODEL_FEATURE_NAMES),
            "relative_correction_bounds": [
                MIN_RELATIVE_CORRECTION,
                MAX_RELATIVE_CORRECTION,
            ],
            "max_model_age_days_for_applicable_metrics": MAX_MODEL_AGE_DAYS,
            "ridge_alpha": ALPHA,
            "forecast_horizon_days": FORECAST_HORIZON_DAYS,
        },
    }


def forecast_model(
    model: dict, dataset: dict, product_id: str, target: date, cutoff: date
) -> dict:
    """Use a matching as-of artifact, or an explicit safe B1/manual fallback."""
    target, cutoff = _day(target), _day(cutoff)
    if cutoff >= target:
        raise ValueError("Forecast cutoff must precede target")
    identity, records, _ = _snapshot(dataset, cutoff)
    if not isinstance(model, dict) or any(
        model.get(key) != identity[key] for key in IDENTITY_KEYS
    ):
        raise ValueError("Model dataset/bakery/target/unit identity mismatch")
    try:
        trained = _day(model.get("training_cutoff"))
    except ValueError, TypeError:
        trained = None
    if trained is not None and trained > cutoff:
        raise ValueError("Model training cutoff is after forecast cutoff")
    features, baseline = _features(
        _history(records).get(product_id, {}), target, cutoff
    )
    result = {
        "forecast_mean": baseline["b1_mean"],
        **baseline,
        "method": "B1" if baseline["b1_mean"] is not None else "manual",
        "fallback_reason": None,
        "evaluation_applicability": evaluation_applicability(
            model, target, cutoff, product_id
        ),
    }
    if model.get("id") is not None:
        result["model_id"] = model["id"]

    def fallback(reason):
        result["fallback_reason"] = reason
        result["evaluation_applicability"].update(applicable=False, reason=reason)
        return result

    if features is None:
        return fallback("no_b1_history")
    if model.get("protocol_version") != PROTOCOL_VERSION:
        return fallback("unsupported_protocol")
    if trained is None:
        return fallback("invalid_training_cutoff")
    if model.get("status") != "ready":
        return fallback("model_not_ready")
    if not isinstance(model.get("products"), dict):
        return fallback("invalid_artifact")
    product = model["products"].get(product_id)
    if not isinstance(product, dict):
        return fallback("product_not_trained")
    if product.get("status") != "ready":
        return fallback("product_not_ready")
    _, _, actual_snapshot = _snapshot(dataset, trained)
    if model.get("snapshot_hash") != actual_snapshot:
        return fallback("snapshot_mismatch")
    if not _safe_features(features):
        return fallback("feature_out_of_bounds")
    try:
        params = product.get("parameters")
        _validate_params(params)
        if _digest(params) != product.get("parameters_hash"):
            return fallback("invalid_artifact")
        prediction = _predict(params, features, baseline["b1_mean"])
        if not math.isfinite(prediction):
            return fallback("nonfinite_prediction")
    except _PredictionBoundsError:
        return fallback("prediction_out_of_bounds")
    except ValueError, TypeError, ArithmeticError, KeyError:
        return fallback("invalid_artifact")
    result.update(forecast_mean=prediction, method="ML", fallback_reason=None)
    return result
