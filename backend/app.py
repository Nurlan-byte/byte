"""BakePlan local baseline API. Business data stays in the configured SQLite file."""

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, StrictInt

CATALOG = [
    {"product_id": f"p{i}", "name": name, "batch_size": batch, "max_daily_qty": maximum}
    for i, (name, batch, maximum) in enumerate(
        [
            ("Хлеб пшеничный", 12, 100),
            ("Хлеб ржаной", 10, 100),
            ("Багет", 8, 80),
            ("Круассан", 6, 120),
            ("Булочка", 12, 144),
        ],
        1,
    )
]
QUANTITIES = ["opening", "produced", "sold", "waste", "closing", "other_out"]
REQUIRED = ["date", "product_id", "is_open", *QUANTITIES, "stockout_status"]
VERSION = "baseline-1"
for _product in CATALOG:
    _product.update(active_from=None, active_to=None, price=None, cost=None)


@contextmanager
def connection():
    path = Path(os.getenv("BAKEPLAN_DB", "data/bakeplan.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.execute(
        "CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS confirmations(plan_id TEXT, key TEXT, body TEXT, response TEXT, PRIMARY KEY(plan_id,key))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS revisions(plan_id TEXT, revision INTEGER, payload TEXT, PRIMARY KEY(plan_id,revision))"
    )
    con.commit()
    try:
        with con:
            yield con
    finally:
        con.close()


def fetch(con, table, ident):
    row = con.execute(f"SELECT payload FROM {table} WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise HTTPException(404, "Объект не найден")
    return json.loads(row[0])


class ImportRequest(BaseModel):
    csv_text: str = Field(max_length=1_000_000)
    source: Literal["synthetic", "local", "external"]


class PlanRequest(BaseModel):
    dataset_id: str
    target_date: date
    cutoff: date
    method: Literal["B0", "B1"] = "B1"
    mode: Literal["replay", "operational"] = "operational"


class ConfirmRequest(BaseModel):
    revision: StrictInt
    idempotency_key: str = Field(min_length=1, max_length=100)
    quantities: dict[str, StrictInt]


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if (
        reader.fieldnames is None
        or len(reader.fieldnames) != len(set(reader.fieldnames))
        or any(c not in reader.fieldnames for c in REQUIRED)
    ):
        raise HTTPException(
            422,
            {"errors": ["Отсутствуют обязательные колонки: " + ", ".join(REQUIRED)]},
        )
    rows, errors, seen = [], [], set()
    for line, raw in enumerate(reader, 2):
        try:
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError("Число полей строки не соответствует заголовку")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw["date"]):
                raise ValueError("Дата должна иметь формат YYYY-MM-DD")
            d = date.fromisoformat(raw["date"])
            if d > datetime.now(timezone(timedelta(hours=5))).date():
                raise ValueError("дата истории находится в будущем")
            product = raw["product_id"]
            if product not in {p["product_id"] for p in CATALOG}:
                raise ValueError("неизвестный product_id")
            if (str(d), product) in seen:
                raise ValueError("дубликат даты и продукта")
            seen.add((str(d), product))
            if raw["is_open"] not in ("true", "false", "1", "0"):
                raise ValueError("is_open: true/false или 1/0")
            nums = {}
            for key in QUANTITIES:
                value = raw[key]
                if not value.isascii() or not value.isdigit():
                    raise ValueError(f"{key}: требуется неотрицательное целое")
                nums[key] = int(value)
            if nums["opening"] + nums["produced"] != sum(
                nums[k] for k in ["sold", "waste", "closing", "other_out"]
            ):
                raise ValueError("нарушен баланс остатков")
            if raw["stockout_status"] not in ("none", "occurred", "unknown"):
                raise ValueError("stockout_status: none/occurred/unknown")
            opened = raw["is_open"] in ("true", "1")
            if not opened and (nums["sold"] or nums["produced"]):
                raise ValueError("закрытый день содержит продажи/производство")
            rows.append(
                dict(
                    date=str(d),
                    product_id=product,
                    is_open=opened,
                    stockout_status=raw["stockout_status"],
                    **nums,
                )
            )
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"Строка {line}: {exc}")
        if line > 10002:
            raise HTTPException(422, "Максимум 10000 строк")
    if errors or not rows:
        raise HTTPException(422, {"errors": errors[:100] or ["CSV пуст"]})
    return rows


