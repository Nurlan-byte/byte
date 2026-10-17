"""Regression evidence for the provisional, fully synthetic club-bar case."""

import csv
import json
import os
from collections import defaultdict
from datetime import date

from backend.procurement_imports import parse_procurement_csv
from scripts.generate_cgz_synthetic import (
    CATALOG_PATH, CONFIG, CSV_PATH, generate,
)
from scripts.replay_cgz_synthetic import replay


def test_synthetic_fixture_is_reproducible_balanced_and_has_friday_pattern():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["status"] == "provisional_synthetic_assumptions_pending_owner_interview"
    csv_text, catalog_text = generate(config)
    assert CSV_PATH.read_text(encoding="utf-8") == csv_text
    assert CATALOG_PATH.read_text(encoding="utf-8") == catalog_text
    catalog = json.loads(catalog_text)
    rows, warnings = parse_procurement_csv(
        csv_text, date(2026, 10, 17), {product["product_id"] for product in catalog}
    )
    assert len(rows) == 91 * 6 == 546
    assert warnings == []
    assert all(row["is_open"] is True for row in rows)
    assert 0 < sum(row["stockout_status"] == "occurred" for row in rows) < 10
    weekdays = defaultdict(list)
    for row in rows:
        weekdays[date.fromisoformat(row["date"]).weekday()].append(row["sold"])
    assert sum(weekdays[4]) / len(weekdays[4]) > sum(weekdays[0]) / len(weekdays[0])
    assert csv_text.startswith("date,product_id,opening,received,sold,waste,closing,is_open,stockout_status\n")
    assert len(list(csv.DictReader(csv_text.splitlines()))) == 546


def test_synthetic_api_replay_preserves_external_database(tmp_path, monkeypatch):
    sentinel_db = tmp_path / "external.sqlite3"
    sentinel_db.write_bytes(b"unrelated database must remain untouched")
    monkeypatch.setenv("BAKEPLAN_DB", str(sentinel_db))
    output = tmp_path / "synthetic-evidence"
    report = replay(output)
    assert os.environ["BAKEPLAN_DB"] == str(sentinel_db)
    assert sentinel_db.read_bytes() == b"unrelated database must remain untouched"
    assert report["source"].startswith("SYNTHETIC")
    assert report["rows"] == 546 and report["products"] == 6
    assert report["friday_target"] == "2026-10-16"
    assert report["forecast_cutoff"] == "2026-10-14"
    assert report["model_status"] == "ready"
    assert {item["method"] for item in report["forecast_items"]} == {"ML", "B1"}
    assert report["manual_adjustment"] == {
        "product_id": "water", "recommended": 24, "confirmed": 36
    }
    assert report["history_found"] and report["confirmed_revision"] == 1
    export = (output / "confirmed-order.csv").read_text(encoding="utf-8-sig")
    assert "forecast_sales_units" in export and "confirmed_order_units" in export
    assert json.loads((output / "replay.json").read_text(encoding="utf-8")) == report
