"""Bounded frozen-protocol sales ML comparison; no operational integration."""

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
from tools.evaluate_sales import metric, sales_features
from tools.ml_candidates import FEATURES, fit_candidate, predict_candidate
from tools.modeling import build_poisson_pipeline
from tools.sales_adapter import (
    MAX_SOURCE_BYTES,
    build_daily,
    read_breadbasket,
    select_items,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "tools/ml_improvement_protocol.json"
REFERENCE_METHODS = ("B0", "B1", "priorPoisson")
CANDIDATE_IDS = (
    "ridge_residual_a100",
    "ridge_residual_a10",
    "hgb_residual_d2",
    "hgb_residual_d3",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_protocol():
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8-sig"))
    if (
        len(protocol["candidates"]) != 4
        or tuple(spec["id"] for spec in protocol["candidates"]) != CANDIDATE_IDS
        or len({spec["family"] for spec in protocol["candidates"]}) != 2
        or protocol["candidate_budget"]["maximum_fits"] != 36
        or protocol["candidate_budget"]["new_configurations"] != 4
        or protocol["candidate_budget"]["new_families"] != 2
        or not protocol["candidate_budget"]["no_grid_search_or_followup_retuning"]
    ):
        raise ValueError("Frozen protocol candidate budget/configurations mismatch")
    if (
        protocol["train_end"] != "2017-01-31"
        or protocol["validation_start"] != "2017-02-01"
        or protocol["validation_end"] != "2017-02-28"
        or protocol["test_start"] != "2017-03-01"
        or protocol["test_end"] != "2017-04-09"
        or len(protocol["cv_folds"]) != 4
    ):
        raise ValueError("Frozen protocol temporal split mismatch")
    for i, fold in enumerate(protocol["cv_folds"]):
        expected_start = date(2017, 2, 1) + timedelta(days=7 * i)
        if fold != {
            "start": expected_start.isoformat(),
            "end": (expected_start + timedelta(days=6)).isoformat(),
            "train_end": (expected_start - timedelta(days=1)).isoformat(),
        }:
            raise ValueError("Frozen rolling CV folds mismatch")
    return protocol


class FitBudget:
    def __init__(self, maximum):
        self.maximum, self.log = maximum, []

    def record(self, name, policy, fit_end):
        if len(self.log) >= self.maximum:
            raise ValueError("Frozen maximum fit budget exhausted")
        self.log.append(
            {"name": name, "policy": policy, "fit_end": fit_end.isoformat()}
        )


def finite(value):
    return value is not None and math.isfinite(value)


def fit_reference(observations, fit_end, policy, budget):
    train = [o for o in observations if o.date <= fit_end]
    index = {(o.item, o.date): o for o in observations}
    budget.record("priorPoisson", policy, fit_end)
    model = build_poisson_pipeline()
    model.fit(
        [sales_features(o, index, o.date - timedelta(days=1)) for o in train],
        [o.item_line_count for o in train],
    )
    return model


def reference_records(observations, model, start, end):
    targets = [o for o in observations if start <= o.date <= end]
    index = {(o.item, o.date): o for o in observations}
    rows = [
        {"date": o.date, "product_id": o.item, "item_line_count": o.item_line_count}
        for o in observations
    ]
    predictions = (
        model.predict(
            [sales_features(o, index, o.date - timedelta(days=1)) for o in targets]
        )
        if targets
        else []
    )
    records = []
    for target, prediction in zip(targets, predictions, strict=True):
        values = {
            method: baseline(
                rows,
                product_id=target.item,
                target_date=target.date,
                cutoff=target.date - timedelta(days=1),
                method=method,
                eligible=lambda _: True,
                value_key="item_line_count",
            )
            for method in ("B0", "B1")
        }
        forecasts = {method: values[method]["forecast_mean"] for method in ("B0", "B1")}
        forecasts["priorPoisson"] = float(prediction)
        records.append(
            {
                "date": target.date.isoformat(),
                "item": target.item,
                "actual": target.item_line_count,
                "inferred_zero": target.inferred_zero,
                "cutoff": (target.date - timedelta(days=1)).isoformat(),
                "forecasts": forecasts,
                "common": all(finite(v) for v in forecasts.values()),
                "baseline_used_dates": {
                    method: values[method]["used_dates"] for method in ("B0", "B1")
                },
            }
        )
    return records


def summarize(records, cohort, methods):
    return {
        "n": len(records),
        "paired_dates_per_item": {
            item: len({r["date"] for r in records if r["item"] == item})
            for item in cohort
        },
        "pooled": {
            method: metric([(r["actual"], r["forecasts"][method]) for r in records])
            for method in methods
        },
        "per_item": {
            item: {
                method: metric(
                    [
                        (r["actual"], r["forecasts"][method])
                        for r in records
                        if r["item"] == item
                    ]
                )
                for method in methods
            }
            for item in cohort
        },
    }


def reference_summary(records, cohort):
    common = [r for r in records if r["common"]]
    return {
        **summarize(common, cohort, REFERENCE_METHODS),
        "candidate_n": len(records),
        "common_key_excluded_n": len(records) - len(common),
        "finite_coverage": {
            method: sum(finite(r["forecasts"][method]) for r in records) / len(records)
            if records
            else None
            for method in REFERENCE_METHODS
        },
        "predictions": records,
    }


def fit_named(spec, observations, fit_end, policy, cohort, budget):
    budget.record(spec["id"], policy, fit_end)
    try:
        fitted = fit_candidate(spec, observations, fit_end, cohort)
        return fitted, None
    except (ValueError, TypeError, ArithmeticError) as error:
        return None, str(error)


def candidate_score(fitted, fit_error, observations, references, cohort):
    index = {(o.item, o.date): o for o in observations}
    records = []
    for reference in references:
        if not reference["common"]:
            continue
        target = index[(reference["item"], date.fromisoformat(reference["date"]))]
        prediction = predict_candidate(
            fitted, target, index, target.date - timedelta(days=1)
        )
        value = prediction["value"]
        reason = prediction["reason"]
        raw_finite = bool(prediction["raw_finite"]) and finite(value)
        fallback = bool(prediction["used_fallback"])
        if not finite(value):
            value, raw_finite, fallback, reason = (
                reference["forecasts"]["B1"],
                False,
                True,
                "invalid candidate output; B1 fallback",
            )
        record = dict(reference)
        record["forecasts"] = {**reference["forecasts"], "candidate": float(value)}
        record.update(
            raw_finite=raw_finite,
            used_fallback=fallback,
            fallback_reason=fit_error or reason if fallback else None,
        )
        records.append(record)
    reasons = Counter(r["fallback_reason"] for r in records if r["used_fallback"])
    return {
        **summarize(records, cohort, ("candidate", *REFERENCE_METHODS)),
        "raw_finite_n": sum(r["raw_finite"] for r in records),
        "raw_finite_coverage": sum(r["raw_finite"] for r in records) / len(records)
        if records
        else None,
        "fallback_n": sum(r["used_fallback"] for r in records),
        "fallback_reasons": dict(sorted(reasons.items())),
        "fit_error": fit_error,
        "fit_metadata": fitted.metadata if fitted is not None else None,
        "predictions": records,
    }


def relative_gain(candidate_mae, baseline_mae):
    if candidate_mae is None or baseline_mae is None:
        return None
    if baseline_mae == 0:
        return 0.0 if candidate_mae == 0 else None
    return (baseline_mae - candidate_mae) / baseline_mae


def select_candidate(results, protocol):
    rules = protocol["selection"]
    decisions = {}
    eligible = []
    for spec in protocol["candidates"]:
        result = results[spec["id"]]
        validation, sensitivity = result["validation"], result["missing_validation"]
        folds = result["cv"]
        pooled = [r for fold in folds for r in fold["predictions"]]
        cv_metrics = summarize(pooled, protocol["cohort"], ("candidate", "B1"))
        cv_gain = relative_gain(
            cv_metrics["pooled"]["candidate"]["mae"], cv_metrics["pooled"]["B1"]["mae"]
        )
        val_gain = relative_gain(
            validation["pooled"]["candidate"]["mae"], validation["pooled"]["B1"]["mae"]
        )
        wins = sum(
            fold["pooled"]["candidate"]["mae"] is not None
            and fold["pooled"]["candidate"]["mae"] < fold["pooled"]["B1"]["mae"]
            for fold in folds
        )
        item_gates = {}
        for item in protocol["cohort"]:
            candidate_mae = validation["per_item"][item]["candidate"]["mae"]
            b1_mae = validation["per_item"][item]["B1"]["mae"]
            item_gates[item] = (
                candidate_mae is not None
                and b1_mae is not None
                and candidate_mae
                <= (
                    rules["zero_B1_per_item_absolute_tolerance"]
                    if b1_mae == 0
                    else b1_mae
                    * (1 + rules["maximum_validation_per_item_relative_degradation"])
                )
            )
        missing_mae, missing_b1 = (
            sensitivity["pooled"]["candidate"]["mae"],
            sensitivity["pooled"]["B1"]["mae"],
        )
        gates = {
            "cv_wins": wins >= rules["minimum_cv_wins"],
            "cv_gain": cv_gain is not None
            and cv_gain >= rules["minimum_pooled_cv_gain_vs_B1"],
            "validation_gain": val_gain is not None
            and val_gain >= rules["minimum_validation_gain_vs_B1"],
            "raw_finite_coverage": all(
                score["raw_finite_coverage"] is not None
                and score["raw_finite_coverage"] >= rules["minimum_raw_finite_coverage"]
                for score in [*folds, validation, sensitivity]
            ),
            "per_item_degradation": all(item_gates.values()),
            "missing_validation_guard": missing_mae is not None
            and missing_b1 is not None
            and missing_mae
            <= missing_b1
            * (1 + rules["maximum_missing_validation_relative_degradation"]),
            "paired_dates": all(
                n >= rules["minimum_paired_dates_per_item"]
                for score in (*folds, validation, sensitivity)
                for n in score["paired_dates_per_item"].values()
            ),
        }
        passed = all(gates.values())
        decisions[spec["id"]] = {
            "eligible": passed,
            "gates": gates,
            "per_item_gates": item_gates,
            "cv_wins": wins,
            "cv_gain_vs_B1": cv_gain,
            "validation_gain_vs_B1": val_gain,
            "pooled_cv": cv_metrics,
        }
        if passed:
            eligible.append(spec)
    if not eligible:
        return {
            "selected": "B1",
            "reason": "No candidate passed all frozen gates; retain unchanged B1, no claimed ML win.",
            "decisions": decisions,
        }
    best_mae = min(
        results[spec["id"]]["validation"]["pooled"]["candidate"]["mae"]
        for spec in eligible
    )
    ties = [
        spec
        for spec in eligible
        if results[spec["id"]]["validation"]["pooled"]["candidate"]["mae"]
        <= best_mae * (1 + rules["tie_within_best_relative_mae"])
    ]
    selected = min(ties, key=lambda spec: spec["complexity_order"])
    return {
        "selected": selected["id"],
        "reason": "Eligible validation-MAE winner with frozen 1% simpler-model tie rule.",
        "eligible_ids": [spec["id"] for spec in eligible],
        "tie_ids": [spec["id"] for spec in ties],
        "decisions": decisions,
    }


def compare_sales(lines, *, source, dataset_sha, history_mode, protocol=None):
    if (
        source not in {"synthetic-sales-only", "external-sales-only"}
        or history_mode != "rolling-one-day-ahead"
    ):
        raise ValueError("Explicit source and rolling-one-day-ahead history required")
    protocol = load_protocol() if protocol is None else protocol
    train_end = date.fromisoformat(protocol["train_end"])
    cohort = protocol["cohort"]
    base = {
        "version": protocol["version"],
        "source": source,
        "dataset_sha256": dataset_sha,
        "protocol_sha256": digest(PROTOCOL_PATH),
        "protocol": protocol,
        "history_mode": history_mode,
        "selected_items": cohort,
        "features": list(FEATURES),
        "code_sha256": {
            name: digest(ROOT / name)
            for name in (
                "tools/compare_sales_ml.py",
                "tools/ml_candidates.py",
                "tools/modeling.py",
                "tools/evaluate_sales.py",
                "backend/forecasting.py",
                "tools/sales_adapter.py",
            )
        },
        "test_status": protocol["test_status"],
        "limitations": [
            "Sales proxy item-line counts; no demand, physical pieces, stockout, monetary savings or operational plan claims.",
            "Synthetic experiments establish code execution only; external-store results do not establish local bakery accuracy.",
        ],
    }
    try:
        selected = select_items(lines, train_end=train_end, n=5, min_coverage=0.5)
    except ValueError as error:
        return {
            **base,
            "status": "refused",
            "reason": str(error),
            "selected": "B1",
            "fits_n": 0,
        }
    if set(selected) != set(cohort):
        return {
            **base,
            "status": "refused",
            "reason": "Original-training selected cohort differs from frozen cohort",
            "selected": "B1",
            "fits_n": 0,
        }
    observations = {
        policy: sorted(
            [
                o
                for o in build_daily(lines, cohort, zero_policy=policy)
                if o.date <= date.fromisoformat(protocol["test_end"])
            ],
            key=lambda o: (o.date, o.item),
        )
        for policy in ("observed-store-zero", "missing")
    }
    for policy, obs in observations.items():
        training = [o for o in obs if o.date <= train_end]
        counts = Counter(o.item for o in training)
        if len({o.date for o in training}) < 28 or any(
            counts[item] < 14 for item in cohort
        ):
            return {
                **base,
                "status": "refused",
                "reason": f"Insufficient original training observations: {policy}",
                "selected": "B1",
                "fits_n": 0,
            }
    budget = FitBudget(protocol["candidate_budget"]["maximum_fits"])
    reference_cv, models, reference_validation = [], {}, {}
    for fold in protocol["cv_folds"]:
        model = fit_reference(
            observations["observed-store-zero"],
            date.fromisoformat(fold["train_end"]),
            "observed-store-zero",
            budget,
        )
        records = reference_records(
            observations["observed-store-zero"],
            model,
            date.fromisoformat(fold["start"]),
            date.fromisoformat(fold["end"]),
        )
        reference_cv.append(records)
    for policy, obs in observations.items():
        models[policy] = fit_reference(obs, train_end, policy, budget)
        reference_validation[policy] = reference_records(
            obs,
            models[policy],
            date.fromisoformat(protocol["validation_start"]),
            date.fromisoformat(protocol["validation_end"]),
        )
    for policy, records in reference_validation.items():
        summary = reference_summary(records, cohort)
        if any(
            n < protocol["selection"]["minimum_paired_dates_per_item"]
            for n in summary["paired_dates_per_item"].values()
        ):
            return {
                **base,
                "status": "refused",
                "reason": f"Insufficient fixed reference validation keys: {policy}",
                "selected": "B1",
                "reference_validation": {
                    p: reference_summary(r, cohort)
                    for p, r in reference_validation.items()
                },
                "fits_n": len(budget.log),
                "fit_log": budget.log,
            }
    results, fitted_validation = {}, {}
    for spec in protocol["candidates"]:
        cv = []
        for fold, references in zip(protocol["cv_folds"], reference_cv, strict=True):
            fitted, error = fit_named(
                spec,
                observations["observed-store-zero"],
                date.fromisoformat(fold["train_end"]),
                "observed-store-zero",
                cohort,
                budget,
            )
            cv.append(
                candidate_score(
                    fitted,
                    error,
                    observations["observed-store-zero"],
                    references,
                    cohort,
                )
            )
        full = {}
        for policy in observations:
            fitted, error = fit_named(
                spec, observations[policy], train_end, policy, cohort, budget
            )
            fitted_validation[(spec["id"], policy)] = (fitted, error)
            full[policy] = candidate_score(
                fitted,
                error,
                observations[policy],
                reference_validation[policy],
                cohort,
            )
        results[spec["id"]] = {
            "config": spec,
            "cv": cv,
            "validation": full["observed-store-zero"],
            "missing_validation": full["missing"],
        }
    selection = select_candidate(results, protocol)
    test = {}
    start, end = (
        date.fromisoformat(protocol["test_start"]),
        date.fromisoformat(protocol["test_end"]),
    )
    midpoint = start + timedelta(days=((end - start).days + 1) // 2)
    for policy, obs in observations.items():
        references = reference_records(obs, models[policy], start, end)
        score = reference_summary(references, cohort)
        chosen = selection["selected"]
        if chosen != "B1":
            fitted, error = fitted_validation[(chosen, policy)]
            score = candidate_score(fitted, error, obs, references, cohort)
            methods = ("candidate", *REFERENCE_METHODS)
            records = score["predictions"]
        else:
            methods = REFERENCE_METHODS
            records = [r for r in references if r["common"]]
        sufficient = all(
            n >= protocol["selection"]["minimum_paired_dates_per_item"]
            for n in score["paired_dates_per_item"].values()
        )
        test[policy] = {
            **score,
            "status": "evaluated" if sufficient else "refused",
            "selected": chosen,
            "interpretation": protocol["test_status"],
            "fit_end": train_end.isoformat(),
            "halves": {
                "first": summarize(
                    [r for r in records if date.fromisoformat(r["date"]) < midpoint],
                    cohort,
                    methods,
                ),
                "second": summarize(
                    [r for r in records if date.fromisoformat(r["date"]) >= midpoint],
                    cohort,
                    methods,
                ),
            },
            "second_half_start": midpoint.isoformat(),
        }
    return {
        **base,
        "status": "evaluated"
        if all(s["status"] == "evaluated" for s in test.values())
        else "refused",
        "fits_n": len(budget.log),
        "fit_log": budget.log,
        "candidate_results": results,
        "reference_cv": [reference_summary(r, cohort) for r in reference_cv],
        "reference_validation": {
            p: reference_summary(r, cohort) for p, r in reference_validation.items()
        },
        "selection": selection,
        "test": test,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--source",
        required=True,
        choices=["external-sales-only", "synthetic-sales-only"],
    )
    parser.add_argument(
        "--history-mode", required=True, choices=["rolling-one-day-ahead"]
    )
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    try:
        if args.csv.stat().st_size > MAX_SOURCE_BYTES:
            parser.error("Sales CSV exceeds20MB limit")
        dataset_sha = digest(args.csv)
        if args.expected_sha256 and dataset_sha != args.expected_sha256:
            parser.error("Source SHA256 mismatch")
        protocol = load_protocol()
        if (
            args.source == "external-sales-only"
            and dataset_sha != protocol["dataset_sha256"]
        ):
            parser.error("External dataset differs from frozen protocol source")
        report = compare_sales(
            read_breadbasket(args.csv),
            source=args.source,
            dataset_sha=dataset_sha,
            history_mode=args.history_mode,
            protocol=protocol,
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
