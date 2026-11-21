"""Immutable, dated demand context. Each sample reads only records known at its issue cutoff."""

from collections import defaultdict
from datetime import date
import hashlib
import json
from math import nan
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CONTEXT_FEATURE_NAMES = (
    "weekend",
    "day_off",
    "public_holiday",
    "latest_temperature_c",
    "recent_temperature_change_c",
    "latest_precipitation_mm",
    "forecast_temperature_c",
    "forecast_temperature_change_c",
    "forecast_precipitation_mm",
)


class DatedContextRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    available_on: date


class CalendarDay(DatedContextRecord):
    day_type: Literal["holiday", "day_off", "workday"]


class WeatherDay(DatedContextRecord):
    kind: Literal["observation", "forecast"]
    temperature_c: float | None = Field(default=None, strict=True, ge=-100, le=65)
    precipitation_mm: float | None = Field(default=None, strict=True, ge=0, le=2500)

    @model_validator(mode="after")
    def check_availability(self):
        if self.kind == "observation" and self.available_on < self.date:
            raise ValueError("Факт погоды не мог быть доступен раньше измерения")
        if self.kind == "forecast" and self.available_on > self.date:
            raise ValueError("Прогноз погоды должен быть выпущен до конца целевого дня")
        if self.temperature_c is None and self.precipitation_mm is None:
            raise ValueError("Нужна температура или количество осадков")
        return self


class DemandContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
        ]
        | None
    ) = None
    calendar_source: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
        ]
        | None
    ) = None
    weather_source: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
        ]
        | None
    ) = None
    calendar: list[CalendarDay] = Field(default_factory=list, max_length=10_000)
    weather: list[WeatherDay] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def distinct_editions(self):
        calendar_keys = {(row.date, row.available_on) for row in self.calendar}
        weather_keys = {(row.date, row.kind, row.available_on) for row in self.weather}
        if len(self.calendar) != len(calendar_keys) or len(self.weather) != len(
            weather_keys
        ):
            raise ValueError("Повтор даты, типа и даты доступности в контексте")
        return self

    def canonical(self):
        data = self.model_dump(mode="json", exclude_none=True)
        data["calendar"].sort(key=lambda row: (row["date"], row["available_on"]))
        data["weather"].sort(
            key=lambda row: (row["date"], row["kind"], row["available_on"])
        )
        return data


def with_demand_context(dataset, context):
    """Preserve the sales snapshot identity while deriving a new immutable content version."""
    base = dataset.get("demand_context_base_version", dataset["version"])
    data = DemandContext.model_validate(context).canonical()
    encoded = json.dumps(
        {"base_version": base, "demand_context": data}, sort_keys=True
    ).encode()
    version = hashlib.sha256(encoded).hexdigest()
    return {
        **dataset,
        "demand_context": data,
        "demand_context_base_version": base,
        "version": version,
        "content_hash": version,
    }


class ContextIndex:
    """Group vintages by day once for the entire batch of products."""

    def __init__(self, snapshot):
        data = DemandContext.model_validate(snapshot)
        self.calendar = defaultdict(list)
        self.observations = defaultdict(list)
        self.forecasts = defaultdict(list)
        for row in sorted(data.calendar, key=lambda r: r.available_on):
            self.calendar[row.date.toordinal()].append(row)
        for row in sorted(data.weather, key=lambda r: r.available_on):
            index = self.observations if row.kind == "observation" else self.forecasts
            index[row.date.toordinal()].append(row)

    @staticmethod
    def _known(index, ordinal, cutoff):
        return next(
            (
                entry
                for entry in reversed(index.get(ordinal, ()))
                if entry.available_on <= cutoff
            ),
            None,
        )

    def features(self, target, cutoff):
        ordinal = target.toordinal()
        calendar = self._known(self.calendar, ordinal, cutoff)
        day_type = calendar.day_type if calendar else None
        weekend = target.weekday() >= 5
        forecast = self._known(self.forecasts, ordinal, cutoff)
        forecast_temp = forecast.temperature_c if forecast else None
        forecast_rain = forecast.precipitation_mm if forecast else None
        # Find the most recent available temperature and rainfall independently.
        # Missing days remain missing; do not carry a stale observation indefinitely.
        end = min(cutoff.toordinal(), ordinal - 1)
        observed = [
            (n, self._known(self.observations, n, cutoff))
            for n in range(end, max(end - 14, 0), -1)
        ]
        recent = [(n, row) for n, row in observed if row and end - n < 7]
        last_temp = next(
            (
                (n, row.temperature_c)
                for n, row in recent
                if row.temperature_c is not None
            ),
            None,
        )
        last_rain = next(
            (
                row.precipitation_mm
                for _, row in recent
                if row.precipitation_mm is not None
            ),
            nan,
        )
        prior = (
            [
                row.temperature_c
                for n, row in observed
                if row
                and row.temperature_c is not None
                and last_temp[0] - 7 <= n < last_temp[0]
            ]
            if last_temp
            else []
        )
        reference_temp = last_temp[1] if last_temp else None
        if reference_temp is None and forecast_temp is not None:
            # An archive of issued forecasts may lack observations; compare to
            # an earlier target day's forecast that was already known at cutoff.
            for n in range(end, max(end - 7, 0), -1):
                previous = self._known(self.forecasts, n, cutoff)
                if previous is not None and previous.temperature_c is not None:
                    reference_temp = previous.temperature_c
                    break
        return [
            int(weekend),
            int(day_type != "workday") if calendar else int(weekend),
            int(day_type == "holiday") if calendar else nan,
            last_temp[1] if last_temp else nan,
            last_temp[1] - sum(prior) / len(prior) if prior else nan,
            last_rain,
            forecast_temp if forecast_temp is not None else nan,
            forecast_temp - reference_temp
            if forecast_temp is not None and reference_temp is not None
            else nan,
            forecast_rain if forecast_rain is not None else nan,
        ]
