"""Read SmartShell's daily sales report and resolve sold goods to catalog IDs."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from backend.errors import ServiceError
from backend.pos_providers import invalid_response, name_text, positive_id


def _catalog(reader):
    goods = reader.smartshell_query("query BakePlanGoods { goods(input: {}) { id title } }")["goods"]
    if not isinstance(goods, list):
        raise invalid_response()
    by_title = defaultdict(list)
    by_id = set()
    for good in goods:
        ident = positive_id(good["id"])
        title = good["title"]
        if ident in by_id or not isinstance(title, str) or not title.strip():
            raise invalid_response()
        by_id.add(ident)
        by_title[title.strip()].append(ident)
    return by_title


def _reports(reader, days):
    # Each field covers one complete business day. A week per HTTP request keeps
    # even 120 days plus discovery and catalog within the 20 requests/min limit.
    definitions = []
    fields = []
    variables = {}
    for i, day in enumerate(days):
        definitions.extend((f"$from{i}: DateTime!", f"$to{i}: DateTime!"))
        variables[f"from{i}"] = f"{day} 00:00:00"
        variables[f"to{i}"] = f"{day} 23:59:59"
        fields.append(
            f"d{i}: salesReport(input: {{from: $from{i}, to: $to{i}}}) "
            "{ labels keys extraLabels points data { values } }"
        )
    query = f"query BakePlanDailyReports({', '.join(definitions)}) {{ {' '.join(fields)} }}"
    return reader.smartshell_query(query, variables)


def _add_report(sales, day, report, catalog):
    names, kinds, rows = report["keys"], report["extraLabels"], report["data"]
    points = report["points"]
    if (
        type(points) is not int
        or not all(isinstance(column, list) and len(column) == points for column in (names, kinds, rows))
    ):
        raise invalid_response()
    seen = set()
    for title, kind, row in zip(names, kinds, rows, strict=True):
        if not isinstance(kind, str) or not kind.strip():
            raise invalid_response()
        if kind != "GOOD":
            continue
        labels, values = report["labels"], row["values"]
        if (not isinstance(labels, list) or len(labels) < 3 or
                labels[2].strip().casefold() not in {"продано", "sold"} or
                not isinstance(values, list) or not values):
            raise invalid_response()
        units = Decimal(str(values[0]))
        if not units.is_finite() or units < 0 or units != units.to_integral_value():
            raise invalid_response()
        if units == 0:
            continue
        # The report supplies a display name but no SKU. Never merge ambiguous
        # products or turn an aggregated report row into a single receipt.
        candidates = catalog.get(title.strip(), ())
        if len(candidates) != 1:
            raise ServiceError(422, "Не удалось однозначно сопоставить проданный товар с каталогом SmartShell.")
        ident = candidates[0]
        if ident in seen:
            raise invalid_response()
        seen.add(ident)
        sales.add(day, ident, name_text(title), units, True)


def read_sales(reader, sales):
    catalog = _catalog(reader)
    start = sales.start
    while start <= sales.end:
        width = min(7, (sales.end - start).days + 1)
        days = [start + timedelta(days=i) for i in range(width)]
        reports = _reports(reader, days)
        for i, day in enumerate(days):
            _add_report(sales, day, reports[f"d{i}"], catalog)
        start += timedelta(days=width)
