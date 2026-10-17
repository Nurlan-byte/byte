"""Exercise the synthetic club-bar procurement flow in a fresh local SQLite DB."""

import argparse
import hashlib
import json
import os
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import app
from scripts.generate_cgz_synthetic import CATALOG_PATH, CONFIG, CSV_PATH


def replay(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    csv_text = CSV_PATH.read_text(encoding="utf-8")
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    output.mkdir(parents=True)
    database = output / "synthetic-replay.sqlite3"
    previous_db = os.environ.get("BAKEPLAN_DB")
    os.environ["BAKEPLAN_DB"] = str(database)
    try:
        with TestClient(app) as client:
            def call(method: str, path: str, **kwargs):
                response = client.request(method, path, **kwargs)
                assert response.status_code in (200, 201), (
                    method, path, response.status_code, response.text
                )
                return response

            dataset = call("POST", "/api/datasets", json={
                "schema_version": "procurement-1", "source": "synthetic",
                "bakery_id": config["site_id"], "target": "sold_units",
                "unit": "physical_unit",
                "provenance": "Fully synthetic club-bar fixture; provisional assumptions, not owner receipts",
                "catalog": catalog, "csv_text": csv_text,
            }).json()
            assert dataset["planning_kind"] == "procurement"
            assert len(dataset["rows"]) == config["days"] * len(catalog)
            assert dataset["continuity_warnings"] == []
            cutoff, target = date(2026, 10, 14), date(2026, 10, 16)
            model = call(
                "POST", f"/api/datasets/{dataset['id']}/train",
                json={"cutoff": cutoff.isoformat(), "idempotency_key": "cgz-synthetic-friday"},
            ).json()
            forecast = call("POST", "/api/forecasts", json={
                "dataset_id": dataset["id"], "target_date": target.isoformat(),
                "cutoff": cutoff.isoformat(), "method": "ML", "model_id": model["id"],
            }).json()
            assert all(
                day <= cutoff.isoformat()
                for item in forecast["items"] for day in item["used_dates"]
            )
            stock = {product["product_id"]: 0 for product in catalog}
            plan = call("POST", "/api/plans", json={
                "dataset_id": dataset["id"], "target_date": target.isoformat(),
                "cutoff": cutoff.isoformat(), "method": "ML", "model_id": model["id"],
                "mode": "replay", "lead_time_days": 1,
                "usable_stock_assumption": stock,
            }).json()
            quantities = {item["product_id"]: item["recommended_qty"] for item in plan["items"]}
            first = plan["items"][0]
            pack, maximum = first["pack_size"], first["max_order_qty"]
            recommendation = quantities[first["product_id"]]
            adjusted = recommendation + pack
            if maximum is not None and adjusted > maximum:
                adjusted = max(0, recommendation - pack)
            assert adjusted != recommendation
            quantities[first["product_id"]] = adjusted
            confirmed = call("POST", f"/api/plans/{plan['id']}/confirm", json={
                "revision": plan["revision"],
                "idempotency_key": "cgz-synthetic-manual-adjustment",
                "quantities": quantities,
            }).json()
            history = call("GET", "/api/plans?limit=100").json()
            assert any(entry["id"] == plan["id"] for entry in history)
            assert call("GET", f"/api/plans/{plan['id']}").json()["status"] == "confirmed"
            export = call("GET", f"/api/plans/{plan['id']}/export").content
            (output / "confirmed-order.csv").write_bytes(export)
        report = {
            "source": "SYNTHETIC; generated from provisional assumptions, no owner receipts",
            "csv_sha256": hashlib.sha256(CSV_PATH.read_bytes()).hexdigest(),
            "catalog_sha256": hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest(),
            "rows": len(dataset["rows"]), "days": config["days"],
            "products": len(catalog), "continuity_warnings": dataset["continuity_warnings"],
            "model_status": model["status"], "model_cutoff": model["training_cutoff"],
            "friday_target": target.isoformat(), "forecast_cutoff": cutoff.isoformat(),
            "forecast_items": [
                {"product_id": item["product_id"], "method": item["method"],
                 "forecast_mean": item["forecast_mean"],
                 "fallback_reason": item.get("fallback_reason")}
                for item in forecast["items"]
            ],
            "plan_items": [
                {"product_id": item["product_id"], "method": item["method"],
                 "forecast_sales_units": item["forecast_mean"],
                 "pack_size": item["pack_size"], "max_order_qty": item["max_order_qty"],
                 "recommended_qty": item["recommended_qty"],
                 "confirmed_qty": next(i["confirmed_qty"] for i in confirmed["items"]
                                       if i["product_id"] == item["product_id"]),
                 "fallback_reason": item.get("fallback_reason")}
                for item in plan["items"]
            ],
            "manual_adjustment": {
                "product_id": first["product_id"],
                "recommended": recommendation, "confirmed": adjusted,
            },
            "history_found": True, "confirmed_revision": confirmed["revision"],
            "export_sha256": hashlib.sha256(export).hexdigest(),
        }
        (output / "replay.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report
    finally:
        if previous_db is None:
            os.environ.pop("BAKEPLAN_DB", None)
        else:
            os.environ["BAKEPLAN_DB"] = previous_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = replay(args.output_dir)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
