"""Admin workflow from observed POS units to an immutable, exportable order."""

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import context_routes, context_sources, pos, pos_providers
from backend.app import app

TODAY = date(2026, 10, 12)
PRODUCTS = {"31": "Чай", "32": "Вода"}
PACKS = {"31": 6, "32": 4}


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "orders.db"))
    monkeypatch.setenv("BAKEPLAN_POS_ENABLED", "1")
    monkeypatch.setattr(pos, "business_today", lambda: TODAY)
    monkeypatch.setattr(context_routes, "business_today", lambda: TODAY)
    monkeypatch.setattr(
        pos_providers, "locations", lambda _: [pos_providers.Location("1", "Клуб", "a")]
    )
    monkeypatch.setattr(context_sources, "load_demand_context", lambda *args: ({}, []))
    with TestClient(app) as client:

        def connect(physical=True):
            def receipts(reader, outlet, sales):
                for i in range(56):
                    day = TODAY - timedelta(days=i + 1)
                    for product, name in PRODUCTS.items():
                        sales.add(day, product, name, 13, physical)

            monkeypatch.setattr(pos_providers, "poster_sales", receipts)
            reply = client.post(
                "/api/pos/import", json={"provider": "poster", "api_key": "fixture"}
            )
            assert reply.status_code == 200, reply.text
            return reply.json()["dataset"]

        yield client, connect


def test_order_after_key_import_preserves_sales_and_unknown_stock(shop):
    client, connect = shop
    source = connect()
    source_url = f"/api/datasets/{source['id']}"
    reply = client.post(source_url + "/order-catalog", json={"packs": PACKS})
    assert reply.status_code == 201, reply.text
    ds = reply.json()
    assert (
        ds["planning_kind"] == "procurement"
        and ds["schema_version"] == "pos-procurement-1"
    )
    assert ds["forecast_only"] is False
    assert (
        ds["source_dataset_id"] == source["id"]
        and ds["source_dataset_version"] == source["version"]
    )
    assert ds["id"] != source["id"] and ds["version"] != source["version"]
    for field in (
        "rows",
        "row_count",
        "bakery_id",
        "pos_location",
        "demand_context",
        "target",
        "unit",
        "provenance",
    ):
        assert ds[field] == source[field]
    assert all(set(row) == {"product_id", "date", "sold"} for row in ds["rows"])
    assert ds["catalog"] == [
        {"product_id": p, "name": n, "pack_size": PACKS[p], "max_order_qty": None}
        for p, n in PRODUCTS.items()
    ]
    assert client.get(source_url).json() == source
    url = f"/api/datasets/{ds['id']}"
    assert client.get(url).json() == ds

    params = {
        "dataset_id": ds["id"],
        "target_date": "2026-10-11",
        "cutoff": "2026-10-09",
        "mode": "replay",
        "method": "B1",
        "lead_time_days": 1,
        "usable_stock_assumption": {"32": 2},
    }
    plan = client.post("/api/plans", json=params)
    assert plan.status_code == 201, plan.text
    plan = plan.json()
    assert plan["dataset_schema_version"] == "pos-procurement-1"
    tea, water = plan["items"]
    assert tea["unknown_stockout_n"] == tea["unknown_open_n"] == 54
    assert tea["method"] == "manual" and not tea["automatic_eligible"]
    assert (
        tea["available_stock_units"] is None and tea["usable_stock_assumption"] is None
    )
    assert tea["confirmed_qty"] is None and tea["forecast_mean"] == 13
    assert water["method"] == "B1" and water["recommended_qty"] == 12
    endpoint = f"/api/plans/{plan['id']}"

    def export(revision):
        reply = client.get(endpoint + f"/export?revision={revision}")
        assert reply.status_code == 200, reply.text
        return list(csv.DictReader(io.StringIO(reply.text.lstrip("\ufeff"))))

    draft = export(0)
    fields = ("suggested_order_units", "suggested_order_packs", "confirmed_order_units")
    assert [tuple(row[k] for k in fields) for row in draft] == [
        ("", "", ""),
        ("12", "3", ""),
    ]
    assert (
        client.post(
            endpoint + "/confirm",
            json={
                "revision": 0,
                "idempotency_key": "bad",
                "quantities": {"31": 5, "32": 12},
            },
        ).status_code
        == 422
    )
    saved = client.post(
        endpoint + "/confirm",
        json={
            "revision": 0,
            "idempotency_key": "good",
            "quantities": {"31": 18, "32": 12},
        },
    )
    assert saved.status_code == 200, saved.text
    assert client.get(endpoint).json() == saved.json()
    rows = export(1)
    assert [(r["name"], r["pack_size"], r["confirmed_order_units"]) for r in rows] == [
        ("Чай", "6", "18"),
        ("Вода", "4", "12"),
    ]
    assert (
        rows[0]["available_stock_units"] == ""
        and rows[1]["available_stock_units"] == "2"
    )
    saved_zero = client.post(
        endpoint + "/confirm",
        json={
            "revision": 1,
            "idempotency_key": "manual-zero",
            "quantities": {"31": 0, "32": 12},
        },
    )
    assert saved_zero.status_code == 200, saved_zero.text
    zero, automatic = export(2)
    assert tuple(zero[k] for k in fields) == ("", "", "0")
    assert zero["confirmed_order_packs"] == "0" and zero["available_stock_units"] == ""
    assert tuple(automatic[k] for k in fields) == ("12", "3", "12")
    assert export(0) == draft and export(1) == rows
    trained = client.post(
        url + "/train", json={"cutoff": params["cutoff"], "idempotency_key": "train"}
    )
    assert trained.status_code == 201, trained.text
    model = trained.json()
    assert model["counts"]["eligible_labels"] == 108
    assert model["protocol_version"].startswith("procurement-")
    forecast = client.post(
        "/api/forecasts",
        json={
            "dataset_id": ds["id"],
            "target_date": params["target_date"],
            "cutoff": params["cutoff"],
            "method": "ML",
            "model_id": model["id"],
        },
    )
    assert forecast.status_code == 200, forecast.text
    assert [item["forecast_mean"] for item in forecast.json()["items"]] == [13, 13]


