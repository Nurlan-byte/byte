"""Select an outlet and import its observed daily sales into an immutable snapshot."""

import csv
import hashlib
import io
import os
from datetime import timedelta

from backend import pos_providers
from backend.dataset_identity import identity
from backend.errors import ServiceError
from backend.schemas import ImportRequest
from backend.services import import_history

PROVIDER_NAMES = {"poster": "Poster POS", "moysklad": "МойСклад"}


def enabled():
    return os.getenv("BAKEPLAN_POS_ENABLED") == "1"


class NoSales(ServiceError):
    """The verified outlet has no sales in the completed-day window."""


class StaleSales(ServiceError):
    """Historical receipts exist but none confirms activity on the requested final day."""


def require_enabled():
    if not enabled():
        raise ServiceError(
            403, "Подключение кассы выключено в этом рабочем пространстве."
        )


def branch_id(provider, outlet):
    digest = hashlib.sha256(
        f"{provider}/{outlet.account}/{outlet.id}".encode()
    ).hexdigest()[:32]
    return f"{provider}-{digest}"


def select_outlet(reader, location_id):
    try:
        outlets = pos_providers.locations(reader)
        if location_id is None and len(outlets) > 1:
            return None, {
                "status": "choose_location",
                "locations": [s.public() for s in outlets],
            }
        selected = (
            outlets[0]
            if location_id is None
            else next((s for s in outlets if s.id == location_id), None)
        )
        if selected is None:
            raise ServiceError(
                422, "Выберите одну из доступных этому ключу торговых точек."
            )
        return selected, None
    except pos_providers.BAD_PAYLOAD:
        raise pos_providers.invalid_response() from None


def import_outlet(reader, selected, days, city, today, *, require_last_day=False):
    start = today - timedelta(days=days)
    sales = pos_providers.Observations(start, today - timedelta(days=1))
    try:
        if reader.provider == "poster":
            pos_providers.poster_sales(reader, selected, sales)
        else:
            pos_providers.moysklad_sales(reader, selected, sales)
    except pos_providers.BAD_PAYLOAD:
        raise pos_providers.invalid_response() from None
    if not sales.lines:
        raise NoSales(
            422,
            "За выбранные завершённые дни продаж по этой точке нет. Увеличьте период.",
        )
    # The corpus includes only observed trading days for products already seen.
    # Between the first and last observed sale, a missing product on a confirmed
    # trading day means zero recorded sales (no assumption about stock or demand).
    values = sales.quantities if sales.physical else sales.lines
    days = sorted({day for day, _ in values})
    if require_last_day and days[-1] != str(sales.end):
        raise StaleSales(422, "Нет подтверждения продаж за последний завершённый день.")
    observed = {}
    for day, item in values:
        low, high = observed.get(item, (day, day))
        observed[item] = min(low, day), max(high, day)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    field = "sold" if sales.physical else "item_line_count"
    writer.writerow(["date", "product_id", field])
    for day in days:
        for product, (low, high) in sorted(observed.items()):
            if low <= day <= high:
                writer.writerow([day, product, values.get((day, product), 0)])
    from backend.context_sources import load_demand_context, resolve_city

    city = (
        city
        if city is not None
        else (resolve_city(selected.address) or resolve_city(selected.name))
    )
    context, warnings = load_demand_context(
        start, today + timedelta(days=1), today, None if city == "none" else city
    )
    provenance = (
        f"{PROVIDER_NAMES[reader.provider]} · {selected.name} · {sales.start} — {sales.end}. "
        "Продажи до вычета возвратов, без складского баланса. "
        + (
            "Количество в штуках."
            if sales.physical
            else "Строки чеков: единицы измерения смешаны или не подтверждены."
        )
    )
    request = ImportRequest(
        source="local",
        bakery_id=branch_id(reader.provider, selected),
        provenance=provenance,
        csv_text=stream.getvalue(),
        schema_version="pos-units-1" if sales.physical else "sales-1",
        target="sold_units" if sales.physical else "item_line_count",
        unit="physical_unit" if sales.physical else "item_line",
        demand_context=context,
    )
    dataset, _ = import_history(
        request,
        today,
        pos_metadata={
            "pos_provider": reader.provider,
            "pos_location": selected.public(),
            "forecast_only": True,
            "product_names": sales.names,
        },
    )
    return {
        "status": "imported",
        "location": selected.public(),
        "dataset": identity(dataset),
        "context_warnings": warnings,
    }
