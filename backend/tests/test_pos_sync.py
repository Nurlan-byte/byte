"""Public operator API, restart catch-up, duplicate workers and outlet isolation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from queue import Queue
import threading
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend import context_sources, pos_monitoring, pos_providers, pos_sync, pos_vault, storage
from backend.app import app
from backend.errors import ServiceError

URL = "/api/pos/connections"
TOKEN = "operator-test-key-123"
START = datetime(2026, 10, 12, 1, tzinfo=UTC)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("BAKEPLAN_HEALTHCHECKS_FILE", raising=False)
    # Only the model's numerical inference is substituted. Re-import, date
    # slicing, forecast selection, retries and persistence use the real code.
    monkeypatch.setattr(
        "backend.chronos_order._predict",
        lambda inputs, horizon: [
            float(next(value for value in reversed(item["target"]) if math.isfinite(value)))
            for item in inputs
        ],
    )
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
            if day == observed.end and (state.get("stale") or location.id in state.get("stale_shops", ())):
                observed.add(day, "9", "Чай", 0, True)
                continue
            product = "11" if state.get("new_item") and day == observed.end else "9"
            observed.add(day, product, "Чай", 3 + day.weekday(), True)

    monkeypatch.setattr(pos_providers, "locations", shops)
    monkeypatch.setattr(pos_providers, "poster_sales", sales)
    monkeypatch.setattr(pos_providers, "moysklad_sales", sales)
    return state


def register(client, **kwargs):
    return client.post(
        URL, json={"provider": "poster", "api_key": TOKEN, "days": 14, **kwargs}
    )


@contextmanager
def otlp_collector(monkeypatch):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    received = Queue()

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/v1/traces"
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            received.put(ExportTraceServiceRequest.FromString(payload))
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/traces")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "20")
    try:
        yield received
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_stale_http_success_keeps_last_pair_and_exports_safe_spans(shop, monkeypatch):
    with otlp_collector(monkeypatch) as received, TestClient(app) as client:
        good = register(client).json()
        ident = good["connection"]["id"]
        assert good["connection"]["last_observed_day"] == "2026-10-11"
        shop["now"] += timedelta(days=1)
        shop["stale"] = True
        pos_sync.run_due(shop["now"])
        latest = client.get(f"{URL}/{ident}/latest").json()
        assert latest["forecast"] == good["forecast"] and latest["dataset"]["id"] == good["dataset"]["id"]
        assert latest["connection"]["last_success_day"] == "2026-10-12"
        assert "не подтвердила продажи" in latest["connection"]["error"]
        with storage.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM datasets").fetchone() == (1,)
            assert db.execute("SELECT business_day,observed_through FROM pos_fresh_days").fetchall() == [
                ("2026-10-12", "2026-10-11")
            ]
        spans = []
        while len(spans) < 6:
            export = received.get(timeout=8)
            assert TOKEN not in str(export) and "Чай" not in str(export)
            for resource in export.resource_spans:
                for scope in resource.scope_spans:
                    spans.extend(scope.spans)
        assert sorted(s.name for s in spans) == sorted(["pos.daily", "pos.import", "pos.forecast", "pos.publish", "pos.daily", "pos.import"])
        parents = {s.span_id: s for s in spans if s.name == "pos.daily"}
        for stage in (s for s in spans if s.name != "pos.daily"):
            assert parents[stage.parent_span_id].trace_id == stage.trace_id
        assert sorted(s.status.code for s in parents.values()) == [0, 2]
        assert not any(s.events for s in spans)


def test_heartbeat_failure_retries_after_commit_without_reimport_or_stale_signal(shop, monkeypatch, tmp_path):
    check_id = str(uuid4())
    sent = []
    config = tmp_path / "checks.json"
    with TestClient(app) as client:
        ident = register(client).json()["connection"]["id"]
        config.write_text(json.dumps({ident: check_id}), encoding="utf-8")
        monkeypatch.setenv("BAKEPLAN_HEALTHCHECKS_FILE", str(config))

        def provider(key):
            assert key == check_id
            with storage.connection() as db:
                assert db.execute("SELECT last_success_day FROM pos_connections WHERE id=?", (ident,)).fetchone() == (
                    str(shop["now"].date()),)
            sent.append(key)
            if len(sent) == 1:
                raise TimeoutError("simulated timeout")
            return True

        monkeypatch.setattr(pos_monitoring, "_send_success", provider)
        pos_sync.run_due(shop["now"])
        pos_sync.run_due(shop["now"] + timedelta(minutes=14))
        assert len(sent) == 1 and len(shop["reads"]) == 1
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(pos_sync.run_due, [shop["now"] + timedelta(minutes=15)] * 3))
        assert len(sent) == 2 and len(shop["reads"]) == 1
        shop["now"] += timedelta(days=1)
        shop["stale"] = True
        pos_sync.run_due(shop["now"])
        assert len(sent) == 2
        assert client.get(URL).json()[0]["status"] == "error"
        shop["stale"] = False
        pos_sync.run_due(shop["now"] + timedelta(minutes=15))
        assert len(sent) == 3 and client.get(URL).json()[0]["status"] == "ready"


def test_heartbeat_is_per_outlet_and_never_replays_yesterdays_signal(shop, monkeypatch, tmp_path):
    shop["shops"].append("2")
    with TestClient(app) as client:
        first, second = [register(client, location_id=n).json()["connection"]["id"] for n in ("1", "2")]
        check1, check2 = str(uuid4()), str(uuid4())
        config = tmp_path / "checks.json"
        config.write_text(json.dumps({first: check1, second: check2, "bad": "bad"}), encoding="utf-8")
        monkeypatch.setenv("BAKEPLAN_HEALTHCHECKS_FILE", str(config))
        sent = []

        def success(key):
            sent.append(key)
            return True

        monkeypatch.setattr(pos_monitoring, "_send_success", success)
        shop["stale_shops"] = {"1"}
        shop["new_item"] = True
        shop["now"] += timedelta(days=1)
        calculate = pos_sync.forecast_for_day
        forecast_reads = []

        def measured_forecast(snapshot, day):
            with patch.object(storage, "fetch", wraps=storage.fetch) as fetch:
                result = calculate(snapshot, day)
            forecast_reads.append([c.args[2] for c in fetch.call_args_list if c.args[1] == "datasets"])
            return result

        monkeypatch.setattr(pos_sync, "forecast_for_day", measured_forecast)
        pos_sync.run_due(shop["now"])
        assert forecast_reads == [[]]  # Only the fresh branch ran; its snapshot was already in memory.
        assert sent == [check2]
        first_saved = client.get(f"{URL}/{first}/latest").json()
        second_saved = client.get(f"{URL}/{second}/latest").json()
        assert first_saved["connection"]["last_observed_day"] == "2026-10-11"
        assert second_saved["connection"]["last_observed_day"] == "2026-10-12"
        assert {r["product_id"] for r in second_saved["dataset"]["rows"] if r["date"] == "2026-10-12"} == {"11"}
        assert client.delete(f"{URL}/{second}").status_code == 204
        with storage.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM pos_fresh_days WHERE pinged_at IS NOT NULL").fetchone() == (1,)


@pytest.mark.parametrize("provider", ["poster", "moysklad"])
def test_once_then_daily_after_restart_without_key_or_browser(shop, provider, monkeypatch):
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
        assert first["forecast"]["method"] == "AUTO"
        assert first["forecast"]["result"]["items"][0]["method"] == "Chronos-2"
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
        # The operator opens exactly the snapshot published by the daily worker.
        monkeypatch.setattr("backend.context_routes.business_today", lambda: date(2026, 10, 14))
        dataset_id = new["dataset"]["id"]
        with patch.object(storage, "fetch", wraps=storage.fetch) as fetch:
            manual = client.post(f"/api/datasets/{dataset_id}/first-forecast")
        assert manual.status_code == 200, manual.text
        assert manual.json() == {
            key: value for key, value in new["forecast"].items() if key != "observed_through"
        }
        assert [c.args[1:] for c in fetch.call_args_list] == [("datasets", dataset_id)]
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


@pytest.mark.parametrize("legacy_snapshot", [False, True])
def test_daily_forecast_service_outage_keeps_pair_until_successful_retry(shop, monkeypatch, legacy_snapshot):
    from backend import chronos_order
    from backend.storage import connection

    predict = chronos_order._predict

    def unavailable(*args):
        raise chronos_order.ChronosUnavailable("runtime unavailable")

    with TestClient(app) as client:
        old = register(client).json()
        latest = f"{URL}/{old['connection']['id']}/latest"
        if legacy_snapshot:
            # A previous release already completed today's sync using Ridge.
            first = old["forecast"]
            first["method"] = "ML"
            first["result"].pop("protocol_version")
            first["result"]["model_id"] = "legacy-ridge-id"
            first["result"]["items"] = [dict(product_id="9", method="ML", forecast_mean=17)]
            with connection() as db:
                db.execute("UPDATE pos_connections SET forecast=? WHERE id=?",
                           (json.dumps(first), old["connection"]["id"]))
            assert client.get(latest).json()["forecast"] == first
        shop["now"] += timedelta(days=1)
        monkeypatch.setattr(chronos_order, "_predict", unavailable)
        pos_sync.run_due(shop["now"])
        kept = client.get(latest).json()
        assert "пересчитать прогноз" in kept["connection"]["error"]
        assert kept["dataset"] == old["dataset"] and kept["forecast"] == old["forecast"]
        with connection() as db:
            assert db.execute("SELECT business_day FROM pos_fresh_days").fetchall() == [("2026-10-12",)]
        monkeypatch.setattr(chronos_order, "_predict", predict)
        pos_sync.run_due(shop["now"] + timedelta(minutes=20))
        recovered = client.get(latest).json()
        assert recovered["connection"]["error"] is None
        assert recovered["dataset"]["id"] != old["dataset"]["id"]
        assert recovered["forecast"]["result"]["dataset_id"] == recovered["dataset"]["id"]
        assert recovered["forecast"]["result"]["protocol_version"] == chronos_order.PROTOCOL_VERSION
        assert recovered["forecast"]["method"] == "AUTO" and recovered["forecast"]["result"]["model_id"] is None


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


@pytest.mark.parametrize("bad_field", ["dataset_id", "cutoff", "target_date", "items"])
def test_inconsistent_or_missing_forecast_cannot_signal_success(shop, monkeypatch, tmp_path, bad_field):
    produce = pos_sync.forecast_for_day

    def invalid(dataset, day):
        forecast = produce(dataset, day)
        forecast["result"][bad_field] = [] if bad_field == "items" else "unrelated"
        return forecast

    with TestClient(app) as client:
        saved = register(client).json()
        ident = saved["connection"]["id"]
        config = tmp_path / "checks.json"
        config.write_text(json.dumps({ident: str(uuid4())}), encoding="utf-8")
        monkeypatch.setenv("BAKEPLAN_HEALTHCHECKS_FILE", str(config))
        monkeypatch.setattr(pos_monitoring, "_send_success", lambda *_: pytest.fail("Unverified forecast"))
        monkeypatch.setattr(pos_sync, "forecast_for_day", invalid)
        shop["now"] += timedelta(days=1)
        pos_sync.run_due(shop["now"])
        kept = client.get(f"{URL}/{ident}/latest").json()
        assert kept["connection"]["status"] == "error"
        assert kept["forecast"] == saved["forecast"] and kept["dataset"] == saved["dataset"]
