"""Offline retrospective experiment. Never used to confirm an operational plan."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

VERSION = "offline-poisson-v1"
QUANTITIES = ("opening", "produced", "sold", "waste", "closing", "other_out")


def read_rows(path: Path) -> list[dict]:
    """Strict whole-file validation; IDs remain strings and missing days remain absent."""
    rows, seen = [], set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "product_id", "is_open", "stockout_status", *QUANTITIES}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Missing required CSV columns")
        for line, raw in enumerate(reader, 2):
            row = dict(raw)
            try:
                row["date"] = date.fromisoformat(raw["date"])
                if not raw["product_id"] or raw["is_open"].lower() not in {"0", "1", "true", "false"}:
                    raise ValueError("Invalid ID or is_open (expected 0/1/true/false)")
                row["is_open"] = raw["is_open"].lower() in {"1", "true"}
                if raw["stockout_status"] not in {"none", "occurred", "unknown"}:
                    raise ValueError("Invalid stockout_status")
                for field in QUANTITIES:
                    value = raw[field]
                    if not value.isascii() or not value.isdigit():
                        raise ValueError(f"{field} must be a nonnegative integer")
                    row[field] = int(value)
                if row["opening"] + row["produced"] != sum(row[f] for f in ("sold", "waste", "closing", "other_out")):
                    raise ValueError("Inventory balance mismatch")
                key = row["product_id"], row["date"]
                if key in seen:
                    raise ValueError("Duplicate product/date")
                seen.add(key)
                rows.append(row)
            except (ValueError, TypeError) as error:
                raise ValueError(f"CSV line {line}: {error}") from error
    return sorted(rows, key=lambda r: (r["date"], r["product_id"]))


def suitable(row: dict) -> bool:
    return row["is_open"] and row["stockout_status"] == "none" and row["opening"] == 0


def features(row: dict, index: dict) -> list:
    """Calendar lags, never previous-row lags. Historical observations only."""
    lag_values = []
    for lag in (7, 14):
        prior = index.get((row["product_id"], row["date"] - timedelta(days=lag)))
        lag_values.append(prior["sold"] if prior and suitable(prior) else float("nan"))
    return [row["product_id"], row["date"].weekday(), *lag_values]


def baseline(row: dict, index: dict, method: str) -> float | None:
    previous = []
    for lag in range(7, 57, 7):
        prior = index.get((row["product_id"], row["date"] - timedelta(days=lag)))
        if prior and suitable(prior):
            previous.append(prior["sold"])
        if method == "B0":
            return float(previous[0]) if previous else None
        if len(previous) == 4:
            break
    return sum(previous) / len(previous) if previous else None


def evaluate(rows: list[dict], source: str, dataset_sha: str, split_date: date) -> dict:
    report = {
        "version": VERSION, "dataset_sha256": dataset_sha, "source": source,
        "split_date": split_date.isoformat(), "mode": "historical_replay_only",
        "warning": "Synthetic metrics are test evidence only; no real prediction quality or savings claim.",
        "assumptions": ["All lag observations available before target day; no intraday forecast.",
                        "Opening inventory zero; open, known non-stockout rows only.",
                        "Sold quantity is an imperfect demand proxy.",
                        "Holdout uses rolling observed history, without retraining."],
    }
    usable = [r for r in rows if suitable(r)]
    train = [r for r in usable if r["date"] < split_date]
    test = [r for r in usable if r["date"] >= split_date]
    report.update(train_n=len(train), holdout_n=len(test), excluded_n=len(rows) - len(usable))
    counts = Counter(r["product_id"] for r in train)
    if len({r["date"] for r in train}) < 28 or len({r["date"] for r in test}) < 7 or any(counts[r["product_id"]] < 14 for r in test) or not test:
        return {**report, "status": "refused", "reason": "Need >=28 training dates, >=7 later holdout dates and >=14 training rows per holdout product."}
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import PoissonRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    index = {(r["product_id"], r["date"]): r for r in rows}
    numeric = Pipeline([("impute", SimpleImputer(strategy="constant", fill_value=0, add_indicator=True)), ("scale", StandardScaler())])
    preprocess = ColumnTransformer([("category", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [0, 1]), ("numeric", numeric, [2, 3])])
    model = Pipeline([("preprocess", preprocess), ("model", PoissonRegressor(alpha=1.0, max_iter=500))])
    if sum(r["sold"] for r in train) == 0:
        return {**report, "status": "refused", "reason": "All training targets zero; use explicit manual/rule handling."}
    model.fit([features(r, index) for r in train], [r["sold"] for r in train])
    predictions = model.predict([features(r, index) for r in test])
    results = {}
    for method in ("Poisson", "B0", "B1"):
        pairs = [(r["sold"], float(predictions[i]) if method == "Poisson" else baseline(r, index, method)) for i, r in enumerate(test)]
        available = [(actual, pred) for actual, pred in pairs if pred is not None and math.isfinite(pred)]
        results[method] = {"n": len(available), "unavailable_n": len(pairs) - len(available), "mae": sum(abs(a - p) for a, p in available) / len(available) if available else None}
    return {**report, "status": "evaluated", "metrics": results, "config": {"alpha": 1.0, "lags_days": [7, 14], "fit_transform_scope": "train_only"}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--source", choices=["synthetic", "local-real", "external"], required=True)
    parser.add_argument("--split-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = read_rows(args.csv)
    report = evaluate(rows, args.source, hashlib.sha256(args.csv.read_bytes()).hexdigest(), args.split_date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["status"])


if __name__ == "__main__":
    main()
