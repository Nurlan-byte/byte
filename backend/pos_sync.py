"""Daily POS imports and forecasts for a private operator workspace."""

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone

from opentelemetry.trace import StatusCode

from backend import pos_connection_store as store, pos_import, pos_monitoring, pos_providers, pos_vault
from backend.daily_forecast import forecast_for_day
from backend.dataset_identity import identity
from backend.errors import ServiceError
from backend.storage import get_dataset

LOCAL_TIME = timezone(timedelta(hours=5))
DAILY_HOUR = 5
POLL_SECONDS = 60
ERROR_MESSAGES = {
    "unauthorized": "Касса отклонила доступ. Проверьте права или подключите точку заново с новым ключом.",
    "branch_changed": "Ключ больше не открывает сохранённую торговую точку. Проверьте подключение филиала.",
    "no_sales": "За выбранный период у точки пока нет продаж. BakePlan повторит проверку.",
    "vendor_unavailable": "Касса временно недоступна или ограничила запросы. BakePlan повторит обновление.",
    "vault_unavailable": "Серверу недоступен сохранённый ключ. Проверьте настройку защищённого хранилища.",
    "forecast_unavailable": "Не удалось пересчитать прогноз. BakePlan повторит обновление.",
    "sync_failed": "Не удалось обновить продажи. Проверьте период и доступ к кассе.",
    "freshness_unverified": "Касса не подтвердила продажи за последний завершённый день. BakePlan повторит проверку.",
}


def now_utc():
    return datetime.now(UTC)


def scheduled_day(now):
    local = now.astimezone(LOCAL_TIME)
    return (
        local.date() if local.hour >= DAILY_HOUR else local.date() - timedelta(days=1)
    )


def public_connection(row):
    return {
        "id": row["id"],
        "provider": row["provider"],
        "location": {"id": row["location_id"], "name": row["location_name"]},
        "status": row["status"],
        "dataset_id": row["dataset_id"],
        "last_success_day": row["last_success_day"],
        "last_observed_day": row["last_observed_day"],
        "last_attempt_at": row["last_attempt_at"],
        "error": ERROR_MESSAGES.get(row["error"]),
    }


def _update(claim, day, clock, verified=None):
    """Publish the new pair only while this worker still owns the connection."""
    with pos_monitoring.span("pos.daily", **{"bakeplan.connection.id": claim["id"], "bakeplan.day": str(day)}) as span:
        published, outcome = _update_stages(claim, day, clock, verified)
        span.set_attribute("bakeplan.outcome", outcome)
        if outcome != "fresh":
            span.set_status(StatusCode.ERROR)
    if outcome == "fresh":
        pos_monitoring.notify_ready(clock, only=claim["id"])
    return published


def _update_stages(claim, day, clock, verified):
    failure = "vault_unavailable"
    try:
        if verified is None:
            reader = pos_providers.VendorReader(
                claim["provider"], store.claim_key(claim)
            )
            failure = "branch_changed"
            outlet, _ = pos_import.select_outlet(reader, claim["location_id"])
            if pos_import.branch_id(reader.provider, outlet) != claim["bakery_id"]:
                raise ServiceError(422, "Изменилась учётная запись кассы.")
        else:
            reader, outlet = verified
        failure = "sync_failed"
        with pos_monitoring.span("pos.import"):
            imported = pos_import.import_outlet(reader, outlet, claim["days"], claim["city"], day, require_last_day=True)
            dataset = imported["dataset"]
            dataset_id = dataset["id"]
            observed = max((r["date"] for r in dataset["rows"]), default=None)
            if dataset["bakery_id"] != claim["bakery_id"]:
                raise ServiceError(422, "Неверный филиал в импорте")
            if observed != str(day - timedelta(days=1)):
                # These providers supply receipts, with no closing-shift watermark.
                # A gap can mean a closed or zero-sales day, or an outdated feed.
                failure = "freshness_unverified"
                raise ServiceError(422, "Нет подтверждения за нужную дату")
        failure = "forecast_unavailable"
        with pos_monitoring.span("pos.forecast"):
            forecast = forecast_for_day(dataset, day)
            forecast["observed_through"] = observed
            forecast["warnings"] = list(
                dict.fromkeys(imported["context_warnings"] + forecast["warnings"])
            )
        with pos_monitoring.span("pos.publish"):
            published = store.finish(claim, dataset_id, forecast, day, clock())
        return published, "fresh" if published else "superseded"
    except pos_import.NoSales:
        failure = "no_sales"
    except pos_import.StaleSales:
        failure = "freshness_unverified"
    except ServiceError as error:
        if failure in {"branch_changed", "sync_failed"}:
            if error.status_code in {401, 403}:
                failure = "unauthorized"
            elif error.status_code == 429 or error.status_code >= 500:
                failure = "vendor_unavailable"
    except Exception:
        # Vendor exceptions can contain a credential in a request URL or message.
        # Keep only a fixed stage/error code and preserve the previous successful pair.
        pass
    return store.fail(claim, failure, clock()), failure


def register_connection(provider, key, location_id, days, city):
    pos_import.require_enabled()
    if not pos_vault.available():
        raise ServiceError(
            503, "Для ежедневного подключения настройте ключ шифрования на сервере."
        )
    reader = pos_providers.VendorReader(provider, key)
    selected, choices = pos_import.select_outlet(reader, location_id)
    if selected is None:
        return choices
    now = now_utc()
    claim = store.start_connection(
        provider,
        selected.id,
        selected.name,
        pos_import.branch_id(provider, selected),
        days,
        city,
        key,
        now,
    )
    if not _update(
        claim, now.astimezone(LOCAL_TIME).date(), now_utc, (reader, selected)
    ):
        raise ServiceError(
            409,
            "Подключение изменилось во время обновления. Откройте его актуальное состояние.",
        )
    return {
        "status": "connected",
        **connection_result(claim["id"]),
        "context_warnings": [],
    }


def connection_result(ident):
    row, forecast = store.connection_result(ident)
    return {
        "connection": public_connection(row),
        "dataset": identity(get_dataset(row["dataset_id"]))
        if row["dataset_id"]
        else None,
        "forecast": forecast,
    }


def run_due(now=None, stop=None):
    """Catch up to the latest scheduled day. Independent app processes share leases."""
    if not pos_import.enabled() or not os.getenv("BAKEPLAN_POS_MASTER_KEY_FILE"):
        return
    clock = now_utc if now is None else lambda: now
    while stop is None or not stop.is_set():
        instant = clock()
        day = scheduled_day(instant)
        claim = store.claim_next(day, instant)
        if claim is None:
            break
        _update(claim, date.fromisoformat(claim["day"]), clock)
    pos_monitoring.notify_ready(clock)


def _loop(stop):
    while not stop.is_set():
        try:
            run_due(stop=stop)
        except Exception:
            # A locked/unavailable DB must not terminate future scheduled checks.
            logging.getLogger(__name__).warning("Scheduled POS check unavailable")
        stop.wait(POLL_SECONDS)


@asynccontextmanager
async def scheduler():
    if not pos_import.enabled() or not os.getenv("BAKEPLAN_POS_MASTER_KEY_FILE"):
        yield
        return
    stop = threading.Event()
    worker = threading.Thread(
        target=_loop, args=(stop,), name="bakeplan-pos-sync", daemon=True
    )
    worker.start()
    try:
        yield
    finally:
        stop.set()
        await asyncio.to_thread(worker.join)
