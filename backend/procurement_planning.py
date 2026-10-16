"""Single-target-day procurement suggestions for ready-made goods."""

import math
from datetime import date

from backend.forecasting import baseline


def suggested_order(forecast: float, usable_stock: int, pack_size: int, maximum: int | None):
    """Round the uncovered sales forecast up to supplier packs, then apply a cap."""
    needed = max(0, math.ceil((forecast - usable_stock) / pack_size) * pack_size)
    if maximum is None:
        return needed, False
    permitted = maximum // pack_size * pack_size
    return min(needed, permitted), needed > permitted


def procurement_item(
    rows: list[dict], product: dict, target: date, cutoff: date,
    method: str, target_is_open: bool, usable_stock: int | None,
) -> dict:
    product_id = product["product_id"]
    observed = [
        row for row in rows
        if row["product_id"] == product_id and row["date"] <= cutoff.isoformat()
    ]
    forecast = baseline(
        rows, product_id=product_id, target_date=target, cutoff=cutoff,
        method=method, eligible=lambda row: row.get("is_open") is not False,
    )
    automatic = target_is_open and usable_stock is not None and forecast["n"] > 0
    warnings = [
        "Forecast concerns observed unit sales, not unconstrained demand.",
        "Usable stock is a user scenario for the target day, not inferred from historical closing stock.",
    ]
    unknown_open = sum(row.get("is_open") is None for row in observed)
    unknown_stockout = sum(row.get("stockout_status") == "unknown" for row in observed)
    if unknown_open or unknown_stockout:
        warnings.append(
            f"Historical availability is unverified: {unknown_open} open-status unknown, "
            f"{unknown_stockout} stockout-status unknown rows."
        )
    if not target_is_open:
        warnings.append("Target day is marked closed; order must be zero.")
    if usable_stock is None:
        warnings.append("Enter estimated usable stock at target-day opening for an automatic order.")
    if forecast["n"] == 0:
        warnings.append("No suitable weekday history: manual order only; zero is not a sales forecast.")
    quantity, capped = (0, False)
    if automatic:
        quantity, capped = suggested_order(
            forecast["forecast_mean"], usable_stock,
            product["pack_size"], product["max_order_qty"],
        )
    if capped:
        warnings.append("Supplier maximum is below forecast uncovered sales; unmet need remains.")
    return {
        **product,
        **forecast,
        "recommended_qty": quantity,
        "confirmed_qty": None,
        "method": method if automatic else "manual",
        "diagnostic_method": method,
        "target_is_open": target_is_open,
        "automatic_eligible": automatic,
        "usable_stock_assumption": usable_stock,
        "unknown_open_n": unknown_open,
        "unknown_stockout_n": unknown_stockout,
        "max_constraint_active": capped,
        "warnings": warnings,
    }
