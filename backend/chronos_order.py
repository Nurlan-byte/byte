"""Operational order forecast: Chronos-2 expected volume with a paired sparse-item B1 check."""

from collections import defaultdict, OrderedDict
from datetime import date
from functools import lru_cache
import itertools
import hashlib
import math
from threading import Lock

import numpy as np

from backend.chronos_assets import MODEL_REVISION, model_directory, verify_model
from backend.demand_context import CONTEXT_FEATURE_NAMES, ContextIndex
from backend.forecasting import baseline

PROTOCOL_VERSION = f"chronos-2-{MODEL_REVISION[:12]}-expected-volume-sparse-b1-v1"
MAX_HORIZON = 1024
CONTEXT_DAYS = 8192  # Official checkpoint's maximum input length.
SPARSE_WINDOW = 56
VALIDATION_WINDOW = 28
MAX_PAIRS = 14
MIN_PAIRS = 7
_INFERENCE_LOCK = Lock()
# Small, process-local cache of scalar predictions (no raw sales or keys retained).
_POINT_CACHE = OrderedDict()


class ChronosUnavailable(RuntimeError):
    """A ready order must not be issued using a substitute for unavailable inference."""


@lru_cache(maxsize=1)
def _pipeline():
    import torch
    from chronos import Chronos2Pipeline

    path = model_directory()
    verify_model(path)
    torch.set_num_threads(min(torch.get_num_threads(), 3))
    return Chronos2Pipeline.from_pretrained(
        str(path), device_map="cpu", dtype=torch.float32, local_files_only=True
    )


def _expected_units(levels, values):
    # Flat tails below/above the extreme quantiles; monotone rearrangement handles crossings.
    values = np.asarray(values, dtype=np.float64)
    if values.shape != levels.shape or not np.isfinite(values).all():
        raise ValueError("Invalid order forecast quantiles")
    values = np.sort(np.maximum(values, 0.0))
    return float(np.trapezoid(np.r_[values[0], values, values[-1]], np.r_[0., levels, 1.]))


def _input_key(entry, horizon):
    # Reuse overlapping validation windows after a daily refresh; a changed sale
    # or weather vintage changes the digest for every affected forecast.
    digest = hashlib.sha256(horizon.to_bytes(2, "big"))
    sections = (("target", {"sales": entry["target"]}),
                ("past", entry.get("past_covariates", {})), ("future", entry.get("future_covariates", {})))
    for section, columns in sections:
        for name, values in columns.items():
            digest.update(f"{section}/{name}/{values.size}/".encode())
            digest.update(values.tobytes())
    return digest.digest()


def _predict(inputs, horizon):
    # Batch independent series. Never share information between products or validation dates.
    keys = [_input_key(entry, horizon) for entry in inputs]
    try:
        with _INFERENCE_LOCK:
            groups = defaultdict(dict)
            for key, entry in zip(keys, inputs):
                if key in _POINT_CACHE:
                    _POINT_CACHE.move_to_end(key)
                    continue
                schema = tuple(entry.get("past_covariates", {})), tuple(entry.get("future_covariates", {}))
                groups[schema][key] = entry
            for group in groups.values():
                model = _pipeline()
                levels = np.asarray(model.quantiles, dtype=np.float64)
                quantiles, _ = model.predict_quantiles(
                    list(group.values()), prediction_length=horizon,
                    quantile_levels=levels.tolist(), batch_size=32, cross_learning=False,
                )
                for key, predicted in zip(group, quantiles, strict=True):
                    _POINT_CACHE[key] = _expected_units(levels, predicted[0, horizon - 1, :].tolist())
            output = [_POINT_CACHE[key] for key in keys]
            while len(_POINT_CACHE) > 4096:
                _POINT_CACHE.popitem(last=False)
    except (ImportError, OSError, RuntimeError, ValueError, TypeError, IndexError, KeyError) as exc:
        raise ChronosUnavailable("Не удалось выполнить прогноз Chronos-2") from exc
    return output


def _known_sales(dataset, cutoff, products):
    target = dataset.get("target", "sold_units")
    targets = {"sold_units": ("sold", "physical_unit"), "item_line_count": ("item_line_count", "item_line")}
    if target not in targets:
        raise ValueError("Unsupported order forecast target")
    field, unit = targets[target]
    if dataset.get("unit", unit) != unit:
        raise ValueError("Forecast target and unit do not match")
    history = {p: {} for p in products}
    for row in dataset["rows"]:
        product = row["product_id"]
        if product not in history or row.get("is_open") is False:
            continue
        day = date.fromisoformat(row["date"]).toordinal()
        if day > cutoff:
            continue
        # Procurement and POS forecast recorded sales even if stockout status is unknown.
        value = row.get(field)
        if type(value) is int and value >= 0:
            history[product][day] = value
    return history


