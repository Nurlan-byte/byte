import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import CATALOG, REQUIRED, app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "db.sqlite3"))
    return TestClient(app)


def sample():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(REQUIRED)
    for week in range(8):
        for p in CATALOG:
            sold = 10 * (week + 1)
            writer.writerow(
                [
                    date(2026, 8, 17) + timedelta(days=week * 7),
                    p["product_id"],
                    "true",
                    0,
                    sold,
                    sold,
                    0,
                    0,
                    0,
                    "none",
                ]
            )
    return out.getvalue()


def plan(client, text=None, method="B1"):
    imported = client.post(
        "/api/datasets", json={"csv_text": text or sample(), "source": "synthetic"}
    )
    assert imported.status_code == 201
    response = client.post(
        "/api/plans",
        json={
            "dataset_id": imported.json()["id"],
            "target_date": "2026-10-12",
            "cutoff": "2026-10-11",
            "method": method,
            "mode": "replay",
        },
    )
    assert response.status_code == 201
    return response.json()


def body(p, key="once"):
    return {
        "revision": p["revision"],
        "idempotency_key": key,
        "quantities": {i["product_id"]: i["recommended_qty"] for i in p["items"]},
    }


def test_baselines(client):
    p = plan(client)
    assert p["items"][0]["forecast_mean"] == 65
    assert p["items"][0]["recommended_qty"] == 60
    assert plan(client, method="B0")["items"][0]["forecast_mean"] == 80
    assert (
        plan(client, sample().replace("80,80,0,0,0,none", "80,80,0,0,0,occurred"))[
            "items"
        ][0]["forecast_mean"]
        == 55
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda t: t + t.splitlines()[1] + "\n",
        lambda t: t.replace("p1", "unknown"),
        lambda t: t.replace(",10,10,", ",10.5,10,"),
        lambda t: t.replace(",10,10,", ",10,11,"),
        lambda t: t.replace(",10,10,", ",NaN,10,"),
    ],
)
def test_atomic_invalid_csv(client, change):
    r = client.post(
        "/api/datasets", json={"csv_text": change(sample()), "source": "local"}
    )
    assert r.status_code == 422
    assert client.get("/api/datasets").json() == []


def test_confirm_persistence_idempotency_conflict(client):
    p = plan(client)
    b = body(p)
    first = client.post(f"/api/plans/{p['id']}/confirm", json=b)
    assert first.status_code == 200
    assert client.post(f"/api/plans/{p['id']}/confirm", json=b).json() == first.json()
    assert (
        client.post(f"/api/plans/{p['id']}/confirm", json=body(p, "stale")).status_code
        == 409
    )
    b["quantities"]["p1"] = 96
    assert client.post(f"/api/plans/{p['id']}/confirm", json=b).status_code == 409
    assert TestClient(app).get(f"/api/plans/{p['id']}").json() == first.json()
    assert len(client.get(f"/api/plans/{p['id']}/revisions").json()) == 2
    assert "synthetic" in client.get(f"/api/plans/{p['id']}/export").text


def test_batch_max_and_strict_integer(client):
    p = plan(client)
    for invalid in [100, -12, 12.0, True]:
        b = body(p, str(invalid))
        b["quantities"]["p1"] = invalid
        assert client.post(f"/api/plans/{p['id']}/confirm", json=b).status_code == 422
    b = body(p)
    b["quantities"]["p1"] = 96
    assert client.post(f"/api/plans/{p['id']}/confirm", json=b).status_code == 200


def test_missing_stockout_and_future(client):
    text = sample()
    text += "2026-10-12,p1,true,0,999,999,0,0,0,none\n"
    assert plan(client, text)["items"][0]["forecast_mean"] == 65
    text = "\n".join(
        line for line in sample().splitlines() if not line.startswith("2026-09-21")
    )
    assert (
        plan(client, text.replace("80,80,0,0,0,none", "80,80,0,0,0,unknown"))["items"][
            0
        ]["forecast_mean"]
        == 47.5
    )
    text = sample().replace(",none", ",unknown")
    assert plan(client, text)["items"][0]["method"] == "manual"


def test_concurrent_confirm(client):
    p = plan(client)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda key: (
                    client.post(
                        f"/api/plans/{p['id']}/confirm", json=body(p, key)
                    ).status_code
                ),
                ["a", "b"],
            )
        )
    assert sorted(results) == [200, 409]


def test_active_dates_and_impossible_batch(client, monkeypatch):
    monkeypatch.setitem(CATALOG[0], "active_from", "2026-10-06")
    monkeypatch.setitem(CATALOG[1], "max_daily_qty", 5)
    p = plan(client)
    assert p["items"][0]["forecast_mean"] is None
    assert p["items"][1]["recommended_qty"] == 0
    assert any("партии" in w for w in p["items"][1]["warnings"])


def test_replay_and_future_date_validation(client):
    data = client.post(
        "/api/datasets", json={"csv_text": sample(), "source": "synthetic"}
    ).json()
    r = client.post(
        "/api/plans",
        json={
            "dataset_id": data["id"],
            "target_date": "2026-10-12",
            "cutoff": "2026-10-11",
        },
    )
    assert r.status_code == 422
    assert (
        client.post(
            "/api/datasets",
            json={
                "csv_text": sample().replace("2026-08-17", "2099-08-17"),
                "source": "local",
            },
        ).status_code
        == 422
    )


def test_carryover_manual(client):
    text = sample().replace(",true,0,10,10,0,0,0,none", ",true,1,10,10,0,1,0,none")
    assert plan(client, text)["items"][0]["method"] == "manual"


def test_exact_csv_shape_and_health(client):
    assert client.get("/api/health").json()["db_status"] == "ok"
    for text in [
        sample().replace("2026-08-17", "20260817"),
        sample().replace("date,product_id", "date,date,product_id"),
        sample() + "2026-09-01,p1,true,0,1,1,0,0,0,none,extra\n",
    ]:
        assert (
            client.post(
                "/api/datasets", json={"csv_text": text, "source": "synthetic"}
            ).status_code
            == 422
        )
    assert client.get("/api/datasets").json() == []


def test_snapshots_survive_config_change_and_same_key_race(client, monkeypatch):
    p = plan(client)
    b = body(p)
    b["quantities"]["p1"] = 84
    monkeypatch.setitem(CATALOG[0], "max_daily_qty", 12)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: client.post(f"/api/plans/{p['id']}/confirm", json=b), range(2)
            )
        )
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json()
    assert results[0].json()["items"][0]["max_daily_qty"] == 100
    assert client.get(f"/api/plans/{p['id']}").json()["items"][0]["confirmed_qty"] == 84
