from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.storage import connection


ORDER = {"number": "INV-12", "product_id": "water", "arrival_date": "2026-11-24", "ordered_qty": 12}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "ledger.sqlite3"))
    with TestClient(app) as active:
        yield active


def ledger(client, branch="north", source_note="initial import"):
    response = client.post("/api/datasets", json={
        "schema_version": "procurement-1", "source": "synthetic", "bakery_id": branch,
        "target": "sold_units", "unit": "physical_unit", "provenance": source_note,
        "catalog": [
            {"product_id": "water", "name": "Water", "pack_size": 6},
            {"product_id": "tea", "name": "Tea", "pack_size": 4},
        ],
        "csv_text": "date,product_id,opening,received,sold,waste,closing\n"
                    "2026-01-01,water,1,2,3,0,0\n2026-01-01,tea,1,2,3,0,0",
    })
    assert response.status_code == 201, response.text
    return f"/api/datasets/{response.json()['id']}/deliveries"


def test_partial_receipt_reschedule_retries_and_cancellation(client):
    url = ledger(client)
    created = client.post(url, json=ORDER)
    assert created.status_code == 201
    original = created.json()
    item = f"{url}/{original['id']}"
    assert (original["remaining_qty"], original["received_qty"], original["status"]) == (12, 0, "ordered")
    body = {"idempotency_key": "unload-1", "quantity": 5}
    first = client.post(item + "/receipts", json=body)
    assert first.status_code == 200
    row = first.json()
    assert (row["remaining_qty"], row["received_qty"], row["status"]) == (7, 5, "partial")

    changed = client.patch(item, json={"arrival_date": "2026-11-25"}).json()
    assert changed["arrival_date"] == "2026-11-25"
    assert client.post(url, json=ORDER).json() == changed
    assert client.post(url, json={**ORDER, "ordered_qty": 14}).status_code == 409
    assert client.post(item + "/receipts", json={**body, "quantity": 4}).status_code == 409
    assert client.post(item + "/receipts", json={"idempotency_key": "too-many", "quantity": 8}).status_code == 409
    cancelled = client.post(item + "/cancel").json()
    assert (cancelled["remaining_qty"], cancelled["received_qty"], cancelled["status"]) == (0, 5, "cancelled")
    assert client.post(item + "/cancel").json() == cancelled
    assert client.post(item + "/receipts", json={"idempotency_key": "too-late", "quantity": 1}).status_code == 409
    assert client.patch(item, json={"arrival_date": "2026-11-26"}).status_code == 409
    assert client.post(item + "/receipts", json=body).json() == row
    with TestClient(app) as restarted:
        assert restarted.get(url).json() == [cancelled]
        assert restarted.post(item + "/receipts", json=body).json() == row
    with connection() as con:
        qty, stamp = con.execute("SELECT quantity, received_at FROM delivery_receipts").fetchone()
        assert qty == 5 and stamp


def test_same_branch_reimports_share_the_register_and_other_branches_are_isolated(client):
    north = ledger(client)
    second_import = ledger(client, source_note="new sales file")
    south = ledger(client, branch="south")
    row = client.post(north, json=ORDER).json()
    assert client.get(second_import).json() == [row]
    assert client.get(south).json() == []
    assert client.post(second_import, json=ORDER).json() == row
    local = client.post(south, json=ORDER)
    assert local.status_code == 201
    assert local.json()["id"] != row["id"]
    assert client.post(north, json={**ORDER, "product_id": "tea"}).status_code == 201
    assert client.post(north, json={**ORDER, "product_id": "missing"}).status_code == 422
    wrong = f"{south}/{row['id']}"
    receipt = {"idempotency_key": "all", "quantity": 12}
    assert client.post(wrong + "/receipts", json=receipt).status_code == 404
    assert client.patch(wrong, json={"arrival_date": ORDER["arrival_date"]}).status_code == 404
    assert client.post(wrong + "/cancel").status_code == 404
    completed = client.post(f"{second_import}/{row['id']}/receipts", json=receipt).json()
    assert (completed["received_qty"], completed["remaining_qty"], completed["status"]) == (12, 0, "received")
    assert client.post(wrong + "/receipts", json=receipt).status_code == 404
    assert client.post(f"{north}/{row['id']}/cancel").status_code == 409
    assert client.get(south).json()[0]["remaining_qty"] == 12


def test_invalid_registry_inputs_write_nothing(client):
    url = ledger(client)
    for bad in ({"number": "  "}, {"arrival_date": "2026-02-31"}, {"arrival_date": 0}, {"ordered_qty": 0},
                {"ordered_qty": 1.5}, {"ordered_qty": True}, {"ordered_qty": 1_000_000_001}):
        assert client.post(url, json={**ORDER, **bad}).status_code == 422
    assert client.get(url).json() == []
    row = client.post(url, json=ORDER).json()
    item = f"{url}/{row['id']}"
    for bad in ({"quantity": 0}, {"quantity": 1.2}, {"quantity": True}, {"idempotency_key": " "}):
        assert client.post(item + "/receipts", json={"quantity": 1, "idempotency_key": "x", **bad}).status_code == 422
    assert client.get(url).json() == [row]
    assert client.get("/api/datasets/missing/deliveries").status_code == 404
    production = client.post("/api/datasets", json={
        "source": "synthetic",
        "csv_text": "date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status\n"
                    "2026-01-01,p1,true,0,12,12,0,0,0,none",
    })
    assert production.status_code == 201, production.text
    assert client.get(f"/api/datasets/{production.json()['id']}/deliveries").status_code == 422


@pytest.mark.parametrize("same_key,expected_statuses", [(True, [200, 200]), (False, [200, 409])])
def test_concurrent_receipts_cannot_count_the_same_or_unavailable_goods_twice(client, same_key, expected_statuses):
    url = ledger(client)
    row = client.post(url, json=ORDER).json()
    endpoint = f"{url}/{row['id']}/receipts"
    barrier = Barrier(2)

    def receive(key):
        barrier.wait(timeout=10)
        return client.post(endpoint, json={"idempotency_key": key, "quantity": 8})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(receive, ["first", "first" if same_key else "second"]))
    assert sorted(result.status_code for result in results) == expected_statuses
    only, = client.get(url).json()
    assert (only["received_qty"], only["remaining_qty"]) == (8, 4)
    with connection() as con:
        assert con.execute("SELECT count(*) FROM delivery_receipts").fetchone()[0] == 1
