"""Dataset-scoped train/status/forecast workflows with explicit safe fallback."""

import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from backend.dataset_identity import identity
from backend.errors import ServiceError
from backend.forecasting import baseline
from backend.ml_core import PROTOCOL_VERSION, evaluation_applicability, forecast_model
from backend.model_storage import (
    InvalidArtifact,
    TrainingConflict,
    find_model,
    get_model,
    list_models,
    previous_training,
    save_training,
)
from backend.storage import get_dataset

TRAINING_TIMEOUT_SECONDS = 60
TRAINING_SLOTS = threading.BoundedSemaphore(1)
SUMMARY_FIELDS = (
    "id",
    "status",
    "reason",
    "dataset_id",
    "dataset_version",
    "bakery_id",
    "target",
    "unit",
    "provenance",
    "source",
    "training_cutoff",
    "snapshot_hash",
    "protocol_version",
    "created_at",
    "evaluation",
    "bounds",
    "counts",
    "warnings",
)


def model_summary(model):
    result = {key: model[key] for key in SUMMARY_FIELDS if key in model}
    result["products"] = {
        product: {
            key: value
            for key, value in details.items()
            if key in {"status", "reason", "evaluation", "counts", "exclusions"}
        }
        for product, details in model.get("products", {}).items()
    }
    return result


def models_for_dataset(ident):
    get_dataset(ident)
    try:
        return [model_summary(model) for model in list_models(ident)]
    except InvalidArtifact:
        raise ServiceError(
            409,
            "Сохранённая модель повреждена. Восстановите проверенную резервную копию; прогноз использует B1.",
        ) from None


