"""Private POS subscriptions and fenced, recoverable daily work in the application database."""

import json
import math
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from backend import pos_vault, storage

LEASE_DURATION = timedelta(minutes=10)
RETRY_DELAY = timedelta(minutes=15)
BUSINESS_TZ = timezone(timedelta(hours=5))
ERROR_CODES = {
    "unauthorized",
    "no_sales",
    "vendor_unavailable",
    "forecast_unavailable",
    "vault_unavailable",
    "branch_changed",
    "sync_failed",
    "freshness_unverified",
}
PUBLIC = """id, provider, location_id, location_name, bakery_id, days, city,
            updated_at, last_success_day, last_attempt_at, retry_at, error, dataset_id, target_date,
            (SELECT f.observed_through FROM pos_fresh_days f WHERE f.connection_id=pos_connections.id
             AND f.business_day=pos_connections.last_success_day) AS last_observed_day,
            CASE WHEN lease_token IS NOT NULL THEN 'syncing'
                 WHEN error IS NOT NULL THEN 'error' ELSE 'ready' END AS status"""
PRIVATE = """id, provider, location_id, bakery_id, days, city, attempt_day AS day, lease_token AS token"""


def _time(now):
    if now.utcoffset() is None:
        raise ValueError("Use an aware instant")
    return now.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _query_one(db, sql, params):
    cursor = db.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        raise storage.NotFound("POS connection")
    return dict(zip((column[0] for column in cursor.description), row, strict=True))


def has_connections():
    with storage.connection() as db:
        return (
            db.execute("SELECT 1 FROM pos_connections LIMIT 1").fetchone() is not None
        )


def list_connections():
    with storage.connection() as db:
        cursor = db.execute(
            f"SELECT {PUBLIC} FROM pos_connections ORDER BY location_name, id"
        )
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor]


def connection_result(ident):
    with storage.connection() as db:
        summary = _query_one(
            db, f"SELECT {PUBLIC}, forecast FROM pos_connections WHERE id=?", (ident,)
        )
    forecast = summary.pop("forecast")
    return summary, json.loads(forecast) if forecast is not None else None


def start_connection(
    provider, location_id, location_name, bakery_id, days, city, api_key, now
):
    """Register the verified POS outlet; keep its prior result until the new import succeeds."""
    with storage.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id FROM pos_connections WHERE provider=? AND bakery_id=? AND location_id=?",
            (provider, bakery_id, location_id),
        ).fetchone()
        ident = existing[0] if existing else str(uuid4())
        sealed = pos_vault.seal(api_key, ident, provider, bakery_id, location_id)
        db.execute(
            """
            INSERT INTO pos_connections(id, provider, bakery_id, location_id, location_name,
                days, city, ciphertext, updated_at, last_attempt_at, attempt_day, lease_token, lease_until)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET location_name=excluded.location_name, days=excluded.days,
                city=excluded.city, ciphertext=excluded.ciphertext, updated_at=excluded.updated_at,
                last_attempt_at=excluded.last_attempt_at, attempt_day=excluded.attempt_day,
                lease_token=excluded.lease_token, lease_until=excluded.lease_until, retry_at=NULL, error=NULL
            """,
            (
                ident,
                provider,
                bakery_id,
                location_id,
                location_name,
                days,
                city,
                sealed,
                _time(now),
                _time(now),
                str(now.astimezone(BUSINESS_TZ).date()),
                str(uuid4()),
                _time(now + LEASE_DURATION),
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO pos_monitoring_epochs(connection_id,started_at) VALUES(?,?)",
            (ident, _time(now)),
        )
        return _query_one(
            db, f"SELECT {PRIVATE} FROM pos_connections WHERE id=?", (ident,)
        )


def claim_next(day, now):
    """Lease one due branch. A crash leaves a bounded lease. Concurrent owners cannot publish."""
    instant = _time(now)
    with storage.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """
            SELECT id, attempt_day FROM pos_connections WHERE
                (((last_success_day IS NULL OR last_success_day < ?) AND attempt_day <= ?)
                OR ((error IS NOT NULL OR lease_token IS NOT NULL) AND attempt_day <= ?))
                AND (retry_at IS NULL OR retry_at <= ?) AND (lease_until IS NULL OR lease_until <= ?)
            ORDER BY last_attempt_at, id LIMIT 1
            """,
            (
                str(day),
                str(day),
                str(now.astimezone(BUSINESS_TZ).date()),
                instant,
                instant,
            ),
        ).fetchone()
        if row is None:
            return None
        ident, attempt_day = row
        run_day = max(str(day), attempt_day)
        db.execute(
            """
            UPDATE pos_connections SET lease_token=?, lease_until=?, last_attempt_at=?, attempt_day=?,
                updated_at=?, retry_at=NULL WHERE id=?
            """,
            (
                str(uuid4()),
                _time(now + LEASE_DURATION),
                instant,
                run_day,
                instant,
                ident,
            ),
        )
        return _query_one(
            db, f"SELECT {PRIVATE} FROM pos_connections WHERE id=?", (ident,)
        )


