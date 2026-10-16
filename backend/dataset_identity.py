"""Explicit target boundaries and a strictly sales-only import adapter."""

import csv
import io
import re
from datetime import date

from backend.imports import CsvValidationError


def identity(dataset):
    """Legacy history remains usable by B1, but cannot implicitly train ML."""
    return {
        **dataset,
        "planning_kind": dataset.get("planning_kind", "production"),
        "bakery_id": dataset.get("bakery_id", "legacy"),
        "target": dataset.get("target", "sold_units"),
        "unit": dataset.get("unit", "physical_unit"),
        "provenance": dataset.get(
            "provenance", "Legacy import; provenance not declared"
        ),
        "metadata_declared": dataset.get("metadata_declared", False),
    }


def parse_sales_csv(text, today):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    try:
        headers = reader.fieldnames
    except csv.Error as error:
        raise CsvValidationError([f"Ошибка заголовка CSV: {error}"]) from error
    expected = {"date", "product_id", "item_line_count"}
    if (
        not headers
        or len(headers) != 3
        or set(headers) != expected
    ):
        raise CsvValidationError(
            [
                "Sales-only CSV: date,product_id,item_line_count; остатки и физические штуки не выводятся"
            ]
        )
    rows, seen, products = [], set(), set()
    try:
        for raw in reader:
            if len(rows) >= 10_000:
                raise ValueError("Максимум 10000 строк")
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError("Число полей строки не соответствует заголовкам")
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw["date"]):
                raise ValueError("Дата должна иметь формат YYYY-MM-DD")
            day = date.fromisoformat(raw["date"])
            product = raw["product_id"]
            value = raw["item_line_count"]
            if day > today or not product.strip() or len(product) > 100:
                raise ValueError("Недопустимая дата или product_id")
            if not value.isascii() or not value.isdigit() or int(value) > 1_000_000_000:
                raise ValueError("item_line_count: требуется целое от 0 до 1000000000")
            if (raw["date"], product) in seen:
                raise ValueError("Дубликат даты и продукта")
            seen.add((raw["date"], product))
            products.add(product)
            if len(products) > 50:
                raise ValueError("Максимум 50 продуктов для sales-only")
            rows.append(
                {
                    "date": raw["date"],
                    "product_id": product,
                    "item_line_count": int(value),
                }
            )
    except (ValueError, csv.Error) as error:
        raise CsvValidationError([f"Строка {reader.line_num}: {error}"]) from error
    if not rows:
        raise CsvValidationError(["CSV пуст"])
    return rows
