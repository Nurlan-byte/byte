"""The public order responses must preserve immutable revisions, including old ones."""

import pytest
from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from backend.app import app
from backend.storage import save_plan
from backend.tests.test_api import sample
from backend.tests.test_procurement import payload


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "contract.sqlite3"))
    with TestClient(app) as client:
        yield client


def test_make_and_confirm_advertise_their_response_shape(client):
    schema = client.get("/openapi.json").json()
    for path, status in [("/api/plans", "201"), ("/api/plans/{ident}/confirm", "200")]:
        response = schema["paths"][path]["post"]["responses"][status]["content"]["application/json"]
        name = response["schema"]["$ref"].split("/")[-1]
        plan = schema["components"]["schemas"][name]
        assert {"id", "revision", "status", "items"} <= set(plan["required"])
        item_ref = plan["properties"]["items"]["items"]["$ref"]
        line = schema["components"]["schemas"][item_ref.split("/")[-1]]
        assert {"product_id", "forecast_mean", "recommended_qty", "confirmed_qty"} <= set(line["required"])


@pytest.mark.parametrize("procurement", [False, True], ids=["production", "supplier"])
def test_api_returns_persisted_draft_and_confirmed_revision(client, procurement):
    imported = client.post("/api/datasets", json=payload() if procurement else {"csv_text": sample(), "source": "synthetic"})
    assert imported.status_code == 201, imported.text
    request = {"dataset_id": imported.json()["id"], "target_date": "2026-10-12", "cutoff": "2026-10-09",
               "method": "B1", "mode": "replay"}
    if procurement:
        request.update(lead_time_days=1, usable_stock_assumption={"tea": 0}, expected_deliveries=[
            {"product_id": "tea", "arrival_date": "2026-10-11", "quantity": 3},
        ])
    response = client.post("/api/plans", json=request)
    assert response.status_code == 201, response.text
    draft = response.json()
    url = f"/api/plans/{draft['id']}"
    assert draft == client.get(url).json()
    assert "confirmed_at" not in draft
    assert all(line["confirmed_qty"] is None for line in draft["items"])
    if procurement:
        assert draft["expected_deliveries"] == request["expected_deliveries"]
        assert "batch_size" not in draft["items"][0]
    else:
        assert "planning_kind" not in draft
        assert "pack_size" not in draft["items"][0]
    body = {"revision": 0, "idempotency_key": "confirm-once",
            "quantities": {line["product_id"]: line["recommended_qty"] for line in draft["items"]}}
    response = client.post(url + "/confirm", json=body)
    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert confirmed["status"] == "confirmed" and confirmed["revision"] == 1
    assert client.get(url + "/revisions").json() == [draft, confirmed]
    assert client.post(url + "/confirm", json=body).json() == confirmed


def test_old_supplier_snapshot_preserves_optional_fields_and_numeric_representation(client):
    old = {
        "id": "archived-order", "dataset_id": "old-import", "revision": 0, "status": "draft",
        "source": "synthetic", "target_date": "2026-10-12", "cutoff": "2026-10-09",
        "planning_kind": "procurement", "mode": "replay", "legacy_origin": "old-importer",
        "items": [{"product_id": "tea", "name": "Tea", "pack_size": 6, "max_order_qty": None,
                   "forecast_mean": 12, "recommended_qty": 12, "confirmed_qty": None, "method": "B1",
                   "legacy_note": "supplier A"}],
    }
    save_plan(old)
    request = {"revision": 0, "idempotency_key": "original", "quantities": {"tea": 6}}
    url = f"/api/plans/{old['id']}"
    response = client.post(url + "/confirm", json=request)
    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert confirmed == client.get(url).json()
    assert confirmed["legacy_origin"] == "old-importer"
    assert confirmed["items"] == [{**old["items"][0], "confirmed_qty": 6}]
    assert type(confirmed["items"][0]["forecast_mean"]) is int
    edit = {"revision": 1, "idempotency_key": "edit", "quantities": {"tea": 0}}
    assert client.post(url + "/confirm", json=edit).json()["revision"] == 2
    assert client.post(url + "/confirm", json=request).json() == confirmed


def test_api_rejects_malformed_success_payload(client, monkeypatch):
    request = {"dataset_id": "test", "target_date": "2026-10-12", "cutoff": "2026-10-09"}
    malformed = {**request, "id": "p", "revision": "0", "source": "synthetic", "status": "draft", "items": []}
    monkeypatch.setattr("backend.app.build_plan", lambda *_: malformed)
    with pytest.raises(ResponseValidationError) as exc:
        client.post("/api/plans", json=request)
    assert any(e["loc"] == ("response", "revision") for e in exc.value.errors())