def claim_key(claim):
    with storage.connection() as db:
        row = _query_one(
            db,
            """SELECT ciphertext, id, provider, bakery_id, location_id FROM pos_connections
            WHERE id=? AND lease_token=?""",
            (claim["id"], claim["token"]),
        )
    return pos_vault.unseal(
        row["ciphertext"],
        row["id"],
        row["provider"],
        row["bakery_id"],
        row["location_id"],
    )


def finish(claim, dataset_id, forecast, day, now):
    """Publish the immutable snapshot and success date in the same fenced update."""
    observed = str(day - timedelta(days=1))
    target = str(day + timedelta(days=1))
    result = forecast["result"]
    quantities = (item.get("forecast_mean") for item in result["items"])
    if (
        claim["day"] != str(day)
        or result["dataset_id"] != dataset_id
        or forecast["observed_through"] != observed
        or forecast["cutoff"] != observed
        or result["cutoff"] != observed
        or forecast["targetDate"] != target
        or result["target_date"] != target
        or not any(isinstance(q, (int, float)) and math.isfinite(q) and q >= 0 for q in quantities)
    ):
        raise ValueError("Forecast must match the fresh import, cutoff and target")
    encoded = json.dumps(
        forecast, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    with storage.connection() as db:
        db.execute("PRAGMA foreign_keys=ON")
        cursor = db.execute(
            """
            UPDATE pos_connections SET dataset_id=?, forecast=?, target_date=?, last_success_day=?,
                updated_at=?, error=NULL, retry_at=NULL, lease_token=NULL, lease_until=NULL
            WHERE id=? AND lease_token=? AND (last_success_day IS NULL OR last_success_day <= ?)
            """,
            (
                dataset_id,
                encoded,
                forecast["targetDate"],
                str(day),
                _time(now),
                claim["id"],
                claim["token"],
                str(day),
            ),
        )
        if cursor.rowcount != 1:
            return False
        db.execute("""
            INSERT OR IGNORE INTO pos_fresh_days(connection_id,business_day,observed_through,fresh_at)
            VALUES(?,?,?,?)
        """, (claim["id"], str(day), observed, _time(now)))
        return True


def fail(claim, error, now):
    """The database may only see fixed public error codes, never transport messages."""
    safe_code = error if error in ERROR_CODES else "sync_failed"
    with storage.connection() as db:
        cursor = db.execute(
            """
            UPDATE pos_connections SET error=?, retry_at=?, updated_at=?, lease_token=NULL, lease_until=NULL
            WHERE id=? AND lease_token=?
            """,
            (
                safe_code,
                _time(now + RETRY_DELAY),
                _time(now),
                claim["id"],
                claim["token"],
            ),
        )
        return cursor.rowcount == 1


def revoke(ident, now=None):
    with storage.connection() as db:
        db.execute("PRAGMA secure_delete=ON")
        db.execute(
            "UPDATE pos_monitoring_epochs SET ended_at=? WHERE connection_id=? AND ended_at IS NULL",
            (_time(now or datetime.now(UTC)), ident),
        )
        db.execute("DELETE FROM pos_connections WHERE id=?", (ident,))


def claim_ping(ident, day, now):
    """Lease a committed, fresh publication. A crash permits a later retry that day."""
    token = str(uuid4())
    with storage.connection() as db:
        cursor = db.execute("""
            UPDATE pos_fresh_days SET ping_token=?, next_ping_at=?
            WHERE connection_id=? AND business_day=? AND pinged_at IS NULL
            AND (next_ping_at IS NULL OR next_ping_at<=?)
            AND EXISTS (SELECT 1 FROM pos_connections c WHERE c.id=connection_id
                AND c.last_success_day=business_day AND c.error IS NULL AND c.lease_token IS NULL)
        """, (token, _time(now + LEASE_DURATION), ident, str(day), _time(now)))
        return token if cursor.rowcount == 1 else None


def finish_ping(ident, day, token, now, success):
    with storage.connection() as db:
        db.execute("""
            UPDATE pos_fresh_days SET ping_token=NULL,pinged_at=?,next_ping_at=?
            WHERE connection_id=? AND business_day=? AND ping_token=?
        """, (_time(now) if success else None, None if success else _time(now + RETRY_DELAY),
              ident, str(day), token))
