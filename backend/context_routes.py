from datetime import date, timedelta
from uuid import uuid4

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from backend.config import business_today
from backend.context_sources import CITIES, WEATHER_SOURCE, load_demand_context
from backend.demand_context import with_demand_context
from backend.errors import ServiceError
from backend.ml_core import protocol_for_dataset
from backend.ml_service import create_forecast, train_dataset, uses_automatic_sales_forecast
from backend.schemas import ForecastRequest, TrainRequest
from backend.storage import get_dataset, save_dataset, timestamp

router = APIRouter()


class ContextChoice(BaseModel):
    city: str | None = Field(default=None, max_length=64)


@router.get("/api/context/cities")
def city_choices():
    return [{"id": code, "name": place[0]} for code, place in CITIES.items()]


@router.post("/api/datasets/{ident}/demand-context")
def add_context(ident: str, choice: ContextChoice, response: Response):
    dataset = get_dataset(ident)
    if not dataset.get("metadata_declared"):
        raise ServiceError(
            422, "Для обучения сначала укажите происхождение и единицу истории."
        )
    if choice.city not in {None, "none", "weekday_only"} and choice.city not in CITIES:
        raise ServiceError(422, "Выберите город из списка.")
    days = [date.fromisoformat(row["date"]) for row in dataset["rows"]]
    today = business_today()
    # A historical CSV should keep its own weather period even when loaded years later.
    context, warnings = load_demand_context(
        min(days), max(days) + timedelta(days=2), today, choice.city
    )
    if max(days) + timedelta(days=2) < today + timedelta(days=1):
        start = max(today - timedelta(days=7), max(days) + timedelta(days=3))
        recent, more_warnings = load_demand_context(
            start, today + timedelta(days=1), today, choice.city
        )
        for field in ("weather", "calendar"):
            context[field].extend(recent[field])
        if recent.get("weather_source"):
            context["weather_source"] = recent["weather_source"]
        warnings = list(dict.fromkeys([*warnings, *more_warnings]))
    previous = dataset.get("demand_context", {})
    if (
        context.get("city")
        and previous.get("city") == context["city"]
        and previous.get("weather_source") == WEATHER_SOURCE
        and previous.get("weather")
    ):
        # Preserve known forecast editions when a refresh has gaps or is offline.
        weather = {}
        for row in [*previous["weather"], *context["weather"]]:
            key = (row["date"], row["kind"], row["available_on"])
            weather.setdefault(key, {}).update(
                {field: value for field, value in row.items() if value is not None}
            )
        if len(weather) > len(context["weather"]):
            warnings.append(
                "Для пропусков в обновлении сохранили ранее полученную погоду этого города."
            )
        context.update(weather=list(weather.values()), weather_source=WEATHER_SOURCE)
    snapshot = with_demand_context(dataset, context)
    if snapshot["version"] == dataset["version"]:
        return {"dataset": dataset, "warnings": warnings}
    snapshot.update(id=str(uuid4()), created_at=timestamp())
    result, created = save_dataset(snapshot)
    response.status_code = 201 if created else 200
    return {"dataset": result, "warnings": warnings}


@router.post("/api/datasets/{ident}/first-forecast")
def first_forecast(ident: str):
    return forecast_for_day(ident, business_today())


def forecast_for_day(ident: str, today: date):
    dataset = get_dataset(ident)
    yesterday = today - timedelta(days=1)
    last = max(date.fromisoformat(row["date"]) for row in dataset["rows"])
    cutoff = yesterday if dataset.get("pos_provider") else min(last, yesterday)
    target = cutoff + timedelta(days=2)
    warnings = []
    if target <= today:
        warnings.append(
            "Это исторический расчёт по последней дате загруженного набора."
        )
    model_id = None
    method = "B1"
    if uses_automatic_sales_forecast(dataset):
        method = "AUTO"
    elif dataset.get("metadata_declared"):
        try:
            model, _ = train_dataset(
                ident,
                TrainRequest(
                    cutoff=cutoff,
                    idempotency_key=f"first:{cutoff}:{protocol_for_dataset(dataset)}",
                ),
                today,
            )
            if model["status"] == "ready":
                model_id, method = model["id"], "ML"
            elif any(
                product.get("reason") == "not_better_than_b1_validation"
                for product in model.get("products", {}).values()
            ):
                warnings.append(
                    "На исторической проверке прогноз по похожим дням оказался не хуже."
                )
            else:
                warnings.append(
                    "Для обучения пока недостаточно истории. Использован прогноз по похожим дням."
                )
        except ServiceError as error:
            if error.status_code == 409:
                warnings.append(
                    "Не удалось использовать сохранённую модель. Расчёт выполнен по похожим дням."
                )
            elif error.status_code == 503:
                warnings.append(
                    "Обучение временно недоступно. Для этого расчёта использованы похожие дни."
                )
            else:
                raise
    result = create_forecast(
        ForecastRequest(
            dataset_id=ident,
            target_date=target,
            cutoff=cutoff,
            method=method,
            model_id=model_id,
        ),
        today,
    )
    return {
        "result": result,
        "targetDate": str(target),
        "cutoff": str(cutoff),
        "method": method,
        "warnings": warnings,
    }
