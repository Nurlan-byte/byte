"""Wire formats for persisted production plans and supplier orders."""

from typing import Literal, NotRequired, TypedDict

from pydantic import ConfigDict, with_config

from backend.forecast_contract import EvaluationApplicability, ForecastSelection
from backend.schemas import ExpectedDelivery


class CountedDelivery(ExpectedDelivery):
    id: str
    number: str


OrderConstraint = Literal["supplier_limit", "system_limit"]
OverrideReason = Literal["forecast", "stock", "event", "supply", "other"]
ForecastNumber = int | float


# Confirmation can return an existing, immutable receipt. Keep extension fields
# in snapshots and use NotRequired so absent fields stay absent on the wire.
@with_config(ConfigDict(extra="allow", strict=True))
class PlanItemResponse(TypedDict):
    product_id: str
    name: str
    method: str
    forecast_mean: ForecastNumber | None
    recommended_qty: int
    confirmed_qty: int | None
    warnings: NotRequired[list[str]]
    used_dates: NotRequired[list[str]]
    n: NotRequired[int]
    eligible_weekday_n: NotRequired[int]
    automatic_eligible: NotRequired[bool]
    diagnostic_method: NotRequired[str]
    target_is_open: NotRequired[bool]
    model_id: NotRequired[str | None]
    fallback_reason: NotRequired[str | None]
    evaluation_applicability: NotRequired[EvaluationApplicability | None]
    demand_features: NotRequired[dict[str, ForecastNumber]]
    b1_mean: NotRequired[ForecastNumber | None]
    reason: NotRequired[str | None]
    selection: NotRequired[ForecastSelection]

    # Production catalog and eligibility diagnostics.
    batch_size: NotRequired[int]
    max_daily_qty: NotRequired[int]
    active_from: NotRequired[str | None]
    active_to: NotRequired[str | None]
    price: NotRequired[ForecastNumber | None]
    cost: NotRequired[ForecastNumber | None]
    observed_open_n: NotRequired[int]
    deficit_or_unknown_n: NotRequired[int]

    # Supplier packs, availability and the separately confirmed decision.
    pack_size: NotRequired[int]
    max_order_qty: NotRequired[int | None]
    usable_stock_assumption: NotRequired[int | None]
    incoming_units: NotRequired[int]
    available_stock_units: NotRequired[int | None]
    unknown_open_n: NotRequired[int]
    unknown_stockout_n: NotRequired[int]
    recommended_packs: NotRequired[int]
    max_constraint_active: NotRequired[bool]
    shortage_units: NotRequired[int | None]
    constraint_reason: NotRequired[OrderConstraint | None]
    confirmed_packs: NotRequired[int]
    confirmed_shortage_units: NotRequired[int | None]
    confirmed_constraint_reason: NotRequired[OrderConstraint | None]
    override_reason: NotRequired[OverrideReason]


@with_config(ConfigDict(extra="allow", strict=True))
class PlanResponse(TypedDict):
    id: str
    revision: int
    status: Literal["draft", "confirmed"]
    dataset_id: str
    source: str
    target_date: str
    cutoff: str
    items: list[PlanItemResponse]
    planning_kind: NotRequired[Literal["production", "procurement"]]
    mode: NotRequired[Literal["replay", "operational"]]
    bakery_id: NotRequired[str]
    target: NotRequired[Literal["sold_units"]]
    unit: NotRequired[Literal["physical_unit"]]
    provenance: NotRequired[str]
    requested_method: NotRequired[Literal["AUTO", "ML", "B0", "B1"]]
    model_id: NotRequired[str | None]
    protocol_version: NotRequired[str | None]
    dataset_version: NotRequired[str]
    dataset_schema_version: NotRequired[str]
    config_version: NotRequired[str]
    config_hash: NotRequired[str]
    created_at: NotRequired[str]
    confirmed_at: NotRequired[str]
    timezone: NotRequired[str]
    availability_assumption: NotRequired[str]
    order_date: NotRequired[str]
    lead_time_days: NotRequired[int]
    usable_stock_assumption: NotRequired[dict[str, int]]
    delivery_source: NotRequired[Literal["registry", "manual"]]
    expected_deliveries: NotRequired[list[ExpectedDelivery]]
    registry_deliveries: NotRequired[list[CountedDelivery]]
