"""Named offline ML corrections around unchanged B1; never operational quantities."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
import math
from backend.forecasting import baseline

FEATURES = (
    "item",
    "weekday",
    "lag1",
    "lag7",
    "lag14",
    "lag21",
    "lag28",
    "b1",
    "mean7",
    "mean14",
    "trend7",
    "lag7_minus_b1",
    "coverage7",
)


def b1_value(observation, index, cutoff):
    result = baseline(
        (
            {
                "date": row.date,
                "product_id": row.item,
                "item_line_count": row.item_line_count,
            }
            for row in index.values()
        ),
        product_id=observation.item,
        target_date=observation.date,
        cutoff=cutoff,
        method="B1",
        eligible=lambda _: True,
        value_key="item_line_count",
    )
    return result["forecast_mean"]


def enriched_features(observation, index, cutoff):
    def value(day):
        row = (
            index.get((observation.item, day))
            if day <= cutoff and day < observation.date
            else None
        )
        return float(row.item_line_count) if row is not None else float("nan")

    def trailing(start, stop):
        values = [
            value(observation.date - timedelta(days=lag)) for lag in range(start, stop)
        ]
        available = [v for v in values if math.isfinite(v)]
        return (
            sum(available) / len(available) if len(available) >= 3 else float("nan"),
            len(available),
        )

    lags = [value(observation.date - timedelta(days=lag)) for lag in (1, 7, 14, 21, 28)]
    b1 = b1_value(observation, index, cutoff)
    mean7, count7 = trailing(1, 8)
    mean14, _ = trailing(1, 15)
    previous7, _ = trailing(8, 15)
    b1 = float(b1) if b1 is not None else float("nan")
    return [
        observation.item,
        observation.date.weekday(),
        *lags,
        b1,
        mean7,
        mean14,
        mean7 - previous7,
        lags[1] - b1,
        count7 / 7,
    ]


@dataclass
class FittedCandidate:
    pipeline: object
    spec: dict
    metadata: dict


def fit_candidate(spec, observations, fit_end: date, cohort: list[str]):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.pipeline import Pipeline
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import HistGradientBoostingRegressor

    training = sorted(
        (o for o in observations if o.date <= fit_end and o.item in cohort),
        key=lambda o: (o.date, o.item),
    )
    index = {(o.item, o.date): o for o in training}
    fit_rows = []
    corrections = []
    counts = Counter()
    for observation in training:
        past_b1 = b1_value(observation, index, observation.date - timedelta(days=1))
        if past_b1 is not None and math.isfinite(past_b1):
            fit_rows.append(
                enriched_features(
                    observation, index, observation.date - timedelta(days=1)
                )
            )
            corrections.append(observation.item_line_count - past_b1)
            counts[observation.item] += 1
    if any(counts[item] < 14 for item in cohort):
        raise ValueError(
            "Need at least14 past-B1-available training rows per fixed item"
        )
    numeric = Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median", add_indicator=True, keep_empty_features=True
                ),
            ),
            ("scale", StandardScaler()),
        ]
    )
    preprocess = ColumnTransformer(
        [
            (
                "category",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                [0, 1],
            ),
            ("numeric", numeric, list(range(2, len(FEATURES)))),
        ]
    )
    if spec["family"] == "Ridge residual around unchanged B1":
        estimator = Ridge(alpha=spec["alpha"])
    elif spec["family"] == "HistGradientBoosting residual around unchanged B1":
        parameters = {
            key: spec[key]
            for key in (
                "loss",
                "max_iter",
                "learning_rate",
                "max_depth",
                "max_leaf_nodes",
                "min_samples_leaf",
                "l2_regularization",
                "early_stopping",
                "random_state",
            )
        }
        estimator = HistGradientBoostingRegressor(**parameters)
    else:
        raise ValueError("Unsupported frozen candidate family")
    pipeline = Pipeline([("preprocess", preprocess), ("model", estimator)])
    pipeline.fit(fit_rows, corrections)
    metadata = {
        "id": spec["id"],
        "fit_end": fit_end.isoformat(),
        "fit_rows_n": len(fit_rows),
        "fit_rows_per_item": dict(sorted(counts.items())),
        "transform_fit_scope": "strictly train prefix only",
        "features": list(FEATURES),
        "target": "item_line_count minus unchanged past-only B1",
        "output": "B1 plus learned residual, clipped nonnegative; flagged B1 fallback",
    }
    return FittedCandidate(pipeline, dict(spec), metadata)


def predict_candidate(fitted, observation, index, cutoff):
    past_b1 = b1_value(observation, index, cutoff)
    if past_b1 is None or not math.isfinite(past_b1):
        return {
            "value": None,
            "raw_finite": False,
            "used_fallback": False,
            "reason": "B1 unavailable",
            "b1": past_b1,
        }
    try:
        if fitted is None:
            raise ValueError("candidate fit unavailable")
        residual = float(
            fitted.pipeline.predict([enriched_features(observation, index, cutoff)])[0]
        )
        estimate = past_b1 + residual
        if not math.isfinite(estimate):
            raise ValueError("nonfinite candidate prediction")
        return {
            "value": max(0.0, estimate),
            "raw_finite": True,
            "used_fallback": False,
            "reason": None,
            "b1": past_b1,
        }
    except (ValueError, TypeError, OverflowError, ArithmeticError) as error:
        return {
            "value": past_b1,
            "raw_finite": False,
            "used_fallback": True,
            "reason": str(error),
            "b1": past_b1,
        }
