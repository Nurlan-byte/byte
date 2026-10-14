"""As-of guarantees of the integrated model on noisy generated data only."""

from copy import deepcopy
from datetime import date, timedelta
import random

import pytest

from backend import ml_core as core
from backend.planning import plan_item

START = date(2026, 1, 5)
WEEKLY = (1.0, 0.95, 0.95, 1.0, 1.15, 1.35, 0.8)


def noisy(seed, days=126, products=("p1", "p2", "p3")):
    """Noisy weekly demand with closures, gaps, stockouts, carryover, unknowns."""
    rng = random.Random(seed)
    rows = []
    for offset in range(days):
        day = START + timedelta(days=offset)
        closed = rng.random() < 0.04
        for number, product in enumerate(products):
            if not closed and rng.random() < 0.05:
                continue  # absent export row, never a zero label
            mean = (20 + 25 * number) * WEEKLY[day.weekday()]
            sold = max(0, round(rng.gauss(mean, mean * 0.25)))
            status = "none"
            draw = rng.random()
            if draw < 0.08:
                status = "occurred"
            elif draw < 0.11:
                status = "unknown"
            carry = 2 if rng.random() < 0.03 else 0
            rows.append(
                {
                    "date": day.isoformat(),
                    "product_id": product,
                    "is_open": not closed,
                    "opening": carry,
                    "produced": sold,
                    "sold": 0 if closed else sold,
                    "waste": 0,
                    "closing": carry,
                    "other_out": 0,
                    "stockout_status": status,
                }
            )
    return {
        "id": f"generated-{seed}",
        "version": "v1",
        "bakery_id": "bakery-generated",
        "target": "sold_units",
        "unit": "physical_unit",
        "source": "synthetic",
        "provenance": "generated-in-test",
        "rows": rows,
    }


def day(offset):
    return START + timedelta(days=offset)


@pytest.mark.parametrize("seed", range(3))
def test_rows_after_training_cutoff_cannot_change_the_model(seed):
    data = noisy(seed)
    model = core.train_model(data, day(90))
    changed = deepcopy(data)
    for row in changed["rows"]:
        if row["date"] > day(90).isoformat():
            row["sold"] = row["sold"] * 7 + 3
            row["stockout_status"] = "none"
    assert core.train_model(changed, day(90)) == model


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("horizon", [1, 2, 5])
def test_rows_after_forecast_cutoff_cannot_change_the_forecast(seed, horizon):
    data = noisy(seed)
    model = core.train_model(data, day(80))
    target, cutoff = day(100), day(100 - horizon)
    expected = core.forecast_model(model, data, "p2", target, cutoff)
    changed = deepcopy(data)
    for row in changed["rows"]:
        if row["date"] > cutoff.isoformat():
            row["sold"] += 50
    assert core.forecast_model(model, changed, "p2", target, cutoff) == expected


@pytest.mark.parametrize("seed", range(3))
def test_holdout_is_reproduced_by_a_model_trained_only_before_it(seed):
    data = noisy(seed)
    full = core.train_model(data, day(125))
    evaluation = full["evaluation"]
    early = core.train_model(
        data, date.fromisoformat(evaluation["first_holdout_cutoff"])
    )
    for product_id, product in full["products"].items():
        if product["status"] != "ready":
            continue
        assert (
            product["evaluation_parameters_hash"]
            == early["products"][product_id]["parameters_hash"]
        )
        ml_errors, b1_errors = [], []
        for row in data["rows"]:
            if (
                row["product_id"] != product_id
                or row["date"] < evaluation["holdout_start"]
            ):
                continue
            value, reason = core._label(row, "sold_units")
            if reason:
                continue
            target = date.fromisoformat(row["date"])
            result = core.forecast_model(
                early, data, product_id, target, target - timedelta(days=2)
            )
            if result["method"] != "ML":
                continue
            ml_errors.append(abs(value - result["forecast_mean"]))
            b1_errors.append(abs(value - result["b1_mean"]))
        metrics = product["evaluation"]
        assert len(ml_errors) == metrics["paired_n"]
        assert sum(ml_errors) / len(ml_errors) == pytest.approx(metrics["ml_mae"])
        assert sum(b1_errors) / len(b1_errors) == pytest.approx(metrics["b1_mae"])


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("horizon", [1, 2, 4])
def test_planning_and_model_use_the_same_b1(seed, horizon):
    data = noisy(seed)
    model = core.train_model(data, day(60))
    for offset in range(70, 126, 3):
        target, cutoff = day(offset), day(offset - horizon)
        for product_id in ("p1", "p2", "p3"):
            product = {"product_id": product_id, "batch_size": 6, "max_daily_qty": 500}
            planned = plan_item(data["rows"], product, target, cutoff, "B1", True)
            modelled = core.forecast_model(model, data, product_id, target, cutoff)
            if planned["n"]:
                assert planned["forecast_mean"] == pytest.approx(modelled["b1_mean"])
                assert planned["used_dates"] == modelled["used_dates"]


def test_row_order_does_not_change_the_model():
    data = noisy(7)
    shuffled = deepcopy(data)
    random.Random(7).shuffle(shuffled["rows"])
    assert core.train_model(data, day(110)) == core.train_model(shuffled, day(110))
