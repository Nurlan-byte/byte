"""Public operator API, restart catch-up, duplicate workers and outlet isolation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from backend import context_sources, pos_providers, pos_sync, pos_vault
from backend.app import app
from backend.errors import ServiceError

URL = "/api/pos/connections"
TOKEN = "operator-test-key-123"
START = datetime(2026, 10, 12, 1, tzinfo=UTC)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    keyfile = tmp_path / "master.key"
    pos_vault.init_key(keyfile)
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "shop.db"))
    monkeypatch.setenv("BAKEPLAN_POS_ENABLED", "1")
    monkeypatch.setenv("BAKEPLAN_POS_MASTER_KEY_FILE", str(keyfile))
    state = {
        "now": START,
        "account": "tenant",
        "shops": ["1"],
        "reads": [],
        "valid_key": TOKEN,
    }
    monkeypatch.setattr(pos_sync, "POLL_SECONDS", 3600)
    monkeypatch.setattr(pos_sync, "now_utc", lambda: state["now"])
    monkeypatch.setattr(context_sources, "load_demand_context", lambda *args: ({}, []))

    def shops(reader):
        if reader.key != state["valid_key"]:
            raise ServiceError(403, "Касса отклонила ключ")
        return [
            pos_providers.Location(ident, f"Клуб {ident}", state["account"])
            for ident in state["shops"]
        ]

    def sales(reader, location, observed):
        assert reader.key == state["valid_key"]
        state["reads"].append((location.id, observed.end))
        if state.get("outage"):
            raise ServiceError(503, f"Vendor URL with {reader.key}")
        if state.get("empty"):
            return
        for offset in range((observed.end - observed.start).days + 1):
            day = observed.start + timedelta(days=offset)
            observed.add(day, "9", "Чай", 3 + day.weekday(), True)

    monkeypatch.setattr(pos_providers, "locations", shops)
    monkeypatch.setattr(pos_providers, "poster_sales", sales)
    monkeypatch.setattr(pos_providers, "moysklad_sales", sales)
    return state


def register(client, **kwargs):
    return client.post(
        URL, json={"provider": "poster", "api_key": TOKEN, "days": 14, **kwargs}
    )


@pytest.mark.parametrize("provider", ["poster", "moysklad"])
def test_once_then_daily_after_restart_without_key_or_browser(shop, provider):
    shop["shops"].append("2")
    with TestClient(app) as client:
        assert client.get("/api/pos/status").json()["daily_enabled"] is True
        assert register(client, provider=provider).json()["status"] == "choose_location"
        assert client.get(URL).json() == [] and not shop["reads"]
        result = register(client, provider=provider, location_id="2")
        assert result.status_code == 200, result.text
        first = result.json()
        ident = first["connection"]["id"]
        latest = f"{URL}/{ident}/latest"
        original = client.get(latest).json()
        assert first["forecast"] == original["forecast"]
        assert first["dataset"] == original["dataset"]
        assert first["forecast"]["cutoff"] == "2026-10-11"
        assert first["forecast"]["targetDate"] == "2026-10-13"
        pos_sync.run_due(START + timedelta(hours=1))
        # 04:59 local the next day still belongs to the prior scheduled work.
        pos_sync.run_due(datetime(2026, 10, 12, 23, 59, tzinfo=UTC))
        assert len(shop["reads"]) == 1

    # Two days of downtime: ASGI lifespan alone must fetch the newest window
    # using the encrypted key and recalculate; no reconnect/tick HTTP request.
    shop["now"] = datetime(2026, 10, 14, 0, tzinfo=UTC)
    with TestClient(app) as client:
        deadline = monotonic() + 20
        while client.get(URL).json()[0]["last_success_day"] != "2026-10-14":
            assert monotonic() < deadline, client.get(URL).text
            sleep(0.05)
        new = client.get(latest).json()
        assert (
            new["connection"]["provider"] == new["dataset"]["pos_provider"] == provider
        )
        assert new["dataset"]["id"] != original["dataset"]["id"]
        assert max(row["date"] for row in new["dataset"]["rows"]) == "2026-10-13"
        assert new["forecast"]["result"]["dataset_id"] == new["dataset"]["id"]
        assert new["forecast"]["targetDate"] == "2026-10-15"
        assert (
            new["forecast"]["result"]["items"][0]["forecast_mean"]
            != original["forecast"]["result"]["items"][0]["forecast_mean"]
        )
        assert (
            client.get("/api/datasets/" + original["dataset"]["id"]).json()
            == original["dataset"]
        )
        assert shop["reads"] == [("2", date(2026, 10, 11)), ("2", date(2026, 10, 13))]
        pos_sync.run_due(shop["now"])
        assert len(shop["reads"]) == 2


@pytest.mark.parametrize("local_hour", [2, 6])
def test_verified_but_empty_first_import_recovers_without_new_key(shop, local_hour):
    connected_at = START.replace(hour=(local_hour - 5) % 24)
    shop["now"] = connected_at
    shop["empty"] = True
    with TestClient(app) as client:
        result = register(client).json()
        ident = result["connection"]["id"]
        latest = f"{URL}/{ident}/latest"
        assert result["status"] == "connected" and result["forecast"] is None
        assert "нет продаж" in result["connection"]["error"]
        assert client.get(latest).status_code == 409
        shop["empty"] = False
        pos_sync.run_due(connected_at + timedelta(minutes=14, seconds=59))
        assert len(shop["reads"]) == 1
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(pos_sync.run_due, [connected_at + timedelta(minutes=15)] * 4))
        assert len(shop["reads"]) == 2
        assert client.get(URL).json()[0]["status"] == "ready"
        assert client.get(latest).json()["forecast"]["cutoff"] == str(
            connected_at.astimezone(pos_sync.LOCAL_TIME).date() - timedelta(days=1)
        )


def test_outage_and_changed_account_preserve_previous_forecast(shop, monkeypatch):
    with TestClient(app) as client:
        saved = register(client).json()
        ident = saved["connection"]["id"]
        latest = f"{URL}/{ident}/latest"
        expected = client.get(latest).json()
        shop["now"] += timedelta(days=1)
        shop["outage"] = True
        pos_sync.run_due(shop["now"])
        status = client.get(URL)
        assert TOKEN not in status.text and "недоступна" in status.json()[0]["error"]
        for field in ("dataset", "forecast"):
            assert client.get(latest).json()[field] == expected[field]
        shop["account"] = "other-tenant"
        shop["outage"] = False
        pos_sync.run_due(shop["now"] + timedelta(minutes=15))
        assert "торговую точку" in client.get(URL).json()[0]["error"]
        assert len(shop["reads"]) == 2  # no sales are read for the new tenant
        assert client.get(latest).json()["forecast"] == expected["forecast"]
        monkeypatch.setenv("BAKEPLAN_POS_MASTER_KEY_FILE", "/missing/master")
        assert client.get("/api/pos/status").json()["daily_enabled"] is False
        assert client.get(latest).json()["forecast"] == expected["forecast"]
        monkeypatch.delenv("BAKEPLAN_POS_MASTER_KEY_FILE")
        assert client.get("/api/pos/status").json() == {
            "enabled": True,
            "daily_configured": True,
            "daily_enabled": False,
        }
        assert client.delete(f"{URL}/{ident}").status_code == 204
        assert client.get(latest).status_code == 404 and client.get(URL).json() == []
        pos_sync.run_due(shop["now"] + timedelta(days=1))
        assert len(shop["reads"]) == 2


def test_durable_connect_requires_private_mode_master_and_verified_key(
    shop, monkeypatch
):
    with TestClient(app) as client:
        assert register(client, api_key="rejected").status_code == 403
        assert client.get(URL).json() == []
        monkeypatch.delenv("BAKEPLAN_POS_MASTER_KEY_FILE")
        assert client.get("/api/pos/status").json() == {"enabled": True}
        assert register(client).status_code == 503
        monkeypatch.delenv("BAKEPLAN_POS_ENABLED")
        assert register(client).status_code == client.get(URL).status_code == 403
        assert not shop["reads"]


def test_replacement_preserves_the_old_result_until_new_credential_succeeds(shop):
    with TestClient(app) as client:
        original = register(client).json()
        shop["now"] += timedelta(days=1)
        shop["valid_key"] = TOKEN + "-rotated"
        shop["outage"] = True
        renewal = register(client, api_key=shop["valid_key"])
        assert renewal.status_code == 200 and shop["valid_key"] not in renewal.text
        old = renewal.json()
        assert old["connection"]["id"] == original["connection"]["id"]
        assert old["connection"]["status"] == "error"
        assert (
            old["connection"]["last_success_day"]
            == original["connection"]["last_success_day"]
        )
        assert (
            old["dataset"] == original["dataset"]
            and old["forecast"] == original["forecast"]
        )
        shop["outage"] = False
        pos_sync.run_due(shop["now"] + timedelta(minutes=15))
        refreshed = client.get(URL).json()
        assert len(refreshed) == 1 and refreshed[0]["last_success_day"] == "2026-10-13"
        assert (
            refreshed[0]["status"] == "ready"
            and refreshed[0]["dataset_id"] != old["dataset"]["id"]
        )
