"""Read-only POS adapters. Only fixed vendor origins receive credentials."""

import gzip
import io
import json
import re
import time
import zlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from backend.errors import ServiceError

POSTER = "https://joinposter.com/api/"
MOYSKLAD = "https://api.moysklad.ru/api/remap/1.2/entity/"
SMARTSHELL = "https://billing.smartshell.gg/api/graphql"
MAX_PAGE_BYTES = 8_000_000
MAX_DOCUMENTS = 25_000


class RangeTooWide(ServiceError):
    """Poster error 52 is a request to split the selection into three-day windows."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def invalid_response():
    return ServiceError(
        502, "Касса вернула неполные или некорректные данные. Повторите импорт."
    )


class VendorReader:
    def __init__(self, provider, key):
        self.provider = provider
        self.key = key
        self.calls = 0
        self.bytes = 0
        self.deadline = time.monotonic() + 90
        self.opener = build_opener(NoRedirect())

    def get(self, path, **params):
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip"}
        if self.provider == "poster":
            url = POSTER + path
            params["token"] = self.key
        elif self.provider == "moysklad":
            url = MOYSKLAD + path
            headers["Authorization"] = "Bearer " + self.key
            headers["Accept"] = "application/json;charset=utf-8"
        else:
            raise ValueError("Unsupported POS transport")
        request = Request(url + "?" + urlencode(params), headers=headers, method="GET")
        return self._read(request)

    def smartshell_query(self, query, variables=None):
        if self.provider != "smartshell":
            raise ValueError("Unsupported POS transport")
        request = Request(
            SMARTSHELL,
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Accept-Encoding": "gzip", "Authorization": "Bearer " + self.key},
            method="POST",
        )
        envelope = self._read(request)
        if envelope.get("errors"):
            # Even with HTTP 200, a partially authorized report may contain both data and errors.
            errors = envelope["errors"]
            codes = {str(e["extensions"].get("code")) for e in errors
                     if isinstance(e, dict) and isinstance(e.get("extensions"), dict)}
            if codes & {"401", "403", "UNAUTHENTICATED", "FORBIDDEN"}:
                raise ServiceError(403, "SmartShell отклонил токен или права чтения клуба.")
            if "429" in codes:
                raise ServiceError(429, "SmartShell ограничил частоту запросов. Повторите позже.")
            raise invalid_response()
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise invalid_response()
        return data

    def _read(self, request):
        self.calls += 1
        remaining = self.deadline - time.monotonic()
        if self.calls > 160 or remaining <= 0 or self.bytes >= 100_000_000:
            raise ServiceError(
                422, "Слишком большой импорт для одного запроса. Выберите меньше дней."
            )
        try:
            with self.opener.open(request, timeout=min(10, remaining)) as response:
                raw = response.read(MAX_PAGE_BYTES + 1)
                if len(raw) > MAX_PAGE_BYTES:
                    raise ServiceError(
                        422, "Страница кассы слишком велика. Выберите меньше дней."
                    )
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                        raw = stream.read(MAX_PAGE_BYTES + 1)
                if len(raw) > MAX_PAGE_BYTES:
                    raise ServiceError(
                        422, "Страница кассы слишком велика. Выберите меньше дней."
                    )
                self.bytes += len(raw)
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise invalid_response()
                if self.provider == "poster":
                    if "response" not in data:
                        code = str((data.get("error") or {}).get("code"))
                        if code == "52":
                            raise RangeTooWide(
                                422, "Poster принимает не более трёх дней за запрос."
                            )
                        if code in {"10", "11", "12", "20", "54"}:
                            raise ServiceError(
                                403, "Poster отклонил токен или права на чтение."
                            )
                        raise ServiceError(
                            422,
                            "Poster отклонил запрос. Проверьте токен и права чтения продаж.",
                        )
                    return data["response"]
                return data
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in (401, 403):
                raise ServiceError(
                    403,
                    "Касса отклонила ключ или права. Нужен доступ к чтению точек и продаж.",
                ) from None
            if status == 429:
                raise ServiceError(
                    429, "Касса ограничила частоту запросов. Повторите позже."
                ) from None
            raise invalid_response() from None
        except URLError, OSError, EOFError, ValueError, HTTPException, zlib.error:
            raise ServiceError(
                502, "Не удалось прочитать ответ кассы. Проверьте доступность сервиса."
            ) from None


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    account: str
    address: str = ""

    def public(self):
        return {"id": self.id, "name": self.name}


def uuid_text(value):
    return str(UUID(str(value)))


def positive_id(value):
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,18}", value):
        raise invalid_response()
    return value


def name_text(value):
    if not isinstance(value, str) or not value.strip():
        raise invalid_response()
    return value.strip()[:160]


def page_rows(reader, path, *, first=None, **params):
    """Rebuild pagination on the fixed origin, including expanded subcollections."""
    offset = 0
    total = None
    seen = set()
    while True:
        page = (
            first
            if first is not None
            else reader.get(path, offset=offset, limit=100, **params)
        )
        first = None
        meta, rows = page["meta"], page["rows"]
        size = int(meta["size"])
        if (
            not isinstance(rows, list)
            or int(meta["offset"]) != offset
            or size < offset + len(rows)
        ):
            raise invalid_response()
        if total is not None and total != size:
            raise invalid_response()
        if size > MAX_DOCUMENTS:
            raise ServiceError(
                422, "В периоде слишком много документов. Выберите меньше дней."
            )
        total = size
        for row in rows:
            ident = uuid_text(row["id"])
            if ident in seen:
                raise invalid_response()
            seen.add(ident)
            yield row
        offset += len(rows)
        if offset == total:
            return
        if not rows:
            raise invalid_response()


def locations(reader):
    if reader.provider == "smartshell":
        # salesReport is scoped by the credential, so bind only its current club.
        club = reader.smartshell_query("query BakePlanClub { currentClub { id name city } }")["currentClub"]
        ident = positive_id(club["id"])
        result = [Location(ident, name_text(club["name"]), ident, str(club.get("city") or ""))]
    elif reader.provider == "poster":
        shops = reader.get("spots.getSpots")
        if not isinstance(shops, list):
            raise invalid_response()
        account = name_text(reader.get("settings.getAllSettings")["COMPANY_ID"])
        result = [
            Location(
                positive_id(s["spot_id"]),
                name_text(s["name"]),
                account,
                str(s.get("address") or ""),
            )
            for s in shops
        ]
    else:
        result = [
            Location(
                uuid_text(s["id"]),
                name_text(s["name"]),
                uuid_text(s["accountId"]),
                str((s.get("addressFull") or {}).get("city") or s.get("address") or ""),
            )
            for s in page_rows(reader, "retailstore")
            if s["active"] is True
        ]
    if not result:
        raise ServiceError(422, "Нет торговых точек, доступных этому ключу.")
    if len(result) > 250 or len({s.id for s in result}) != len(result):
        raise invalid_response()
    return result


class Observations:
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.names = {}
        self.physical = True
        self.quantities = defaultdict(int)
        self.lines = defaultdict(int)

    def add(self, day, product, name, quantity, pieces):
        if not self.start <= day <= self.end:
            raise invalid_response()
        value = Decimal(str(quantity))
        if not value.is_finite() or abs(value) > 1_000_000_000:
            raise invalid_response()
        if value <= 0:
            return  # Gross sales exclude return and zero-quantity lines.
        key = day.isoformat(), product
        self.names[product] = name_text(name)
        each = pieces and value == value.to_integral_value()
        self.physical = self.physical and each
        if each:
            self.quantities[key] += int(value)
        self.lines[key] += 1
        if len(self.names) > 50 or len(self.lines) > 10_000:
            raise ServiceError(
                422,
                "За один импорт допустимы 50 товаров и 10 000 товаро-дней. Уменьшите период.",
            )


def poster_sales(reader, location, sales):
    try:
        catalog = reader.get("menu.getProducts")
    except ServiceError as exc:
        if exc.status_code != 403:
            raise
        # A receipts-only token can still measure item-line frequency.
        catalog = []
    if not isinstance(catalog, list):
        raise invalid_response()
    products = {positive_id(item["product_id"]): item for item in catalog}
    seen = set()
    start = sales.start
    step = (sales.end - sales.start).days + 1
    while start <= sales.end:
        end = min(start + timedelta(days=step - 1), sales.end)
        try:
            poster_period(reader, location, sales, products, seen, start, end)
        except RangeTooWide:
            if seen or step <= 3:
                raise
            step = 3
            continue
        start = end + timedelta(days=1)


def poster_period(reader, location, sales, products, seen, start, end):
    previous = len(seen)
    total = None
    page = 1
    while True:
        data = reader.get(
            "transactions.getTransactions",
            date_from=str(start),
            date_to=str(end),
            per_page=500,
            page=page,
        )
        count, receipts, info = int(data["count"]), data["data"], data["page"]
        if (
            not isinstance(receipts, list)
            or int(info["page"]) != page
            or int(info["count"]) != len(receipts)
            or count < len(seen) - previous + len(receipts)
            or (total is not None and count != total)
        ):
            raise invalid_response()
        if previous + count > MAX_DOCUMENTS:
            raise ServiceError(
                422, "За период больше 25 000 чеков. Выберите меньше дней."
            )
        total = count
        for doc in receipts:
            ident = positive_id(doc["transaction_id"])
            if ident in seen:
                raise invalid_response()
            seen.add(ident)
            if (
                positive_id(doc["spot_id"]) != location.id
                or str(doc["print_fiscal"]) == "2"
                or str(doc["pay_type"]) == "0"
            ):
                continue
            day = datetime.fromisoformat(doc["date_close"]).date()
            if not start <= day <= end:
                raise invalid_response()
            for line in doc["products"]:
                product = positive_id(line["product_id"])
                entry = products.get(product, {})
                name = entry.get("product_name") or f"Товар {product}"
                pieces = str(entry.get("weight_flag")) == "0" and entry.get("unit") in (
                    "",
                    "p",
                    "pcs",
                    "шт",
                )
                sales.add(day, product, name, line["num"], pieces)
        if len(seen) - previous == count:
            return
        if not receipts:
            raise invalid_response()
        page += 1


def moysklad_sales(reader, location, sales):
    path = "retaildemand"
    store_ref = MOYSKLAD + "retailstore/" + location.id
    filters = (
        f"retailStore={store_ref};applicable=true;moment>={sales.start} 00:00:00;"
        f"moment<{sales.end + timedelta(days=1)} 00:00:00"
    )
    for doc in page_rows(
        reader, path, filter=filters, expand="positions.assortment.uom"
    ):
        if (
            doc["retailStore"]["meta"]["href"] != store_ref
            or uuid_text(doc["accountId"]) != location.account
            or doc["applicable"] is not True
        ):
            raise invalid_response()
        day = datetime.fromisoformat(doc["moment"]).date()
        pos = doc["positions"]
        pos_path = f"{path}/{uuid_text(doc['id'])}/positions"
        for line in page_rows(
            reader,
            pos_path,
            first=pos if "rows" in pos else None,
            expand="assortment.uom",
        ):
            item = line["assortment"]
            meta = item["meta"]
            product = uuid_text(meta["href"].rsplit("/", 1)[-1])
            uom = item.get("uom") or {}
            pieces = (
                meta["type"] in {"product", "bundle", "variant"}
                and not line.get("pack")
                and uom.get("code") == "796"
            )
            sales.add(
                day,
                product,
                item.get("name") or f"Товар {product}",
                line["quantity"],
                pieces,
            )


BAD_PAYLOAD = (
    KeyError,
    TypeError,
    ValueError,
    InvalidOperation,
    ArithmeticError,
    AttributeError,
)
