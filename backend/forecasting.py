"""Numerical baselines; recommendation eligibility belongs to planning policy."""

from collections.abc import Callable, Iterable
from datetime import date, timedelta


def suitable(row: dict) -> bool:
    return bool(row["is_open"]) and row["stockout_status"] == "none"


def row_date(row: dict) -> date:
    value = row["date"]
    return value if isinstance(value, date) else date.fromisoformat(value)


def baseline(
    rows: Iterable[dict],
    *,
    product_id: str,
    target_date: date,
    cutoff: date,
    method: str,
    eligible: Callable[[dict], bool] | None = None,
    value_key: str = "sold",
) -> dict:
    if method not in {"B0", "B1"}:
        raise ValueError("Unknown baseline method")
    eligibility = eligible or suitable
    history_start = date.fromordinal(max(1, target_date.toordinal() - 56))
    candidates = [
        row
        for row in rows
        if row["product_id"] == product_id
        and history_start <= row_date(row) < target_date
        and row_date(row) <= cutoff
        and row_date(row).weekday() == target_date.weekday()
        and eligibility(row)
    ]
    candidates.sort(key=row_date, reverse=True)
    selected = (
        [row for row in candidates if row_date(row) == target_date - timedelta(days=7)]
        if method == "B0"
        else candidates[:4]
    )
    return {
        "forecast_mean": sum(row[value_key] for row in selected) / len(selected)
        if selected
        else None,
        "used_dates": [row_date(row).isoformat() for row in selected],
        "n": len(selected),
        "eligible_weekday_n": len(candidates),
    }
