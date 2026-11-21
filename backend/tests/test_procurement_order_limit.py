from fastapi.testclient import TestClient

from backend.app import app


def test_recommended_packs_fit_confirmation_limit_without_supplier_maximum(monkeypatch, tmp_path):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "order-limit.sqlite3"))
    dataset = {
        "schema_version": "procurement-1", "source": "synthetic", "bakery_id": "pack-limit",
        "target": "sold_units", "unit": "physical_unit", "provenance": "Order limit regression",
        "catalog": [{"product_id": "tea", "name": "Tea", "pack_size": 600_000_000}],
        "csv_text": "date,product_id,opening,received,sold,waste,closing\n2026-01-04,tea,0,1000000000,1000000000,0,0",
    }
    with TestClient(app) as client:
        imported = client.post("/api/datasets", json=dataset)
        assert imported.status_code == 201, imported.text
        response = client.post("/api/plans", json={
            "dataset_id": imported.json()["id"], "method": "B1", "mode": "replay",
            "target_date": "2026-01-11", "cutoff": "2026-01-09",
            "lead_time_days": 1, "usable_stock_assumption": {"tea": 0},
        })
        assert response.status_code == 201, response.text
        plan = response.json()
        item = plan["items"][0]
        assert item["forecast_mean"] == 1_000_000_000
        assert item["recommended_qty"] == 600_000_000
        assert item["max_constraint_active"]
        assert any("order quantity limit" in warning for warning in item["warnings"])
        saved = client.post(f"/api/plans/{plan['id']}/confirm", json={
            "revision": 0, "idempotency_key": "confirm-recommended",
            "quantities": {"tea": item["recommended_qty"]},
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["items"][0]["confirmed_qty"] == item["recommended_qty"]
