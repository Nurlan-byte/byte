"""Aggregate public POS receipts, rental counts, forecast vintages and national calendars."""

import csv
import hashlib
import io
import json
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

ONE_DAY = timedelta(days=1)
WEATHER_FILE = Path(__file__).with_name("surabaya_gfs.csv")
WEATHER_URL = (
    "https://previous-runs-api.open-meteo.com/v1/forecast?latitude=-7.25&longitude=112.75"
    "&start_date=2025-01-02&end_date=2025-09-30&timezone=Asia%2FJakarta"
    "&models=gfs_seamless&hourly=temperature_2m_previous_day3,precipitation_previous_day3"
)
SEOUL_URL = (
    "https://archive.ics.uci.edu/static/public/560/seoul+bike+sharing+demand.zip"
)
SOURCE_HASHES = {
    "SeoulBikeData.csv": "373339b71a8935d69e9af0abf26a70744632119862eeb3919efb389a7b749c60",
    "Transactions.csv": "36f2412969f8241e72b5f3eb395e5af13a7ba042de1e6ecdbe32bee23723ef90",
    "Products.csv": "09e3d91530797fb0c2c5a214f5660a6e2235521b1cf332382343c75e7708d047",
}


def read_source(name, raw, encoding):
    if hashlib.sha256(raw).hexdigest() != SOURCE_HASHES[name]:
        raise ValueError(
            f"Source content changed: {name}. Check its version before comparing results."
        )
    return csv.DictReader(io.StringIO(raw.decode(encoding)))


def dates(start, stop):
    while start <= stop:
        yield start
        start += ONE_DAY


def calendar(start, stop, holidays, announced, leave=()):
    return [
        {
            "date": str(day),
            "available_on": announced,
            "day_type": "holiday"
            if str(day) in holidays
            else "day_off"
            if str(day) in leave or day.weekday() >= 5
            else "workday",
        }
        for day in dates(start, stop)
    ]


def cafe_calendar():
    holidays = {
        "2025-" + md
        for md in "01-01 01-27 01-29 03-29 03-31 04-01 04-18 04-20 05-01 05-12 "
        "05-29 06-01 06-06 06-27 08-17 09-05".split()
    }
    leave = {
        "2025-" + md
        for md in "01-28 03-28 04-02 04-03 04-04 04-07 05-13 05-30 06-09".split()
    }
    result = calendar(
        date(2025, 1, 2), date(2025, 9, 30), holidays, "2024-10-14", leave
    )
    result.append(
        {"date": "2025-08-18", "available_on": "2025-08-07", "day_type": "day_off"}
    )
    return result


def prepare_weather(archive):
    """Return normalized daily values. Each hourly forecast has a 72-hour lead."""
    hours = archive["hourly"]
    days = defaultdict(list)
    for iso, temp, rain in zip(
        hours["time"],
        hours["temperature_2m_previous_day3"],
        hours["precipitation_previous_day3"],
        strict=True,
    ):
        when = datetime.fromisoformat(iso)
        if temp is None or rain is None:
            raise ValueError(f"Missing archived forecast for {iso}")
        days[when.date()].append((when.hour, temp, rain))
    if set(days) != set(dates(date(2025, 1, 2), date(2025, 9, 30))):
        raise ValueError("Incomplete city forecast series")
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(["date", "available_on", "temperature_c", "precipitation_mm"])
    for day, rows in sorted(days.items()):
        if sorted(hour for hour, _, _ in rows) != list(range(24)):
            raise ValueError(f"Incomplete hourly forecast for {day}")
        # Even the 23:00 local hourly value is from the previous third day.
        # Allow an extra day for model dissemination and publication.
        writer.writerow(
            [
                day,
                day - 2 * ONE_DAY,
                f"{statistics.mean(t for _, t, _ in rows):.3f}",
                f"{sum(p for _, _, p in rows):.2f}",
            ]
        )
    return text.getvalue()


