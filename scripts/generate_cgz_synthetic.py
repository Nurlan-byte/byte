"""Generate a reproducible, explicitly synthetic procurement-1 club-bar fixture.

All interview-replaceable assumptions live in one JSON configuration file.
No private workbook or cash-register records are read.
"""

import argparse
import csv
import io
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "fixtures" / "cloud_game_zone_synthetic_config.json"
CSV_PATH = ROOT / "fixtures" / "cloud_game_zone_synthetic.csv"
CATALOG_PATH = ROOT / "fixtures" / "cloud_game_zone_synthetic_catalog.json"
FIELDS = (
    "date", "product_id", "opening", "received", "sold", "waste", "closing",
    "is_open", "stockout_status",
)


def generate(config: dict) -> tuple[str, str]:
    rng = random.Random(config["seed"])
    start = date.fromisoformat(config["start_date"])
    days = config["days"]
    if days < 90 or not 5 <= len(config["products"]) <= 8:
        raise ValueError("The synthetic fixture needs at least 90 days and 5–8 SKUs")
    if len({p["product_id"] for p in config["products"]}) != len(config["products"]):
        raise ValueError("Product IDs must be unique")
    if start + timedelta(days=days - 1) > date.today():
        raise ValueError("A historical demo fixture cannot contain future observations")
    rows = []
    for product in config["products"]:
        opening = 0
        pack = product["pack_size"]
        maximum = product["max_order_qty"]
        if pack <= 0 or maximum < pack or maximum % pack:
            raise ValueError(f"Invalid supplier pack/maximum for {product['product_id']}")
        for index in range(days):
            day = start + timedelta(days=index)
            factor = (
                config["friday_multiplier"] if day.weekday() == 4 else
                config["weekend_multiplier"] if day.weekday() >= 5 else 1
            )
            if index and index % config["peak_every_days"] == 0:
                factor *= config["peak_multiplier"]
            demand = max(0, round(product["base_daily_sales"] * factor) +
                         rng.randint(-config["noise_units"], config["noise_units"]))
            received = 0
            if opening <= product["reorder_point"]:
                needed = max(0, product["target_stock"] - opening)
                received = min(maximum, ((needed + pack - 1) // pack) * pack)
            available = opening + received
            if index in product["stockout_day_indices"]:
                demand = available + rng.randint(1, 4)
            sold = min(demand, available)
            waste = int(available > sold and rng.random() < config["waste_probability"])
            closing = available - sold - waste
            rows.append({
                "date": day.isoformat(), "product_id": product["product_id"],
                "opening": opening, "received": received, "sold": sold,
                "waste": waste, "closing": closing, "is_open": "true",
                "stockout_status": "occurred" if demand > available else "none",
            })
            opening = closing
    rows.sort(key=lambda row: (row["date"], row["product_id"]))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    catalog = [
        {key: product[key] for key in ("product_id", "name", "pack_size", "max_order_qty")}
        for product in config["products"]
    ]
    return output.getvalue(), json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify committed fixtures without writing")
    args = parser.parse_args()
    generated = generate(json.loads(CONFIG.read_text(encoding="utf-8")))
    for path, content in zip((CSV_PATH, CATALOG_PATH), generated):
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Fixture differs from configuration: {path}")
        else:
            path.write_text(content, encoding="utf-8", newline="")
    print("Synthetic Cloud Game Zone fixtures verified" if args.check else
          "Synthetic Cloud Game Zone fixtures generated")


if __name__ == "__main__":
    main()