def _b1(history, product, target, origin):
    # Reuse BakePlan's weekday policy. Index only the eight possible candidate dates.
    rows = [
        {"product_id": product, "date": date.fromordinal(d), "sold": history[d]}
        for d in range(target - 7, max(0, target - 57), -7)
        if d <= origin and d in history
    ]
    return baseline(rows, product_id=product, target_date=date.fromordinal(target),
                    cutoff=date.fromordinal(origin), method="B1", eligible=lambda row: True)


def _make_input(history, first_sale, origin, horizon, features):
    start = max(first_sale, origin - CONTEXT_DAYS + 1)
    values = np.asarray([history.get(d, np.nan) for d in range(start, origin + 1)], dtype=np.float32)
    result = {"target": values}
    if features is None:
        return result
    # Training and holdout examples get the forecast vintage available at their own lead.
    past = np.array([features(d, max(1, d - horizon)) for d in range(start, origin + 1)], dtype=np.float32)
    future = np.array([features(d, origin) for d in range(origin + 1, origin + horizon + 1)], dtype=np.float32)
    result["past_covariates"] = {
        name: past[:, i] for i, name in enumerate(CONTEXT_FEATURE_NAMES)
        if np.isfinite(past[:, i]).any() or np.isfinite(future[:, i]).any()
    }
    result["future_covariates"] = {
        name: future[:, i] for i, name in enumerate(CONTEXT_FEATURE_NAMES)
        if np.isfinite(future[:, i]).any()
    }
    return result


def forecast_order(dataset, target_date, cutoff, product_ids):
    """Return one selected, unrounded forecast per product using only information at cutoff."""
    target, end = target_date.toordinal(), cutoff.toordinal()
    horizon = target - end
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"Горизонт прогноза должен быть от 1 до {MAX_HORIZON} дней")
    histories = _known_sales(dataset, end, product_ids)
    context = dataset.get("demand_context")
    features = None
    if context is not None:
        index = ContextIndex(context)

        @lru_cache(maxsize=32768)
        def features(day, asof):
            return index.features(date.fromordinal(day), date.fromordinal(asof))

    outputs, jobs = {}, []
    for product, history in histories.items():
        weekday = _b1(history, product, target, end)
        days = sorted(d for d in history if d > end - CONTEXT_DAYS)
        recent = [history[d] for d in days if d > end - SPARSE_WINDOW]
        zeros = recent.count(0)
        selection = dict(observed_days=len(recent), zero_days=zeros,
                         validation_pairs=0, b1_mae=None, chronos_mae=None)
        outputs[product] = dict(product_id=product, method="manual", forecast_mean=None,
                                b1_mean=weekday["forecast_mean"], reason="no_sales_history",
                                n=len(days), used_dates=[date.fromordinal(d).isoformat() for d in days[-4:]],
                                eligible_weekday_n=weekday["eligible_weekday_n"], selection=selection)
        if not days:
            continue
        jobs.append((product, end, None, None))
        if 2 * zeros <= len(recent) or weekday["forecast_mean"] is None:
            continue
        pairs = []
        for d in reversed(days):
            if d <= end - VALIDATION_WINDOW:
                break
            origin = d - horizon
            if origin < 1:
                break
            historical_b1 = _b1(history, product, d, origin)["forecast_mean"]
            if historical_b1 is not None:
                pairs.append((product, origin, history[d], historical_b1))
                if len(pairs) == MAX_PAIRS:
                    break
        if len(pairs) >= MIN_PAIRS:
            jobs.extend(pairs)

    errors = defaultdict(list)
    first_sale = {p: min(h) for p, h in histories.items() if h}
    for batch in itertools.batched(jobs, 12):
        inputs = [_make_input(histories[p], first_sale[p], origin, horizon, features)
                  for p, origin, _, _ in batch]
        predictions = _predict(inputs, horizon)
        for (product, _, actual, b1), forecast in zip(batch, predictions, strict=True):
            if actual is None:
                outputs[product].update(method="Chronos-2", forecast_mean=forecast, reason=None)
            else:
                errors[product].append((abs(actual - b1), abs(actual - forecast)))

    for product, paired in errors.items():
        result = outputs[product]
        b1_mae = math.fsum(pair[0] for pair in paired) / len(paired)
        chronos_mae = math.fsum(pair[1] for pair in paired) / len(paired)
        result["selection"].update(validation_pairs=len(paired), b1_mae=b1_mae, chronos_mae=chronos_mae)
        if b1_mae < chronos_mae:
            weekday = _b1(histories[product], product, target, end)
            result.update(method="B1", forecast_mean=weekday["forecast_mean"],
                          n=weekday["n"], used_dates=weekday["used_dates"], reason="b1_more_accurate_on_sparse_sales")
    return outputs
