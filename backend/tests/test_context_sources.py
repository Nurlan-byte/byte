"""Provider fixtures and real HTTP/SQLite verification of the optional context workflow."""

from datetime import date, timedelta
from http.client import IncompleteRead
import io
import random
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from backend import context_routes, context_sources as source
from backend.app import app
from backend.errors import ServiceError
from backend.imports import REQUIRED
from backend.storage import connection, save_dataset
from backend.tests.test_ml_api import import_dataset, synthetic_csv, table_counts

DAY = timedelta(days=1)
TODAY = date(2026, 10, 21)
TARGET = date(2026, 10, 22)
REAL_FETCH = source._fetch_weather


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("BAKEPLAN_WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("BAKEPLAN_WEATHER_MODE", raising=False)
    monkeypatch.setattr(
        source, "_fetch_weather", lambda *args: pytest.fail("unexpected network call")
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "context.sqlite3"))
    monkeypatch.setattr(context_routes, "business_today", lambda: TODAY)
    monkeypatch.setattr("backend.app.business_today", lambda: TODAY)
    with TestClient(app) as active:
        yield active


def original():
    return synthetic_csv(days=22, products=["p1"])


def meteo(days):
    return {
        "hourly_units": {source.TEMP: "°C", source.RAIN: "mm"},
        "hourly": {
            "time": [f"{day}T{hour:02d}:00" for day in days for hour in range(24)],
            source.TEMP: [h - 7 for _ in days for h in range(24)],
            source.RAIN: [0.25 for _ in days for _ in range(24)],
        },
    }


def install_weather(monkeypatch, payload):
    monkeypatch.setenv("BAKEPLAN_WEATHER_MODE", "evaluation")
    calls = []

    def fetch(host, params):
        calls.append((host, params))
        return payload

    monkeypatch.setattr(source, "_fetch_weather", fetch)
    return calls


def test_weather_window_for_long_and_historical_dates(monkeypatch):
    calls = install_weather(monkeypatch, meteo([]))
    result, warnings = source.load_demand_context(
        TODAY - 500 * DAY, TODAY, TODAY, "almaty"
    )
    assert calls[-1][1]["start_date"] == str(TODAY - 364 * DAY)
    assert result["weather"] == [] and any("последнего года" in w for w in warnings)
    result, warnings = source.load_demand_context(date.min, date.min, TODAY, "almaty")
    assert calls[-1][1]["start_date"] == "0001-01-01"
    assert result["weather"] == [] and warnings


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Cloud Game Zone · г. Шымкент", "shymkent"),
        ("POSTER (AlMaTy)", "almaty"),
        ("Филиал №2, Қызылорда", "kyzylorda"),
        ("Ақтөбе HQ", "aktobe"),
        ("Қарағанды", "karaganda"),
        ("Астана, Алматы", None),
        ("Алматинская область", None),
        ("", None),
    ],
)
def test_city_requires_unambiguous_complete_word(name, expected):
    assert source.resolve_city(name) == expected


def test_calendar_vintages_transfers_and_unverified_year():
    def values(target, asof):
        context, _ = source.load_demand_context(target, target, asof, None)
        return [(r["day_type"], r["available_on"]) for r in context["calendar"]]

    assert values(date(2025, 1, 3), TODAY) == [("day_off", "2024-11-28")]
    assert values(date(2025, 1, 5), TODAY) == [("workday", "2024-11-28")]
    assert values(date(2026, 3, 8), TODAY) == [("holiday", "2025-12-05")]
    assert values(date(2026, 3, 9), TODAY) == [("day_off", "2025-12-05")]
    assert values(date(2026, 3, 8), date(2025, 11, 1)) == []
    for target, before, after in [
        (date(2026, 8, 30), "holiday", "day_off"),
        (date(2026, 8, 31), "day_off", "workday"),
    ]:
        assert values(target, date(2026, 6, 28)) == [(before, "2025-12-05")]
        assert values(target, date(2026, 6, 29)) == [
            (before, "2025-12-05"),
            (after, "2026-06-29"),
        ]
    qurbon = date(2026, 5, 27)
    assert values(qurbon, date(2025, 12, 4)) == []
    assert values(qurbon, date(2025, 12, 5)) == [("holiday", "2025-12-05")]
    context, warnings = source.load_demand_context(
        date(2027, 1, 1), date(2027, 1, 3), TODAY, None
    )
    assert context["calendar"] == [] and any("2025–2026" in w for w in warnings)


