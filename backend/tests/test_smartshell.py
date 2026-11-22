"""SmartShell club-token import and unattended refresh through the public API."""

from datetime import UTC, datetime, timedelta
import json
import re

import pytest
from fastapi.testclient import TestClient

from backend import context_sources, pos, pos_providers, pos_sync, pos_vault, storage
from backend.app import app
from backend.tests.test_pos import Response
from scripts import pos_branches


TOKEN = "smartshell-test-token-for-club-42"
BODY = {"provider": "smartshell", "api_key": TOKEN, "days": 8, "city": "none"}
CONNECTIONS = "/api/pos/connections"
NOW = datetime(2026, 10, 12, 1, tzinfo=UTC)
LABELS = ["Название позиции", "Категория", "Продано", "Сумма", "Ср. стоимость", "Кол-во отмен"]


class SmartShellTransport:
    def __init__(self):
        self.now = NOW
        self.club_id = 42
        self.calls = []
        self.quantity = None
        self.catalog = [{"id": 90, "title": "Чай"}]
        self.error = None
        self.sales_denied_token = None
        self.incomplete = False
        self.dates = []

    def open(self, request, timeout):
        assert request.full_url == "https://billing.smartshell.gg/api/graphql"
        assert request.get_method() == "POST" and timeout > 0
        authorization = request.get_header("Authorization")
        assert authorization.startswith("Bearer ")
        token = authorization.removeprefix("Bearer ")
        assert token in {TOKEN, self.sales_denied_token} and token.encode() not in request.data
        payload = json.loads(request.data)
        query = payload["query"]
        assert not re.search(r"\b(mutation|phone|email|clients|visits|deposit)\b", query, re.I)
        self.calls.append(query)
        data = {}
        if "currentClub" in query:
            data["currentClub"] = {"id": self.club_id, "name": "Клуб 42", "city": "Алматы"}
        if re.search(r"\bgoods\b", query):
            data["goods"] = self.catalog
        aliases = re.findall(r"\b(\w+)\s*:\s*salesReport\s*\(", query)
        if "salesReport(" in query.replace(" ", ""):
            assert 1 <= len(aliases) <= 7
            for alias in aliases:
                index = int(alias.removeprefix("d"))
                start = datetime.fromisoformat(payload["variables"][f"from{index}"])
                end = datetime.fromisoformat(payload["variables"][f"to{index}"])
                self.dates.append((start, end))
                units = start.day if self.quantity is None else self.quantity
                data[alias] = {
                    "labels": LABELS,
                    "keys": ["Чай", "Игра по часу", "Депозит"],
                    "points": 3,
                    "extraLabels": ["GOOD", "TARIFF", "DEPOSIT"],
                    "data": [
                        {"values": [units, units * 100, 100, 2]},
                        {"values": [40, 1000, 25, 0]},
                        {"values": [1, 250, 250, 0]},
                    ],
                }
            if self.incomplete:
                del data[aliases[-1]]
            error = 403 if token == self.sales_denied_token else self.error
            if error is not None:
                return Response({"data": data, "errors": [
                    {"message": token, "extensions": {"code": error}}
                ]})
        assert data or self.incomplete
        return Response({"data": data})


@pytest.fixture
def club(tmp_path, monkeypatch):
    vendor = SmartShellTransport()
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "club.db"))
    monkeypatch.setenv("BAKEPLAN_POS_ENABLED", "1")
    monkeypatch.setattr(pos, "business_today", lambda: vendor.now.date())
    monkeypatch.setattr(pos_sync, "now_utc", lambda: vendor.now)
    monkeypatch.setattr(pos_sync, "POLL_SECONDS", 3600)
    monkeypatch.setattr(context_sources, "load_demand_context", lambda *args: ({}, []))
    monkeypatch.setattr(pos_providers, "build_opener", lambda *args: vendor)
    return vendor


@pytest.fixture
def daily_club(club, tmp_path, monkeypatch):
    master = tmp_path / "master.key"
    pos_vault.init_key(master)
    monkeypatch.setenv(pos_vault.KEY_FILE_ENV, str(master))
    monkeypatch.setattr("backend.chronos_order._predict", lambda inputs, horizon: [5.0] * len(inputs))
    return club


def test_single_club_daily_units_and_idempotent_readback(club):
    with TestClient(app) as client:
        wrong = client.post("/api/pos/import", json={**BODY, "location_id": "43"})
        assert wrong.status_code == 422
        assert not any("salesReport" in query for query in club.calls)
        response = client.post("/api/pos/import", json=BODY)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["location"] == {"id": "42", "name": "Клуб 42"}
        ds = result["dataset"]
        assert ds["pos_provider"] == "smartshell" and ds["unit"] == "physical_unit"
        assert ds["product_names"] == {"90": "Чай"}
        days = [NOW.date() - timedelta(days=d) for d in range(8, 0, -1)]
        assert ds["rows"] == [
            {"date": str(day), "product_id": "90", "sold": day.day} for day in days
        ]
        assert [start.date() for start, _ in club.dates] == days
        for start, end in club.dates:
            assert start.strftime("%H:%M:%S") == "00:00:00"
            assert end - start == timedelta(days=1, seconds=-1)
        assert client.get("/api/datasets/" + ds["id"]).json() == ds
        again = client.post("/api/pos/import", json=BODY)
        assert again.json()["dataset"]["id"] == ds["id"]


