import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.storage import initialize_database
from tools.evaluate import read_rows


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "api.sqlite3"))
    with TestClient(app) as active:
        yield active


def csv_text():
    return Path("fixtures/synthetic_demo.csv").read_text("utf-8")


def make_plan(client, text=None, opened=True):
    dataset = client.post(
        "/api/datasets", json={"csv_text": text or csv_text(), "source": "synthetic"}
    ).json()
    return client.post(
        "/api/plans",
        json={
            "dataset_id": dataset["id"],
            "target_date": "2026-10-12",
            "cutoff": "2026-10-11",
            "mode": "replay",
            "target_is_open": opened,
        },
    ).json()


def test_api_offline_reject_same_base_contract(client, tmp_path):
    for invalid in [
        csv_text().replace(",true,", ",TRUE,"),
        csv_text().replace(
            "2026-08-17,p1,true,0,15,10,5,0,0,none",
            "2026-08-17,p1,false,0,15,10,5,0,0,none",
        ),
    ]:
        path = tmp_path / "invalid.csv"
        path.write_text(invalid, "utf-8")
        with pytest.raises(ValueError):
            read_rows(path)
        assert (
            client.post(
                "/api/datasets", json={"csv_text": invalid, "source": "synthetic"}
            ).status_code
            == 422
        )


def test_post_cutoff_rows_never_change_any_item(client):
    original = make_plan(client)
    for future in [
        "2026-10-12,p1,false,0,0,0,0,0,0,none\n",
        "2026-10-12,p1,true,0,999,999,0,0,0,unknown\n",
    ]:
        changed = make_plan(client, csv_text() + future)
        assert changed["items"] == original["items"]
        assert changed["config_hash"] == original["config_hash"]
    closed = make_plan(client, opened=False)
    assert all(
        i["forecast_mean"] is None and i["recommended_qty"] == 0
        for i in closed["items"]
    )


def test_413_json_contract_and_paginated_summaries(client):
    unsupported = client.post(
        "/api/datasets",
        json={
            "csv_text": csv_text(),
            "source": "synthetic",
            "schema_version": "unsupported",
        },
    )
    assert unsupported.status_code == 422
    response = client.post(
        "/api/datasets",
        content=b"x" * 2_000_001,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert client.get("/api/datasets").json() == []
    ids = [make_plan(client)["id"] for _ in range(3)]
    first = client.get("/api/plans?limit=2").json()
    assert [p["id"] for p in first] == list(reversed(ids))[:2]
    assert "items" not in first[0]
    assert client.get("/api/plans?limit=2&offset=2").json()[0]["id"] == ids[0]
    dataset = client.get("/api/datasets").json()[0]
    assert "rows" not in dataset
    assert client.get(f"/api/datasets/{dataset['id']}").json()["rows"]
    assert client.get("/api/plans?limit=101").status_code == 422


def test_concurrent_same_import_returns_one_dataset(client):
    with ThreadPoolExecutor(4) as pool:
        results = list(
            pool.map(
                lambda _: client.post(
                    "/api/datasets",
                    json={"csv_text": csv_text(), "source": "synthetic"},
                ),
                range(4),
            )
        )
    assert sorted(r.status_code for r in results) == [200, 200, 200, 201]
    assert len({r.json()["id"] for r in results}) == 1
    assert len(client.get("/api/datasets").json()) == 1


def test_legacy_plan_migration_confirm_export(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(path))
    with TestClient(app) as client:
        plan = make_plan(client)
        dataset = client.get(f"/api/datasets/{plan['dataset_id']}").json()
    # Recreate pre-v2 storage with a referenced duplicate, never discard legacy IDs.
    path.unlink()
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE datasets(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        con.execute("CREATE TABLE plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        con.execute(
            "CREATE TABLE revisions(plan_id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(plan_id,revision))"
        )
        con.execute(
            "CREATE TABLE confirmations(plan_id TEXT,key TEXT,body TEXT,response TEXT,PRIMARY KEY(plan_id,key))"
        )
        duplicate = {**dataset, "id": "legacy-duplicate"}
        for item in [dataset, duplicate]:
            con.execute(
                "INSERT INTO datasets VALUES (?,?)", (item["id"], json.dumps(item))
            )
        plan["dataset_id"] = duplicate["id"]
        payload = json.dumps(plan)
        con.execute("INSERT INTO plans VALUES (?,?)", (plan["id"], payload))
        con.execute("INSERT INTO revisions VALUES (?,?,?)", (plan["id"], 0, payload))
    with TestClient(app) as client:
        assert client.get(f"/api/plans/{plan['id']}").json() == plan
        assert len(client.get("/api/datasets").json()) == 2
        body = {
            "revision": 0,
            "idempotency_key": "migrated",
            "quantities": {
                i["product_id"]: i["recommended_qty"] for i in plan["items"]
            },
        }
        assert (
            client.post(f"/api/plans/{plan['id']}/confirm", json=body).status_code
            == 200
        )
        assert "synthetic" in client.get(f"/api/plans/{plan['id']}/export").text
        assert (
            client.get(f"/api/plans/{plan['id']}").json()["dataset_id"]
            == duplicate["id"]
        )
    initialize_database(path)
    assert path.with_name(path.name + ".pre-v2.bak").exists()
