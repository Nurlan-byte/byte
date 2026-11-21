"""One-shot key -> outlet -> sales -> forecast contracts with documented vendor payloads."""

from datetime import date, timedelta
from http.client import IncompleteRead
from io import BytesIO
import gzip
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend import context_sources, pos, pos_providers
from backend.app import app

KEY = "test-key-never-stored"
TODAY = date(2026, 10, 12)


def uid(number):
    return str(UUID(int=number))


class Response(BytesIO):
    def __init__(self, data):
        super().__init__(gzip.compress(json.dumps(data).encode()))
        self.headers = {"Content-Encoding": "gzip"}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "pos.sqlite"))
    monkeypatch.setenv("BAKEPLAN_POS_ENABLED", "1")
    monkeypatch.setattr(pos, "business_today", lambda: TODAY)
    context_calls = []

    def weather(start, end, as_of, city):
        context_calls.append((start, end, as_of, city))
        return {"calendar": [], "weather": []}, []

    monkeypatch.setattr(context_sources, "load_demand_context", weather)
    with TestClient(app) as client:
        yield client, tmp_path, context_calls


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def open(self, req, timeout):
        url = urlsplit(req.full_url)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        assert req.get_method() == "GET" and url.scheme == "https" and 0 < timeout <= 10
        assert req.data is None
        if url.netloc == "joinposter.com":
            assert query.pop("token") == KEY and not req.get_header("Authorization")
        else:
            assert (
                url.netloc == "api.moysklad.ru"
                and req.get_header("Authorization") == "Bearer " + KEY
            )
            assert KEY not in req.full_url
        self.calls.append((url.path, query))
        return Response(self.handler(url.path, query))


def poster_page(number, total, receipts):
    return {
        "response": {
            "count": total,
            "page": {"page": number, "count": len(receipts)},
            "data": receipts,
        }
    }


def receipt(ident, day, *, store=1, returned=False, paid=True, qty=3):
    return {
        "transaction_id": ident,
        "spot_id": store,
        "print_fiscal": 2 if returned else 1,
        "pay_type": 1 if paid else 0,
        "date_close": day + " 12:00:00",
        "client_firstname": "PRIVATE_CUSTOMER",
        "products": [{"product_id": 31, "num": qty}],
    }


