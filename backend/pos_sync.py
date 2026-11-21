"""Daily POS imports and forecasts for a private operator workspace."""

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Response

from backend import pos, pos_connection_store as store, pos_providers, pos_vault
from backend.context_routes import forecast_for_day
from backend.dataset_identity import identity
from backend.errors import ServiceError
from backend.storage import get_dataset

router = APIRouter(prefix="/api/pos/connections")
LOCAL_TIME = timezone(timedelta(hours=5))
DAILY_HOUR = 5
POLL_SECONDS = 60
ConnectionID = Annotated[UUID, Path()]
ERROR_MESSAGES = {
    "unauthorized": "Касса отклонила доступ. Проверьте права или подключите точку заново с новым ключом.",
    "branch_changed": "Ключ больше не открывает сохранённую торговую точку. Проверьте подключение филиала.",
    "no_sales": "За выбранный период у точки пока нет продаж. BakePlan повторит проверку.",
    "vendor_unavailable": "Касса временно недоступна или ограничила запросы. BakePlan повторит обновление.",
    "vault_unavailable": "Серверу недоступен сохранённый ключ. Проверьте настройку защищённого хранилища.",
    "forecast_unavailable": "Не удалось пересчитать прогноз. BakePlan повторит обновление.",
    "sync_failed": "Не удалось обновить продажи. Проверьте период и доступ к кассе.",
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
        "last_attempt_at": row["last_attempt_at"],
        "error": ERROR_MESSAGES.get(row["error"]),
    }


def _update(claim, day, clock, verified=None):
    """Publish the new pair only while this worker still owns the connection."""
    failure = "vault_unavailable"
    try:
        if verified is None:
            reader = pos_providers.VendorReader(
                claim["provider"], store.claim_key(claim)
            )
            failure = "branch_changed"
            outlet, _ = pos.select_outlet(reader, claim["location_id"])
            if pos.branch_id(reader.provider, outlet) != claim["bakery_id"]:
                raise ServiceError(422, "Изменилась учётная запись кассы.")
        else:
            reader, outlet = verified
        failure = "sync_failed"
        imported = pos.import_outlet(reader, outlet, claim["days"], claim["city"], day)
        dataset_id = imported["dataset"]["id"]
        failure = "forecast_unavailable"
        forecast = forecast_for_day(dataset_id, day)
        forecast["warnings"] = list(
            dict.fromkeys(imported["context_warnings"] + forecast["warnings"])
        )
        return store.finish(claim, dataset_id, forecast, day, clock())
    except pos.NoSales:
        failure = "no_sales"
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
    return store.fail(claim, failure, clock())


@router.post("")
def connect(body: pos.PosRequest):
    pos.require_enabled()
    if not pos_vault.available():
        raise ServiceError(
            503, "Для ежедневного подключения настройте ключ шифрования на сервере."
        )
    key = body.api_key.get_secret_value()
    reader = pos_providers.VendorReader(body.provider, key)
    selected, choices = pos.select_outlet(reader, body.location_id)
    if selected is None:
        return choices
    now = now_utc()
    claim = store.start_connection(
        body.provider,
        selected.id,
        selected.name,
        pos.branch_id(body.provider, selected),
        body.days,
        body.city,
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


@router.get("")
def connections():
    pos.require_enabled()
    return [public_connection(row) for row in store.list_connections()]


def connection_result(ident):
    row, forecast = store.connection_result(ident)
    return {
        "connection": public_connection(row),
        "dataset": identity(get_dataset(row["dataset_id"]))
        if row["dataset_id"]
        else None,
        "forecast": forecast,
    }


@router.get("/{ident}/latest")
def latest(ident: ConnectionID):
    pos.require_enabled()
    result = connection_result(str(ident))
    if result["forecast"] is None:
        raise ServiceError(409, "Первый прогноз ещё не готов.")
    return result


@router.delete("/{ident}", status_code=204)
def disconnect(ident: ConnectionID):
    pos.require_enabled()
    store.revoke(str(ident))
    return Response(status_code=204)


def run_due(now=None, stop=None):
    """Catch up to the latest scheduled day. Independent app processes share leases."""
    if not pos.enabled() or not os.getenv("BAKEPLAN_POS_MASTER_KEY_FILE"):
        return
    clock = now_utc if now is None else lambda: now
    while stop is None or not stop.is_set():
        instant = clock()
        day = scheduled_day(instant)
        claim = store.claim_next(day, instant)
        if claim is None:
            break
        _update(claim, date.fromisoformat(claim["day"]), clock)


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
    if not pos.enabled() or not os.getenv("BAKEPLAN_POS_MASTER_KEY_FILE"):
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
