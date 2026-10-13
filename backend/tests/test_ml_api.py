"""ML lifecycle integration tests using generated, explicitly synthetic CSV only."""

import csv
import importlib
import io
import json
import math
import subprocess
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import ml_service
from backend.app import app
from backend.imports import REQUIRED
from backend.storage import connection

CUTOFF = "2026-10-11"
TARGET_DATE = "2026-10-12"
PRODUCTS = tuple(f"p{number}" for number in range(1, 6))


def synthetic_csv(days=90, *, sales_only=False, products=PRODUCTS, guard=None):
    """Keep fixtures reproducible, nonconstant, bounded and free of real data."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["date", "product_id", "item_line_count"] if sales_only else REQUIRED
    )
    end = date.fromisoformat(CUTOFF)
    for offset in range(days):
        day = end - timedelta(days=days - offset - 1)
        for product_no, product in enumerate(products):
            sold = 20 + product_no * 3 + day.weekday() * 2 + offset // 11
            if sales_only:
                writer.writerow([day, product, sold])
                continue
            carryover = int(guard == "carryover" and product == "p1" and day == end)
            stockout = (
                "unknown"
                if guard == "unknown" and product == "p1" and offset >= days - 40
                else "none"
            )
            writer.writerow(
                [day, product, "true", carryover, sold, sold, 0, carryover, 0, stockout]
            )
    return output.getvalue()


def import_body(
    csv_text=None, *, bakery="synthetic-bakery-a", sales_only=False, **changes
):
    return {
        "csv_text": csv_text
        if csv_text is not None
        else synthetic_csv(sales_only=sales_only),
        "source": "synthetic",
        "schema_version": "sales-1" if sales_only else "history-1",
        "bakery_id": bakery,
        "target": "item_line_count" if sales_only else "sold_units",
        "unit": "item_line" if sales_only else "physical_unit",
        "provenance": "Generated exclusively inside test_ml_api.py; no production data",
        **changes,
    }


def import_dataset(client, csv_text=None, **changes):
    response = client.post("/api/datasets", json=import_body(csv_text, **changes))
    assert response.status_code in (200, 201), response.text
    return response.json()


def train(client, dataset, *, cutoff=CUTOFF, key="train-once"):
    return client.post(
        f"/api/datasets/{dataset['id']}/train",
        json={"cutoff": cutoff, "idempotency_key": key},
    )


def forecast_body(dataset, **changes):
    return {
        "dataset_id": dataset["id"],
        "cutoff": CUTOFF,
        "target_date": TARGET_DATE,
        "method": "ML",
        **changes,
    }


def forecast(client, dataset, **changes):
    return client.post("/api/forecasts", json=forecast_body(dataset, **changes))


def make_plan(client, dataset, **changes):
    return client.post(
        "/api/plans", json=forecast_body(dataset, mode="replay", **changes)
    )


def table_counts():
    with connection() as con:
        return {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "datasets",
                "models",
                "training_requests",
                "plans",
                "revisions",
                "confirmations",
            )
        }


def assert_error(response, status):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert isinstance(error["code"], str)
    assert isinstance(error["message"], str) and error["message"]
    assert "details" in error


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "ml-api.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(path))
    monkeypatch.setattr(
        importlib.import_module("backend.app"),
        "business_today",
        lambda: date(2026, 10, 13),
    )
    return path


@pytest.fixture
def client(database):
    with TestClient(app) as active:
        yield active


def test_train_status_and_forecast_complete_synthetic_lifecycle(client):
    dataset = import_dataset(client)
    assert client.get(f"/api/datasets/{dataset['id']}/models").json() == []
    response = train(client, dataset)
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["status"] == "ready"
    for key in ("bakery_id", "target", "unit", "source", "provenance"):
        assert model[key] == dataset[key]
    assert model["dataset_id"] == dataset["id"]
    assert model["dataset_version"] == dataset["version"]
    assert model["training_cutoff"] == CUTOFF
    assert len(model["snapshot_hash"]) == 64
    assert "experimental" in model["protocol_version"]
    assert model["evaluation"]["experimental"] is True
    assert model["evaluation"]["selection_performed"] is False
    assert model["evaluation"]["fit_end"] < model["evaluation"]["holdout_start"]
    assert model["evaluation"]["holdout_end"] <= CUTOFF
    assert model["evaluation"]["paired_n"] > 0
    assert set(model["products"]) == set(PRODUCTS)
    assert all(item["status"] == "ready" for item in model["products"].values())
    assert "parameters" not in json.dumps(model)
    assert client.get(f"/api/datasets/{dataset['id']}/models").json() == [model]
    prediction = forecast(client, dataset)
    assert prediction.status_code == 200, prediction.text
    result = prediction.json()
    assert result["model_id"] == model["id"]
    assert result["unit"] == "physical_unit"
    assert set(item["product_id"] for item in result["items"]) == set(PRODUCTS)
    assert all(
        item["method"] == "ML" and item["fallback_reason"] is None
        for item in result["items"]
    )
    assert all(
        math.isfinite(item["forecast_mean"]) and item["forecast_mean"] >= 0
        for item in result["items"]
    )


def test_model_versions_retry_keys_plan_and_export_survive_restart(database):
    with TestClient(app) as first:
        dataset = import_dataset(first)
        early = train(first, dataset, cutoff="2026-10-04", key="early").json()
        response = train(first, dataset)
        assert response.status_code == 201, response.text
        latest = response.json()
        original_forecast = forecast(first, dataset).json()
        assert original_forecast["model_id"] == latest["id"]
        assert (
            forecast(first, dataset, model_id=early["id"]).json()["model_id"]
            == early["id"]
        )
        repeated = train(first, dataset)
        assert repeated.status_code == 200
        assert repeated.json() == latest
        assert train(first, dataset, key="second-key").json() == latest
        before = table_counts()
        assert_error(train(first, dataset, cutoff="2026-10-10"), 409)
        assert table_counts() == before
        plan_response = make_plan(first, dataset, model_id=latest["id"])
        assert plan_response.status_code == 201, plan_response.text
        plan = plan_response.json()
        assert plan["model_id"] == latest["id"]
        assert plan["requested_method"] == "ML"
        body = {
            "revision": 0,
            "idempotency_key": "confirm-once",
            "quantities": {
                item["product_id"]: item["recommended_qty"] for item in plan["items"]
            },
        }
        confirmation = first.post(f"/api/plans/{plan['id']}/confirm", json=body)
        assert confirmation.status_code == 200, confirmation.text
        confirmed = confirmation.json()
        assert all(
            item["confirmed_qty"] == body["quantities"][item["product_id"]]
            for item in confirmed["items"]
        )
    # The first lifespan and all its connections have closed. Reopen the same DB.
    assert database.is_file()
    with TestClient(app) as restarted:
        assert restarted.get(f"/api/datasets/{dataset['id']}/models").json() == [
            latest,
            early,
        ]
        assert forecast(restarted, dataset).json() == original_forecast
        assert train(restarted, dataset).json() == latest
        assert restarted.get(f"/api/plans/{plan['id']}").json() == confirmed
        assert (
            restarted.post(f"/api/plans/{plan['id']}/confirm", json=body).json()
            == confirmed
        )
        revisions = restarted.get(f"/api/plans/{plan['id']}/revisions").json()
        assert len(revisions) == 2
        assert all(revision["model_id"] == latest["id"] for revision in revisions)
        exported = restarted.get(f"/api/plans/{plan['id']}/export")
        assert exported.status_code == 200
        rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert len(rows) == 5
        for row in rows:
            assert row["model_id"] == latest["id"]
            assert row["protocol_version"] == latest["protocol_version"]
            assert row["dataset_version"] == dataset["version"]
            assert row["bakery_id"] == dataset["bakery_id"]
            assert row["target"] == "sold_units" and row["unit"] == "physical_unit"
            assert row["requested_method"] == "ML"
        assert table_counts()["models"] == 2
        assert table_counts()["training_requests"] == 3
        assert table_counts()["confirmations"] == 1


@pytest.mark.parametrize("sales_only", [False, True])
def test_no_model_uses_same_b1_values_with_explicit_reason(client, sales_only):
    dataset = import_dataset(client, sales_only=sales_only)
    fallback = forecast(client, dataset).json()
    baseline = forecast(client, dataset, method="B1").json()
    assert fallback["model_id"] is None
    for actual, reference in zip(fallback["items"], baseline["items"], strict=True):
        assert actual["method"] == "B1"
        assert actual["fallback_reason"]
        assert actual["forecast_mean"] == reference["forecast_mean"]
        assert actual["used_dates"] == reference["used_dates"]
    assert table_counts()["models"] == 0
    assert table_counts()["plans"] == 0


def test_short_history_records_not_ready_and_falls_back(client):
    dataset = import_dataset(client, synthetic_csv(21))
    response = train(client, dataset)
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["status"] == "not_ready" and model["reason"]
    assert all(item["status"] == "not_ready" for item in model["products"].values())
    predicted = forecast(client, dataset, model_id=model["id"]).json()
    assert all(
        item["method"] == "B1" and item["fallback_reason"]
        for item in predicted["items"]
    )
    assert all(item["forecast_mean"] is not None for item in predicted["items"])
    assert client.get(f"/api/datasets/{dataset['id']}/models").json() == [model]


def test_new_bakery_same_bytes_and_new_snapshot_are_isolated(client):
    text = synthetic_csv()
    first = import_dataset(client, text)
    duplicate = client.post("/api/datasets", json=import_body(text))
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first["id"]
    other = import_dataset(client, text, bakery="synthetic-bakery-b")
    changed = import_dataset(client, text + "2026-07-01,p1,true,0,10,10,0,0,0,none\n")
    assert len({first["id"], other["id"], changed["id"]}) == 3
    assert len({first["version"], other["version"], changed["version"]}) == 3
    model = train(client, first).json()
    for dataset in (other, changed):
        assert client.get(f"/api/datasets/{dataset['id']}/models").json() == []
        assert forecast(client, dataset).json()["model_id"] is None
        assert_error(forecast(client, dataset, model_id=model["id"]), 422)
        assert_error(make_plan(client, dataset, model_id=model["id"]), 422)
    assert table_counts()["plans"] == 0


def test_sales_only_arbitrary_ids_train_but_never_become_units_or_plans(client):
    sales = import_dataset(
        client,
        synthetic_csv(sales_only=True, products=("Bread", "Coffee")),
        sales_only=True,
    )
    sales_model_response = train(client, sales)
    assert sales_model_response.status_code == 201, sales_model_response.text
    sales_model = sales_model_response.json()
    assert sales_model["status"] == "ready"
    result = forecast(client, sales, model_id=sales_model["id"]).json()
    assert result["target"] == "item_line_count" and result["unit"] == "item_line"
    assert {item["product_id"] for item in result["items"]} == {"Bread", "Coffee"}
    assert all(item["method"] == "ML" for item in result["items"])
    assert all(
        "recommended_qty" not in item and "produced" not in item
        for item in result["items"]
    )
    for method in ("B0", "B1", "ML"):
        assert_error(
            make_plan(client, sales, method=method, model_id=sales_model["id"]), 422
        )
    units = import_dataset(client)
    units_model = train(client, units).json()
    assert_error(forecast(client, units, model_id=sales_model["id"]), 422)
    assert_error(forecast(client, sales, model_id=units_model["id"]), 422)
    assert table_counts()["plans"] == 0


def test_future_cutoff_and_future_model_rejected_without_writes(client):
    dataset = import_dataset(client)
    model = train(client, dataset).json()
    before = table_counts()
    assert_error(train(client, dataset, cutoff="2026-10-13", key="future"), 422)
    assert_error(forecast(client, dataset, cutoff=TARGET_DATE), 422)
    assert_error(
        forecast(client, dataset, cutoff="2026-10-10", model_id=model["id"]), 422
    )
    assert_error(
        make_plan(client, dataset, cutoff="2026-10-10", model_id=model["id"]), 422
    )
    assert table_counts() == before


def test_corrupted_model_has_safe_forecast_and_plan_fallback(client):
    dataset = import_dataset(client)
    model = train(client, dataset).json()
    reference = forecast(client, dataset, method="B1").json()
    with connection() as con:
        con.execute(
            "UPDATE models SET payload=? WHERE id=?", ('{"corrupt": true}', model["id"])
        )
    assert_error(client.get(f"/api/datasets/{dataset['id']}/models"), 409)
    for model_id in (None, model["id"]):
        response = forecast(client, dataset, model_id=model_id)
        assert response.status_code == 200, response.text
        fallback = response.json()
        assert fallback["model_id"] is None
        for actual, expected in zip(fallback["items"], reference["items"], strict=True):
            assert actual["method"] == "B1" and actual["fallback_reason"]
            assert actual["forecast_mean"] == expected["forecast_mean"]
    plan = make_plan(client, dataset).json()
    assert plan["model_id"] is None
    assert all(
        item["method"] == "B1" and item["fallback_reason"] for item in plan["items"]
    )


def test_mutated_persisted_training_snapshot_cannot_use_ml(client):
    dataset = import_dataset(client)
    model = train(client, dataset).json()
    with connection() as con:
        changed = json.loads(
            con.execute(
                "SELECT payload FROM datasets WHERE id=?", (dataset["id"],)
            ).fetchone()[0]
        )
        changed["rows"][0]["sold"] += 1
        changed["rows"][0]["produced"] += 1
        con.execute(
            "UPDATE datasets SET payload=? WHERE id=?",
            (json.dumps(changed), dataset["id"]),
        )
    response = forecast(client, dataset, model_id=model["id"])
    assert response.status_code == 200, response.text
    assert all(
        item["method"] == "B1" and item["fallback_reason"] == "snapshot_mismatch"
        for item in response.json()["items"]
    )


@pytest.mark.parametrize("guard", ["carryover", "unknown", "closed"])
def test_ml_cannot_bypass_manual_or_closed_day_plan_guards(client, guard):
    dataset = import_dataset(client, synthetic_csv(guard=guard))
    model_response = train(client, dataset)
    assert model_response.status_code == 201, model_response.text
    response = make_plan(
        client,
        dataset,
        target_is_open=guard != "closed",
        model_id=model_response.json()["id"],
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    guarded = plan["items"] if guard == "closed" else [plan["items"][0]]
    assert all(
        not item["automatic_eligible"]
        and item["method"] == "manual"
        and item["recommended_qty"] == 0
        for item in guarded
    )
    assert all(item["warnings"] for item in guarded)
    if guard == "closed":
        assert all(item["forecast_mean"] is None for item in guarded)
        body = {
            "revision": 0,
            "idempotency_key": "must-stay-closed",
            "quantities": {product: 0 for product in PRODUCTS},
        }
        body["quantities"]["p1"] = 12
        assert_error(client.post(f"/api/plans/{plan['id']}/confirm", json=body), 422)
        assert client.get(f"/api/plans/{plan['id']}").json() == plan


@pytest.mark.parametrize(
    "changes",
    [
        {"unit": "item_line"},
        {"target": "item_line_count"},
        {"bakery_id": ""},
        {"provenance": " "},
        {"schema_version": "unsupported"},
        {"csv_text": ",".join(REQUIRED) + "\n"},
        {"csv_text": synthetic_csv().replace("p1", "unknown-product")},
        {"csv_text": synthetic_csv().replace(",true,0,", ",true,-1,", 1)},
        {"csv_text": synthetic_csv().replace(",true,0,", ",true,1,", 1)},
    ],
)
def test_invalid_import_is_atomic_with_structured_error(client, changes):
    before = table_counts()
    assert_error(client.post("/api/datasets", json=import_body(**changes)), 422)
    assert table_counts() == before


@pytest.mark.parametrize(
    "text",
    [
        "date,product_id,item_line_count\n2026-10-01,Coffee,1.5\n",
        "date,product_id,item_line_count\n2026-10-01,Coffee,NaN\n",
        "date,product_id,item_line_count\n2026-10-01,Coffee,-1\n",
        "date,product_id,item_line_count\n2026-10-01,Coffee,1\n2026-10-01,Coffee,1\n",
        synthetic_csv(
            1, sales_only=True, products=tuple(f"sku-{index}" for index in range(51))
        ),
    ],
)
def test_invalid_sales_only_import_has_no_partial_writes(client, text):
    before = table_counts()
    assert_error(
        client.post("/api/datasets", json=import_body(text, sales_only=True)), 422
    )
    assert table_counts() == before


def test_legacy_metadata_and_missing_objects_fail_without_training_writes(client):
    legacy = client.post(
        "/api/datasets", json={"csv_text": synthetic_csv(), "source": "synthetic"}
    ).json()
    before = table_counts()
    assert_error(train(client, legacy), 422)
    assert_error(client.get("/api/datasets/missing/models"), 404)
    assert_error(train(client, {"id": "missing"}), 404)
    assert_error(forecast(client, legacy, model_id="missing"), 404)
    assert table_counts() == before


def test_training_timeout_is_atomic_and_releases_training_slot(client, monkeypatch):
    dataset = import_dataset(client)
    original = ml_service.subprocess.run

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic-test-worker", 60)

    monkeypatch.setattr(ml_service.subprocess, "run", timeout)
    before = table_counts()
    assert_error(train(client, dataset), 503)
    assert table_counts() == before
    monkeypatch.setattr(ml_service.subprocess, "run", original)
    assert train(client, dataset).status_code == 201


def test_busy_training_slot_does_not_write_and_allows_retry(client):
    dataset = import_dataset(client)
    before = table_counts()
    assert ml_service.TRAINING_SLOTS.acquire(blocking=False)
    try:
        assert_error(train(client, dataset), 503)
        assert table_counts() == before
    finally:
        ml_service.TRAINING_SLOTS.release()
    assert train(client, dataset).status_code == 201


def test_blank_training_key_is_validation_error_without_writes(client):
    dataset = import_dataset(client)
    before = table_counts()
    assert_error(train(client, dataset, key="   "), 422)
    assert table_counts() == before


@pytest.mark.parametrize(
    "quantity", ["opening", "produced", "sold", "waste", "closing", "other_out"]
)
def test_each_history_quantity_accepts_numeric_limit_and_rejects_overflow(
    client, quantity
):
    def bounded_csv(value):
        row = dict.fromkeys(
            ("opening", "produced", "sold", "waste", "closing", "other_out"), 0
        )
        if quantity in ("opening", "produced"):
            row[quantity] = row["sold"] = value
        else:
            row["produced"] = row[quantity] = value
        row.update(
            date="2026-10-05", product_id="p1", is_open="true", stockout_status="none"
        )
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=REQUIRED)
        writer.writeheader()
        writer.writerow(row)
        return output.getvalue()

    accepted = client.post(
        "/api/datasets", json=import_body(bounded_csv(1_000_000_000))
    )
    assert accepted.status_code == 201, accepted.text
    before = table_counts()
    assert_error(
        client.post("/api/datasets", json=import_body(bounded_csv(1_000_000_001))), 422
    )
    assert table_counts() == before


def test_sales_numeric_limit_preserves_b1_and_rejects_overflow(client):
    text = "date,product_id,item_line_count\n2026-10-05,Coffee,1000000000\n"
    dataset = import_dataset(client, text, sales_only=True)
    before = table_counts()
    assert_error(
        client.post(
            "/api/datasets",
            json=import_body(text.replace("1000000000", "1000000001"), sales_only=True),
        ),
        422,
    )
    assert table_counts() == before
    model = train(client, dataset).json()
    assert model["status"] == "not_ready"
    assert model["bounds"]["max_diagnostic_count"] == 1_000_000_000
    assert model["bounds"]["max_label_and_prediction"] == 1_000_000
    result = forecast(client, dataset, model_id=model["id"]).json()["items"][0]
    assert result["method"] == "B1"
    assert result["forecast_mean"] == 1_000_000_000
    assert result["fallback_reason"]
