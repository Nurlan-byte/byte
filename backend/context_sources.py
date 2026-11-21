"""Optional branch context. Only a city's coordinates and dates leave this process."""

import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from http.client import HTTPException
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, build_opener

# Approximate city centres; no shop address or POS key goes to the weather provider.
CITIES = {
    "shymkent": ("Шымкент", 42.32, 69.59),
    "almaty": ("Алматы", 43.24, 76.89),
    "astana": ("Астана", 51.17, 71.45),
    "taraz": ("Тараз", 42.90, 71.37),
    "karaganda": ("Караганда", 49.80, 73.10),
    "kyzylorda": ("Кызылорда", 44.85, 65.50),
    "aktobe": ("Актобе", 50.28, 57.17),
    "atyrau": ("Атырау", 47.10, 51.92),
}
CITY_ALIASES = {
    "karaganda": {"қарағанды"},
    "kyzylorda": {"қызылорда"},
    "aktobe": {"ақтөбе"},
}
COMMON_HOLIDAYS = {
    "01-01",
    "01-02",
    "03-08",
    "03-21",
    "03-22",
    "03-23",
    "05-01",
    "05-07",
    "05-09",
    "07-06",
    "10-25",
    "12-16",
}
# Kazakhstan public five-day calendar (demand covariate, never a store closure).
# gov.kz/org/enbek/documents/details/756543, /932608, /1034069 and order /753502.
CALENDAR_PUBLICATION = {2025: date(2024, 11, 28), 2026: date(2025, 12, 5)}
AUGUST_REVISION_PUBLICATION = date(2026, 6, 29)
DAYS_OFF = {
    2025: {"01-03", "03-10", "03-24", "03-25", "07-07", "09-01", "10-27"},
    2026: {"03-09", "03-24", "03-25", "05-11", "10-26"},
}
DAY = timedelta(days=1)
WEATHER_DAYS = 365
WEATHER_SOURCE = "Open-Meteo · NOAA GFS"
TEMP = "temperature_2m_previous_day3"
RAIN = "precipitation_previous_day3"


def resolve_city(text: str) -> str | None:
    words = set(re.findall(r"[^\W\d_]+", text.casefold()))
    matches = [
        code
        for code, (name, _, _) in CITIES.items()
        if ({code, name.casefold()} | CITY_ALIASES.get(code, set())) & words
    ]
    return matches[0] if len(matches) == 1 else None


def _calendar(start, end, as_of):
    records = []
    for year, off in DAYS_OFF.items():
        d = max(start, date(year, 1, 1))
        while d <= min(end, date(year, 12, 31)):
            md = d.strftime("%m-%d")
            # Entries are as-of dates, not the time BakePlan happened to download them.
            known = CALENDAR_PUBLICATION[year]
            typ = "day_off" if d.weekday() >= 5 else "workday"
            if (
                md in COMMON_HOLIDAYS
                or md == "01-07"
                or (year == 2025 and md == "08-30")
            ):
                typ = "holiday"
            elif md in off:
                typ = "day_off"
            if d == date(2025, 1, 5):
                typ = "workday"  # Explicit transfer from Jan 5 to Jan 3.
            if d in {date(2025, 6, 6), date(2026, 5, 27)}:
                typ = "holiday"
            # The revised calendar was published June 29, before the July 1 reform.
            if year == 2026 and md in {"08-30", "08-31"} and known <= as_of:
                records.append(
                    {
                        "date": d.isoformat(),
                        "available_on": known.isoformat(),
                        "day_type": "holiday" if md == "08-30" else "day_off",
                    }
                )
                known = AUGUST_REVISION_PUBLICATION
            if known <= as_of:
                records.append(
                    {
                        "date": d.isoformat(),
                        "available_on": known.isoformat(),
                        "day_type": typ,
                    }
                )
            d += DAY
    return records


def _variable(values, low, high):
    if all(
        isinstance(v, (float, int)) and not isinstance(v, bool) and low <= v <= high
        for v in values
    ):
        return values
    return None


