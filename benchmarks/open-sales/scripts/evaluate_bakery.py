"""Repeat the French bakery recorded-sales comparison with BakePlan ac3c2af."""

import argparse
import csv
import hashlib
import io
import json
import random
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

SOURCE_HASH = "af5eede55b6eb2efb6bebb0e6dd1a51568c7eb73554d2f8ad2563081025681b2"
CORE_HASHES = {
    "backend/ml_core.py": "855de87f08b533bdade9f96e1b09e371d63da7b8bd5c77a8b3be41ed42edf3d1",
    "backend/forecasting.py": "82854f8841c836d222b9cae07d40c8eba26bcf7b52bdb1ccf1eb90da7ca16794",
}
UNKNOWN_OR_SERVICE = {"", ".", "UNKNOWN", "COUPE"}
TRAIN_END = date(2022, 6, 30)
START = date(2022, 7, 2)
END = date(2022, 9, 30)


def write_csv(path, values):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(values[0]))
        writer.writeheader()
        writer.writerows(values)


def get_data(path, ranks=slice(0, 6)):
    with zipfile.ZipFile(path) as handle:
        raw = handle.read("Bakery sales.csv")
    if hashlib.sha256(raw).hexdigest() != SOURCE_HASH:
        raise ValueError("Expected original 'Bakery sales.csv' from Kaggle version 1")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    net_units = defaultdict(Counter)
    training_units = Counter()
    first_sale = {}
    adjustments = Counter()
    tickets, indices, articles, raw_dates = set(), set(), set(), set()
    line_count = 0
    for row in reader:
        line_count += 1
        if row[""] in indices:
            raise ValueError("Unexpected duplicate source row index")
        indices.add(row[""])
        sku = row["article"].strip()
        day = date.fromisoformat(row["date"])
        raw_dates.add(day)
        articles.add(sku)
        tickets.add(row["ticket_number"])
        value = float(row["Quantity"])
        if not value.is_integer():
            raise ValueError("Unexpected fractional sales")
        units = int(value)
        if units < 0:
            adjustments["negative_lines"] += 1
            adjustments["negative_units_signed"] += units
        if sku in UNKNOWN_OR_SERVICE:
            continue
        if units > 0 and (sku not in first_sale or day < first_sale[sku]):
            first_sale[sku] = day
        net_units[day][sku] += units
        if day <= TRAIN_END:
            training_units[sku] += units
    ranked = sorted(training_units, key=lambda sku: (-training_units[sku], sku))[ranks]
    active_days = sorted(
        day for day, by_sku in net_units.items() if sum(by_sku.values()) > 0
    )
    signed_negative = []
    daily = []
    for day in active_days:
        for sku in ranked:
            if day < first_sale[sku]:
                continue
            units = net_units[day][sku]
            if units < 0:
                signed_negative.append((str(day), sku))
                continue
            daily.append(
                dict(
                    date=str(day),
                    product_id=sku,
                    sold=units,
                    is_open=True,
                    stockout_status="unknown",
                )
            )
    coverage = dict(
        original_rows=line_count,
        source_sha256=SOURCE_HASH,
        tickets=len(tickets),
        raw_article_labels=len(articles),
        original_first=str(min(raw_dates)),
        original_last=str(max(raw_dates)),
        original_observed_days=len(raw_dates),
        shop_days_with_positive_net_sales=len(active_days),
        selected_training_units={sku: training_units[sku] for sku in ranked},
        selected_first_sale={sku: str(first_sale[sku]) for sku in ranked},
        negative_selected_days_excluded=signed_negative,
        daily_rows=len(daily),
        **adjustments,
    )
    return ranked, daily, coverage


def scores(rows):
    valid = [
        r
        for r in rows
        if r["abs_error_b1"] is not None and r["abs_error_bakeplan"] is not None
    ]
    n = len(valid)
    obs = sum(r["observed_net_units"] for r in valid)
    b1 = sum(r["abs_error_b1"] for r in valid)
    model = sum(r["abs_error_bakeplan"] for r in valid)
    return dict(
        eligible_rows=len(rows),
        paired_rows=n,
        observed_units=obs,
        zero_unit_rows=sum(r["observed_net_units"] == 0 for r in valid),
        ridge_rows=sum(r["method"] == "ML" for r in valid),
        fallback_b1_rows=sum(r["method"] == "B1" for r in valid),
        b1_mae=b1 / n if n else None,
        bakeplan_mae=model / n if n else None,
        delta_mae=model / n - b1 / n if n else None,
        b1_wape_pct=100 * b1 / obs if obs else None,
        bakeplan_wape_pct=100 * model / obs if obs else None,
        error_reduction_pct=100 * (b1 - model) / b1 if b1 else None,
    )


