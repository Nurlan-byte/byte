"""Offline, untuned hypotheses against the current Chronos expected-volume estimator."""
import math

import numpy as np

from backend import chronos_order as engine


def previous_examples(values, horizon):
    """Recent observed labels and the length of their strictly as-of input prefix."""
    observed = np.flatnonzero(np.isfinite(values[-28:])) + max(len(values) - 28, 0)
    result = [(int(i + 1 - horizon), float(values[i])) for i in observed[-14:]
              if i >= horizon and np.isfinite(values[:i + 1 - horizon]).any()]
    return result if len(result) == 14 else []


def correct_bias(point, residuals):
    if len(residuals) != 14:
        return point
    early = math.fsum(residuals[:7]) / 7
    late = math.fsum(residuals[7:]) / 7
    if early * late <= 0:
        return point
    # Both weeks agree on the direction. Shrink the average correction by half
    # and cap it to avoid turning a short-lived outlier into a large order.
    limit = 0.25 * max(1.0, point)
    return max(0.0, point + max(-limit, min(limit, (early + late) / 4)))


class PairedPoints:
    """Run one verified checkpoint; cache by complete as-of inputs, never by labels or SKU."""

    def __init__(self):
        self.cache = {}

    def _ensure(self, entries, horizon):
        missing = {}
        for entry in entries:
            key = engine._input_key(entry, horizon)
            if key not in self.cache:
                missing[key] = entry
        if not missing:
            return
        model = engine._pipeline()
        levels = np.asarray(model.quantiles, dtype=np.float64)
        quantiles, _ = model.predict_quantiles(
            list(missing.values()), prediction_length=horizon, quantile_levels=levels.tolist(),
            cross_learning=False, batch_size=32,
        )
        for key, tensor in zip(missing, quantiles, strict=True):
            values = np.asarray(tensor[0, horizon - 1, :].tolist())
            self.cache[key] = (
                engine._expected_units(levels, values),
                engine._expected_units(levels, np.floor(values + 0.5)),
            )

    def predict(self, entries, horizon, candidate):
        # This numeric benchmark uses only recorded sales. The production
        # calendar/weather encoder is untouched and requires separate testing.
        if any(set(entry) != {"target"} for entry in entries):
            raise ValueError("This experiment supports only sales histories")
        self._ensure(entries, horizon)
        keys = [engine._input_key(entry, horizon) for entry in entries]
        if candidate == "baseline":
            return [self.cache[key][0] for key in keys]
        if candidate == "integer_quantiles":
            return [self.cache[key][1] for key in keys]
        if candidate != "causal_bias":
            raise ValueError("Unknown candidate")
        examples = [previous_examples(entry["target"], horizon) for entry in entries]
        previous = [[{"target": entry["target"][:length]} for length, _ in terms]
                    for entry, terms in zip(entries, examples)]
        self._ensure([entry for group in previous for entry in group], horizon)
        points = []
        for key, terms, group in zip(keys, examples, previous):
            residuals = [actual - self.cache[engine._input_key(entry, horizon)][0]
                         for (_, actual), entry in zip(terms, group)]
            points.append(correct_bias(self.cache[key][0], residuals))
        return points
