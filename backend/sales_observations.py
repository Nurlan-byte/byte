"""Which recorded sales may stand for demand in forecasts."""

HISTORY_RULE = "no-stockout-days-v1"


def is_demand_observation(row: dict) -> bool:
    # POS rows normally have unknown availability, so only a marked stockout is excluded.
    return row.get("is_open") is not False and row.get("stockout_status") != "occurred"


def without_stockouts(dataset: dict) -> dict:
    """Hide marked stockouts from a forecaster, which treats absent days as missing."""
    rows = [row for row in dataset["rows"] if row.get("stockout_status") != "occurred"]
    return {**dataset, "rows": rows}
