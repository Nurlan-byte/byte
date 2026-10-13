"""HTTP adapter for pure calculations and transactional workflows."""

import json
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from backend.config import CATALOG, business_today
from backend.imports import CsvValidationError, parse_csv as parse_history
from backend.imports import REQUIRED as REQUIRED
from backend.schemas import (
    ImportRequest,
    PlanRequest,
    ConfirmRequest,
    TrainRequest,
    ForecastRequest,
)
from backend.dataset_identity import identity
from backend.ml_service import train_dataset, models_for_dataset, create_forecast
from backend.services import (
    ServiceError,
    import_history,
    create_plan as build_plan,
    confirm_plan,
    export_csv,
)
from backend.storage import (
    NotFound,
    initialize_database,
    connection,
    fetch,
    get_dataset,
    get_plan,
    list_datasets,
    list_plans,
)


@asynccontextmanager
async def lifespan(app):
    initialize_database()
    yield


app = FastAPI(title="BakePlan", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def error_response(status, message, details=None, code=None):
    return JSONResponse(
        {
            "error": {
                "code": code or f"HTTP_{status}",
                "message": message,
                "details": details,
            }
        },
        status_code=status,
    )


@app.exception_handler(ServiceError)
@app.exception_handler(StarletteHTTPException)
async def service_error(request, exc):
    return error_response(exc.status_code, str(exc.detail))


@app.exception_handler(CsvValidationError)
async def csv_error(request, exc):
    return error_response(
        422, "CSV отклонён: исправьте ошибки", exc.errors, "INVALID_CSV"
    )


@app.exception_handler(NotFound)
async def not_found(request, exc):
    return error_response(404, "Объект не найден")


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    details = [{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return error_response(422, "Проверьте поля запроса", details, "INVALID_REQUEST")


@app.middleware("http")
async def limit_body(request, call_next):
    if request.method == "POST":
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2_000_000:
                return error_response(
                    413,
                    "Запрос превышает допустимый размер 2 МБ",
                    code="PAYLOAD_TOO_LARGE",
                )
            chunks.append(chunk)
        request._body = b"".join(chunks)
    return await call_next(request)


def parse_csv(text):
    return parse_history(
        text,
        allowed_product_ids={p["product_id"] for p in CATALOG},
        latest_date=business_today(),
    )


@app.get("/api/health")
def health():
    try:
        with connection() as con:
            con.execute("SELECT 1")
    except OSError, sqlite3.Error:
        raise HTTPException(503, "Хранилище недоступно") from None
    return {"status": "ok", "app_version": "0.3.0", "db_status": "ok"}


@app.get("/api/catalog")
def catalog():
    return CATALOG


@app.post("/api/datasets")
def import_dataset(body: ImportRequest, response: Response):
    dataset, created = import_history(body, business_today())
    response.status_code = 201 if created else 200
    return dataset


@app.get("/api/datasets")
def datasets(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return list_datasets(limit, offset)


@app.get("/api/datasets/{ident}")
def dataset_detail(ident: str):
    return identity(get_dataset(ident))


@app.post("/api/datasets/{ident}/train")
def train(ident: str, body: TrainRequest, response: Response):
    model, created = train_dataset(ident, body, business_today())
    response.status_code = 201 if created else 200
    return model


@app.get("/api/datasets/{ident}/models")
def model_status(ident: str):
    return models_for_dataset(ident)


@app.post("/api/forecasts")
def forecast(body: ForecastRequest):
    return create_forecast(body, business_today())


@app.post("/api/plans", status_code=201)
def create_plan(body: PlanRequest):
    return build_plan(body, business_today())


@app.get("/api/plans")
def plans(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return list_plans(limit, offset)


@app.get("/api/plans/{ident}")
def plan_detail(ident: str):
    return get_plan(ident)


@app.post("/api/plans/{ident}/confirm")
def confirm(ident: str, body: ConfirmRequest):
    return confirm_plan(ident, body, business_today())


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
    return Response(
        export_csv(ident),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="plan-{ident}.csv"'},
    )