def test_maximum_history_fits_vendor_request_limit(club):
    with TestClient(app) as client:
        result = client.post("/api/pos/import", json={**BODY, "days": 120})
        assert result.status_code == 200, result.text
        assert sorted(d.date() for d, _ in club.dates) == [
            NOW.date() - timedelta(days=offset) for offset in range(120, 0, -1)
        ]
        assert len(club.calls) <= 20


def test_operator_reads_one_sales_day_without_storing_key_or_sales(
    club, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(pos_branches, "getpass", lambda _: TOKEN)
    pos_branches.main(["smartshell", "--sales-on", "2026-10-11"])
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err
    report = json.loads(output.out)
    assert report["id"] == "42" and report["bakery_id"].startswith("smartshell-")
    assert report["sales_date"] == "2026-10-11"
    assert report["goods"] == [{"product_id": "90", "name": "Чай", "sold": 11}]
    assert [str(start.date()) for start, _ in club.dates] == ["2026-10-11"]
    assert not list(tmp_path.glob("club.db*"))
    club.error = 401
    with pytest.raises(SystemExit) as error:
        pos_branches.main(["smartshell", "--sales-on", "2026-10-11"])
    denied = capsys.readouterr()
    assert error.value.code == 1 and denied.out == ""
    assert "токен" in denied.err.lower() and TOKEN not in denied.err
    assert not list(tmp_path.glob("club.db*"))


@pytest.mark.parametrize("case", ["unknown", "ambiguous", "fractional", "incomplete", "partial_auth"])
def test_bad_vendor_data_never_publishes_or_exposes_key(club, caplog, case):
    if case == "unknown":
        club.catalog = []
    elif case == "ambiguous":
        club.catalog.append({"id": 91, "title": "Чай"})
    elif case == "fractional":
        club.quantity = 1.25
    elif case == "incomplete":
        club.incomplete = True
    else:
        club.error = 401
    with TestClient(app) as client:
        result = client.post("/api/pos/import", json=BODY)
        expected = {403} if case == "partial_auth" else {422, 502}
        assert result.status_code in expected, result.text
        assert TOKEN not in result.text + caplog.text
        assert client.get("/api/datasets").json() == []


def test_saved_token_refreshes_and_preserves_last_success_after_revocation(daily_club, tmp_path, caplog):
    club = daily_club
    with TestClient(app) as client:
        result = client.post(CONNECTIONS, json=BODY)
        assert result.status_code == 200, result.text
        first = result.json()
        assert first["connection"]["status"] == "ready", first["connection"]
        latest_url = f"{CONNECTIONS}/{first['connection']['id']}/latest"
        assert client.get(latest_url).json()["dataset"] == first["dataset"]
        assert first["forecast"]["observed_through"] == "2026-10-11"
        assert first["forecast"]["targetDate"] == "2026-10-13"
        with storage.connection() as db:
            ciphertext, = db.execute("SELECT ciphertext FROM pos_connections").fetchone()
            assert ciphertext and TOKEN.encode() not in bytes(ciphertext)

        club.now += timedelta(days=1)
        pos_sync.run_due(club.now)
        updated = client.get(latest_url).json()
        assert updated["connection"]["status"] == "ready"
        assert updated["dataset"]["id"] != first["dataset"]["id"]
        assert updated["forecast"]["observed_through"] == "2026-10-12"
        assert updated["forecast"]["targetDate"] == "2026-10-14"

        club.error = 401
        club.now += timedelta(days=1)
        pos_sync.run_due(club.now)
        failed = client.get(latest_url).json()
        assert failed["connection"]["error"] and failed["connection"]["status"] != "ready"
        assert failed["dataset"] == updated["dataset"] and failed["forecast"] == updated["forecast"]
        assert TOKEN not in result.text + client.get(latest_url).text + caplog.text
        for path in tmp_path.glob("club.db*"):
            assert TOKEN.encode() not in path.read_bytes()


def test_report_denial_never_saves_token_or_replaces_working_connection(daily_club, caplog):
    club = daily_club
    denied_token = TOKEN + "-without-sales"
    club.sales_denied_token = denied_token
    body = {**BODY, "api_key": denied_token}
    with TestClient(app) as client:
        first_try = client.post(CONNECTIONS, json=body)
        assert first_try.status_code == 403, first_try.text
        assert client.get(CONNECTIONS).json() == []
        assert client.get("/api/datasets").json() == []
        club.calls.clear()
        connected = client.post(CONNECTIONS, json=BODY)
        assert connected.status_code == 200, connected.text
        first = connected.json()
        assert first["connection"]["status"] == "ready"
        assert len(club.calls) == 4  # Club, catalog, seven days, last day.
        latest = f"{CONNECTIONS}/{first['connection']['id']}/latest"
        original = client.get(latest).json()
        with storage.connection() as db:
            encrypted = db.execute("SELECT ciphertext FROM pos_connections").fetchone()
        failed_rotation = client.post(CONNECTIONS, json=body)
        assert failed_rotation.status_code == 403
        assert client.get(latest).json() == original
        with storage.connection() as db:
            assert db.execute("SELECT ciphertext FROM pos_connections").fetchone() == encrypted
        club.now += timedelta(days=1)
        pos_sync.run_due(club.now)
        updated = client.get(latest).json()
        assert updated["connection"]["status"] == "ready"
        assert updated["connection"]["last_observed_day"] == "2026-10-12"
        assert denied_token not in failed_rotation.text + caplog.text
