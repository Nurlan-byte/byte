"""Sales-only transaction-line observations. Never an operational DailyActual."""

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

FOOD_ITEMS = frozenset(
    {
        "Bread",
        "Cake",
        "Pastry",
        "Medialuna",
        "Muffin",
        "Cookies",
        "Brownie",
        "Scone",
        "Tartine",
        "Alfajores",
    }
)
MAX_SOURCE_BYTES = 20_000_000

ZERO_POLICIES = {"observed-store-zero", "missing"}


@dataclass(frozen=True)
class SalesLine:
    date: date
    item: str
    transaction_id: str


@dataclass(frozen=True)
class SalesObservation:
    date: date
    item: str
    item_line_count: int
    inferred_zero: bool


def read_breadbasket(path: Path) -> list[SalesLine]:
    path = Path(path)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Sales-only CSV exceeds20MB limit")
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not {
            "Transaction",
            "Item",
            "date_time",
        }.issubset(headers):
            raise ValueError(
                "Expected corrected BreadBasket columns Transaction,Item,date_time; original mixed-date file is unsupported"
            )
        for number, raw in enumerate(reader, 2):
            if number > 100_001:
                raise ValueError("Sales-only CSV exceeds100000 item lines")
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError(f"CSV line{number}: wrong field count")
            item, transaction = raw["Item"].strip(), raw["Transaction"].strip()
            if (
                not item
                or item.upper() in {"NONE", "NAN"}
                or not transaction.isascii()
                or not transaction.isdigit()
            ):
                raise ValueError(
                    f"CSV line{number}: nonempty item and integer transaction identifier required"
                )
            observed_date = None
            for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S"):
                try:
                    observed_date = datetime.strptime(raw["date_time"], fmt).date()
                    break
                except ValueError:
                    pass
            if observed_date is None:
                raise ValueError(
                    f"CSV line{number}: date_time must be unambiguous MM/DD/YYYY HH:MM[:SS]"
                )
            rows.append(SalesLine(observed_date, item, transaction))
    if not rows:
        raise ValueError("Sales-only CSV is empty")
    return rows


def select_items(
    lines: list[SalesLine], train_end: date, n: int = 5, min_coverage: float = 0.5
) -> list[str]:
    if n < 1 or not 0 <= min_coverage <= 1:
        raise ValueError("Invalid selection settings")
    training = [line for line in lines if line.date <= train_end]
    store_days = {line.date for line in training}
    if len(store_days) < 28:
        raise ValueError(
            "Need at least28 observed training dates before selecting food items"
        )
    item_dates = defaultdict(set)
    for line in training:
        if line.item in FOOD_ITEMS:
            item_dates[line.item].add(line.date)
    candidates = [
        item
        for item, days in item_dates.items()
        if len(days) / len(store_days) >= min_coverage
    ]
    candidates.sort(key=lambda item: (-len(item_dates[item]), item))
    if len(candidates) < n:
        raise ValueError(
            f"Need{n} food items meeting train-only positive-date coverage threshold"
        )
    return candidates[:n]


def build_daily(
    lines: list[SalesLine],
    selected_items: list[str],
    zero_policy: str = "observed-store-zero",
) -> list[SalesObservation]:
    if zero_policy not in ZERO_POLICIES or len(selected_items) != len(
        set(selected_items)
    ):
        raise ValueError("Invalid zero policy or duplicate item selection")
    store_days = sorted({line.date for line in lines})
    counts = Counter(
        (line.date, line.item) for line in lines if line.item in selected_items
    )
    first_seen = {
        item: min(line.date for line in lines if line.item == item)
        for item in selected_items
        if any(line.item == item for line in lines)
    }
    result = []
    for observed_date in store_days:
        for item in selected_items:
            if item not in first_seen or observed_date < first_seen[item]:
                continue
            count = counts[(observed_date, item)]
            if count == 0 and zero_policy == "missing":
                continue
            result.append(SalesObservation(observed_date, item, count, count == 0))
    return result
