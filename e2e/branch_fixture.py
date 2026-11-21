"""Run the real authenticated API with isolated synthetic branch accounts."""

from datetime import date, timedelta
import os
import tempfile
from pathlib import Path

import bcrypt
import uvicorn

from backend.branch_accounts import set_account
from backend.branch_access import branch_scope
from backend.ml_service import create_forecast
from backend.schemas import ForecastRequest, ImportRequest
from backend.services import import_history
from backend.storage import initialize_database


def issue_before_sales():
    # Seed the historical issuance using the same application workflow as POST /api/forecasts.
    # This branch has no orders. The browser supplies actual sales after opening its report.
    issue_date = date(2025, 8, 15)
    rows = ["date,product_id,opening,received,sold,waste,closing,is_open,stockout_status"]
    for days_ago in range(30, 0, -1):
        day = issue_date - timedelta(days=days_ago)
        for sku, qty in (("tea", 14 if days_ago <= 5 else 4), ("chips", 2 if days_ago <= 5 else 10)):
            rows.append(f"{day},{sku},0,{qty},{qty},0,0,true,none")
    with branch_scope("matched"):
        dataset, _ = import_history(ImportRequest(
            bakery_id="matched", source="synthetic", schema_version="procurement-1",
            unit="physical_unit", target="sold_units", provenance="Synthetic history before issuance",
            catalog=[{"product_id": p, "name": p, "pack_size": 1} for p in ("tea", "chips")],
            csv_text="\n".join(rows),
        ), issue_date)
        create_forecast(ForecastRequest(dataset_id=dataset["id"], target_date=issue_date + timedelta(days=1),
                                        cutoff=issue_date - timedelta(days=1), method="AUTO"), issue_date)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="bakeplan-branches-") as folder:
        os.environ["BAKEPLAN_DB"] = str(Path(folder) / "test.sqlite3")
        os.environ.pop("BAKEPLAN_LOCAL_DEMO", None)
        initialize_database()
        for branch in ("north", "south", "matched"):
            password_hash = bcrypt.hashpw(b"fixture-only", bcrypt.gensalt(rounds=4))
            set_account(f"admin-{branch}", branch, password_hash.decode("ascii"))
        issue_before_sales()
        uvicorn.run("backend.app:app", host="127.0.0.1", port=18007)
