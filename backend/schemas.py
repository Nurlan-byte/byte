from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, StrictInt


class ImportRequest(BaseModel):
    csv_text: str = Field(max_length=1_000_000)
    source: Literal["synthetic", "local", "external"]
    schema_version: Literal["history-1"] = "history-1"


class PlanRequest(BaseModel):
    dataset_id: str
    target_date: date
    cutoff: date
    method: Literal["B0", "B1"] = "B1"
    mode: Literal["replay", "operational"] = "operational"
    target_is_open: bool = True


class ConfirmRequest(BaseModel):
    revision: StrictInt
    idempotency_key: str = Field(min_length=1, max_length=100)
    quantities: dict[str, StrictInt]
