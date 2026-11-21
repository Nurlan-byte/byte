"""Wire types for forecasts from the automatic policy and historical models."""
from datetime import date
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import Field, with_config


# The service returns ISO strings. Validate them as dates and retain their JSON representation.
ForecastDate = Annotated[date, Field(strict=False)]


@with_config(extra="forbid", strict=True)
class EvaluationApplicability(TypedDict):
    applicable: bool
    reason: str | None
    evaluated_horizon_days: int | None
    requested_horizon_days: int


@with_config(extra="forbid", strict=True)
class ForecastSelection(TypedDict):
    observed_days: int
    zero_days: int
    validation_pairs: int
    b1_mae: float | None
    chronos_mae: float | None


@with_config(extra="forbid", strict=True)
class ForecastItem(TypedDict):
    product_id: str
    method: Literal["Chronos-2", "B1", "ML", "manual"]
    forecast_mean: float | None
    b1_mean: float | None
    n: int
    used_dates: list[ForecastDate]
    eligible_weekday_n: NotRequired[int]
    selection: NotRequired[ForecastSelection]
    reason: NotRequired[str | None]
    fallback_reason: NotRequired[str | None]
    model_id: NotRequired[str]
    evaluation_applicability: NotRequired[EvaluationApplicability]
    demand_features: NotRequired[dict[str, int | float]]


@with_config(extra="forbid", strict=True)
class ForecastResponse(TypedDict):
    dataset_id: str
    dataset_version: str
    bakery_id: str
    target: Literal["sold_units", "item_line_count"]
    unit: Literal["physical_unit", "item_line"]
    source: str
    provenance: str
    target_date: ForecastDate
    cutoff: ForecastDate
    model_id: str | None
    items: list[ForecastItem]
    protocol_version: NotRequired[str]
    evaluation_applicability: NotRequired[EvaluationApplicability]
    warning: str
