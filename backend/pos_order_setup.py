"""Attach administrator-confirmed pack sizes to immutable POS unit sales."""

import hashlib
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.dataset_identity import identity
from backend.demand_context import with_demand_context
from backend.errors import ServiceError
from backend.procurement_planning import MAX_ORDER_QTY
from backend.storage import get_dataset, save_dataset, timestamp

router = APIRouter()
SCHEMA = "pos-procurement-1"
PackSize = Annotated[StrictInt, Field(ge=1, le=MAX_ORDER_QTY)]


class OrderCatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packs: dict[str, PackSize] = Field(min_length=1, max_length=50)


@router.post("/api/datasets/{ident}/order-catalog")
def set_order_catalog(ident: str, body: OrderCatalogRequest, response: Response):
    source = get_dataset(ident)
    if not (
        source.get("schema_version") == "pos-units-1"
        and source.get("target") == "sold_units"
        and source.get("unit") == "physical_unit"
        and source.get("forecast_only") is True
        and source.get("pos_provider")
    ):
        raise ServiceError(422, "Для заказа нужна история кассы с продажами в штуках.")

    products = {row["product_id"] for row in source["rows"]}
    if set(body.packs) != products:
        raise ServiceError(
            422, "Укажите упаковку для каждого товара из загруженной истории."
        )
    catalog = [
        {
            "product_id": product,
            "name": source["product_names"][product],
            "pack_size": body.packs[product],
            "max_order_qty": None,
        }
        for product in sorted(products)
    ]

    key = {
        "schema": SCHEMA,
        "source_id": ident,
        "source_version": source["version"],
        "packs": body.packs,
    }
    base_version = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    dataset = {
        **source,
        "id": str(uuid4()),
        "created_at": timestamp(),
        "source_dataset_id": ident,
        "source_dataset_version": source["version"],
        "schema_version": SCHEMA,
        "planning_kind": "procurement",
        "forecast_only": False,
        "catalog": catalog,
        "version": base_version,
        "content_hash": base_version,
        "demand_context_base_version": base_version,
    }
    if source.get("demand_context") is not None:
        dataset = with_demand_context(dataset, source["demand_context"])
    saved, created = save_dataset(dataset)
    response.status_code = 201 if created else 200
    return identity(saved)
