"""Exercise the normal order workflow as two assigned administrators on a single DB."""

from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.branch_accounts import revoke_account, set_account
from backend.tests.test_api import body, sample
from backend.tests.test_procurement import payload


NORTH = ("north.admin", "north-fixture")
SOUTH = ("south.admin", "south-fixture")


def verifier(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "branches.sqlite3"))
    monkeypatch.delenv("BAKEPLAN_LOCAL_DEMO", raising=False)
    with TestClient(app) as client:
        yield client


def test_assignment_revoke_and_explicit_local_demo(client, monkeypatch, tmp_path):
    url = "/api/session"
    assert client.get("/api/health").is_success
    denied = client.get(url, headers={"X-Bakery-Id": "north", "X-Forwarded-User": NORTH[0]})
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"].startswith("Basic ")
    monkeypatch.setenv("BAKEPLAN_LOCAL_DEMO", "1")
    assert client.get(url).json() == {"bakery_id": None, "operator_id": None}
    assert client.get(url, headers={"Forwarded": "for=192.0.2.1"}).status_code == 401
    path = tmp_path / "fixture-users"
    path.write_text(f"{NORTH[0]} {verifier(NORTH[1])}\n", encoding="utf-8")
    command = [sys.executable, "-m", "backend.branch_accounts"]
    subprocess.run([*command, "assign", NORTH[0], "north", "--caddy-users", str(path)], check=True)
    for credentials in (None, (NORTH[0], "wrong"), SOUTH):
        assert client.get(url, auth=credentials).status_code == 401
    assert client.get(url, auth=NORTH).json() == {"bakery_id": "north", "operator_id": NORTH[0]}
    set_account(NORTH[0], "south", verifier(NORTH[1]))
    assert client.get(url, auth=NORTH).json()["bakery_id"] == "south"
    subprocess.run([*command, "revoke", NORTH[0]], check=True)
    assert client.get(url, auth=NORTH).status_code == 401
    assert client.get(url).status_code == 401


def test_two_admins_can_only_work_on_their_own_order(client):
    datasets, orders = {}, {}
    for auth, branch in ((NORTH, "north"), (SOUTH, "south")):
        set_account(auth[0], branch, verifier(auth[1]))
        imported = client.post("/api/datasets", auth=auth, json={**payload(), "bakery_id": branch})
        assert imported.status_code == 201, imported.text
        datasets[branch] = imported.json()["id"]
        request = dict(dataset_id=datasets[branch], cutoff="2026-10-09", target_date="2026-10-12",
                       method="B1", mode="replay", lead_time_days=1, usable_stock_assumption={"tea": 0})
        response = client.post("/api/plans", json=request, auth=auth)
        assert response.status_code == 201, response.text
        orders[branch] = response.json()
    south = orders["south"]
    url = f"/api/plans/{south['id']}"
    confirm_body = body(south)
    confirmed = client.post(url + "/confirm", json=confirm_body, auth=SOUTH)
    assert confirmed.status_code == 200, confirmed.text
    report = dict(bakery_id="north", start="2026-10-09", end="2026-10-15", source="synthetic", mode="replay")
    foreign = [
        client.get(url, auth=NORTH),
        client.get(url + "/revisions", auth=NORTH),
        client.get(url + "/export", auth=NORTH),
        client.get(url + "/export?revision=0", auth=NORTH),
        client.post(url + "/confirm", json=confirm_body, auth=NORTH),
        client.post(url + "/confirm", json={**confirm_body, "idempotency_key": "new"}, auth=NORTH),
        client.post("/api/plans", json=request, auth=NORTH),
        client.post("/api/forecasts", json={k: request[k] for k in ("dataset_id", "cutoff", "target_date", "method")},
                    auth=NORTH),
        client.get(f"/api/datasets/{datasets['south']}/deliveries", auth=NORTH),
        client.post("/api/datasets", json={**payload(), "bakery_id": "south"}, auth=NORTH),
        client.get("/api/reports/order-adjustments", params={**report, "bakery_id": "south"}, auth=NORTH),
        client.get("/api/reports/order-adjustments", params={**report, "actual_dataset_id": datasets["south"]}, auth=NORTH),
    ]
    missing = client.get("/api/plans/missing", auth=NORTH)
    for response in foreign:
        assert (response.status_code, response.json()) == (404, missing.json()), response.text
    assert client.get("/api/reports/order-adjustments", params=report, auth=NORTH).is_success
    for auth, branch in ((NORTH, "north"), (SOUTH, "south")):
        assert [p["id"] for p in client.get("/api/plans?limit=1", auth=auth).json()] == [orders[branch]["id"]]
        assert client.get("/api/plans?offset=1", auth=auth).json() == []
        assert [d["id"] for d in client.get("/api/datasets", auth=auth).json()] == [datasets[branch]]
    assert client.get(url + "/revisions", auth=SOUTH).json() == [south, confirmed.json()]
    exported = client.get(url + "/export?revision=1", auth=SOUTH)
    assert exported.is_success and "tea" in exported.text
    assert "no-store" in exported.headers["cache-control"] and "Authorization" in exported.headers["vary"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda auth: client.get(url, auth=auth).status_code, [NORTH, SOUTH] * 4))
    assert responses == [404, 200] * 4
    revoke_account(SOUTH[0])
    assert client.post(url + "/confirm", json=confirm_body, auth=SOUTH).status_code == 401


