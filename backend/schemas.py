from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, StrictInt, model_validator


class ProcurementProduct(BaseModel):
    product_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    pack_size: StrictInt = Field(ge=1, le=1_000_000_000)
    max_order_qty: StrictInt | None = Field(default=None, ge=0, le=1_000_000_000)


class ImportRequest(BaseModel):
    csv_text: str = Field(max_length=1_000_000)
    source: Literal["synthetic", "local", "external"]
    schema_version: Literal["history-1", "sales-1", "procurement-1"] = "history-1"
    catalog: list[ProcurementProduct] | None = Field(default=None, max_length=50)
    bakery_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    target: Literal["sold_units", "item_line_count"] | None = None
    unit: Literal["physical_unit", "item_line"] | None = None
    provenance: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def consistent_identity(self):
        fields = (self.bakery_id, self.target, self.unit, self.provenance)
        if any(value is not None for value in fields):
            if any(value is None or not value.strip() for value in fields):
                raise ValueError(
                    "Укажите пекарню, целевую величину, единицу и происхождение данных вместе"
                )
            expected = (
                ("sold_units", "physical_unit")
                if self.schema_version in {"history-1", "procurement-1"}
                else ("item_line_count", "item_line")
            )
            if (self.target, self.unit) != expected:
                raise ValueError(
                    "Целевая величина и единица не соответствуют CSV-схеме"
                )
        elif self.schema_version == "procurement-1":
            raise ValueError("procurement-1 requires explicit identity and provenance")
        elif self.schema_version != "history-1":
            raise ValueError(
                "Для sales-1 необходимо явно указать происхождение и единицу item_line"
            )
        if self.schema_version == "procurement-1":
            if not self.catalog or len({p.product_id for p in self.catalog}) != len(self.catalog):
                raise ValueError("procurement-1 requires a unique, nonempty product catalog")
        elif self.catalog is not None:
            raise ValueError("catalog is supported only for procurement-1")
        return self


class PlanRequest(BaseModel):
    dataset_id: str
    target_date: date
    cutoff: date
    method: Literal["B0", "B1", "ML"] = "B1"
    model_id: str | None = None
    mode: Literal["replay", "operational"] = "operational"
    target_is_open: bool = True
    lead_time_days: StrictInt | None = Field(default=None, ge=1, le=365)
    usable_stock_assumption: dict[str, StrictInt] | None = None

    @model_validator(mode="after")
    def nonnegative_stock(self):
        if self.usable_stock_assumption is not None and any(
            value < 0 or value > 1_000_000_000
            for value in self.usable_stock_assumption.values()
        ):
            raise ValueError("usable_stock_assumption must contain nonnegative units")
        return self


class ConfirmRequest(BaseModel):
    revision: StrictInt
    idempotency_key: str = Field(min_length=1, max_length=100)
    quantities: dict[str, StrictInt]


class TrainRequest(BaseModel):
    cutoff: date
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"\S")


class ForecastRequest(BaseModel):
    dataset_id: str
    target_date: date
    cutoff: date
    method: Literal["B1", "ML"] = "ML"
    model_id: str | None = None