def train_dataset(ident, body, today):
    if body.cutoff >= today:
        raise ServiceError(
            422, "Обучение допускает только завершённые дни строго раньше сегодня"
        )
    dataset = identity(get_dataset(ident))
    if not dataset["metadata_declared"]:
        raise ServiceError(
            422,
            "Модель не готова: повторно импортируйте CSV с явной пекарней, целевой величиной, единицей и происхождением",
        )
    encoded = json.dumps(
        {"cutoff": str(body.cutoff), "protocol_version": PROTOCOL_VERSION},
        sort_keys=True,
    )
    try:
        previous = previous_training(ident, body.idempotency_key, encoded)
        if previous:
            return model_summary(previous), False
        existing = find_model(ident, body.cutoff, PROTOCOL_VERSION)
        if existing and existing["training_cutoff"] == str(body.cutoff):
            saved, created = save_training(existing, body.idempotency_key, encoded)
            return model_summary(saved), created
        if not TRAINING_SLOTS.acquire(blocking=False):
            raise ServiceError(
                503, "Обучение уже выполняется. Повторите запрос с тем же ключом позже"
            )
        try:
            environment = {
                **os.environ,
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
            }
            result = subprocess.run(
                [sys.executable, "-m", "backend.train_worker"],
                input=json.dumps(
                    {"dataset": dataset, "cutoff": str(body.cutoff)}, allow_nan=False
                ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=TRAINING_TIMEOUT_SECONDS,
                cwd=Path(__file__).resolve().parent.parent,
                env=environment,
            )
            try:
                output = json.loads(result.stdout)
            except ValueError, TypeError:
                raise ServiceError(
                    503,
                    "Обучение не завершено. Проверьте установку зависимостей; B1 доступен",
                ) from None
            if result.returncode or "model" not in output:
                raise ServiceError(
                    422,
                    "Модель не готова: " + str(output.get("error", "ошибка обучения")),
                )
            model = output["model"]
            model.update(
                id=hashlib.sha256(
                    f"{ident}:{body.cutoff}:{PROTOCOL_VERSION}".encode()
                ).hexdigest(),
                source=dataset["source"],
                provenance=dataset["provenance"],
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            saved, created = save_training(model, body.idempotency_key, encoded)
            return model_summary(saved), created
        except subprocess.TimeoutExpired:
            raise ServiceError(
                503,
                "Превышен лимит обучения 60 секунд. Модель не сохранена; уменьшите набор. B1 доступен",
            ) from None
        finally:
            TRAINING_SLOTS.release()
    except TrainingConflict:
        raise ServiceError(
            409, "Ключ обучения уже использован с другими параметрами"
        ) from None
    except InvalidArtifact:
        raise ServiceError(
            409,
            "Сохранённая модель повреждена. Восстановите проверенную резервную копию; B1 доступен",
        ) from None


def _b1(dataset, product, target, cutoff, reason):
    kwargs = (
        {"eligible": lambda _: True, "value_key": "item_line_count"}
        if dataset["target"] == "item_line_count"
        else {}
    )
    value = baseline(
        dataset["rows"],
        product_id=product,
        target_date=target,
        cutoff=cutoff,
        method="B1",
        **kwargs,
    )
    return {
        **value,
        "product_id": product,
        "b1_mean": value["forecast_mean"],
        "method": "B1" if value["forecast_mean"] is not None else "manual",
        "fallback_reason": reason,
    }


def forecast_items(dataset, target, cutoff, model_id=None, method="ML"):
    dataset = identity(dataset)
    model, evaluation_model, reason = None, None, None
    if method == "ML":
        try:
            model = (
                get_model(model_id)
                if model_id
                else find_model(dataset["id"], cutoff, PROTOCOL_VERSION)
            )
        except InvalidArtifact:
            reason = "Сохранённая модель повреждена; используется B1"
        if model is None and reason is None:
            reason = "Нет модели для этого набора и среза; используется B1"
        if model:
            evaluation_model = model
            fields = ("dataset_id", "dataset_version", "bakery_id", "target", "unit")
            expected = (
                dataset["id"],
                dataset["version"],
                dataset["bakery_id"],
                dataset["target"],
                dataset["unit"],
            )
            if tuple(model.get(key) for key in fields) != expected:
                raise ServiceError(
                    422,
                    "Модель принадлежит другому набору, пекарне или единице измерения",
                )
            if date.fromisoformat(model["training_cutoff"]) > cutoff:
                raise ServiceError(
                    422,
                    "Срез модели позже доступных данных: выберите более раннюю модель или B1",
                )
            if model.get("protocol_version") != PROTOCOL_VERSION:
                reason = "Версия протокола модели не поддерживается; используется B1"
                model = None
    items = []
    for product in sorted(
        {row["product_id"] for row in dataset["rows"] if row["date"] <= str(cutoff)}
    ):
        if model:
            try:
                prediction = forecast_model(model, dataset, product, target, cutoff)
            except ValueError, TypeError, KeyError, ArithmeticError:
                prediction = _b1(
                    dataset,
                    product,
                    target,
                    cutoff,
                    "Модель неприменима или повреждена; используется B1",
                )
            items.append({**prediction, "product_id": product})
        else:
            items.append(_b1(dataset, product, target, cutoff, reason))
    applicability = evaluation_applicability(evaluation_model, target, cutoff)
    if method == "B1":
        applicability.update(applicable=False, reason="b1_requested")
    elif applicability["applicable"] and not any(
        item["method"] == "ML" for item in items
    ):
        applicability.update(applicable=False, reason="no_ml_forecasts")
    elif applicability["applicable"] and any(
        not item.get("evaluation_applicability", {}).get("applicable") for item in items
    ):
        applicability.update(applicable=False, reason="partial_product_coverage")
    for item in items:
        if "evaluation_applicability" not in item:
            item["evaluation_applicability"] = {
                **applicability,
                "applicable": False,
                "reason": applicability["reason"] or "forecast_not_ml",
            }
    return {
        "items": items,
        "model_id": model["id"] if model else None,
        "model": model,
        "evaluation_applicability": applicability,
    }


def create_forecast(body, today):
    if body.cutoff >= today or body.cutoff >= body.target_date:
        raise ServiceError(422, "Срез должен быть раньше целевой даты и сегодня")
    dataset = identity(get_dataset(body.dataset_id))
    result = forecast_items(
        dataset, body.target_date, body.cutoff, body.model_id, body.method
    )
    return {
        key: dataset[key]
        for key in ("bakery_id", "target", "unit", "source", "provenance")
    } | {
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
        "target_date": str(body.target_date),
        "cutoff": str(body.cutoff),
        "model_id": result["model_id"],
        "items": result["items"],
        "evaluation_applicability": result["evaluation_applicability"],
        "warning": "Прогноз наблюдаемых продаж; скрытый спрос и экономия не оцениваются. Item-line не переводится в физические штуки.",
    }