def test_poster_choose_then_import_full_readback_and_branch_scope(
    environment, monkeypatch, caplog
):
    client, tmp, contexts = environment
    account = ["tenant-A"]

    def vendor(path, query):
        if path.endswith("spots.getSpots"):
            return {
                "response": [
                    {"spot_id": 1, "name": "A", "address": "г. Шымкент, ул. Пример"},
                    {"spot_id": 2, "name": "B"},
                ]
            }
        if path.endswith("settings.getAllSettings"):
            return {"response": {"COMPANY_ID": account[0]}}
        if path.endswith("menu.getProducts"):
            return {
                "response": [
                    {
                        "product_id": "31",
                        "product_name": "Хлеб",
                        "unit": "",
                        "weight_flag": "0",
                    }
                ]
            }
        assert path.endswith("transactions.getTransactions")
        assert query["date_from"] == "2026-08-17" and query["date_to"] == "2026-10-11"
        if query["page"] == "1":
            return poster_page(
                1,
                5,
                [
                    receipt(1, "2026-10-01"),
                    receipt(2, "2026-10-01", store=2),
                    receipt(3, "2026-10-02", returned=True),
                ],
            )
        return poster_page(
            2,
            5,
            [receipt(4, "2026-10-08", qty=5), receipt(5, "2026-10-08", paid=False)],
        )

    transport = FakeTransport(vendor)
    monkeypatch.setattr(pos_providers, "build_opener", lambda *args: transport)
    body = {"provider": "poster", "api_key": KEY}
    assert client.get("/api/pos/status").json() == {"enabled": True}
    assert client.post("/api/pos/import", json=body).json() == {
        "status": "choose_location",
        "locations": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}],
    }
    assert client.get("/api/datasets").json() == [] and contexts == []
    assert (
        client.post("/api/pos/import", json={**body, "location_id": "3"}).status_code
        == 422
    )
    result = client.post("/api/pos/import", json={**body, "location_id": "1"})
    assert result.status_code == 200, result.text
    ds = result.json()["dataset"]
    assert ds["rows"] == [
        {"date": "2026-10-01", "product_id": "31", "sold": 3},
        {"date": "2026-10-08", "product_id": "31", "sold": 5},
    ]
    assert (
        ds["forecast_only"]
        and ds["unit"] == "physical_unit"
        and ds["product_names"] == {"31": "Хлеб"}
    )
    assert contexts[-1] == (date(2026, 8, 17), date(2026, 10, 13), TODAY, "shymkent")
    assert client.get("/api/datasets/" + ds["id"]).json() == ds
    retry = client.post("/api/pos/import", json={**body, "location_id": "1"})
    assert retry.json()["dataset"]["id"] == ds["id"]
    plan = client.post(
        "/api/plans",
        json={
            "dataset_id": ds["id"],
            "target_date": "2026-10-09",
            "cutoff": "2026-10-08",
            "mode": "replay",
        },
    )
    assert plan.status_code == 422 and "размеры упаковок" in plan.text
    forecast = client.post(
        "/api/forecasts",
        json={
            "dataset_id": ds["id"],
            "target_date": "2026-10-15",
            "cutoff": "2026-10-11",
            "method": "B1",
        },
    )
    assert forecast.status_code == 200, forecast.text
    assert forecast.json()["items"][0]["forecast_mean"] == 4.0
    account[0] = "tenant-B"
    other = client.post("/api/pos/import", json={**body, "location_id": "1"}).json()[
        "dataset"
    ]
    assert ds["bakery_id"] != other["bakery_id"] and ds["id"] != other["id"]
    assert (
        KEY not in result.text + caplog.text and "PRIVATE_CUSTOMER" not in result.text
    )
    for file in tmp.glob("pos.sqlite*"):
        assert (
            KEY.encode() not in file.read_bytes()
            and b"PRIVATE_CUSTOMER" not in file.read_bytes()
        )


def ms_page(rows, *, size=None, offset=0):
    return {
        "meta": {"offset": offset, "size": len(rows) if size is None else size},
        "rows": rows,
    }


def test_poster_splits_range_and_preserves_zeros_when_menu_permission_is_missing(
    environment, monkeypatch
):
    client, _, _ = environment
    requested = []

    def vendor(path, query):
        if path.endswith("spots.getSpots"):
            return {"response": [{"spot_id": 1, "name": "Клуб"}]}
        if path.endswith("settings.getAllSettings"):
            return {"response": {"COMPANY_ID": "example-account"}}
        if path.endswith("menu.getProducts"):
            return {"error": {"code": 12}}
        start, end = (
            date.fromisoformat(query["date_from"]),
            date.fromisoformat(query["date_to"]),
        )
        requested.append((start, end))
        if (end - start).days >= 3:
            return {"error": {"code": 52}}
        receipts = []
        for day in range(start.toordinal(), end.toordinal() + 1):
            doc = receipt(day, date.fromordinal(day).isoformat())
            if day == TODAY.toordinal() - 3:
                doc["products"] = [{"product_id": 32, "num": 5}]
            receipts.append(doc)
        return poster_page(1, len(receipts), receipts)

    monkeypatch.setattr(
        pos_providers, "build_opener", lambda *args: FakeTransport(vendor)
    )
    result = client.post(
        "/api/pos/import", json={"provider": "poster", "api_key": KEY, "days": 4}
    )
    assert result.status_code == 200, result.text
    assert requested == [
        (TODAY - timedelta(days=4), TODAY - timedelta(days=1)),
        (TODAY - timedelta(days=4), TODAY - timedelta(days=2)),
        (TODAY - timedelta(days=1), TODAY - timedelta(days=1)),
    ]
    ds = result.json()["dataset"]
    assert ds["unit"] == "item_line"
    assert [
        row["item_line_count"] for row in ds["rows"] if row["product_id"] == "31"
    ] == [1, 0, 1, 1]
    assert [
        row["item_line_count"] for row in ds["rows"] if row["product_id"] == "32"
    ] == [1]