class _WeatherRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _fetch_weather(host, params):
    # The customer API accepts its secret in the query. Do not use a client
    # which writes request URLs to a shared HTTP logger.
    url = f"https://{host}.open-meteo.com/v1/forecast?{urlencode(params)}"
    with build_opener(_WeatherRedirects()).open(url, timeout=10) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Weather response exceeds the daily history limit")
    return json.loads(raw)


def _weather(start, end, as_of, city, key):
    _, lat, lon = CITIES[city]
    host = "customer-previous-runs-api" if key else "previous-runs-api"
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Asia/Almaty",
        "models": "gfs_seamless",
        "hourly": f"{TEMP},{RAIN}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if key:
        params["apikey"] = key
    data = _fetch_weather(host, params)
    hourly = data["hourly"]
    if data["hourly_units"][TEMP] != "°C" or data["hourly_units"][RAIN] != "mm":
        raise ValueError("Unexpected weather units")
    groups = defaultdict(dict)
    duplicate_days = set()
    for stamp, temp, rain in zip(
        hourly["time"], hourly[TEMP], hourly[RAIN], strict=True
    ):
        day, hour = stamp.split("T")
        day = date.fromisoformat(day)
        if start <= day <= end:
            if hour in groups[day]:
                duplicate_days.add(day)
            groups[day][hour] = (temp, rain)
    records = []
    hours = {f"{h:02d}:00" for h in range(24)}
    for day, values in sorted(groups.items()):
        # Use a 72h issue offset and allow another day for publication. The last
        # hourly prediction is available by the END of local day T-2.
        if day in duplicate_days or set(values) != hours or day.toordinal() <= 2:
            continue
        available = day - 2 * DAY
        if available > as_of:
            continue
        temps, rain = zip(*values.values())
        temps, rain = _variable(temps, -100, 65), _variable(rain, 0, 2500)
        total = round(sum(rain), 2) if rain is not None and sum(rain) <= 2500 else None
        if temps is not None or total is not None:
            records.append(
                {
                    "date": str(day),
                    "available_on": str(available),
                    "kind": "forecast",
                    "temperature_c": round(sum(temps) / 24, 2)
                    if temps is not None
                    else None,
                    "precipitation_mm": total,
                }
            )
    return records


def load_demand_context(start: date, end: date, as_of: date, city: str | None):
    """Return a context snapshot and operator-facing coverage notes on optional sources."""
    if city == "weekday_only":
        return {"calendar": [], "weather": []}, []
    if city not in CITIES and city not in {None, "none"}:
        raise ValueError("Неизвестный город")
    warnings = []
    calendar = _calendar(start, end, as_of)
    context = {
        "calendar": calendar,
        "weather": [],
        "calendar_source": "Казахстан · пятидневная неделя",
    }
    if start.year < min(DAYS_OFF) or end.year > max(DAYS_OFF):
        warnings.append(
            "Официальный календарь Казахстана проверен за 2025–2026 годы. Для остальных лет доступны дни недели."
        )
    if city not in CITIES:
        return context, warnings
    context["city"] = CITIES[city][0]
    key = os.environ.get("BAKEPLAN_WEATHER_API_KEY", "").strip()
    if not key and os.environ.get("BAKEPLAN_WEATHER_MODE", "").lower() != "evaluation":
        warnings.append(
            "Источник погоды не подключён на сервере. Доступен прогноз по продажам и календарю."
        )
        return context, warnings
    end = min(end, as_of + 2 * DAY)
    weather_start = start
    if (end - start).days >= WEATHER_DAYS:
        weather_start = end - (WEATHER_DAYS - 1) * DAY
        warnings.append(
            "Погодные признаки подключены для последнего года выбранного периода."
        )
    try:
        records = (
            _weather(weather_start, end, as_of, city, key)
            if weather_start <= end
            else []
        )
    except (
        HTTPException,
        URLError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        warnings.append(
            "Архив погоды временно недоступен. Продажи и календарь доступны для расчёта."
        )
        return context, warnings
    if records:
        context.update(weather=records, weather_source=WEATHER_SOURCE)
    if (
        len(records) < (end - weather_start).days + 1
        or not records
        or any(
            row["temperature_c"] is None or row["precipitation_mm"] is None
            for row in records
        )
    ):
        warnings.append(
            "Для части дат нет полного погодного ряда. Прогноз использует доступные признаки."
        )
    return context, warnings
