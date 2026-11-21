"""Branch-local order overrides and observed sales, on immutable confirmed snapshots."""

from collections import Counter
from datetime import date
import json
from typing import Literal

from fastapi import APIRouter

from backend.branch_access import current_branch, require_branch
from backend.config import business_today
from backend.errors import ServiceError
from backend.procurement_planning import has_order_suggestion, pack_adjustment
from backend.storage import connection, fetch

router = APIRouter()


@router.get("/api/reports/order-adjustments")
def order_adjustments(
    bakery_id: str,
    start: date,
    end: date,
    mode: Literal["operational", "replay"] = "operational",
    source: Literal["local", "external", "synthetic"] = "local",
    actual_dataset_id: str | None = None,
):
    require_branch(bakery_id)
    if end < start:
        raise ServiceError(422, "Конец периода должен быть не раньше начала.")
    seen = set()
    reasons = Counter()
    actuals = {}
    eligible = adjusted = matched = 0
    total_error = total_bias = 0.0
    with connection() as con:
        con.execute("BEGIN")
        if actual_dataset_id:
            dataset = fetch(con, "datasets", actual_dataset_id)
            if (
                dataset.get("bakery_id"),
                dataset.get("source"),
                dataset.get("target"),
                dataset.get("unit"),
            ) != (
                bakery_id,
                source,
                "sold_units",
                "physical_unit",
            ):
                raise ServiceError(
                    422,
                    "Для сверки выберите продажи в штуках того же филиала и источника.",
                )
            # The current business day may still be incomplete.
            today = business_today().isoformat()
            actuals = {
                (r["date"], r["product_id"]): r["sold"]
                for r in dataset["rows"]
                if r.get("is_open") is not False
                and type(r.get("sold")) is int
                and start.isoformat() <= r["date"] <= end.isoformat()
                and r["date"] < today
            }
        owner_column = "branch_id" if current_branch() is not None else "json_extract(payload,'$.bakery_id')"
        for (payload,) in con.execute(
            f"""
            SELECT payload FROM plans WHERE status='confirmed' AND target_date BETWEEN ? AND ?
            AND source=? AND {owner_column}=?
            AND json_extract(payload,'$.planning_kind')='procurement' AND json_extract(payload,'$.mode')=?
            ORDER BY coalesce(json_extract(payload,'$.confirmed_at'),created_at) DESC, rowid DESC
        """,
            (start.isoformat(), end.isoformat(), source, bakery_id, mode),
        ):
            plan = json.loads(payload)
            for item in plan["items"]:
                key = (plan["target_date"], item["product_id"])
                if key in seen:
                    continue
                # Only the latest confirmed decision for a product on this date counts.
                seen.add(key)
                quantity = item.get("confirmed_qty")
                if item.get("target_is_open") is False:
                    continue
                forecast = item.get("forecast_mean")
                if forecast is not None and key in actuals:
                    error = forecast - actuals[key]
                    matched += 1
                    total_error += abs(error)
                    total_bias += error
                if quantity is None or not has_order_suggestion(item):
                    continue
                eligible += 1
                if pack_adjustment(item, quantity):
                    adjusted += 1
                    reasons[item.get("override_reason") or "unspecified"] += 1
    return {
        "bakery_id": bakery_id,
        "start": start,
        "end": end,
        "source": source,
        "mode": mode,
        "confirmed_product_days": len(seen),
        "comparable_product_days": eligible,
        "adjusted_product_days": adjusted,
        "adjustment_rate": adjusted / eligible if eligible else None,
        "reason_counts": dict(sorted(reasons.items())),
        "actual_dataset_id": actual_dataset_id,
        "matched_sales_days": matched,
        "sales_mae_units": total_error / matched if matched else None,
        "sales_bias_units": total_bias / matched if matched else None,
    }
