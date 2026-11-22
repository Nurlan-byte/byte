"""HTTP endpoints for one-time POS imports and saved connections."""

import os
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, Field, SecretStr, StrictInt, field_validator

from backend import pos_connection_store as store, pos_import, pos_providers, pos_sync, pos_vault
from backend.config import business_today
from backend.errors import ServiceError

router = APIRouter(prefix="/api/pos")
ConnectionID = Annotated[UUID, Path()]


class PosRequest(BaseModel):
    provider: Literal["poster", "moysklad", "smartshell"]
    api_key: SecretStr
    location_id: str | None = Field(default=None, min_length=1, max_length=64)
    days: StrictInt = Field(default=56, ge=1, le=120)
    city: (
        Literal[
            "shymkent",
            "taraz",
            "almaty",
            "astana",
            "karaganda",
            "kyzylorda",
            "aktobe",
            "atyrau",
            "none",
            "weekday_only",
        ]
        | None
    ) = None

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, wrapped):
        key = wrapped.get_secret_value()
        if (
            not 1 <= len(key) <= 1024
            or not key.isascii()
            or not all(32 < ord(c) < 127 for c in key)
        ):
            raise ValueError("Введите ключ без пробелов и переводов строк.")
        return wrapped


@router.get("/status")
def status():
    if not pos_import.enabled():
        return {"enabled": False}
    if os.getenv(pos_vault.KEY_FILE_ENV) or store.has_connections():
        return {
            "enabled": True,
            "daily_configured": True,
            "daily_enabled": pos_vault.available(),
        }
    return {"enabled": True}


@router.post("/import")
def connect(body: PosRequest):
    pos_import.require_enabled()
    reader = pos_providers.VendorReader(body.provider, body.api_key.get_secret_value())
    selected, choices = pos_import.select_outlet(reader, body.location_id)
    if selected is None:
        return choices
    return pos_import.import_outlet(reader, selected, body.days, body.city, business_today())


@router.post("/connections", name="connect")
def connect_daily(body: PosRequest):
    return pos_sync.register_connection(
        body.provider, body.api_key.get_secret_value(), body.location_id, body.days, body.city
    )


@router.get("/connections")
def connections():
    pos_import.require_enabled()
    return [pos_sync.public_connection(row) for row in store.list_connections()]


@router.get("/connections/{ident}/latest")
def latest(ident: ConnectionID):
    pos_import.require_enabled()
    result = pos_sync.connection_result(str(ident))
    if result["forecast"] is None:
        raise ServiceError(409, "Первый прогноз ещё не готов.")
    return result


@router.delete("/connections/{ident}", status_code=204)
def disconnect(ident: ConnectionID):
    pos_import.require_enabled()
    store.revoke(str(ident), pos_sync.now_utc())
    return Response(status_code=204)
