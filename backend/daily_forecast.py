"""Build the daily forecast for an immutable sales snapshot as of its business date."""

from datetime import date, timedelta

from backend.errors import ServiceError
from backend.ml_core import protocol_for_dataset
from backend.ml_service import forecast_from_dataset, train_dataset, uses_automatic_sales_forecast
from backend.schemas import TrainRequest


def forecast_for_day(dataset: dict, today: date):
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
                dataset["id"],
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
    result = forecast_from_dataset(dataset, target, cutoff, method, model_id)
    return {
        "result": result,
        "targetDate": str(target),
        "cutoff": str(cutoff),
        "method": method,
        "warnings": warnings,
    }
