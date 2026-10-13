"""Reject parser-level failures atomically, including ignored note columns."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.mark.parametrize("sales_only", [False, True])
@pytest.mark.parametrize("malformed", ['"unterminated', "x" * 140_000])
def test_malformed_header_is_actionable_422(
    tmp_path, monkeypatch, sales_only, malformed
):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "format.sqlite3"))
    body = {"csv_text": malformed, "source": "synthetic"}
    if sales_only:
        body.update(
            schema_version="sales-1",
            bakery_id="format-test",
            target="item_line_count",
            unit="item_line",
            provenance="Synthetic parser regression",
        )
    with TestClient(app) as client:
        response = client.post("/api/datasets", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_CSV"
        assert "CSV" in response.json()["error"]["details"][0]
        assert client.get("/api/datasets").json() == []


def test_unterminated_ignored_note_is_rejected_without_partial_import(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "note.sqlite3"))
    lines = Path("fixtures/synthetic_demo.csv").read_text("utf-8").splitlines()
    malformed = (
        lines[0] + ",note\n" + lines[1] + ",valid\n" + lines[2] + ',"unterminated'
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/datasets", json={"csv_text": malformed, "source": "synthetic"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_CSV"
        assert client.get("/api/datasets").json() == []
        # Properly quoted multiline notes remain supported and ignored.
        valid = lines[0] + ",note\n" + lines[1] + ',"line one\nline two"\n'
        response = client.post(
            "/api/datasets", json={"csv_text": valid, "source": "synthetic"}
        )
        assert response.status_code == 201
        assert response.json()["row_count"] == 1


def test_sales_unterminated_count_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "sales-format.sqlite3"))
    with TestClient(app) as client:
        response = client.post(
            "/api/datasets",
            json={
                "csv_text": 'date,product_id,item_line_count\n2026-09-01,p1,12\n2026-09-02,p1,"13',
                "source": "synthetic",
                "schema_version": "sales-1",
                "bakery_id": "format-test",
                "target": "item_line_count",
                "unit": "item_line",
                "provenance": "Synthetic parser regression",
            },
        )
        assert response.status_code == 422
        assert client.get("/api/datasets").json() == []