@pytest.mark.parametrize(
    "year, cutoff, total",
    [
        (2025, date(2024, 11, 28), 246),
        (2026, date(2025, 12, 5), 246),
        (2026, date(2026, 6, 29), 247),
    ],
)
def test_working_days_match_published_annual_totals(year, cutoff, total):
    rows = source._calendar(date(year, 1, 1), date(year, 12, 31), cutoff)
    by_date = {row["date"]: row["day_type"] for row in rows}
    assert len(by_date) == 365
    assert list(by_date.values()).count("workday") == total


def test_day_only_and_calendar_only_do_not_require_weather():
    assert source.load_demand_context(TODAY, TARGET, TODAY, "weekday_only") == (
        {"calendar": [], "weather": []},
        [],
    )
    context, warnings = source.load_demand_context(TODAY, TARGET, TODAY, None)
    assert len(context["calendar"]) == 2 and context["weather"] == [] and warnings == []
    context, warnings = source.load_demand_context(TODAY, TARGET, TODAY, "shymkent")
    assert context["city"] == "Шымкент" and context["weather"] == []
    assert any("не подключён" in w for w in warnings)


@pytest.mark.parametrize("paid", [False, True])
def test_archived_lead_times_full_day_and_as_of(monkeypatch, paid):
    calls = install_weather(monkeypatch, meteo([TARGET + i * DAY for i in range(3)]))
    if paid:
        monkeypatch.setenv("BAKEPLAN_WEATHER_API_KEY", "weather-secret-for-test")
    result, warnings = source.load_demand_context(
        TARGET, TARGET + 2 * DAY, TODAY, "shymkent"
    )
    assert warnings == []
    assert result["weather_source"] == "Open-Meteo · NOAA GFS"
    assert result["weather"] == [
        {
            "date": str(day),
            "available_on": str(day - 2 * DAY),
            "kind": "forecast",
            "temperature_c": 4.5,
            "precipitation_mm": 6.0,
        }
        for day in (TARGET, TARGET + DAY)
    ]
    host, params = calls[0]
    assert host == ("customer-previous-runs-api" if paid else "previous-runs-api")
    assert params == {
        "latitude": 42.32,
        "longitude": 69.59,
        "timezone": "Asia/Almaty",
        "models": "gfs_seamless",
        "hourly": "temperature_2m_previous_day3,precipitation_previous_day3",
        "start_date": str(TARGET),
        "end_date": str(TODAY + 2 * DAY),
        **({"apikey": "weather-secret-for-test"} if paid else {}),
    }


@pytest.mark.parametrize(
    "problem",
    [
        "missing_hour",
        "duplicate_hour",
        "null_rain",
        "hot",
        "infinite",
        "huge_integer",
        "wrong_units",
        "mismatched_array",
    ],
)
def test_partial_or_invalid_weather_degrades_with_warning(monkeypatch, problem):
    payload = meteo([TARGET])
    hourly = payload["hourly"]
    if problem == "missing_hour":
        for series in hourly.values():
            series.pop()
    elif problem == "duplicate_hour":
        for series in hourly.values():
            series.append(series[-1])
    elif problem == "null_rain":
        hourly[source.RAIN][6] = None
    elif problem in {"hot", "infinite", "huge_integer"}:
        hourly[source.TEMP][7] = {
            "hot": 250,
            "infinite": float("inf"),
            "huge_integer": 10**690,
        }[problem]
    elif problem == "wrong_units":
        payload["hourly_units"][source.RAIN] = "inch"
    else:
        hourly[source.RAIN].pop()
    install_weather(monkeypatch, payload)
    result, warnings = source.load_demand_context(TARGET, TARGET, TODAY, "almaty")
    assert warnings and result["calendar"]
    if problem == "null_rain":
        assert result["weather"][0]["temperature_c"] == 4.5
        assert result["weather"][0]["precipitation_mm"] is None
    elif problem in {"hot", "infinite", "huge_integer"}:
        assert result["weather"][0]["temperature_c"] is None
        assert result["weather"][0]["precipitation_mm"] == 6
    else:
        assert result["weather"] == []


