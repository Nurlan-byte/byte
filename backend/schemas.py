from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, StrictInt, model_validator


class ImportRequest(BaseModel):
    csv_text: str = Field(max_length=1_000_000)
    source: Literal["synthetic", "local", "external"]
    schema_version: Literal["history-1", "sales-1"] = "history-1"
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
                if self.schema_version == "history-1"
                else ("item_line_count", "item_line")
            )
            if (self.target, self.unit) != expected:
                raise ValueError(
                    "Целевая величина и единица не соответствуют CSV-схеме"
                )
        elif self.schema_version != "history-1":
            raise ValueError(
                "Для sales-1 необходимо явно указать происхождение и единицу item_line"
            )
        return self


class PlanRequest(BaseModel):
    dataset_id: str
    target_date: date
    cutoff: date
    method: Literal["B0", "B1", "ML"] = "B1"
    model_id: str | None = None
    mode: Literal["replay", "operational"] = "operational"
    target_is_open: bool = True


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
