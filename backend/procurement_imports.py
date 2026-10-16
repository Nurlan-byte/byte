"""Strict daily inventory import for purchased ready-made goods."""

import csv
import io
import re
from collections import defaultdict
from datetime import date

from backend.imports import CsvValidationError

REQUIRED = {"date", "product_id", "opening", "received", "sold", "waste", "closing"}
OPTIONAL = {"is_open", "stockout_status"}
COUNT_FIELDS = ("opening", "received", "sold", "waste", "closing")


def parse_procurement_csv(text: str, today: date, product_ids: set[str]):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    try:
        headers = reader.fieldnames
    except csv.Error as error:
        raise CsvValidationError([f"Invalid CSV header: {error}"]) from error
    if (
        headers is None
        or len(headers) != len(set(headers))
        or not REQUIRED.issubset(headers)
        or set(headers) - REQUIRED - OPTIONAL
    ):
        raise CsvValidationError([
            "procurement-1 columns: date,product_id,opening,received,sold,waste,closing"
            "; optional is_open,stockout_status"
        ])
    rows, errors, seen, seen_products = [], [], set(), set()
    try:
        for raw in reader:
            if len(rows) >= 10_000:
                raise CsvValidationError(["At most 10000 rows are supported"])
            try:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError("column count differs from header")
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw["date"]):
                    raise ValueError("date must be YYYY-MM-DD")
                day = date.fromisoformat(raw["date"])
                if day > today:
                    raise ValueError("future observation date")
                product = raw["product_id"]
                if product not in product_ids:
                    raise ValueError("product_id is absent from the supplied catalog")
                key = (day.isoformat(), product)
                if key in seen:
                    raise ValueError("duplicate date/product")
                seen.add(key)
                seen_products.add(product)
                values = {}
                for field in COUNT_FIELDS:
                    value = raw[field]
                    if not value.isascii() or not value.isdigit():
                        raise ValueError(f"{field} must be a nonnegative integer")
                    values[field] = int(value)
                    if values[field] > 1_000_000_000:
                        raise ValueError(f"{field} exceeds 1000000000")
                if values["opening"] + values["received"] != (
                    values["sold"] + values["waste"] + values["closing"]
                ):
                    raise ValueError("opening + received must equal sold + waste + closing")
                open_value = raw.get("is_open", "")
                if open_value not in {"", "true", "false", "1", "0"}:
                    raise ValueError("is_open must be true/false/1/0 or empty")
                is_open = None if open_value == "" else open_value in {"true", "1"}
                if is_open is False and values["sold"]:
                    raise ValueError("closed day cannot have sales")
                stockout = raw.get("stockout_status", "") or "unknown"
                if stockout not in {"none", "occurred", "unknown"}:
                    raise ValueError("stockout_status must be none/occurred/unknown")
                rows.append({
                    "date": day.isoformat(), "product_id": product,
                    "is_open": is_open, "stockout_status": stockout,
                    **values,
                })
            except (KeyError, TypeError, ValueError) as error:
                errors.append(f"row {reader.line_num}: {error}")
    except csv.Error as error:
        raise CsvValidationError([f"Invalid CSV: {error}"]) from error
    if seen_products != product_ids:
        errors.append("Catalog product IDs must exactly match the CSV product IDs")
    if errors or not rows:
        raise CsvValidationError(errors or ["CSV is empty"])
    by_product = defaultdict(list)
    for row in rows:
        by_product[row["product_id"]].append(row)
    continuity = []
    for product, product_rows in by_product.items():
        product_rows.sort(key=lambda row: row["date"])
        for previous, current in zip(product_rows, product_rows[1:]):
            if (date.fromisoformat(current["date"]) - date.fromisoformat(previous["date"])).days == 1:
                if previous["closing"] != current["opening"]:
                    continuity.append({
                        "product_id": product, "date": current["date"],
                        "previous_closing": previous["closing"],
                        "opening": current["opening"],
                    })
    rows.sort(key=lambda row: (row["date"], row["product_id"]))
    return rows, continuity