@pytest.mark.parametrize(
    "error",
    [
        URLError("secret-in-url"),
        TimeoutError(),
        IncompleteRead(b"private-partial-response"),
    ],
)
def test_upstream_failure_preserves_calendar_without_data_disclosure(
    monkeypatch, error
):
    def fail(*args):
        raise error

    monkeypatch.setenv("BAKEPLAN_WEATHER_API_KEY", "secret-in-url")
    monkeypatch.setattr(source, "_fetch_weather", fail)
    context, warnings = source.load_demand_context(TARGET, TARGET, TODAY, "almaty")
    assert context["calendar"] and context["weather"] == []
    assert "временно недоступен" in " ".join(warnings)
    assert "secret" not in repr((context, warnings)) and "private" not in repr(
        (context, warnings)
    )


def test_context_api_stores_immutable_snapshots_and_reuses_retries(client, monkeypatch):
    dataset = import_dataset(client, original())
    ident = dataset["id"]
    calls = install_weather(monkeypatch, meteo([date(2026, 10, 11), TARGET]))
    before = client.get(f"/api/datasets/{ident}").json()
    response = client.post(
        f"/api/datasets/{ident}/demand-context", json={"city": "almaty"}
    )
    assert response.status_code == 201, response.text
    stored = response.json()["dataset"]
    new_id = stored["id"]
    assert new_id != ident and stored["version"] != before["version"]
    assert stored["rows"] == before["rows"]
    assert stored["demand_context"]["city"] == "Алматы"
    assert [r["date"] for r in stored["demand_context"]["weather"]] == [
        "2026-10-11",
        str(TARGET),
    ]
    assert len(calls) == 2 and [params["end_date"] for _, params in calls] == [
        "2026-10-13",
        str(TARGET),
    ]
    assert client.get(f"/api/datasets/{new_id}").json() == stored
    assert client.get(f"/api/datasets/{ident}").json() == before
    for source_id in (ident, new_id):
        again = client.post(
            f"/api/datasets/{source_id}/demand-context", json={"city": "almaty"}
        )
        assert again.status_code == 200 and again.json()["dataset"] == stored
    assert table_counts()["datasets"] == 2
    monkeypatch.delenv("BAKEPLAN_WEATHER_MODE")
    offline = client.post(
        f"/api/datasets/{new_id}/demand-context", json={"city": "almaty"}
    )
    assert offline.status_code == 200 and offline.json()["dataset"] == stored
    assert any("ранее полученную погоду" in w for w in offline.json()["warnings"])
    for choice in ("astana", "none", "weekday_only"):
        changed = client.post(
            f"/api/datasets/{new_id}/demand-context", json={"city": choice}
        )
        assert changed.status_code == 201
        assert changed.json()["dataset"]["demand_context"]["weather"] == []
    assert client.get(f"/api/datasets/{new_id}").json() == stored