def cafe_inputs(directory):
    readers = {
        name: read_source(name, (directory / name).read_bytes(), "utf-8-sig")
        for name in ("Products.csv", "Transactions.csv")
    }
    drinks = set()
    for row in readers["Products.csv"]:
        if row["Category"] in {"Coffee", "Tea"}:
            name = row["ProductName"]
            drinks.add(name if row["Variant"] == "-" else f"{name} ({row['Variant']})")
    history = defaultdict(lambda: defaultdict(Counter))
    ranking = defaultdict(Counter)
    seen = set()
    for row in readers["Transactions.csv"]:
        shop, day, ticket = row["Outlet"], row["Date"], row["ReceiptNumber"]
        if ticket in seen:
            raise ValueError(f"Duplicate receipt on {day}")
        seen.add(ticket)
        day_counts = history[shop][day]
        ordered = row["Items"].split(", ")
        if len(set(ordered)) != len(ordered):
            raise ValueError("Repeated receipt item")
        items = drinks.intersection(ordered)
        day_counts.update(items)
        if day <= "2025-08-04":
            ranking[shop].update(items)
    with WEATHER_FILE.open(encoding="utf-8") as f:
        weather = [
            {
                "date": r["date"],
                "available_on": r["available_on"],
                "kind": "forecast",
                "temperature_c": float(r["temperature_c"]),
                "precipitation_mm": float(r["precipitation_mm"]),
            }
            for r in csv.DictReader(f)
        ]
    for shop, days in sorted(history.items()):
        # Same pre-period selection and measured unit as the published open-sales benchmark.
        skus = sorted(ranking[shop], key=lambda sku: (-ranking[shop][sku], sku))[:6]
        rows = []
        for sku in skus:
            first = min(day for day, counts in days.items() if counts[sku] > 0)
            rows.extend(
                {"date": day, "product_id": sku, "item_line_count": counts[sku]}
                for day, counts in sorted(days.items())
                if day >= first
            )
        yield (
            {
                "id": "cafe-v2-" + shop,
                "version": "1",
                "bakery_id": shop,
                "planning_kind": "procurement",
                "target": "item_line_count",
                "unit": "item_line",
                "rows": rows,
            },
            cafe_calendar(),
            weather,
        )


def seoul_inputs(archive):
    with zipfile.ZipFile(archive) as z:
        reader = read_source("SeoulBikeData.csv", z.read("SeoulBikeData.csv"), "cp1252")
    days = defaultdict(list)
    for row in reader:
        days[datetime.strptime(row["Date"], "%d/%m/%Y").date()].append(row)
    # The published almanac includes substitute days and the June local election.
    holidays = {
        "2018-" + md
        for md in "01-01 02-15 02-16 02-17 03-01 05-05 05-07 05-22 06-06 06-13 "
        "08-15 09-23 09-24 09-25 09-26 10-03 10-09".split()
    } | {"2017-12-25"}
    cal = calendar(min(days), max(days), holidays, "2017-02-27")
    sales, weather = [], []
    for day, rows in sorted(days.items()):
        if sorted(int(r["Hour"]) for r in rows) != list(range(24)):
            raise ValueError(f"Incomplete source day {day}")
        if all(r["Functioning Day"] == "Yes" for r in rows):
            sales.append(
                {
                    "date": str(day),
                    "product_id": "daily-rentals",
                    "sold": sum(int(r["Rented Bike Count"]) for r in rows),
                }
            )
        weather.append(
            {
                "date": str(day),
                "available_on": str(day + ONE_DAY),
                "kind": "observation",
                "temperature_c": statistics.mean(
                    float(r["Temperature(°C)"]) for r in rows
                ),
                "precipitation_mm": sum(float(r["Rainfall(mm)"]) for r in rows),
            }
        )
    return (
        {
            "id": "uci-560",
            "version": "1",
            "bakery_id": "seoul",
            "planning_kind": "procurement",
            "target": "sold_units",
            "unit": "physical_unit",
            "rows": sales,
        },
        cal,
        weather,
    )


if __name__ == "__main__":
    # Refresh explicitly, compare first, and never overwrite the reference silently.
    with urlopen(WEATHER_URL, timeout=60) as response:
        updated = prepare_weather(json.load(response))
    if WEATHER_FILE.read_text(encoding="utf-8") != updated:
        raise SystemExit(
            "Archived forecast changed. Review source and results before refreshing the reference."
        )
    print("272 daily forecasts agree with the archived hourly values.")
