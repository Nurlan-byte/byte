"""Offline retrospective experiment. Never used to confirm an operational plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

# Script execution adds tools/, so explicitly make the repository package importable.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.forecasting import baseline as shared_baseline
from backend.forecasting import suitable as shared_suitable
from backend.imports import parse_csv

VERSION = "offline-poisson-v1"


def read_rows(path: Path) -> list[dict]:
    """Shared validation with arbitrary nonempty string IDs for offline experiments."""
    rows = parse_csv(path.read_text(encoding="utf-8-sig"))
    for row in rows:
        row["date"] = date.fromisoformat(row["date"])
    return sorted(rows, key=lambda r: (r["date"], r["product_id"]))


def suitable(row: dict) -> bool:
    return shared_suitable(row) and row["opening"] == 0


def features(row: dict, index: dict) -> list:
    """Calendar lags, never previous-row lags. Historical observations only."""
    lag_values = []
    for lag in (7, 14):
        prior = index.get((row["product_id"], row["date"] - timedelta(days=lag)))
        lag_values.append(prior["sold"] if prior and suitable(prior) else float("nan"))
    return [row["product_id"], row["date"].weekday(), *lag_values]


def baseline(row: dict, index: dict, method: str) -> float | None:
    result = shared_baseline(
        index.values(),
        product_id=row["product_id"],
        target_date=row["date"],
        cutoff=row["date"] - timedelta(days=1),
        method=method,
        eligible=suitable,
    )
    return result["forecast_mean"]


def evaluate(rows: list[dict], source: str, dataset_sha: str, split_date: date) -> dict:
    report = {
        "version": VERSION,
        "dataset_sha256": dataset_sha,
        "source": source,
        "split_date": split_date.isoformat(),
        "mode": "historical_replay_only",
        "warning": "Synthetic metrics are test evidence only; no real prediction quality or savings claim.",
        "assumptions": [
            "All lag observations available before target day; no intraday forecast.",
            "Opening inventory zero; open, known non-stockout rows only.",
            "Sold quantity is an imperfect demand proxy.",
            "Holdout uses rolling observed history, without retraining.",
        ],
    }
    usable = [r for r in rows if suitable(r)]
    train = [r for r in usable if r["date"] < split_date]
    test = [r for r in usable if r["date"] >= split_date]
    report.update(
        train_n=len(train), holdout_n=len(test), excluded_n=len(rows) - len(usable)
    )
    counts = Counter(r["product_id"] for r in train)
    if (
        len({r["date"] for r in train}) < 28
        or len({r["date"] for r in test}) < 7
        or any(counts[r["product_id"]] < 14 for r in test)
        or not test
    ):
        return {
            **report,
            "status": "refused",
            "reason": "Need >=28 training dates, >=7 later holdout dates and >=14 training rows per holdout product.",
        }
    from tools.modeling import build_poisson_pipeline

    index = {(r["product_id"], r["date"]): r for r in rows}
    model = build_poisson_pipeline()
    if sum(r["sold"] for r in train) == 0:
        return {
            **report,
            "status": "refused",
            "reason": "All training targets zero; use explicit manual/rule handling.",
        }
    model.fit([features(r, index) for r in train], [r["sold"] for r in train])
    predictions = model.predict([features(r, index) for r in test])
    results = {}
    for method in ("Poisson", "B0", "B1"):
        pairs = [
            (
                r["sold"],
                float(predictions[i])
                if method == "Poisson"
                else baseline(r, index, method),
            )
            for i, r in enumerate(test)
        ]
        available = [
            (actual, pred)
            for actual, pred in pairs
            if pred is not None and math.isfinite(pred)
        ]
        results[method] = {
            "n": len(available),
            "unavailable_n": len(pairs) - len(available),
            "mae": sum(abs(a - p) for a, p in available) / len(available)
            if available
            else None,
        }
    return {
        **report,
        "status": "evaluated",
        "metrics": results,
        "config": {
            "alpha": 1.0,
            "lags_days": [7, 14],
            "fit_transform_scope": "train_only",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument(
        "--source", choices=["synthetic", "local-real", "external"], required=True
    )
    parser.add_argument("--split-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = read_rows(args.csv)
    report = evaluate(
        rows,
        args.source,
        hashlib.sha256(args.csv.read_bytes()).hexdigest(),
        args.split_date,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report["status"])


if __name__ == "__main__":
    main()