app = FastAPI(title="BakePlan", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    try:
        with connection() as con:
            con.execute("SELECT 1")
    except OSError, sqlite3.Error:
        raise HTTPException(503, "Хранилище недоступно") from None
    return {"status": "ok", "app_version": "0.1.0", "db_status": "ok"}


@app.middleware("http")
async def limit_body(request, call_next):
    if request.method == "POST":
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2_000_000:
                return Response("Слишком большой запрос", status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    return await call_next(request)


@app.get("/api/catalog")
def catalog():
    return CATALOG


@app.post("/api/datasets", status_code=201)
def import_dataset(body: ImportRequest):
    rows = parse_csv(body.csv_text)
    missing = 0
    for product in CATALOG:
        dates = {
            date.fromisoformat(r["date"])
            for r in rows
            if r["product_id"] == product["product_id"]
        }
        if dates:
            missing += (max(dates) - min(dates)).days + 1 - len(dates)
    dataset = {
        "id": str(uuid.uuid4()),
        "source": body.source,
        "rows": rows,
        "version": hashlib.sha256(body.csv_text.encode()).hexdigest(),
        "diagnostics": [
            "Пропущенные дни не считаются нулевыми продажами.",
            "Статус дефицита задан явно; нулевой остаток его не доказывает.",
        ],
        "row_count": len(rows),
        "missing_calendar_days": missing,
        "stockout_count": sum(r["stockout_status"] == "occurred" for r in rows),
        "unknown_stockout_count": sum(r["stockout_status"] == "unknown" for r in rows),
    }
    with connection() as con:
        con.execute(
            "INSERT INTO datasets VALUES (?,?)", (dataset["id"], json.dumps(dataset))
        )
    return dataset


@app.get("/api/datasets")
def datasets():
    with connection() as con:
        return [
            {k: v for k, v in json.loads(r[0]).items() if k != "rows"}
            for r in con.execute("SELECT payload FROM datasets ORDER BY rowid DESC")
        ]


@app.post("/api/plans", status_code=201)
def create_plan(body: PlanRequest):
    if body.cutoff > datetime.now(timezone(timedelta(hours=5))).date():
        raise HTTPException(422, "Срез не может находиться в будущем")
    if (
        body.mode == "operational"
        and body.target_date <= datetime.now(timezone(timedelta(hours=5))).date()
    ):
        raise HTTPException(422, "Прошедшая дата требует явного режима replay")
    if body.cutoff >= body.target_date:
        raise HTTPException(
            422,
            "cutoff должен быть раньше target_date; доступны данные включительно до cutoff",
        )
    with connection() as con:
        dataset = fetch(con, "datasets", body.dataset_id)
        items = []
        for product in CATALOG:
            suitable = [
                r
                for r in dataset["rows"]
                if r["product_id"] == product["product_id"]
                and r["is_open"]
                and r["stockout_status"] == "none"
                and body.target_date - timedelta(days=56)
                <= date.fromisoformat(r["date"])
                <= body.cutoff
                and date.fromisoformat(r["date"]).weekday()
                == body.target_date.weekday()
                and (
                    product["active_from"] is None
                    or r["date"] >= product["active_from"]
                )
                and (product["active_to"] is None or r["date"] <= product["active_to"])
            ]
            suitable.sort(key=lambda r: r["date"], reverse=True)
            carryover = any(r["opening"] or r["closing"] for r in suitable)
            if carryover:
                suitable = []
            selected = (
                [
                    r
                    for r in suitable
                    if r["date"] == str(body.target_date - timedelta(days=7))
                ]
                if body.method == "B0"
                else suitable[:4]
            )
            warnings = [
                "Общую загрузку печи проверьте вручную. Денежная экономия не рассчитывается."
            ]
            if carryover:
                warnings.append(
                    "Остатки между днями: базовая однодневная модель отключена, требуется ручной расчёт."
                )
            mean = (
                sum(r["sold"] for r in selected) / len(selected) if selected else None
            )
            batch, maximum = product["batch_size"], product["max_daily_qty"]
            if batch > maximum:
                warnings.append("Размер партии превышает максимум: допустимо только 0.")
            qty = (
                min(int((mean / batch) + 0.5 - 1e-12) * batch, maximum // batch * batch)
                if mean is not None
                else 0
            )
            if not selected:
                warnings.append(
                    "Нет подходящей истории: ручное решение обязательно; 0 — заготовка, не прогноз."
                )
            elif len(selected) < (4 if body.method == "B1" else 1):
                warnings.append(
                    "Неполная история: среднее по доступным подходящим дням."
                )
            items.append(
                dict(
                    **product,
                    forecast_mean=mean,
                    recommended_qty=qty,
                    confirmed_qty=None,
                    method=body.method if selected else "manual",
                    warnings=warnings,
                    used_dates=[r["date"] for r in selected],
                    n=len(selected),
                )
            )
        plan = {
            "id": str(uuid.uuid4()),
            "revision": 0,
            "status": "draft",
            "dataset_id": body.dataset_id,
            "target_date": str(body.target_date),
            "cutoff": str(body.cutoff),
            "source": dataset["source"],
            "dataset_version": dataset["version"],
            "config_version": VERSION,
            "config_hash": hashlib.sha256(
                json.dumps(CATALOG, sort_keys=True).encode()
            ).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
            "mode": body.mode,
            "timezone": "Asia/Qyzylorda",
        }
        encoded = json.dumps(plan)
        con.execute("INSERT INTO plans VALUES (?,?)", (plan["id"], encoded))
        con.execute("INSERT INTO revisions VALUES (?,?,?)", (plan["id"], 0, encoded))
        return plan


@app.get("/api/plans")
def plans():
    with connection() as con:
        return [
            json.loads(r[0])
            for r in con.execute("SELECT payload FROM plans ORDER BY rowid DESC")
        ]


@app.get("/api/plans/{ident}")
def get_plan(ident: str):
    with connection() as con:
        return fetch(con, "plans", ident)


@app.post("/api/plans/{ident}/confirm")
def confirm(ident: str, body: ConfirmRequest):
    encoded_body = json.dumps(body.model_dump(), sort_keys=True)
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute(
            "SELECT body,response FROM confirmations WHERE plan_id=? AND key=?",
            (ident, body.idempotency_key),
        ).fetchone()
        if previous:
            if previous[0] != encoded_body:
                raise HTTPException(
                    409, "Ключ повторного запроса использован с другими данными"
                )
            return json.loads(previous[1])
        plan = fetch(con, "plans", ident)
        if body.revision != plan["revision"]:
            raise HTTPException(409, "План изменён. Перезагрузите актуальную версию")
        if set(body.quantities) != {p["product_id"] for p in plan["items"]}:
            raise HTTPException(422, "Требуются количества всех пяти продуктов")
        for item in plan["items"]:
            q = body.quantities[item["product_id"]]
            if (
                q < 0
                or q > item["max_daily_qty"] // item["batch_size"] * item["batch_size"]
                or q % item["batch_size"]
            ):
                raise HTTPException(
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
        con.execute("UPDATE plans SET payload=? WHERE id=?", (payload, ident))
        con.execute(
            "INSERT INTO revisions VALUES (?,?,?)", (ident, plan["revision"], payload)
        )
        con.execute(
            "INSERT INTO confirmations VALUES (?,?,?,?)",
            (ident, body.idempotency_key, encoded_body, payload),
        )
        return plan


@app.get("/api/plans/{ident}/revisions")
def revisions(ident: str):
    with connection() as con:
        fetch(con, "plans", ident)
        return [
            json.loads(r[0])
            for r in con.execute(
                "SELECT payload FROM revisions WHERE plan_id=? ORDER BY revision",
                (ident,),
            )
        ]


@app.get("/api/plans/{ident}/export")
def export(ident: str):
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
        ]
        writer.writerow(
            [
                "'" + v
                if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                else v
                for v in values
            ]
        )
    return Response(
        "\ufeff" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="plan-{ident}.csv"'},
    )