def clustered_interval(rows):
    """Descriptive interval resampling 13 paired seven-calendar-day blocks across SKUs."""
    errors = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        if row["abs_error_b1"] is None or row["abs_error_bakeplan"] is None:
            continue
        week = (date.fromisoformat(row["date"]) - START).days // 7
        errors[week][0] += row["abs_error_b1"]
        errors[week][1] += row["abs_error_bakeplan"]
    blocks = list(errors.values())
    rng = random.Random(20221121)
    values = []
    for _ in range(10000):
        draw = rng.choices(blocks, k=len(blocks))
        a = sum(block[0] for block in draw)
        b = sum(block[1] for block in draw)
        if a:
            values.append(100 * (a - b) / a)
    values.sort()
    return dict(
        blocks=len(blocks),
        draws=len(values),
        seed=20221121,
        error_reduction_pct_percentile_95=[
            values[int(0.025 * (len(values) - 1))],
            values[int(0.975 * (len(values) - 1))],
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", required=True, type=Path, help="BakePlan ac3c2af checkout"
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="matthieugimbert/french-bakery-daily-sales v1 ZIP",
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    for filename, expected in CORE_HASHES.items():
        code = (args.repo / filename).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(code).hexdigest() != expected:
            raise ValueError(f"Unexpected BakePlan source hash: {filename}")
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.forecasting import baseline
    from backend.ml_core import train_model, forecast_model

    skus, daily, details = get_data(args.input)
    data = dict(
        id="french-bakery-v1",
        version="1",
        bakery_id="french-bakery",
        target="sold_units",
        unit="physical_unit",
        rows=daily,
        planning_kind="procurement",
    )
    production = train_model({**data, "planning_kind": "production"}, TRAIN_END)
    planned = sorted(
        (r for r in daily if str(START) <= r["date"] <= str(END)),
        key=lambda r: (r["date"], r["product_id"]),
    )
    predictions = []
    model_cache = {}
    for row in planned:
        target = date.fromisoformat(row["date"])
        cutoff = target - timedelta(days=2)
        training = TRAIN_END + timedelta(days=7 * ((cutoff - TRAIN_END).days // 7))
        if training not in model_cache:
            model_cache[training] = train_model(data, training)
        model = model_cache[training]
        sku, truth = row["product_id"], row["sold"]
        result = forecast_model(model, data, sku, target, cutoff)
        ref = baseline(
            daily,
            product_id=sku,
            target_date=target,
            cutoff=cutoff,
            method="B1",
            eligible=lambda r: r.get("is_open") is not False,
        )
        b1, hybrid = ref["forecast_mean"], result["forecast_mean"]
        if result["b1_mean"] != b1:
            raise ValueError("BakePlan core and operational B1 disagree")
        predictions.append(
            dict(
                date=str(target),
                data_cutoff=str(cutoff),
                training_cutoff=str(training),
                product=sku,
                observed_net_units=truth,
                b1_forecast=b1,
                bakeplan_forecast=hybrid,
                method=result["method"],
                fallback_reason=result["fallback_reason"],
                abs_error_b1=abs(b1 - truth) if b1 is not None else None,
                abs_error_bakeplan=abs(hybrid - truth) if hybrid is not None else None,
            )
        )
    summary = [dict(product="ALL", **scores(predictions))]
    for sku in skus:
        summary.append(
            dict(product=sku, **scores([r for r in predictions if r["product"] == sku]))
        )
    write_csv(args.out / "bakery_forecasts.csv", predictions)
    write_csv(args.out / "bakery_summary.csv", summary)
    details.update(
        train_end=str(TRAIN_END),
        test_start=str(START),
        test_end=str(END),
        core_commit="ac3c2af5a4535c6fcd35ccd7ebdd41c4f7bac4cb",
        mode="procurement observed-sales forecast, no order or production amounts",
        protocol=model["protocol_version"],
        fit_dates=[str(d) for d in model_cache],
        test_active_days=len({r["date"] for r in predictions}),
        strict_production_status=production["status"],
        strict_production_eligible_labels=production["counts"]["eligible_labels"],
        metrics=summary,
        weekly_block_bootstrap=clustered_interval(predictions),
    )
    report = json.dumps(details, ensure_ascii=False, indent=2)
    (args.out / "bakery_details.json").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
