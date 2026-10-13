"""Application workflows. Confirmation is one atomic transaction."""

import csv
import io
import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from backend.config import CATALOG, VERSION
from backend.errors import ServiceError
from backend.dataset_identity import identity, parse_sales_csv
from backend.imports import parse_csv
from backend.planning import plan_item
from backend.schemas import ConfirmRequest, ImportRequest, PlanRequest
from backend.storage import (
    connection,
    fetch,
    get_dataset,
    get_plan,
    save_dataset,
    save_plan,
    update_plan,
)


def import_history(body: ImportRequest, today: date):
    rows = (
        parse_sales_csv(body.csv_text, today)
        if body.schema_version == "sales-1"
        else parse_csv(
            body.csv_text,
            allowed_product_ids={p["product_id"] for p in CATALOG},
            latest_date=today,
        )
    )
    missing = 0
    for product in CATALOG:
        dates = {
            date.fromisoformat(r["date"])
            for r in rows
            if r["product_id"] == product["product_id"]
        }
        if dates:
            missing += (max(dates) - min(dates)).days + 1 - len(dates)
    dataset = dict(
        id=str(uuid.uuid4()),
        source=body.source,
        rows=rows,
        version=hashlib.sha256(body.csv_text.encode()).hexdigest(),
        schema_version=body.schema_version,
        created_at=datetime.now(timezone.utc).isoformat(),
        row_count=len(rows),
        missing_calendar_days=missing,
        stockout_count=sum(r.get("stockout_status") == "occurred" for r in rows)
        if body.schema_version == "history-1"
        else None,
        unknown_stockout_count=sum(r.get("stockout_status") == "unknown" for r in rows)
        if body.schema_version == "history-1"
        else None,
        diagnostics=[
            "Пропуски не заменены нулями; закрытые дни задаются явно.",
            "Нулевой остаток после списания не доказывает дефицит.",
        ],
    )
    if body.bakery_id is not None:
        metadata = {
            key: getattr(body, key)
            for key in ("bakery_id", "target", "unit", "provenance")
        }
        dataset.update(**metadata, metadata_declared=True)
        dataset["content_hash"] = hashlib.sha256(
            json.dumps(
                {**metadata, "csv_hash": dataset["version"]}, sort_keys=True
            ).encode()
        ).hexdigest()
        dataset["version"] = dataset["content_hash"]
    else:
        dataset = identity(dataset)
    if body.schema_version == "sales-1":
        dataset["missing_calendar_days"] = None
        dataset["diagnostics"] = [
            "Только явно указанные item-line counts. Пропуски неизвестны и не заменяются нулями.",
            "Sales-only: единица — строка товара, не физическая штука. Выпуск, остатки, waste и скрытый спрос не оцениваются.",
        ]
    return save_dataset(dataset)


def create_plan(body: PlanRequest, today: date):
    if body.cutoff >= today:
        raise ServiceError(
            422,
            "Срез включает только завершённые календарные дни строго раньше сегодня",
        )
    if body.mode == "operational" and body.target_date <= today:
        raise ServiceError(422, "Прошедшая дата требует явного режима replay")
    if body.cutoff >= body.target_date:
        raise ServiceError(422, "cutoff должен быть раньше target_date")
    dataset = identity(get_dataset(body.dataset_id))
    if dataset["target"] != "sold_units" or dataset["unit"] != "physical_unit":
        raise ServiceError(
            422,
            "Sales-only item-line model cannot create a physical production plan. Используйте прогноз продаж.",
        )
    items = [
        plan_item(
            dataset["rows"],
            product,
            body.target_date,
            body.cutoff,
            "B1" if body.method == "ML" else body.method,
            body.target_is_open,
        )
        for product in CATALOG
    ]
    model = None
    if body.method == "ML":
        from backend.ml_service import forecast_items

        result = forecast_items(dataset, body.target_date, body.cutoff, body.model_id)
        model = result.get("model")
        predictions = {item["product_id"]: item for item in result["items"]}
        from backend.planning import nearest_allowed_quantity

        for item in items:
            prediction = predictions.get(item["product_id"])
            if not prediction:
                continue
            if item.get("active_from") or item.get("active_to"):
                prediction = {
                    **prediction,
                    "method": "B1",
                    "fallback_reason": "Период активности каталога требует расчёта B1 по отфильтрованной истории",
                }
            item.update(
                model_id=result["model_id"],
                fallback_reason=prediction.get("fallback_reason"),
            )
            if prediction.get("fallback_reason"):
                item["warnings"].append("ML: " + prediction["fallback_reason"])
            # plan_item owns the canonical filtered B1 and every production
            # eligibility guard. A fallback must never replace that baseline.
            if body.target_is_open and prediction["method"] == "ML":
                item["forecast_mean"] = prediction["forecast_mean"]
                item["diagnostic_method"] = prediction["method"]
                item["used_dates"] = prediction["used_dates"]
                item["n"] = prediction["n"]
                if (
                    item["automatic_eligible"]
                    and prediction["forecast_mean"] is not None
                ):
                    item["method"] = prediction["method"]
                    item["recommended_qty"] = nearest_allowed_quantity(
                        prediction["forecast_mean"],
                        item["batch_size"],
                        item["max_daily_qty"],
                    )
    plan = dict(
        id=str(uuid.uuid4()),
        revision=0,
        status="draft",
        dataset_id=dataset["id"],
        target_date=str(body.target_date),
        cutoff=str(body.cutoff),
        source=dataset["source"],
        bakery_id=dataset["bakery_id"],
        target=dataset["target"],
        unit=dataset["unit"],
        requested_method=body.method,
        model_id=model["id"] if model else None,
        protocol_version=model["protocol_version"] if model else None,
        dataset_version=dataset["version"],
        config_version=VERSION,
        config_hash=hashlib.sha256(
            json.dumps(CATALOG, sort_keys=True).encode()
        ).hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(),
        items=items,
        mode=body.mode,
        timezone="Asia/Qyzylorda",
        availability_assumption="Completed calendar days strictly before today; daily totals assumed available after day end, no intraday forecasting.",
    )
    save_plan(plan)
    return plan


