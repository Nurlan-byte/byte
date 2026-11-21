"""The operator gets the same selected forecast in preview, order, storage and export."""

import csv
from datetime import date, timedelta
from io import StringIO

import pytest
from fastapi.testclient import TestClient

from backend import chronos_order
from backend.app import app
from backend.tests.test_procurement import payload


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "auto.sqlite"))
    monkeypatch.setattr("backend.app.business_today", lambda: date(2026, 10, 10))
    monkeypatch.setattr("backend.context_routes.business_today", lambda: date(2026, 10, 10))
    cutoff = date(2026, 10, 9)
    data = payload()
    rows = [data["csv_text"].splitlines()[0]]
    for offset in range(70):
        day = cutoff - timedelta(days=69 - offset)
        for sku, sold in (("tea", 24), ("rare", int(day.weekday() == 4)), ("new", 3)):
            if sku != "new" or offset >= 68:
                rows.append(f"{day},{sku},0,{sold},{sold},0,0")
    data["csv_text"] = "\n".join(rows)
    data["catalog"] = [{"product_id": p, "name": p, "pack_size": 5} for p in ("tea", "rare", "new")]
    with TestClient(app) as client:
        ds = client.post("/api/datasets", json=data)
        assert ds.status_code == 201, ds.text
        yield client, {
            "dataset_id": ds.json()["id"], "target_date": "2026-10-11", "cutoff": str(cutoff),
            "lead_time_days": 1, "usable_stock_assumption": {"tea": 3, "rare": 0, "new": 0},
            "expected_deliveries": [{"product_id": "tea", "arrival_date": "2026-10-11", "quantity": 3}],
        }


def test_auto_selection_stays_with_single_order_through_export(catalog, monkeypatch):
    client, body = catalog
    monkeypatch.setattr(chronos_order, "_predict", lambda inputs, horizon: [14.5] * len(inputs))
    preview = client.post(f"/api/datasets/{body['dataset_id']}/first-forecast")
    assert preview.status_code == 200, preview.text
    assert preview.json()["method"] == "AUTO"
    detail = preview.json()["result"]
    for method in ({}, {"method": "AUTO"}):
        r = client.post("/api/forecasts", json={
            **{k: body[k] for k in ("dataset_id", "target_date", "cutoff")}, **method,
        })
        assert r.status_code == 200 and r.json() == detail
    response = client.post("/api/plans", json=body)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["requested_method"] == "AUTO"
    assert order["protocol_version"] == detail["protocol_version"] == chronos_order.PROTOCOL_VERSION
    assert order["model_id"] is None
    items = {r["product_id"]: r for r in order["items"]}
    assert [(r["method"], r["forecast_mean"]) for r in order["items"]] == [
        (r["method"], r["forecast_mean"]) for r in detail["items"]
    ]
    assert (items["tea"]["method"], items["tea"]["recommended_qty"]) == ("Chronos-2", 10)
    rare = items["rare"]
    assert rare["method"] == "B1" and rare["selection"]["b1_mae"] < rare["selection"]["chronos_mae"]
    assert rare["forecast_mean"] == rare["recommended_qty"] == 0
    assert items["new"]["eligible_weekday_n"] == 0 and items["new"]["recommended_qty"] == 15
    url = f"/api/plans/{order['id']}"
    assert client.get(url).json() == order
    assert client.get(f"/api/datasets/{body['dataset_id']}/models").json() == []
    confirm = client.post(url + "/confirm", json={
        "revision": 0, "idempotency_key": "operator", "quantities": {k: r["recommended_qty"] for k, r in items.items()}
    })
    assert confirm.status_code == 200 and confirm.json()["status"] == "confirmed"
    saved = list(csv.DictReader(StringIO(client.get(url + "/export").text.lstrip("\ufeff"))))
    assert [r["confirmed_order_units"] for r in saved] == ["10", "0", "15"]
    assert all(r["protocol_version"] == order["protocol_version"] for r in saved)

    # No usable stock is different from a known stock level of zero. Both POS
    # and ordinary CSV imports must leave a manual line blank on the order.
    incomplete = client.post("/api/plans", json={**body, "usable_stock_assumption": {"rare": 0, "new": 0}})
    assert incomplete.status_code == 201, incomplete.text
    rows = list(csv.DictReader(StringIO(client.get(f"/api/plans/{incomplete.json()['id']}/export").text.lstrip("\ufeff"))))
    assert [(row["suggested_order_units"], row["suggested_order_packs"]) for row in rows] == [
        ("", ""), ("0", "0"), ("15", "3"),
    ]


def test_unavailable_engine_never_turns_into_an_automatic_b1_order(catalog, monkeypatch):
    client, body = catalog

    def unavailable(*args):
        raise chronos_order.ChronosUnavailable("unavailable")

    monkeypatch.setattr(chronos_order, "_predict", unavailable)
    assert client.post("/api/plans", json=body).status_code == 503
    assert client.get("/api/plans").json() == []
    assert client.post(f"/api/datasets/{body['dataset_id']}/first-forecast").status_code == 503
    assert client.post("/api/plans", json={**body, "method": "AUTO", "model_id": "old-ridge"}).status_code == 422
    closed = client.post("/api/plans", json={**body, "target_is_open": False})
    assert closed.status_code == 201, closed.text
    assert all(item["recommended_qty"] == 0 for item in closed.json()["items"])