@pytest.mark.parametrize(
    "weight_code,expected_field,expected_value",
    [("796", "sold", 7), ("166", "item_line_count", 2)],
)
def test_moysklad_only_outlet_expanded_position_pagination_and_units(
    environment, monkeypatch, weight_code, expected_field, expected_value
):
    client, _, contexts = environment
    store, account, ident, item = (uid(i) for i in range(1, 5))
    base = pos_providers.MOYSKLAD
    cashbox = {"id": store, "accountId": account, "name": "Магазин", "active": True}

    def line(number, qty):
        return {
            "id": uid(number),
            "quantity": qty,
            "assortment": {
                "name": "Чай",
                "uom": {"code": weight_code},
                "meta": {"href": base + "product/" + item, "type": "product"},
            },
        }

    def vendor(path, query):
        if path.endswith("/retailstore"):
            return ms_page([cashbox])
        if path.endswith("/retaildemand"):
            assert "retailStore=" + base + "retailstore/" + store in query["filter"]
            assert "moment<2026-10-12 00:00:00" in query["filter"]
            return ms_page(
                [
                    {
                        "id": ident,
                        "moment": "2026-10-11 19:25:00",
                        "applicable": True,
                        "retailStore": {
                            "meta": {"href": base + "retailstore/" + store}
                        },
                        "accountId": account,
                        "positions": ms_page([line(9, 3)], size=2),
                    }
                ]
            )
        assert path.endswith(f"/retaildemand/{ident}/positions")
        assert query["offset"] == "1"
        return ms_page([line(10, 4)], size=2, offset=1)

    monkeypatch.setattr(
        pos_providers, "build_opener", lambda *args: FakeTransport(vendor)
    )
    result = client.post(
        "/api/pos/import", json={"provider": "moysklad", "api_key": KEY, "city": "none"}
    )
    assert result.status_code == 200, result.text
    ds = result.json()["dataset"]
    assert ds["rows"] == [
        {"date": "2026-10-11", "product_id": item, expected_field: expected_value}
    ]
    assert ds["forecast_only"] and ds["source"] == "local" and contexts[-1][-1] is None


def test_secret_unavailable_errors_and_inconsistent_pages_are_atomic(
    environment, monkeypatch
):
    client, _, _ = environment
    body = {"provider": "poster", "api_key": KEY}
    monkeypatch.delenv("BAKEPLAN_POS_ENABLED")
    assert client.get("/api/pos/status").json() == {"enabled": False}
    assert client.post("/api/pos/import", json=body).status_code == 403
    monkeypatch.setenv("BAKEPLAN_POS_ENABLED", "1")
    invalid = client.post("/api/pos/import", json={**body, "api_key": KEY + "\n"})
    assert invalid.status_code == 422 and KEY not in invalid.text

    def handler(path, query):
        raise HTTPError("https://joinposter.com/?token=" + KEY, 401, KEY, {}, BytesIO())

    monkeypatch.setattr(
        pos_providers, "build_opener", lambda *args: FakeTransport(handler)
    )
    rejected = client.post("/api/pos/import", json=body)
    assert rejected.status_code == 403 and KEY not in rejected.text

    def interrupted(path, query):
        raise IncompleteRead(KEY.encode())

    monkeypatch.setattr(
        pos_providers, "build_opener", lambda *args: FakeTransport(interrupted)
    )
    interrupted_response = client.post("/api/pos/import", json=body)
    assert (
        interrupted_response.status_code == 502 and KEY not in interrupted_response.text
    )
    assert (
        pos_providers.NoRedirect().redirect_request(None, None, None, None, None, None)
        is None
    )

    def partial(path, query):
        if path.endswith("spots.getSpots"):
            return {"response": [{"spot_id": 1, "name": "A"}]}
        if path.endswith("settings.getAllSettings"):
            return {"response": {"COMPANY_ID": "tenant"}}
        if path.endswith("menu.getProducts"):
            return {"response": []}
        return poster_page(int(query["page"]), 2, [receipt(1, "2026-10-01")])

    monkeypatch.setattr(
        pos_providers, "build_opener", lambda *args: FakeTransport(partial)
    )
    assert client.post("/api/pos/import", json=body).status_code == 502
    assert client.get("/api/datasets").json() == []
