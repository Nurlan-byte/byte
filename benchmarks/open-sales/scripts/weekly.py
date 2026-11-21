"""Verify paired forecasts and summarize absolute errors in seven-day blocks."""

import argparse
import csv
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


BAKERY_WINDOW = (date(2022, 7, 2), date(2022, 9, 30))
CAFE_WINDOW = (date(2025, 8, 6), date(2025, 9, 30))
BAKERY_COLUMNS = (
    "data_cutoff",
    "observed_net_units",
    "b1_forecast",
    "bakeplan_forecast",
)
CAFE_COLUMNS = ("cutoff", "actual", "b1", "bakeplan")
OUTPUT_COLUMNS = (
    "sample",
    "week_start",
    "observed_days",
    "forecast_pairs",
    "b1_mae",
    "bakeplan_mae",
    "relative_error_change_pct",
)


def load_forecasts(path, columns, window, site_column=None):
    cutoff_column, *value_columns = columns
    start, end = window
    rows = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"date", "training_cutoff", "product", "method", *columns}
        if site_column:
            required.add(site_column)
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {', '.join(sorted(missing))}")
        for row in reader:
            location = f"{path.name}, line {reader.line_num}"
            target = date.fromisoformat(row["date"])
            cutoff = date.fromisoformat(row[cutoff_column])
            training = date.fromisoformat(row["training_cutoff"])
            if (
                not start <= target <= end
                or target - cutoff != timedelta(days=2)
                or training > cutoff
            ):
                raise ValueError(f"{location}: forecast window or cutoff mismatch")
            site = row[site_column] if site_column else "Bakery"
            key = (target, site, row["product"])
            if key in seen:
                raise ValueError(f"{location}: duplicate site/product/date")
            seen.add(key)
            actual, b1, model = (float(row[column]) for column in value_columns)
            if not all(
                math.isfinite(value) and value >= 0 for value in (actual, b1, model)
            ):
                raise ValueError(
                    f"{location}: expected finite nonnegative observations and forecasts"
                )
            if row["method"] not in {"ML", "B1"}:
                raise ValueError(f"{location}: expected a scored forecast")
            if row["method"] == "B1" and abs(model - b1) >= 1e-8:
                raise ValueError(f"{location}: fallback differs from B1")
            rows.append((target, site, abs(actual - b1), abs(actual - model)))
    if not rows:
        raise ValueError(f"{path}: empty forecast file")
    return rows


def weekly_rows(sample, start, forecasts):
    groups = defaultdict(list)
    for row in forecasts:
        week = (row[0] - start).days // 7
        groups[week].append(row)
    for week, group in sorted(groups.items()):
        baseline = sum(row[2] for row in group)
        model = sum(row[3] for row in group)
        yield (
            sample,
            start + timedelta(weeks=week),
            len({row[0] for row in group}),
            len(group),
            baseline / len(group),
            model / len(group),
            100 * (model / baseline - 1) if baseline else None,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bakery-forecasts", type=Path, required=True, help="bakery_forecasts.csv"
    )
    parser.add_argument(
        "--cafe-forecasts", type=Path, required=True, help="cafe_predictions.csv"
    )
    parser.add_argument("--out", type=Path, required=True, help="Destination CSV file")
    args = parser.parse_args()
    bakery = load_forecasts(args.bakery_forecasts, BAKERY_COLUMNS, BAKERY_WINDOW)
    cafe = load_forecasts(args.cafe_forecasts, CAFE_COLUMNS, CAFE_WINDOW, "outlet")
    rows = list(weekly_rows("Bakery", BAKERY_WINDOW[0], bakery))
    rows.extend(weekly_rows("Cafe combined", CAFE_WINDOW[0], cafe))
    for site in sorted({row[1] for row in cafe}):
        rows.extend(
            weekly_rows(site, CAFE_WINDOW[0], (row for row in cafe if row[1] == site))
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(rows)
    print(f"Wrote {len(rows)} weekly summaries to {args.out}")


if __name__ == "__main__":
    main()
