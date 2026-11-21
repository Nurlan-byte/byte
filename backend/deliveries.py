"""Branch-scoped supplier deliveries and atomic, retryable goods receipts."""

import json
import sqlite3
import uuid
from collections.abc import Collection
from datetime import date
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints

from backend.errors import ServiceError
from backend.procurement_planning import MAX_ORDER_QTY
from backend.storage import NotFound, connection, get_dataset, timestamp


Units = Annotated[int, Field(strict=True, gt=0, le=MAX_ORDER_QTY)]
DeliveryNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def _calendar_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
        return value
    raise ValueError("Укажите календарную дату в формате YYYY-MM-DD.")


ArrivalDate = Annotated[date, BeforeValidator(_calendar_date)]


class DeliveryRequest(BaseModel):
    number: DeliveryNumber
    product_id: str = Field(min_length=1, max_length=64)
    arrival_date: ArrivalDate
    ordered_qty: Units


class ReceiptRequest(BaseModel):
    quantity: Units
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"\S")


class RescheduleRequest(BaseModel):
    arrival_date: ArrivalDate


def _procurement_dataset(dataset_id):
    dataset = get_dataset(dataset_id)
    if dataset.get("planning_kind") != "procurement":
        raise ServiceError(422, "Для реестра выберите данные закупок филиала.")
    return dataset


def _select(con, where, parameters):
    cursor = con.execute(
        "SELECT id, bakery_id, number, product_id, arrival_date, initial_arrival_date, "
        "ordered_qty, received_qty, cancelled FROM deliveries WHERE " + where, parameters,
    )
    cursor.row_factory = sqlite3.Row
    return cursor


def _record(row):
    data = dict(row)
    data.pop("initial_arrival_date")
    cancelled = data.pop("cancelled")
    remaining = 0 if cancelled else data["ordered_qty"] - data["received_qty"]
    if cancelled:
        status = "cancelled"
    elif remaining == 0:
        status = "received"
    elif data["received_qty"]:
        status = "partial"
    else:
        status = "ordered"
    return {**data, "remaining_qty": remaining, "status": status}


def _get(con, branch, ident):
    row = _select(con, "bakery_id=? AND id=?", (branch, ident)).fetchone()
    if row is None:
        raise NotFound(ident)
    return _record(row)


def list_deliveries(dataset_id):
    branch = _procurement_dataset(dataset_id)["bakery_id"]
    with connection() as con:
        rows = _select(con, "bakery_id=? ORDER BY arrival_date, number, product_id", (branch,))
        return [_record(row) for row in rows]


def create_delivery(dataset_id, body: DeliveryRequest):
    dataset = _procurement_dataset(dataset_id)
    if body.product_id not in {product["product_id"] for product in dataset["catalog"]}:
        raise ServiceError(422, "Товар отсутствует в каталоге выбранного филиала.")
    branch = dataset["bakery_id"]
    eta = body.arrival_date.isoformat()
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        old = _select(con, "bakery_id=? AND number=? AND product_id=?",
                      (branch, body.number, body.product_id)).fetchone()
        if old is not None:
            if old["ordered_qty"] != body.ordered_qty or old["initial_arrival_date"] != eta:
                raise ServiceError(409, "Для этого товара уже есть поставка с таким номером и другими параметрами.")
            return _record(old), False
        ident = str(uuid.uuid4())
        con.execute(
            "INSERT INTO deliveries(id,bakery_id,number,product_id,arrival_date,initial_arrival_date,ordered_qty) "
            "VALUES (?,?,?,?,?,?,?)",
            (ident, branch, body.number, body.product_id, eta, eta, body.ordered_qty),
        )
        return _get(con, branch, ident), True


def receive_delivery(dataset_id, ident, body: ReceiptRequest):
    branch = _procurement_dataset(dataset_id)["bakery_id"]
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        delivery = _get(con, branch, ident)
        previous = con.execute(
            "SELECT quantity, response FROM delivery_receipts WHERE delivery_id=? AND idempotency_key=?",
            (ident, body.idempotency_key),
        ).fetchone()
        if previous is not None:
            if previous[0] != body.quantity:
                raise ServiceError(409, "Этот ключ приёмки уже использован с другим количеством.")
            return json.loads(previous[1])
        if body.quantity > delivery["remaining_qty"]:
            raise ServiceError(409, "Количество к приёмке превышает остаток поставки в пути.")
        con.execute("UPDATE deliveries SET received_qty=received_qty+? WHERE id=?", (body.quantity, ident))
        received = _get(con, branch, ident)
        con.execute(
            "INSERT INTO delivery_receipts(delivery_id,idempotency_key,quantity,received_at,response) "
            "VALUES (?,?,?,?,?)",
            (ident, body.idempotency_key, body.quantity, timestamp(), json.dumps(received)),
        )
        return received


def cancel_delivery(dataset_id, ident):
    branch = _procurement_dataset(dataset_id)["bakery_id"]
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        delivery = _get(con, branch, ident)
        if delivery["status"] == "received":
            raise ServiceError(409, "Поставка уже полностью принята.")
        if delivery["status"] == "cancelled":
            return delivery
        con.execute("UPDATE deliveries SET cancelled=1 WHERE id=?", (ident,))
        return _get(con, branch, ident)


def reschedule_delivery(dataset_id, ident, body: RescheduleRequest):
    branch = _procurement_dataset(dataset_id)["bakery_id"]
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        delivery = _get(con, branch, ident)
        if not delivery["remaining_qty"]:
            raise ServiceError(409, "Изменить дату можно только для товара в пути.")
        con.execute("UPDATE deliveries SET arrival_date=? WHERE id=?", (body.arrival_date.isoformat(), ident))
        return _get(con, branch, ident)


def outstanding_deliveries(con, bakery_id: str, product_ids: Collection[str], order_date: date, target_date: date):
    """Copy eligible outstanding quantities into the plan inside its save transaction."""
    if not product_ids:
        return []
    placeholders = ",".join("?" for _ in product_ids)
    rows = con.execute(
        "SELECT id, number, product_id, arrival_date, ordered_qty-received_qty FROM deliveries "
        "WHERE bakery_id=? AND cancelled=0 AND arrival_date BETWEEN ? AND ? "
        f"AND ordered_qty>received_qty AND product_id IN ({placeholders}) "
        "ORDER BY arrival_date, number, product_id",
        (bakery_id, order_date.isoformat(), target_date.isoformat(), *product_ids),
    )
    return [
        {"id": ident, "number": number, "product_id": product, "arrival_date": eta, "quantity": qty}
        for ident, number, product, eta, qty in rows
    ]
