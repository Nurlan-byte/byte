"""Shared strict CSV validation, independent of HTTP and persistence."""

import csv
import io
import re
from collections.abc import Collection
from datetime import date

QUANTITIES = ("opening", "produced", "sold", "waste", "closing", "other_out")
REQUIRED = ("date", "product_id", "is_open", *QUANTITIES, "stockout_status")


class CsvValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors[:100]
        super().__init__("; ".join(self.errors))


def parse_csv(
    text: str,
    *,
    allowed_product_ids: Collection[str] | None = None,
    latest_date: date | None = None,
    max_rows: int = 10_000,
) -> list[dict]:
    """Validate whole input before returning rows; extra note columns are ignored."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    headers = reader.fieldnames
    if (
        headers is None
        or len(headers) != len(set(headers))
        or not set(REQUIRED).issubset(headers)
    ):
        raise CsvValidationError(["Нужны уникальные заголовки: " + ", ".join(REQUIRED)])
    rows, errors, seen = [], [], set()
    try:
        for count, raw in enumerate(reader, 1):
            if count > max_rows:
                raise CsvValidationError([f"Максимум {max_rows} строк"])
            try:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError("Число полей строки не соответствует заголовкам")
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw["date"]):
                    raise ValueError("Дата должна иметь формат YYYY-MM-DD")
                day = date.fromisoformat(raw["date"])
                if latest_date is not None and day > latest_date:
                    raise ValueError("Дата истории находится в будущем")
                product = raw["product_id"]
                if not product or (
                    allowed_product_ids is not None
                    and product not in allowed_product_ids
                ):
                    raise ValueError("Неизвестный или пустой product_id")
                key = day.isoformat(), product
                if key in seen:
                    raise ValueError("Дубликат даты и продукта")
                seen.add(key)
                if raw["is_open"] not in {"true", "false", "1", "0"}:
                    raise ValueError("is_open: true/false или 1/0 (строчные буквы)")
                nums = {}
                for field in QUANTITIES:
                    value = raw[field]
                    if not value.isascii() or not value.isdigit():
                        raise ValueError(f"{field}: требуется неотрицательное целое")
                    nums[field] = int(value)
                if nums["opening"] + nums["produced"] != sum(
                    nums[k] for k in ("sold", "waste", "closing", "other_out")
                ):
                    raise ValueError("Нарушен баланс остатков")
                if raw["stockout_status"] not in {"none", "occurred", "unknown"}:
                    raise ValueError("stockout_status: none/occurred/unknown")
                opened = raw["is_open"] in {"true", "1"}
                if not opened and (nums["produced"] or nums["sold"]):
                    raise ValueError("Закрытый день содержит продажи/производство")
                rows.append(
                    dict(
                        date=day.isoformat(),
                        product_id=product,
                        is_open=opened,
                        stockout_status=raw["stockout_status"],
                        **nums,
                    )
                )
            except (ValueError, TypeError, KeyError) as error:
                errors.append(f"Строка {reader.line_num}: {error}")
    except csv.Error as error:
        raise CsvValidationError([f"Ошибка CSV: {error}"]) from error
    if errors or not rows:
        raise CsvValidationError(errors or ["CSV пуст"])
    return rows
