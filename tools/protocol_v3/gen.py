"""Synthetic bakery data with a KNOWN demand process (ground truth)."""

import math
from datetime import date, timedelta

import numpy as np

START = date(2026, 1, 5)  # Monday
WEEKLY = np.array([1.0, 0.95, 0.95, 1.0, 1.15, 1.35, 0.8])  # Mon..Sun


def demand_mean(scenario, base, day_idx, weekday, rng_state):
    m = base * WEEKLY[weekday]
    if scenario == "trend":
        m *= 1 + 0.015 * day_idx / 7  # +1.5% per week
    elif scenario == "level_shift":
        m *= 1.3 if day_idx >= rng_state["shift_at"] else 1.0
    elif scenario == "drop":
        m *= 0.7 if day_idx >= rng_state["shift_at"] else 1.0
    elif scenario == "slow_wave":
        m *= 1 + 0.2 * math.sin(2 * math.pi * day_idx / 63)
    return m


def make_dataset(
    scenario="stationary",
    seed=0,
    days=126,
    products=("p1", "p2", "p3", "p4", "p5"),
    bases=(30, 60, 90, 15, 45),
    dispersion=20.0,
    closed_prob=0.0,
    outlier_prob=0.0,
    stockout_mode=None,  # None | "exclude" (status occurred) | "hide" (status none, sold=min)
    stockout_slack=0.0,  # production = round(prior_mean*(1+slack))
    missing_prob=0.0,
):
    rng = np.random.default_rng(seed)
    state = {"shift_at": int(days * 0.7)}
    rows, truth = [], {}
    closed = {d for d in range(days) if rng.random() < closed_prob}
    for d in range(days):
        day = START + timedelta(days=d)
        for p, base in zip(products, bases):
            mu = demand_mean(scenario, base, d, day.weekday(), state)
            truth[(day.isoformat(), p)] = mu * (1 + 3 * outlier_prob) + 20 * outlier_prob
            if d in closed:
                rows.append(_row(day, p, False, 0, 0, "none"))
                continue
            if rng.random() < missing_prob:
                continue  # row absent from export
            # negative binomial with mean mu
            r = dispersion
            demand = int(rng.negative_binomial(r, r / (r + mu)))
            if rng.random() < outlier_prob:
                demand = int(demand * 4 + 20)  # one-off large order
            produced = demand
            status = "none"
            if stockout_mode:
                produced = max(
                    0, int(round(mu * (1 + stockout_slack) + rng.normal(0, 0.05 * mu)))
                )
                if demand > produced:
                    status = "occurred" if stockout_mode == "exclude" else "none"
            sold = min(demand, produced)
            rows.append(
                _row(
                    day,
                    p,
                    True,
                    sold,
                    produced - sold,
                    status,
                    demand=demand,
                    produced=produced,
                )
            )
    return (
        {
            "id": f"ds-{scenario}-{seed}",
            "version": "v1",
            "bakery_id": "bakery-sim",
            "target": "sold_units",
            "unit": "physical_unit",
            "source": "synthetic",
            "provenance": "stress-test generator",
            "rows": rows,
        },
        truth,
    )


def _row(day, p, is_open, sold, waste, status, demand=0, produced=None):
    produced = sold + waste if produced is None else produced
    return {
        "date": day.isoformat(),
        "product_id": p,
        "is_open": is_open,
        "opening": 0,
        "produced": produced if is_open else 0,
        "sold": sold,
        "waste": waste,
        "closing": 0,
        "other_out": 0,
        "stockout_status": status,
        "_demand": demand,
    }
