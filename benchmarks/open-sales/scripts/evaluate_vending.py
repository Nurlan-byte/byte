"""Replay BakePlan's procurement forecaster on recorded vending unit sales."""

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


def write_csv(path, records):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", required=True, type=Path, help="BakePlan checkout ac3c2af"
    )
    parser.add_argument(
        "--input", required=True, type=Path, help="Original Kaggle v5 ZIP"
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.forecasting import baseline
    from backend.ml_core import forecast_model, train_model

    with zipfile.ZipFile(args.input) as archive:
        source = archive.read("vending_machine_sales.csv")
    digest = hashlib.sha256(source).hexdigest()
    if digest != "cb4e8371db53b2eafab47a5ae4e7e3b019b4c79db7fba769f4cd88e01fc9bacb":
        raise ValueError("Unexpected source CSV hash")
    records = list(csv.DictReader(io.StringIO(source.decode("utf-8-sig"))))
    training_end = date(2022, 9, 30)
    start = training_end + timedelta(days=2)
    end = date(2022, 12, 31)
    machine_units = Counter()
    train_units = defaultdict(Counter)
    sell_months = defaultdict(set)
    day_activity = defaultdict(Counter)
    sold = Counter()
    for row in records:
        if row["Status"] != "Processed":
            continue
        day = datetime.strptime(row["TransDate"], "%m/%d/%Y").date()
        machine = row["Machine"]
        sku = row["Product"].strip()
        units = int(row["MQty"])
        day_activity[machine, day]["processed_lines"] += 1
        if not sku:
            day_activity[machine, day]["unknown_product_lines"] += 1
            continue
        sold[machine, day, sku] += units
        if day <= training_end:
            machine_units[machine] += units
            train_units[machine][sku] += units
            sell_months[machine, sku].add(day.month)
    machine = sorted(machine_units, key=lambda name: (-machine_units[name], name))[0]
    eligible = []
    for sku in train_units[machine]:
        months = sell_months[machine, sku]
        if 9 in months and {1 + (month - 1) // 3 for month in months} == {1, 2, 3}:
            eligible.append(sku)
    skus = sorted(eligible, key=lambda sku: (-train_units[machine][sku], sku))[:6]
    if len(skus) != 6:
        raise ValueError("Fewer than six eligible products")
    active_days = sorted(day for actor, day in day_activity if actor == machine)
    daily = []
    source_rows = []
    for day in active_days:
        for sku in skus:
            quantity = sold[machine, day, sku]
            activity = day_activity[machine, day]
            daily.append(
                dict(
                    date=day.isoformat(),
                    machine=machine,
                    product=sku,
                    observed_units=quantity,
                    machine_processed_lines=activity["processed_lines"],
                    machine_unnamed_lines=activity["unknown_product_lines"],
                )
            )
            source_rows.append(
                dict(
                    date=day.isoformat(),
                    product_id=sku,
                    sold=quantity,
                    stockout_status="unknown",
                )
            )
    write_csv(args.out / "vending_daily.csv", daily)
    dataset = dict(
        id="vending-v5-train-selected",
        version="1",
        bakery_id=machine,
        target="sold_units",
        unit="physical_unit",
        planning_kind="procurement",
        rows=source_rows,
    )
    scored = []
    schedule = {}
    for day in active_days:
        if not start <= day <= end:
            continue
        cutoff = day - timedelta(days=2)
        trained = training_end + timedelta(days=7 * ((cutoff - training_end).days // 7))
        if trained not in schedule:
            schedule[trained] = train_model(dataset, trained)
        model = schedule[trained]
        for sku in skus:
            decision = forecast_model(model, dataset, sku, day, cutoff)
            naive = baseline(
                source_rows,
                product_id=sku,
                target_date=day,
                cutoff=cutoff,
                method="B1",
                eligible=lambda row: row.get("is_open") is not False,
            )
            estimate = decision["forecast_mean"]
            reference = naive["forecast_mean"]
            if reference != decision["b1_mean"]:
                raise AssertionError("Benchmark disagrees with BakePlan")
            truth = sold[machine, day, sku]
            scored.append(
                dict(
                    date=day.isoformat(),
                    data_cutoff=cutoff.isoformat(),
                    training_cutoff=trained.isoformat(),
                    product=sku,
                    observed_units=truth,
                    b1_forecast=reference,
                    bakeplan_forecast=estimate,
                    method=decision["method"],
                    fallback_reason=decision["fallback_reason"],
                    b1_abs_error=abs(reference - truth)
                    if reference is not None
                    else None,
                    bakeplan_abs_error=abs(estimate - truth)
                    if estimate is not None
                    else None,
                )
            )
    write_csv(args.out / "vending_forecasts.csv", scored)

    def summarize(rows):
        valid = [
            r
            for r in rows
            if r["b1_abs_error"] is not None and r["bakeplan_abs_error"] is not None
        ]
        n = len(valid)
        truth_sum = sum(r["observed_units"] for r in valid)
        b1 = sum(r["b1_abs_error"] for r in valid)
        hybrid = sum(r["bakeplan_abs_error"] for r in valid)
        return dict(
            n=n,
            all_rows=len(rows),
            units=truth_sum,
            zero_rows=sum(r["observed_units"] == 0 for r in valid),
            ridge_rows=sum(r["method"] == "ML" for r in valid),
            b1_fallback_rows=sum(r["method"] == "B1" for r in valid),
            mae_b1=b1 / n if n else None,
            mae_bakeplan=hybrid / n if n else None,
            wape_b1_pct=100 * b1 / truth_sum if truth_sum else None,
            wape_bakeplan_pct=100 * hybrid / truth_sum if truth_sum else None,
            improvement_pct=100 * (b1 - hybrid) / b1 if b1 else None,
        )

    summary = [dict(product="ALL", **summarize(scored))]
    for sku in skus:
        summary.append(
            dict(product=sku, **summarize([r for r in scored if r["product"] == sku]))
        )
    write_csv(args.out / "vending_summary.csv", summary)
    details = dict(
        sha256=digest,
        machine=machine,
        candidate_products=len(eligible),
        selected={sku: train_units[machine][sku] for sku in skus},
        machine_training_units=dict(machine_units),
        source_row_count=len(records),
        daily_rows=len(daily),
        machine_observed_days=len(active_days),
        heldout_machine_days=len(scored) // len(skus),
        machine_test_unnamed_lines=sum(
            day_activity[machine, day]["unknown_product_lines"]
            for day in active_days
            if start <= day <= end
        ),
        n_models=len(schedule),
        train_end=str(training_end),
        test_start=str(start),
        test_end=str(end),
        models={str(k): v["status"] for k, v in schedule.items()},
        metrics=summary,
    )
    report = json.dumps(details, ensure_ascii=False, indent=2)
    (args.out / "vending_details.json").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
