"""Evaluate the first live automatic decision per product-day against sales observed later."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal, TypedDict

from fastapi import APIRouter, Query

from backend.branch_access import require_branch
from backend.config import business_today
from backend.errors import ServiceError
from backend.schemas import error_responses
from backend.storage import connection, NotFound


router = APIRouter()
Source = Literal["local", "synthetic", "external"]
Unit = Literal["physical_unit", "item_line"]


class IssuedMetrics(TypedDict):
    issued_product_days: int
    matched_product_days: int
    model_mae: float | None
    b1_mae: float | None
    model_bias: float | None
    b1_bias: float | None
    overforecast_total: float
    underforecast_total: float


class Exclusions(TypedDict):
    pending: int
    missing_actual: int
    closed: int
    stockout: int
    missing_pair: int


class IssuedForecastReport(IssuedMetrics):
    bakery_id: str
    start: date
    end: date
    source: Source
    unit: Unit
    horizon_days: int
    actual_selection: Literal["selected_dataset", "latest_observation"]
    actual_dataset_id: str | None
    actual_dataset_count: int
    issued_predictions: int
    coverage: float | None
    excluded: Exclusions
    availability_unknown_days: int
    by_product: dict[str, IssuedMetrics]
    by_frequency: dict[str, IssuedMetrics]


@dataclass
class _Metrics:
    issued: int = 0
    paired: int = 0
    over: float = 0.0
    under: float = 0.0
    b1_absolute: float = 0.0
    b1_signed: float = 0.0

    def match(self, model, b1, actual):
        self.paired += 1
        self.over += max(model - actual, 0)
        self.under += max(actual - model, 0)
        self.b1_absolute += abs(b1 - actual)
        self.b1_signed += b1 - actual

    def values(self):
        return {
            "issued_product_days": self.issued, "matched_product_days": self.paired,
            "model_mae": (self.over + self.under) / self.paired if self.paired else None,
            "b1_mae": self.b1_absolute / self.paired if self.paired else None,
            "model_bias": (self.over - self.under) / self.paired if self.paired else None,
            "b1_bias": self.b1_signed / self.paired if self.paired else None,
            "overforecast_total": self.over, "underforecast_total": self.under,
        }


def _sales_origin(con, ident, branch, source, unit):
    origin = con.execute("""
        SELECT source,json_extract(payload,'$.unit'),
               COALESCE(json_extract(payload,'$.actual_sales_dataset_id'),json_extract(payload,'$.source_dataset_id'),id)
        FROM datasets WHERE id=? AND branch_id=?
    """, (ident, branch)).fetchone()
    if origin is None:
        raise NotFound(ident)
    if origin[:2] != (source, unit):
        raise ServiceError(422, "Выберите продажи того же источника и в тех же единицах.")
    return origin[2]


@router.get("/api/reports/issued-forecasts", response_model=IssuedForecastReport,
            responses=error_responses(401, 404, 422))
def issued_forecasts(bakery_id: str, start: date, end: date, source: Source = "local",
                     unit: Unit = "physical_unit", horizon_days: Annotated[int, Query(ge=2, le=1024)] = 2,
                     actual_dataset_id: str | None = None):
    """Compare forecasts issued before the target day using completed history through the cutoff.

    A live issue needs a business day between cutoff and target, so the minimum horizon is two days.
    """
    require_branch(bakery_id)
    if end < start:
        raise ServiceError(422, "Конец периода должен быть не раньше начала.")
    today = business_today().isoformat()
    overall = _Metrics()
    by_product = defaultdict(_Metrics)
    by_frequency = {key: _Metrics() for key in ("regular", "sparse", "unknown")}
    exclusions = Counter(dict(pending=0, missing_actual=0, closed=0, stockout=0, missing_pair=0))
    used = set()
    unknown_days = predictions = 0
    with connection() as con:
        con.execute("BEGIN")
        origin = _sales_origin(con, actual_dataset_id, bakery_id, source, unit) if actual_dataset_id is not None else None
        # The indexes bound both sides by branch, source, unit and target. Only the optional
        # explicit dataset needs a metadata lookup. Historical JSON is never scanned for sales.
        restrict = "AND s.dataset_id=?" if origin is not None else ""
        rows = con.execute(f"""
            WITH cohort AS (
                SELECT target_date,product_id,forecast,b1,frequency,bakery_id,source,unit,
                    ROW_NUMBER() OVER w AS rank, COUNT(*) OVER (PARTITION BY target_date,product_id) AS issues
                FROM issued_forecasts WHERE bakery_id=? AND source=? AND unit=?
                    AND target_date BETWEEN ? AND ? AND cutoff=date(target_date,?) AND issued_on<=?
                WINDOW w AS (PARTITION BY target_date,product_id ORDER BY issued_on,issue_id)
            )
            SELECT f.target_date,f.product_id,f.forecast,f.b1,f.frequency,f.issues,a.dataset_id,a.quantity,a.stockout_status
            FROM cohort f LEFT JOIN forecast_actuals a ON a.observation_id=(
                SELECT s.observation_id FROM forecast_actuals s WHERE s.bakery_id=f.bakery_id AND s.source=f.source
                    AND s.unit=f.unit AND s.target_date=f.target_date AND s.product_id=f.product_id
                    AND s.observed_on<=? {restrict} ORDER BY s.observed_on DESC,s.observation_id DESC LIMIT 1
            ) WHERE f.rank=1 ORDER BY f.target_date,f.product_id
        """, (bakery_id, source, unit, start.isoformat(), end.isoformat(), f"-{horizon_days} days", today, today,
              *((origin,) if origin is not None else ())))
        for day, product, model, b1, frequency, issues, actual_id, actual, stockout in rows:
            predictions += issues
            slices = overall, by_product[product], by_frequency[frequency]
            for group in slices:
                group.issued += 1
            if actual_id is not None:
                used.add(actual_id)
            if day >= today:
                exclusions["pending"] += 1
            elif actual is None:
                exclusions["missing_actual"] += 1
            elif stockout == "closed":
                exclusions["closed"] += 1
            elif stockout == "occurred":
                exclusions["stockout"] += 1
            elif model is None or b1 is None:
                exclusions["missing_pair"] += 1
            else:
                unknown_days += stockout == "unknown"
                for group in slices:
                    group.match(model, b1, actual)
    return {
        "bakery_id": bakery_id, "start": start, "end": end, "source": source, "unit": unit, "horizon_days": horizon_days,
        "actual_selection": "selected_dataset" if actual_dataset_id is not None else "latest_observation",
        "actual_dataset_id": next(iter(used)) if len(used) == 1 else None, "actual_dataset_count": len(used),
        "issued_predictions": predictions, "availability_unknown_days": unknown_days, **overall.values(),
        "coverage": overall.paired / overall.issued if overall.issued else None, "excluded": dict(exclusions),
        "by_frequency": {key: m.values() for key, m in by_frequency.items()},
        "by_product": {key: m.values() for key, m in by_product.items()},
    }
