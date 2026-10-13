"""Frozen integrated-model evaluation; data independence must be checked by people."""

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import CATALOG
from backend.dataset_identity import parse_sales_csv
from backend.imports import parse_csv
from backend.ml_core import TARGET_UNITS, _label, forecast_model, train_model

HORIZON_DAYS = 2


def evaluate(dataset, training_cutoff, start, end):
    """Freeze parameters before the first issue cutoff, then roll observed history.

    Neither later labels nor this report select a model, alpha, or parameters.
    The internal training holdout and this later window are separate reports.
    """
    if (
        start.toordinal() <= HORIZON_DAYS
        or start > end
        or training_cutoff > start - timedelta(days=HORIZON_DAYS)
    ):
        raise ValueError(
            "Evaluation must start at least two days after training cutoff"
        )
    model = train_model(dataset, training_cutoff)
    pairs, excluded, eligible, methods = (
        defaultdict(list),
        Counter(),
        Counter(),
        Counter(),
    )
    targets = sorted(
        (row for row in dataset["rows"] if str(start) <= row["date"] <= str(end)),
        key=lambda row: (row["date"], row["product_id"]),
    )
    if not targets:
        raise ValueError("No observed targets in the requested evaluation window")
    for row in targets:
        product = row["product_id"]
        value, reason = _label(row, dataset["target"])
        if reason:
            excluded[reason] += 1
            continue
        eligible[product] += 1
        target = date.fromisoformat(row["date"])
        cutoff = target - timedelta(days=HORIZON_DAYS)
        result = forecast_model(model, dataset, product, target, cutoff)
        methods[result["method"]] += 1
        if result["method"] != "ML" or result["b1_mean"] is None:
            excluded[result.get("fallback_reason") or "no_paired_prediction"] += 1
            continue
        pairs[product].append(
            {
                "date": row["date"],
                "cutoff": str(cutoff),
                "b1_absolute_error": abs(value - result["b1_mean"]),
                "ml_absolute_error": abs(value - result["forecast_mean"]),
            }
        )

    def score(records, denominator):
        n = len(records)
        return {
            "paired_n": n,
            "eligible_observed_n": denominator,
            "coverage": n / denominator if denominator else 0.0,
            "b1_mae": sum(r["b1_absolute_error"] for r in records) / n if n else None,
            "ml_mae": sum(r["ml_absolute_error"] for r in records) / n if n else None,
        }

    combined = [row for records in pairs.values() for row in records]
    products = sorted({row["product_id"] for row in targets})
    return {
        "report_version": "integrated-forward-window-1",
        "protocol_version": model["protocol_version"],
        "target": dataset["target"],
        "unit": dataset["unit"],
        "source": dataset["source"],
        "provenance": dataset["provenance"],
        "dataset_version": dataset["version"],
        "training_cutoff": str(training_cutoff),
        "training_snapshot_hash": model["snapshot_hash"],
        "model_status": model["status"],
        "horizon_days": HORIZON_DAYS,
        "first_issue_cutoff": str(start - timedelta(days=HORIZON_DAYS)),
        "start": str(start),
        "end": str(end),
        "observed_target_rows": len(targets),
        "comparison": "B1 and ML on the exact same eligible product/date pairs",
        "coverage_denominator": "eligible observed target rows; missing dates are not zero",
        "independence": "unverified: a future window must be frozen before outcomes are viewed",
        "warning": "Synthetic/historical runs test procedure only; no bakery accuracy or economic benefit is established.",
        "aggregate": score(combined, sum(eligible.values())),
        "actual_methods": dict(sorted(methods.items())),
        "exclusions_and_unpaired": dict(sorted(excluded.items())),
        "products": {
            product: {
                **score(pairs[product], eligible[product]),
                "pairs": pairs[product],
            }
            for product in products
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument(
        "--source", required=True, choices=["synthetic", "local", "external"]
    )
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--target", default="sold_units", choices=TARGET_UNITS)
    parser.add_argument("--training-cutoff", required=True, type=date.fromisoformat)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        text = args.csv.read_text(encoding="utf-8-sig")
        rows = (
            parse_sales_csv(text, date.max)
            if args.target == "item_line_count"
            else parse_csv(text, allowed_product_ids={p["product_id"] for p in CATALOG})
        )
        digest = hashlib.sha256(text.encode()).hexdigest()
        dataset = {
            "id": "evaluation-" + digest,
            "version": digest,
            "bakery_id": "evaluation",
            "rows": rows,
            "target": args.target,
            "unit": TARGET_UNITS[args.target],
            "source": args.source,
            "provenance": args.provenance,
        }
        report = evaluate(dataset, args.training_cutoff, args.start, args.end)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as error:
        parser.exit(2, f"Evaluation refused: {error}\n")
    print(
        f"Saved {args.output}; paired_n={report['aggregate']['paired_n']}; independence unverified"
    )


if __name__ == "__main__":
    main()
