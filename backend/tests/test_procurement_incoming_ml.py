import json

from fastapi.testclient import TestClient

from backend.app import app
from scripts.generate_cgz_synthetic import CATALOG_PATH, CSV_PATH


def test_incoming_stock_reduces_orders_for_selected_ml_and_fallback_b1(monkeypatch, tmp_path):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "incoming-ml.sqlite3"))
    with TestClient(app) as client:
        saved = client.post("/api/datasets", json={
            "schema_version": "procurement-1", "source": "synthetic",
            "bakery_id": "incoming-ml", "target": "sold_units", "unit": "physical_unit",
            "provenance": "Synthetic club sales and scheduled shipments",
            "catalog": json.loads(CATALOG_PATH.read_text(encoding="utf-8")),
            "csv_text": CSV_PATH.read_text(encoding="utf-8"),
        })
        assert saved.status_code == 201, saved.text
        dataset_id = saved.json()["id"]
        trained = client.post(f"/api/datasets/{dataset_id}/train", json={
            "cutoff": "2026-10-14", "idempotency_key": "incoming-test",
        })
        assert trained.status_code == 201, trained.text
        request = {
            "dataset_id": dataset_id, "model_id": trained.json()["id"],
            "method": "ML", "mode": "replay", "target_date": "2026-10-16",
            "cutoff": "2026-10-14", "lead_time_days": 1,
            "usable_stock_assumption": {"cola": 0, "water": 0},
        }
        before = client.post("/api/plans", json=request)
        assert before.status_code == 201, before.text
        original = {item["product_id"]: item for item in before.json()["items"]}
        assert original["cola"]["method"] == "ML"
        assert original["water"]["method"] == "B1"
        deliveries = []
        for product in ("cola", "water"):
            assert original[product]["recommended_qty"] > 0
            pack = original[product]["pack_size"]
            deliveries.extend([
                {"product_id": product, "quantity": pack, "arrival_date": "2026-10-16"},
                {"product_id": product, "quantity": pack, "arrival_date": "2026-10-17"},
            ])
        updated = client.post("/api/plans", json={**request, "expected_deliveries": deliveries})
        assert updated.status_code == 201, updated.text
        items = {item["product_id"]: item for item in updated.json()["items"]}
        for product in ("cola", "water"):
            old, new = original[product], items[product]
            assert new["method"] == old["method"]
            assert new["forecast_mean"] == old["forecast_mean"]
            assert new["incoming_units"] == new["available_stock_units"] == old["pack_size"]
            assert new["recommended_qty"] == old["recommended_qty"] - old["pack_size"]
