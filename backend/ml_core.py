"""Bounded experimental B1 + Ridge residuals, with numeric JSON artifacts only.

This is a new local deployment protocol, not the earlier offline final test.
The estimator, alpha, features and chronological split are fixed in advance.
Missing calendar dates are missing observations, never zero-sales labels.
"""

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date

import numpy as np

PROTOCOL_VERSION = "residual-ridge-100-v1-experimental"
ALPHA = 100.0
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
    "lag_1",
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
    """Only calendar facts and observations strictly before the target are read."""
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
        *[lag(delta) for delta in (1, 7, 14, 28)],
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


def _fit(samples):
    if any(not _safe_features(sample["features"]) for sample in samples):
        raise ValueError("Features exceed the ML bound")
    raw = np.asarray([sample["features"] for sample in samples], dtype=float)
    missing = np.isnan(raw)
    # B1 is observed for every feature row. Its train-only median is also a
    # safe placeholder for an entirely unobserved lag; missingness stays explicit.
    fallback_median = float(np.median(raw[:, 0]))
    medians = np.asarray(
        [
            np.median(column[~np.isnan(column)])
            if np.any(~np.isnan(column))
            else fallback_median
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
    residuals = np.asarray(
        [sample["value"] - sample["b1"] for sample in samples], dtype=float
    )
    intercept = float(residuals.mean())
    coefficients = np.linalg.solve(
        transformed.T @ transformed + ALPHA * np.eye(transformed.shape[1]),
        transformed.T @ (residuals - intercept),
    )
    params = {
        "alpha": ALPHA,
        "feature_names": list(FEATURE_NAMES),
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
    if params.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("Invalid feature schema")
    for key, size in (
        ("imputation_medians", len(FEATURE_NAMES)),
        ("means", 2 * len(FEATURE_NAMES)),
        ("scales", 2 * len(FEATURE_NAMES)),
        ("coefficients", 2 * len(FEATURE_NAMES)),
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
    missing = np.isnan(raw)
    filled = np.where(missing, params["imputation_medians"], raw)
    expanded = np.concatenate([filled, missing.astype(float)])
    transformed = (expanded - np.asarray(params["means"])) / np.asarray(
        params["scales"]
    )
    result = (
        b1
        + params["intercept"]
        + float(transformed @ np.asarray(params["coefficients"]))
    )
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


def train_model(dataset: dict, cutoff: date) -> dict:
    """Fit an immutable, deterministic JSON object for an explicit as-of snapshot.

    Last max(7, ceil(20%)) distinct dates are the common holdout. Frozen prefix
    estimators use rolling observed history during holdout; deployment estimators
    are then refitted through cutoff. No test-based parameter/model selection.
    """
    cutoff = _day(cutoff)
    identity, records, snapshot_hash = _snapshot(dataset, cutoff)
    dates = sorted({row["date"] for row in records})
    holdout_n = max(MIN_HOLDOUT_DATES, math.ceil(len(dates) * 0.2))
    split_ok = len(dates) - holdout_n >= MIN_TRAIN_DATES
    train_dates, holdout_dates = (
        (dates[:-holdout_n], dates[-holdout_n:]) if split_ok else ([], [])
    )
    train_end = train_dates[-1] if train_dates else None
    holdout_start = holdout_dates[0] if holdout_dates else None
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
            histories[product], day, date.fromordinal(max(1, day.toordinal() - 1))
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
    products, all_pairs = {}, []
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
            "evaluation": {**_score([]), "coverage": 0.0},
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
        try:
            frozen = _fit(prefix)
            pairs = [
                (
                    sample["value"],
                    sample["b1"],
                    _predict(frozen, sample["features"], sample["b1"]),
                )
                for sample in later
            ]
            deployment = _fit(samples[product])
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
                "deployment_fit_end": samples[product][-1]["date"],
                "evaluation": {
                    **_score(pairs),
                    "coverage": len(pairs) / product_holdout_n,
                },
            }
        )
        result["counts"]["deployment_training_rows"] = len(samples[product])
        all_pairs.extend(pairs)
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
        "experimental": True,
        "selection_performed": False,
        "fit_end": train_end,
        "holdout_start": holdout_start,
        "holdout_end": holdout_dates[-1] if holdout_dates else None,
        "train_dates": len(train_dates),
        "holdout_dates": len(holdout_dates),
        "holdout_eligible_n": holdout_eligible_n,
        "holdout_b1_n": sum(
            product["counts"]["holdout_b1_rows"] for product in products.values()
        ),
        **_score(all_pairs),
        "coverage": len(all_pairs) / holdout_eligible_n if holdout_eligible_n else 0.0,
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
            "ridge_alpha": ALPHA,
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
    }
    if model.get("id") is not None:
        result["model_id"] = model["id"]

    def fallback(reason):
        result["fallback_reason"] = reason
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
