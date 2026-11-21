"""Single-target-day procurement suggestions for ready-made goods."""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from backend.forecasting import baseline

MAX_ORDER_QTY = 1_000_000_000
CAP_WARNINGS = {
    "supplier_limit": "Supplier maximum is below forecast uncovered sales; unmet need remains.",
    "system_limit": "The order quantity limit prevents adding another pack; unmet need remains.",
}


def _quantity_limit(maximum: int | None) -> int:
    return MAX_ORDER_QTY if maximum is None else min(maximum, MAX_ORDER_QTY)


def assess_order_quantity(
    forecast: float | None, available_stock: int | None, pack_size: int,
    maximum: int | None, target_is_open: bool, quantity: int,
) -> dict:
    """Explain the coverage of a valid recommended or manually entered quantity."""
    shortage = None
    reason = None
    if not target_is_open:
        shortage = 0
    elif forecast is not None and available_stock is not None:
        shortage = max(0, math.ceil(forecast - available_stock - quantity))
        limit = _quantity_limit(maximum)
        if shortage > 0 and quantity + pack_size > limit:
            reason = "supplier_limit" if maximum == limit else "system_limit"
    return {"shortage_units": shortage, "constraint_reason": reason}


def _recommend_order(
    forecast: dict, usable_stock: int | None, incoming_units: int,
    pack_size: int, maximum: int | None, target_is_open: bool,
) -> dict:
    available = None if usable_stock is None else usable_stock + incoming_units
    sales = forecast["forecast_mean"]
    automatic = (
        target_is_open and available is not None and sales is not None
        and (forecast["diagnostic_method"] == "Chronos-2" or forecast["eligible_weekday_n"] > 0)
    )
    packs = 0
    if automatic:
        needed_units = max(0, math.ceil(sales - available))
        needed_packs = (needed_units + pack_size - 1) // pack_size
        packs = min(needed_packs, _quantity_limit(maximum) // pack_size)
    quantity = packs * pack_size
    assessment = assess_order_quantity(sales, available, pack_size, maximum, target_is_open, quantity)
    return {
        "recommended_qty": quantity,
        "recommended_packs": packs,
        "usable_stock_assumption": usable_stock,
        "incoming_units": incoming_units,
        "available_stock_units": available,
        "method": forecast["diagnostic_method"] if automatic else "manual",
        "automatic_eligible": automatic,
        "max_constraint_active": assessment["constraint_reason"] is not None,
        **assessment,
    }


def _select_forecast(
    rows: list[dict], product_id: str, target: date, cutoff: date, method: str,
    target_is_open: bool, prediction: dict | None, model_id: str | None,
) -> dict:
    if method == "AUTO":
        if not target_is_open:
            return dict(forecast_mean=0, diagnostic_method="manual", used_dates=[], n=0, eligible_weekday_n=0)
        # Each automatic prediction carries its own evidence. A missing Chronos
        # result must never silently turn into a weekday-baseline order.
        if prediction is None:
            raise ValueError("Missing automatic procurement forecast")
        return {
            **prediction, "diagnostic_method": prediction["method"],
            "fallback_reason": prediction.get("reason") if prediction["method"] == "B1" else None,
        }
    baseline_method = "B1" if method == "ML" else method
    forecast = baseline(
        rows, product_id=product_id, target_date=target, cutoff=cutoff,
        method=baseline_method, eligible=lambda row: row.get("is_open") is not False,
    )
    forecast["diagnostic_method"] = baseline_method
    if prediction is not None:
        forecast.update(
            model_id=model_id,
            fallback_reason=prediction.get("fallback_reason"),
            evaluation_applicability=prediction.get("evaluation_applicability"),
        )
        if target_is_open and prediction["method"] == "ML":
            if "demand_features" in prediction:
                forecast["demand_features"] = prediction["demand_features"]
            forecast.update(
                diagnostic_method="ML", forecast_mean=prediction["forecast_mean"],
                used_dates=prediction["used_dates"], n=prediction["n"],
            )
    return forecast


@dataclass(frozen=True)
class ProcurementForecast:
    """The part of one order line that depends on historical observations."""

    product: dict
    forecast: dict
    target_is_open: bool
    unknown_open: int
    unknown_stockout: int
    ml_warning: str | None

    def order(self, usable_stock: int | None, incoming_units: int) -> dict:
        decision = _recommend_order(
            self.forecast, usable_stock, incoming_units, self.product["pack_size"],
            self.product["max_order_qty"], self.target_is_open,
        )
        warnings = [
            "Forecast concerns observed unit sales, not unconstrained demand.",
            "Usable stock is a user scenario for the target day, not inferred from historical closing stock.",
        ]
        if self.unknown_open or self.unknown_stockout:
            warnings.append(
                f"Historical availability is unverified: {self.unknown_open} open-status unknown, "
                f"{self.unknown_stockout} stockout-status unknown rows."
            )
        if not self.target_is_open:
            warnings.append("Target day is marked closed; order must be zero.")
        if usable_stock is None:
            warnings.append("Enter estimated usable stock at target-day opening for an automatic order.")
        if self.target_is_open and self.forecast["n"] == 0:
            warnings.append("No suitable sales history: manual order only; zero is not a sales forecast.")
        if decision["constraint_reason"]:
            warnings.append(CAP_WARNINGS[decision["constraint_reason"]])
        if self.ml_warning:
            warnings.append(self.ml_warning)
        return {
            **self.product,
            **self.forecast,
            **decision,
            "confirmed_qty": None,
            "target_is_open": self.target_is_open,
            "unknown_open_n": self.unknown_open,
            "unknown_stockout_n": self.unknown_stockout,
            "warnings": warnings,
        }


def prepare_procurement_items(
    rows: list[dict], products: list[dict], target: date, cutoff: date,
    method: str, target_is_open: bool, predictions: dict, model_id: str | None,
) -> list[ProcurementForecast]:
    by_product = defaultdict(list)
    last_day = cutoff.isoformat()
    for row in rows:
        if row["date"] <= last_day:
            by_product[row["product_id"]].append(row)

    prepared = []
    for product in products:
        product_id = product["product_id"]
        observed = by_product[product_id]
        prediction = predictions.get(product_id)
        forecast = _select_forecast(
            observed, product_id, target, cutoff, method, target_is_open, prediction, model_id,
        )
        ml_warning = None
        if method == "ML" and prediction is None:
            ml_warning = "No model forecast for this product; B1 remains in use."
        elif method == "ML" and forecast.get("fallback_reason"):
            ml_warning = "ML fallback: " + forecast["fallback_reason"]
        prepared.append(ProcurementForecast(
            product=product, forecast=forecast, target_is_open=target_is_open,
            unknown_open=sum(row.get("is_open") is None for row in observed),
            unknown_stockout=sum(row.get("stockout_status", "unknown") == "unknown" for row in observed),
            ml_warning=ml_warning,
        ))
    return prepared


def procurement_item(
    rows: list[dict], product: dict, target: date, cutoff: date,
    method: str, target_is_open: bool, usable_stock: int | None, incoming_units: int = 0,
    *, prediction: dict | None = None, model_id: str | None = None,
) -> dict:
    """Build a single order line using the batch calculation."""
    predictions = {product["product_id"]: prediction} if prediction is not None else {}
    items = prepare_procurement_items(
        rows, [product], target, cutoff, method, target_is_open, predictions, model_id,
    )
    return items[0].order(usable_stock, incoming_units)
