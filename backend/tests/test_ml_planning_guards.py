"""Canonical planning policy must also apply to ML requests and fallbacks."""

from copy import deepcopy
from datetime import date, timedelta
import csv
import io

import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend import services
from backend.imports import REQUIRED


@pytest.mark.parametrize("trained", [False, True])
def test_active_catalog_keeps_canonical_b1_for_ml(monkeypatch, tmp_path, trained):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "test.sqlite3"))
    catalog = deepcopy(services.CATALOG)
    catalog[0]["active_from"] = "2026-01-29"
    monkeypatch.setattr(services, "CATALOG", catalog)
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(REQUIRED)
    for offset in range(70):
        day = date(2025, 11, 27) + timedelta(days=offset)
        sold = 10 if day >= date(2026, 1, 29) else 100
        writer.writerow([day, "p1", "true", 0, sold, sold, 0, 0, 0, "none"])
    with TestClient(app) as client:
        dataset = client.post(
            "/api/datasets",
            json={
                "csv_text": text.getvalue(),
                "source": "synthetic",
                "bakery_id": "activity",
                "target": "sold_units",
                "unit": "physical_unit",
                "provenance": "Synthetic test",
            },
        ).json()
        if trained:
            response = client.post(
                f"/api/datasets/{dataset['id']}/train",
                json={
                    "cutoff": "2026-02-04",
                    "idempotency_key": "train",
                },
            )
            assert response.status_code == 201
            assert response.json()["status"] == "ready"
        body = {
            "dataset_id": dataset["id"],
            "target_date": "2026-02-05",
            "cutoff": "2026-02-04",
            "mode": "replay",
        }
        b1 = client.post("/api/plans", json={**body, "method": "B1"}).json()["items"][0]
        ml = client.post("/api/plans", json={**body, "method": "ML"}).json()["items"][0]
        for key in (
            "forecast_mean",
            "recommended_qty",
            "used_dates",
            "n",
            "method",
            "automatic_eligible",
        ):
            assert ml[key] == b1[key]
        assert ml["forecast_mean"] == 10
        assert ml["recommended_qty"] == 12
        assert ml["fallback_reason"]
        assert ml["evaluation_applicability"]["applicable"] is False
        assert ml["evaluation_applicability"]["reason"] == "catalogue_history_filter"


def test_earliest_calendar_dates_are_structured_not_server_error(monkeypatch, tmp_path):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "early.sqlite3"))
    with TestClient(app) as client:
        dataset = client.post(
            "/api/datasets",
            json={
                "csv_text": "date,product_id,item_line_count\n0001-01-01,p1,1\n0001-01-02,p1,2\n",
                "source": "synthetic",
                "bakery_id": "dates",
                "schema_version": "sales-1",
                "target": "item_line_count",
                "unit": "item_line",
                "provenance": "Synthetic boundary test",
            },
        ).json()
        response = client.post(
            "/api/forecasts",
            json={
                "dataset_id": dataset["id"],
                "target_date": "0001-01-03",
                "cutoff": "0001-01-02",
                "method": "B1",
            },
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["forecast_mean"] is None