def test_retries_and_new_weather_cannot_mix_pack_sizes(shop, monkeypatch):
    client, connect = shop
    ds = connect()
    endpoint = f"/api/datasets/{ds['id']}/order-catalog"

    def submit(packs):
        return client.post(endpoint, json={"packs": packs})

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(submit, [PACKS, dict(reversed(PACKS.items()))]))
    assert sorted(r.status_code for r in replies) == [200, 201]
    a, b = (r.json() for r in replies)
    assert a == b
    other = submit({"31": 8, "32": 4}).json()
    assert (
        other["id"] != a["id"]
        and other["demand_context_base_version"] != a["demand_context_base_version"]
    )
    context = {
        "calendar": [
            {"date": "2026-10-11", "available_on": "2026-01-01", "day_type": "day_off"}
        ]
    }
    monkeypatch.setattr(
        context_routes, "load_demand_context", lambda *args: (context, [])
    )
    updated = []
    for dataset in (a, other):
        url = f"/api/datasets/{dataset['id']}/demand-context"
        reply = client.post(url, json={})
        assert reply.status_code == 201, reply.text
        enriched = reply.json()["dataset"]
        assert (
            enriched["catalog"] == dataset["catalog"]
            and enriched["version"] != dataset["version"]
        )
        assert client.get(f"/api/datasets/{enriched['id']}").json() == enriched
        updated.append(enriched["id"])
    assert len(set(updated)) == 2


def test_ineligible_sources_and_wrong_packs_are_rejected(shop):
    client, connect = shop
    lines = connect(physical=False)
    assert lines["unit"] == "item_line"
    reply = client.post(
        f"/api/datasets/{lines['id']}/order-catalog", json={"packs": PACKS}
    )
    assert reply.status_code == 422
    physical_csv = client.post(
        "/api/datasets",
        json={
            "csv_text": "date,product_id,sold\n2026-10-01,31,3",
            "schema_version": "pos-units-1",
            "source": "local",
            "target": "sold_units",
            "unit": "physical_unit",
            "bakery_id": "store",
            "provenance": "CSV",
        },
    ).json()
    assert (
        client.post(
            f"/api/datasets/{physical_csv['id']}/order-catalog",
            json={"packs": {"31": 6}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/datasets/missing/order-catalog", json={"packs": PACKS}
        ).status_code
        == 404
    )
    source = connect()
    url = f"/api/datasets/{source['id']}/order-catalog"
    for packs in [
        {},
        {"31": 8},
        {**PACKS, "absent": 4},
        *[{"31": p, "32": 3} for p in (0, -1, True, "2", 2.5, 10**9 + 1)],
    ]:
        assert client.post(url, json={"packs": packs}).status_code == 422
    assert client.post(url, json={"packs": PACKS, "stock": 0}).status_code == 422
    derived = client.post(url, json={"packs": {"31": 1, "32": 10**9}}).json()
    assert (
        client.post(
            f"/api/datasets/{derived['id']}/order-catalog", json={"packs": PACKS}
        ).status_code
        == 422
    )
