from fastapi.testclient import TestClient
import json
import pytest

from backend.app import app
from backend.storage import connection
from backend.tests.test_procurement import payload


@pytest.fixture
def orders(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "report.sqlite3"))
    with TestClient(app) as client:
        dataset = client.post("/api/datasets", json=payload(90)).json()
        query = {
            "bakery_id": "test-shop",
            "start": "2026-10-11",
            "end": "2026-10-11",
            "mode": "replay",
            "source": "synthetic",
        }
        body = {
            "dataset_id": dataset["id"],
            "cutoff": "2026-10-09",
            "target_date": query["end"],
            "mode": "replay",
            "method": "B1",
            "usable_stock_assumption": {"tea": 0},
            "lead_time_days": 1,
        }
        yield client, body, query


def create(client, body):
    r = client.post("/api/plans", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_confirmation_reason_survives_retries_and_report_deduplicates(orders):
    client, body, query = orders
    plan = create(client, body)
    (item,) = plan["items"]
    confirmed = item["recommended_qty"] + item["pack_size"]
    path = f"/api/plans/{plan['id']}"
    params = {**query, "actual_dataset_id": body["dataset_id"]}
    report = "/api/reports/order-adjustments"
    assert client.get(report, params=params).json()["adjustment_rate"] is None
    req = dict(
        revision=0,
        idempotency_key="original",
        quantities={"tea": confirmed},
        override_reasons={"tea": "event"},
    )
    saved = client.post(path + "/confirm", json=req).json()
    assert saved["items"][0]["override_reason"] == "event"
    assert client.post(path + "/confirm", json=req).json() == saved
    assert (
        client.post(
            path + "/confirm", json={**req, "override_reasons": {"tea": "stock"}}
        ).status_code
        == 409
    )
    assert (
        client.get(path, params={"revision": 1}).json()["items"][0]["override_reason"]
        == "event"
    )
    result = client.get(report, params=params).json()
    assert (
        result["confirmed_product_days"],
        result["comparable_product_days"],
        result["adjusted_product_days"],
    ) == (1, 1, 1)
    assert result["adjustment_rate"] == 1 and result["reason_counts"] == {"event": 1}
    assert result["matched_sales_days"] == 1 and result["sales_mae_units"] == 0
    assert (
        client.get(report, params={**query, "mode": "operational"}).json()[
            "confirmed_product_days"
        ]
        == 0
    )
    assert (
        client.get(report, params={**params, "bakery_id": "wrong"}).status_code == 422
    )

    second = create(client, body)
    confirmed_again = client.post(
        f"/api/plans/{second['id']}/confirm",
        json={
            "revision": 0,
            "idempotency_key": "other",
            "quantities": {"tea": item["recommended_qty"]},
        },
    )
    assert confirmed_again.status_code == 200
    result = client.get(report, params=params).json()
    assert result["comparable_product_days"] == 1 and result["adjustment_rate"] == 0
    assert result["reason_counts"] == {}


def test_old_confirmations_missing_reasons_and_manual_decisions(orders):
    client, body, query = orders
    plan = create(client, body)
    (item,) = plan["items"]
    path = f"/api/plans/{plan['id']}/confirm"
    old = {
        "idempotency_key": "legacy",
        "revision": 0,
        "quantities": {"tea": item["recommended_qty"] - item["pack_size"]},
    }
    assert client.post(path, json=old).status_code == 200
    with connection() as con:
        (encoded,) = con.execute(
            "SELECT body FROM confirmations WHERE plan_id=? AND key=?",
            (plan["id"], "legacy"),
        ).fetchone()
        assert encoded == json.dumps(
            old, sort_keys=True
        )  # Exact format of a pre-feature receipt.
    assert client.post(path, json=old).status_code == 200
    report = "/api/reports/order-adjustments"
    assert client.get(report, params=query).json()["reason_counts"] == {
        "unspecified": 1
    }
    newer = {
        **old,
        "revision": 1,
        "idempotency_key": "2",
        "quantities": {"tea": item["recommended_qty"]},
    }
    for reasons in ({"tea": "stock"}, {"bad": "other"}, {"tea": "unrecognised"}):
        assert (
            client.post(path, json={**newer, "override_reasons": reasons}).status_code
            == 422
        )
    assert client.post(path, json=newer).json()["revision"] == 2
    assert client.get(report, params=query).json()["reason_counts"] == {}
    manual = create(client, {**body, "usable_stock_assumption": {}})
    assert not manual["items"][0]["automatic_eligible"]
    assert (
        client.post(f"/api/plans/{manual['id']}/confirm", json=old).status_code == 200
    )
    result = client.get(report, params=query).json()
    assert (
        result["confirmed_product_days"] == 1 and result["comparable_product_days"] == 0
    )
    assert result["adjustment_rate"] is None


def test_actuals_coverage_never_imputes_unobserved_days_as_zero(orders):
    client, body, query = orders
    plan = create(client, body)
    suggested = plan["items"][0]["recommended_qty"]
    assert (
        client.post(
            f"/api/plans/{plan['id']}/confirm",
            json={
                "revision": 0,
                "idempotency_key": "1",
                "quantities": {"tea": suggested},
            },
        ).status_code
        == 200
    )
    snapshot = payload(90)
    rows = snapshot["csv_text"].splitlines()
    snapshot["csv_text"] = "\n".join(r for r in rows if not r.startswith("2026-10-11,"))
    partial = client.post("/api/datasets", json=snapshot).json()["id"]
    url = "/api/reports/order-adjustments"
    report = client.get(url, params={**query, "actual_dataset_id": partial}).json()
    assert report["comparable_product_days"] == 1 and report["matched_sales_days"] == 0
    assert report["sales_mae_units"] is None and report["sales_bias_units"] is None

    # An observed zero *is* a matching sale and positive bias means overforecast.
    snapshot["csv_text"] += "\n2026-10-11,tea,0,0,0,0,0"
    complete = client.post("/api/datasets", json=snapshot).json()["id"]
    report = client.get(url, params={**query, "actual_dataset_id": complete}).json()
    assert report["matched_sales_days"] == 1
    assert (
        report["sales_mae_units"]
        == report["sales_bias_units"]
        == plan["items"][0]["forecast_mean"]
    )
    wrong = client.get(
        url, params={**query, "actual_dataset_id": complete, "source": "local"}
    )
    assert wrong.status_code == 422
