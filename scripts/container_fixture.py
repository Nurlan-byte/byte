"""Fixed public test-only fixture for the isolated loopback container CI job.

Never use these credentials in a hosted demo. The origin is deliberately fixed
and the shell wrapper deletes its secret file and Docker volumes on exit.
"""

import argparse
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.check_demo import check_demo

BASE_URL = "http://127.0.0.1:8080"
USERNAME = "bakeplan-ci-only"
PASSWORD = "public-test-fixture-never-reuse"
HEADERS = {
    "Authorization": "Basic "
    + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode(),
    "X-BakePlan-Request": "1",
    "Content-Type": "application/json",
}


def request(path, body=None, *, expected=200, text=False):
    req = Request(
        BASE_URL + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=HEADERS,
    )
    with urlopen(req, timeout=60) as response:
        assert response.status == expected, (path, response.status)
        output = response.read().decode()
    return output if text else json.loads(output)


def seed(state_path):
    import_body = {
        "csv_text": Path("fixtures/synthetic_demo.csv").read_text(),
        "source": "synthetic",
        "schema_version": "history-1",
        "bakery_id": "synthetic-deployment",
        "target": "sold_units",
        "unit": "physical_unit",
        "provenance": "Synthetic repository fixture only",
    }
    dataset = request("/api/datasets", import_body, expected=201)
    train_body = {"cutoff": "2026-10-11", "idempotency_key": "ci-train-once"}
    model = request(f"/api/datasets/{dataset['id']}/train", train_body, expected=201)
    assert model["status"] == "ready"
    forecast_body = {
        "dataset_id": dataset["id"],
        "cutoff": "2026-10-11",
        "target_date": "2026-10-13",
        "method": "ML",
        "model_id": model["id"],
    }
    forecast = request("/api/forecasts", forecast_body)
    assert forecast["model_id"] == model["id"]
    assert any(item["method"] == "ML" for item in forecast["items"])
    plan = request(
        "/api/plans",
        {**forecast_body, "mode": "replay"},
        expected=201,
    )
    assert any(item["method"] == "ML" for item in plan["items"])
    confirm_body = {
        "revision": plan["revision"],
        "idempotency_key": "ci-confirm-once",
        "quantities": {
            item["product_id"]: item["recommended_qty"] for item in plan["items"]
        },
    }
    plan = request(f"/api/plans/{plan['id']}/confirm", confirm_body)
    export = request(f"/api/plans/{plan['id']}/export", text=True)
    shop = request("/api/datasets", {
        "schema_version": "procurement-1", "source": "synthetic", "bakery_id": "ci-bar",
        "target": "sold_units", "unit": "physical_unit", "provenance": "Synthetic registry check",
        "catalog": [{"product_id": "water", "name": "Water", "pack_size": 6}],
        "csv_text": "date,product_id,opening,received,sold,waste,closing\n2026-01-01,water,0,12,12,0,0",
    }, expected=201)
    registry = f"/api/datasets/{shop['id']}/deliveries"
    delivery = request(registry, {
        "number": "TEST-12", "product_id": "water", "arrival_date": "2026-10-12", "ordered_qty": 12,
    }, expected=201)
    receipt = {"quantity": 5, "idempotency_key": "ci-receive-five"}
    partial = request(f"{registry}/{delivery['id']}/receipts", receipt)
    assert partial["remaining_qty"] == 7
    state_path.write_text(
        json.dumps(
            {
                "dataset": dataset,
                "model": model,
                "forecast": forecast,
                "forecast_body": forecast_body,
                "plan": plan,
                "export": export,
                "import_body": import_body,
                "train_body": train_body,
                "confirm_body": confirm_body,
                "registry": registry, "receipt": receipt, "partial_delivery": partial,
            }
        )
    )
    print(
        "Synthetic dataset, trained model, confirmed plan, revisions and retry keys seeded."
    )


def verify(state_path):
    state = json.loads(state_path.read_text())
    dataset, model, plan = (state[key] for key in ("dataset", "model", "plan"))
    assert request("/api/datasets", state["import_body"]) == dataset
    assert request(f"/api/datasets/{dataset['id']}/models") == [model]
    assert request(f"/api/datasets/{dataset['id']}/train", state["train_body"]) == model
    assert request("/api/forecasts", state["forecast_body"]) == state["forecast"]
    assert request(f"/api/plans/{plan['id']}") == plan
    assert request(f"/api/plans/{plan['id']}/confirm", state["confirm_body"]) == plan
    revisions = request(f"/api/plans/{plan['id']}/revisions")
    assert len(revisions) == 2 and revisions[-1] == plan
    assert request(f"/api/plans/{plan['id']}/export", text=True) == state["export"]
    registry, partial = state["registry"], state["partial_delivery"]
    assert request(registry) == [partial]
    assert request(f"{registry}/{partial['id']}/receipts", state["receipt"]) == partial
    assert request(registry)[0]["remaining_qty"] == 7
    print(
        "Persisted data, exact model/plan/export, revision history and retry keys verified."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("probe", "seed", "verify"))
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    if args.command == "probe":
        print("\n".join(check_demo(BASE_URL, USERNAME, PASSWORD)))
    elif args.command == "seed":
        seed(args.state)
    else:
        verify(args.state)


if __name__ == "__main__":
    main()