def test_import_without_branch_metadata_deduplicates_inside_each_branch(client):
    data = {"source": "local", "csv_text": sample()}
    ids = set()
    for auth, branch in ((NORTH, "north"), (SOUTH, "south")):
        set_account(auth[0], branch, verifier(auth[1]))
        first = client.post("/api/datasets", auth=auth, json=data)
        assert first.status_code == 201, first.text
        dataset = first.json()
        assert dataset["bakery_id"] == branch and dataset["metadata_declared"] is False
        assert client.post("/api/datasets", auth=auth, json=data).json()["id"] == dataset["id"]
        ids.add(dataset["id"])
    assert len(ids) == 2


def test_prefixed_installation_uses_the_same_login(client):
    set_account(NORTH[0], "north", verifier(NORTH[1]))
    with TestClient(app, root_path="/bakeplan") as prefixed:
        assert prefixed.get("/bakeplan/api/health").is_success
        assert prefixed.get("/bakeplan/api/plans").status_code == 401
        assert prefixed.get("/bakeplan/api/session", auth=NORTH).json()["bakery_id"] == "north"


def test_forecast_with_explicit_model_id_is_scoped_to_branch(client):
    datasets = []
    for auth, branch in ((NORTH, "north"), (SOUTH, "south")):
        set_account(auth[0], branch, verifier(auth[1]))
        imported = client.post("/api/datasets", auth=auth, json={
            "csv_text": sample(), "source": "synthetic", "bakery_id": branch,
            "provenance": "Synthetic branch fixture", "target": "sold_units", "unit": "physical_unit",
        })
        assert imported.status_code == 201, imported.text
        datasets.append(imported.json()["id"])
    trained = client.post(f"/api/datasets/{datasets[1]}/train", auth=SOUTH,
                          json={"cutoff": "2026-10-11", "idempotency_key": "training"})
    assert trained.status_code == 201, trained.text
    forecast = dict(dataset_id=datasets[1], model_id=trained.json()["id"], method="ML",
                    cutoff="2026-10-11", target_date="2026-10-12")
    assert client.post("/api/forecasts", auth=SOUTH, json=forecast).is_success
    for ident in (trained.json()["id"], "missing-model"):
        denied = client.post("/api/forecasts", auth=NORTH, json={**forecast, "dataset_id": datasets[0], "model_id": ident})
        assert denied.status_code == 404 and denied.json()["error"]["message"] == "Объект не найден"
