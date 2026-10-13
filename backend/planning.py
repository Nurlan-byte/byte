"""Pure production constraints; numerical baseline and automatic eligibility differ."""

from datetime import date
from backend.forecasting import baseline

DEFICIT_MANUAL_THRESHOLD = 0.5


def nearest_allowed_quantity(mean: float, batch: int, maximum: int) -> int:
    upper_limit = maximum // batch * batch
    lower = min(int(mean // batch) * batch, upper_limit)
    upper = min(lower + batch, upper_limit)
    return lower if abs(mean - lower) <= abs(upper - mean) else upper


def plan_item(
    rows: list[dict],
    product: dict,
    target: date,
    cutoff: date,
    method: str,
    target_is_open: bool,
) -> dict:
    history_start = date.fromordinal(max(1, target.toordinal() - 56))
    history = [
        r
        for r in rows
        if r["product_id"] == product["product_id"]
        and history_start <= date.fromisoformat(r["date"]) <= cutoff
        and (not product.get("active_from") or r["date"] >= product["active_from"])
        and (not product.get("active_to") or r["date"] <= product["active_to"])
    ]
    observed = [r for r in history if r["is_open"]]
    deficit = sum(r["stockout_status"] != "none" for r in observed)
    high_deficit = bool(observed) and deficit / len(observed) > DEFICIT_MANUAL_THRESHOLD
    carryover = any(r["opening"] or r["closing"] for r in observed)
    forecast = baseline(
        history,
        product_id=product["product_id"],
        target_date=target,
        cutoff=cutoff,
        method=method,
    )
    if not target_is_open:
        forecast = {**forecast, "forecast_mean": None, "used_dates": [], "n": 0}
    automatic = (
        target_is_open and not high_deficit and not carryover and forecast["n"] > 0
    )
    warnings = [
        "Общую загрузку печи проверьте вручную. Денежная оптимизация недоступна."
    ]
    if carryover:
        warnings.append(
            "Перенос остатков: baseline показан как диагностика, автоматический выпуск отключён; нужен ручной план."
        )
    if high_deficit:
        warnings.append(
            "Более 50% наблюдаемых открытых дней имеют дефицит или неизвестное наличие: нужен ручной план."
        )
    if not target_is_open:
        warnings.append(
            "Целевой день закрыт по явному календарю: выпуск 0, ручное увеличение запрещено."
        )
    elif not forecast["n"]:
        warnings.append("Нет подходящей истории: 0 — ручная заготовка, не прогноз.")
    elif method == "B1" and forecast["n"] < 4:
        warnings.append("Неполная история: среднее по доступным подходящим дням.")
    if product["batch_size"] > product["max_daily_qty"]:
        warnings.append("Размер партии превышает максимум: допустим только 0.")
    quantity = (
        nearest_allowed_quantity(
            forecast["forecast_mean"], product["batch_size"], product["max_daily_qty"]
        )
        if automatic
        else 0
    )
    return {
        **product,
        **forecast,
        "recommended_qty": quantity,
        "confirmed_qty": None,
        "method": method if automatic else "manual",
        "diagnostic_method": method,
        "target_is_open": target_is_open,
        "automatic_eligible": automatic,
        "warnings": warnings,
        "observed_open_n": len(observed),
        "deficit_or_unknown_n": deficit,
    }