def confirm_plan(ident: str, body: ConfirmRequest, today: date):
    encoded_body = json.dumps(body.model_dump(), sort_keys=True)
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute(
            "SELECT body,response FROM confirmations WHERE plan_id=? AND key=?",
            (ident, body.idempotency_key),
        ).fetchone()
        if previous:
            if previous[0] != encoded_body:
                raise ServiceError(
                    409, "Ключ повторного запроса использован с другими данными"
                )
            return json.loads(previous[1])
        plan = fetch(con, "plans", ident)
        if (
            plan["mode"] == "operational"
            and date.fromisoformat(plan["target_date"]) <= today
        ):
            raise ServiceError(
                409,
                "Дата рабочего плана уже наступила: создайте новый план или используйте историческое воспроизведение",
            )
        if body.revision != plan["revision"]:
            raise ServiceError(409, "План изменён. Перезагрузите актуальную версию")
        if set(body.quantities) != {p["product_id"] for p in plan["items"]}:
            raise ServiceError(422, "Требуются количества всех пяти продуктов")
        for item in plan["items"]:
            q = body.quantities[item["product_id"]]
            if (
                q < 0
                or (not item.get("target_is_open", True) and q != 0)
                or q > item["max_daily_qty"] // item["batch_size"] * item["batch_size"]
                or q % item["batch_size"]
            ):
                raise ServiceError(
                    422,
                    f"{item['name']}: количество должно быть кратно партии и в пределах максимума",
                )
            item["confirmed_qty"] = q
        plan.update(
            revision=plan["revision"] + 1,
            status="confirmed",
            confirmed_at=datetime.now(timezone.utc).isoformat(),
        )
        payload = json.dumps(plan)
        update_plan(con, plan)
        con.execute(
            "INSERT INTO revisions VALUES (?,?,?)", (ident, plan["revision"], payload)
        )
        con.execute(
            "INSERT INTO confirmations VALUES (?,?,?,?)",
            (ident, body.idempotency_key, encoded_body, payload),
        )
        return plan


def export_csv(ident: str):
    plan = get_plan(ident)
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(
        [
            "plan_id",
            "revision",
            "target_date",
            "source",
            "product_id",
            "name",
            "forecast_mean",
            "recommended_qty",
            "confirmed_qty",
            "method",
            "cutoff",
            "mode",
            "dataset_version",
            "config_version",
            "used_dates",
            "bakery_id",
            "target",
            "unit",
            "requested_method",
            "model_id",
            "protocol_version",
            "fallback_reason",
        ]
    )
    for item in plan["items"]:
        values = [
            ident,
            plan["revision"],
            plan["target_date"],
            plan["source"],
            item["product_id"],
            item["name"],
            item["forecast_mean"],
            item["recommended_qty"],
            item["confirmed_qty"],
            item["method"],
            plan["cutoff"],
            plan["mode"],
            plan["dataset_version"],
            plan["config_version"],
            ";".join(item["used_dates"]),
            plan.get("bakery_id", "legacy"),
            plan.get("target", "sold_units"),
            plan.get("unit", "physical_unit"),
            plan.get("requested_method", item["method"]),
            plan.get("model_id"),
            plan.get("protocol_version"),
            item.get("fallback_reason"),
        ]
        writer.writerow(
            [
                "'" + v
                if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                else v
                for v in values
            ]
        )
    return "\ufeff" + out.getvalue()