def test_context_choice_and_legacy_validation(client):
    listed = client.get("/api/context/cities")
    assert listed.status_code == 200 and {
        row["id"]: row["name"] for row in listed.json()
    } == {k: v[0] for k, v in source.CITIES.items()}
    dataset = import_dataset(client, original())
    assert (
        client.post(
            f"/api/datasets/{dataset['id']}/demand-context", json={"city": "unknown"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/datasets/missing/demand-context", json={}).status_code == 404
    )
    legacy = client.post(
        "/api/datasets", json={"csv_text": original(), "source": "synthetic"}
    )
    assert legacy.status_code == 201
    ident = legacy.json()["id"]
    assert (
        client.post(f"/api/datasets/{ident}/demand-context", json={}).status_code == 422
    )
    assert client.post(f"/api/datasets/{ident}/first-forecast").status_code == 200
    assert table_counts()["models"] == 0


@pytest.mark.parametrize("provider", [None, "poster"])
def test_first_forecast_b1_dates_matching_api_and_persisted_state(client, provider):
    dataset = import_dataset(client, original())
    if provider:
        dataset, _ = save_dataset(
            dict(
                dataset,
                id="pos-snapshot",
                content_hash="pos-history",
                version="pos-history",
                pos_provider=provider,
            )
        )
    ident = dataset["id"]
    before = client.get(f"/api/datasets/{ident}").json()
    response = client.post(f"/api/datasets/{ident}/first-forecast")
    assert response.status_code == 200, response.text
    body = response.json()
    cutoff = "2026-10-20" if provider else "2026-10-11"
    target = "2026-10-22" if provider else "2026-10-13"
    assert (body["method"], body["cutoff"], body["targetDate"]) == (
        "B1",
        cutoff,
        target,
    )
    assert any("исторический расчёт" in w for w in body["warnings"]) == (not provider)
    independently = client.post(
        "/api/forecasts",
        json={
            "dataset_id": ident,
            "method": "B1",
            "cutoff": cutoff,
            "target_date": target,
        },
    )
    assert independently.status_code == 200 and body["result"] == independently.json()
    assert body["result"]["items"][0]["method"] == "B1"
    repeated = client.post(f"/api/datasets/{ident}/first-forecast")
    assert repeated.json() == body
    models = client.get(f"/api/datasets/{ident}/models").json()
    assert len(models) == 1 and models[0]["training_cutoff"] == cutoff
    assert client.get(f"/api/datasets/{ident}").json() == before
    counts = table_counts()
    assert counts["training_requests"] == 1 and counts["plans"] == 0


def test_first_forecast_keeps_baseline_after_saved_model_corruption(client):
    dataset = import_dataset(client, original())
    route = f"/api/datasets/{dataset['id']}/first-forecast"
    before = client.post(route)
    assert before.status_code == 200
    with connection() as con:
        con.execute("UPDATE models SET artifact_hash = 'damaged'")
    assert client.get(f"/api/datasets/{dataset['id']}/models").status_code == 409
    after = client.post(route)
    assert (
        after.status_code == 200 and after.json()["result"] == before.json()["result"]
    )
    assert after.json()["warnings"] != before.json()["warnings"]
    assert table_counts()["models"] == 1


def test_first_forecast_temporary_training_failure_only(client, monkeypatch):
    dataset = import_dataset(client, original())
    route = f"/api/datasets/{dataset['id']}/first-forecast"

    def fail(*args):
        raise ServiceError(status, "private detail")

    monkeypatch.setattr(context_routes, "train_dataset", fail)
    status = 503
    fallback = client.post(route)
    assert (
        fallback.status_code == 200
        and fallback.json()["result"]["items"][0]["method"] == "B1"
    )
    assert "private" not in fallback.text
    status = 422
    assert client.post(route).status_code == 422
    assert table_counts()["models"] == 0


def test_keyless_csv_and_optional_weather_reach_trained_forecast_via_http(
    client, monkeypatch
):
    # Colder days sell more in this deliberately generated shop. Weather on the
    # target day is supplied as an archived forecast, never as a measured fact.
    rng = random.Random(12345)
    dates = [TARGET - n * DAY for n in reversed(range(154))]
    temperatures = {day: rng.choice([-12, -8, 5, 13, 22]) for day in dates}
    sales = []
    for day in dates[:-2]:
        sold = 65 - temperatures[day] * 2
        sales.append(f"{day},p1,true,0,{sold},{sold},0,0,0,none")
    dataset = import_dataset(client, "\n".join([",".join(REQUIRED), *sales]))
    archived = meteo(dates)
    archived["hourly"][source.TEMP] = [
        temperatures[day] for day in dates for _ in range(24)
    ]
    install_weather(monkeypatch, archived)
    enriched = client.post(
        f"/api/datasets/{dataset['id']}/demand-context", json={"city": "shymkent"}
    )
    assert enriched.status_code == 201
    ident = enriched.json()["dataset"]["id"]
    forecast = client.post(f"/api/datasets/{ident}/first-forecast")
    assert forecast.status_code == 200, forecast.text
    body = forecast.json()
    assert body["method"] == "ML" and body["targetDate"] == str(TARGET)
    product = body["result"]["items"][0]
    assert product["method"] == "ML"
    assert product["demand_features"]["forecast_temperature_c"] == temperatures[TARGET]
    persisted = client.get(f"/api/datasets/{ident}/models").json()
    assert len(persisted) == 1 and persisted[0]["id"] == body["result"]["model_id"]
    assert persisted[0]["products"]["p1"]["selection"]["selected"] == "context"


def test_weather_request_disallows_redirect_and_bounds_body(monkeypatch):
    class Transport:
        def open(self, url, timeout):
            assert (
                url
                == "https://previous-runs-api.open-meteo.com/v1/forecast?models=gfs_seamless"
            )
            assert timeout == 10
            return io.BytesIO(b" " * 2_000_001)

    def build(handler):
        assert handler.redirect_request(None, None, None, None, None, None) is None
        return Transport()

    # The autouse fixture blocks _fetch_weather elsewhere. Exercise its original implementation.
    monkeypatch.setattr(source, "build_opener", build)
    with pytest.raises(ValueError, match="limit"):
        REAL_FETCH("previous-runs-api", {"models": "gfs_seamless"})
