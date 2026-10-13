"""Frozen sales-only historical experiment; transaction item-lines are not demand."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.forecasting import baseline
from tools.modeling import build_poisson_pipeline
from tools.sales_adapter import (
    MAX_SOURCE_BYTES,
    build_daily,
    read_breadbasket,
    select_items,
)

VERSION = "sales-only-frozen-v1"
METHODS = ("Poisson", "B0", "B1")
ZERO_POLICIES = ("observed-store-zero", "missing")


def sales_features(observation, index: dict, cutoff: date) -> list:
    """Strict shifted calendar lags; never read a target or future observation."""
    values = []
    for lag in (7, 14):
        day = observation.date - timedelta(days=lag)
        previous = index.get((observation.item, day)) if day <= cutoff else None
        values.append(
            previous.item_line_count if previous is not None else float("nan")
        )
    return [observation.item, observation.date.weekday(), *values]


def metric(pairs: list[tuple[int, float]]) -> dict:
    total = sum(actual for actual, _ in pairs)
    error = sum(abs(actual - predicted) for actual, predicted in pairs)
    return {
        "n": len(pairs),
        "actual_sum": total,
        "mae": error / len(pairs) if pairs else None,
        "wape": error / total if total else None,
        "wape_definition": "sum(abs(actual-predicted))/sum(actual); ratio, null at zero denominator",
    }


def _finite(value) -> bool:
    return value is not None and math.isfinite(value)


def evaluate_policy(
    lines,
    selected_items: list[str],
    *,
    zero_policy: str,
    validation_start: date,
    test_start: date,
    test_end: date,
) -> dict:
    train_end = validation_start - timedelta(days=1)
    observations = sorted(
        build_daily(lines, selected_items, zero_policy=zero_policy),
        key=lambda observation: (observation.date, observation.item),
    )
    observations = [o for o in observations if o.date <= test_end]
    train = [o for o in observations if o.date <= train_end]
    validation = [o for o in observations if validation_start <= o.date < test_start]
    test = [o for o in observations if test_start <= o.date <= test_end]
    train_counts = Counter(o.item for o in train)
    result = {
        "zero_policy": zero_policy,
        "train_n": len(train),
        "train_dates_n": len({o.date for o in train}),
        "train_rows_per_item": dict(sorted(train_counts.items())),
        "validation_candidates_n": len(validation),
        "test_candidates_n": len(test),
        "inferred_zero_n": sum(o.inferred_zero for o in observations),
    }
    if (
        len(selected_items) != 5
        or len({o.date for o in train}) < 28
        or len({o.date for o in validation}) < 7
        or len({o.date for o in test}) < 7
        or any(train_counts[item] < 14 for item in selected_items)
        or not sum(o.item_line_count for o in train)
    ):
        return {
            **result,
            "status": "refused",
            "reason": "Need five training-positive selected items, >=28 training dates, >=14 training observations per item, and >=7 dates in both validation and test.",
        }
    index = {(o.item, o.date): o for o in observations}
    model = build_poisson_pipeline()
    model.fit(
        [sales_features(o, index, o.date - timedelta(days=1)) for o in train],
        [o.item_line_count for o in train],
    )
    baseline_rows = [
        {"date": o.date, "product_id": o.item, "item_line_count": o.item_line_count}
        for o in observations
    ]
    first_train = {
        item: min(o.date for o in train if o.item == item and o.item_line_count > 0)
        for item in selected_items
    }
    periods = {}
    for name, candidates, start, end in (
        ("validation", validation, validation_start, test_start - timedelta(days=1)),
        ("test", test, test_start, test_end),
    ):
        predicted = model.predict(
            [sales_features(o, index, o.date - timedelta(days=1)) for o in candidates]
        )
        records = []
        for i, observation in enumerate(candidates):
            cut = observation.date - timedelta(days=1)
            baselines = {
                method: baseline(
                    baseline_rows,
                    product_id=observation.item,
                    target_date=observation.date,
                    cutoff=cut,
                    method=method,
                    eligible=lambda _: True,
                    value_key="item_line_count",
                )
                for method in ("B0", "B1")
            }
            predictions = {
                "Poisson": float(predicted[i]),
                **{
                    method: entry["forecast_mean"]
                    for method, entry in baselines.items()
                },
            }
            records.append(
                {
                    "date": observation.date.isoformat(),
                    "item": observation.item,
                    "actual": observation.item_line_count,
                    "inferred_zero": observation.inferred_zero,
                    "cutoff": cut.isoformat(),
                    "predictions": predictions,
                    "baseline_used_dates": {
                        method: entry["used_dates"]
                        for method, entry in baselines.items()
                    },
                    "compared": all(_finite(value) for value in predictions.values()),
                }
            )
        common = [r for r in records if r["compared"]]
        paired_dates = {
            item: len({r["date"] for r in common if r["item"] == item})
            for item in selected_items
        }
        sufficient = all(count >= 7 for count in paired_dates.values())
        store_days = {line.date for line in lines if start <= line.date <= end}
        expected_grid = sum(
            day >= first_train[item] for day in store_days for item in selected_items
        )
        availability = {
            method: sum(_finite(r["predictions"][method]) for r in records)
            for method in METHODS
        }
        periods[name] = {
            "status": "evaluated" if sufficient else "refused",
            "reason": None
            if sufficient
            else "Need >=7 shared paired forecast dates per selected item.",
            "metrics_interpretation": "paired historical sales-proxy comparison"
            if sufficient
            else "diagnostic only; insufficient paired dates",
            "paired_dates_per_item": paired_dates,
            "raw_item_lines_n": sum(start <= line.date <= end for line in lines),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "observed_store_dates_n": len(store_days),
            "missing_global_calendar_dates_n": (end - start).days + 1 - len(store_days),
            "eligible_item_store_day_slots_n": expected_grid,
            "candidate_n": len(records),
            "absent_item_unknown_excluded_n": expected_grid - len(records),
            "candidate_coverage": len(records) / expected_grid
            if expected_grid
            else None,
            "method_available_n": availability,
            "method_candidate_coverage": {
                method: n / len(records) if records else None
                for method, n in availability.items()
            },
            "comparison_n": len(common),
            "comparison_excluded_n": len(records) - len(common),
            "comparison_rule": "Identical date/item intersection with finite Poisson, B0, B1 forecasts",
            "pooled": {
                method: metric(
                    [(r["actual"], r["predictions"][method]) for r in common]
                )
                for method in METHODS
            },
            "per_item": {
                item: {
                    method: metric(
                        [
                            (r["actual"], r["predictions"][method])
                            for r in common
                            if r["item"] == item
                        ]
                    )
                    for method in METHODS
                }
                for item in selected_items
            },
            "predictions": records,
        }
    return {
        **result,
        "status": "evaluated"
        if all(period["status"] == "evaluated" for period in periods.values())
        else "refused",
        "periods": periods,
    }


def evaluate_sales(
    lines,
    *,
    dataset_sha: str,
    history_mode: str,
    source: str = "external-sales-only",
    validation_start: date = date(2017, 2, 1),
    test_start: date = date(2017, 3, 1),
    test_end: date = date(2017, 4, 9),
) -> dict:
    if source not in {"external-sales-only", "synthetic-sales-only"}:
        raise ValueError("source must be external-sales-only or synthetic-sales-only")
    if history_mode != "rolling-one-day-ahead":
        raise ValueError("Explicit rolling-one-day-ahead history mode is required")
    if not validation_start < test_start <= test_end:
        raise ValueError("Dates must satisfy validation_start < test_start <= test_end")
    train_end = validation_start - timedelta(days=1)
    selection_error = None
    try:
        items = select_items(lines, train_end=train_end, n=5, min_coverage=0.5)
    except ValueError as error:
        items, selection_error = [], str(error)
    result = {
        "version": VERSION,
        "dataset_sha256": dataset_sha,
        "source": source,
        "target": "item_line_count",
        "history_mode": history_mode,
        "selected_items": items,
        "raw_item_lines_n": len(lines),
        "observed_store_dates_n": len({line.date for line in lines}),
        "source_date_start": min(line.date for line in lines).isoformat()
        if lines
        else None,
        "source_date_end": max(line.date for line in lines).isoformat()
        if lines
        else None,
        "raw_period_counts": {
            name: {
                "item_lines_n": len(subset),
                "observed_dates_n": len({line.date for line in subset}),
            }
            for name, subset in (
                ("train", [line for line in lines if line.date <= train_end]),
                (
                    "validation",
                    [
                        line
                        for line in lines
                        if validation_start <= line.date < test_start
                    ],
                ),
                (
                    "test",
                    [line for line in lines if test_start <= line.date <= test_end],
                ),
            )
        },
        "protocol": {
            "train_end": train_end.isoformat(),
            "validation_start": validation_start.isoformat(),
            "validation_end": (test_start - timedelta(days=1)).isoformat(),
            "test_start": test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "selection": "Frozen food allowlist; training positive-day coverage descending, lexical ties; min coverage 0.5; top five",
            "model": "Shared PoissonRegressor alpha=1.0; categorical item/weekday; shifted calendar lag7/lag14; train-only imputer/scaler/encoder",
            "fit_scope": "Train only, frozen for validation and test; no retuning or refitting",
            "availability": "Daily item-line totals assumed available after day end; previous validation/test days may be used for later targets",
            "primary_zero_policy": "observed-store-zero",
            "sensitivity_zero_policy": "missing",
        },
        "limitations": [
            "Transaction item-lines are a sales proxy, not integer pieces or unconstrained demand.",
            "No inventory, production, waste, opening schedule or stockout observations are supplied.",
            "Observed-store-zero is an explicit assumption, not a verified zero sale or open-day fact.",
            "Missing global dates remain missing; absent items in sensitivity remain unknown.",
            "Historical external-store evaluation does not establish local bakery accuracy or savings.",
            "No operational plan, confirmed quantities, economic claims or model upload is produced.",
        ],
    }
    protocol_payload = {
        key: result[key]
        for key in ("protocol", "selected_items", "source", "history_mode")
    }
    result["protocol_sha256"] = hashlib.sha256(
        json.dumps(
            protocol_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    if len(items) != 5:
        return {
            **result,
            "status": "refused",
            "reason": selection_error
            or "Fewer than five training-positive food items meet frozen selection coverage.",
        }
    policies = {
        policy: evaluate_policy(
            lines,
            items,
            zero_policy=policy,
            validation_start=validation_start,
            test_start=test_start,
            test_end=test_end,
        )
        for policy in ZERO_POLICIES
    }
    return {
        **result,
        "status": "evaluated"
        if all(p["status"] == "evaluated" for p in policies.values())
        else "refused",
        "policies": policies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        required=True,
        type=Path,
        help="External sales-only corrected BreadBasket CSV; transaction item-lines, not demand",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--source",
        choices=["external-sales-only", "synthetic-sales-only"],
        required=True,
    )
    parser.add_argument(
        "--expected-sha256",
        help="Optional frozen source hash; mismatch rejects input before fitting",
    )
    parser.add_argument(
        "--validation-start", type=date.fromisoformat, default=date(2017, 2, 1)
    )
    parser.add_argument(
        "--test-start", type=date.fromisoformat, default=date(2017, 3, 1)
    )
    parser.add_argument("--test-end", type=date.fromisoformat, default=date(2017, 4, 9))
    parser.add_argument(
        "--history-mode", choices=["rolling-one-day-ahead"], required=True
    )
    args = parser.parse_args()
    try:
        if args.csv.stat().st_size > MAX_SOURCE_BYTES:
            parser.error("Sales-only CSV exceeds20MB limit")
        dataset_sha = hashlib.sha256(args.csv.read_bytes()).hexdigest()
        if args.expected_sha256 and dataset_sha != args.expected_sha256:
            parser.error("Source SHA256 mismatch")
        report = evaluate_sales(
            read_breadbasket(args.csv),
            dataset_sha=dataset_sha,
            history_mode=args.history_mode,
            source=args.source,
            validation_start=args.validation_start,
            test_start=args.test_start,
            test_end=args.test_end,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        ),
        encoding="utf-8",
    )
    print(report["status"])
    if report["status"] == "refused":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
