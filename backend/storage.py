"""Versioned SQLite persistence. Initialize once before serving requests."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "history-1"
MAX_SQLITE_INTEGER = 2**63 - 1
SQLITE_SCHEMA_VERSION = 4
SQLITE_V3_COLUMNS = {
    "datasets": {
        "id", "payload", "schema_version", "source", "content_hash", "created_at",
        "row_count", "canonical_dataset_id",
    },
    "plans": {
        "id", "payload", "schema_version", "source", "created_at", "target_date",
        "revision", "status", "dataset_id",
    },
    "models": {
        "id", "dataset_id", "training_cutoff", "protocol_version", "payload", "artifact_hash",
    },
    "training_requests": {"dataset_id", "key", "body", "model_id"},
    "revisions": {"plan_id", "revision", "payload"},
    "confirmations": {"plan_id", "key", "body", "response"},
    "dataset_import_keys": {"source", "schema_version", "content_hash", "dataset_id"},
}
SQLITE_SCHEMA_COLUMNS = {
    **SQLITE_V3_COLUMNS,
    "deliveries": {
        "id", "bakery_id", "number", "product_id", "arrival_date", "initial_arrival_date",
        "ordered_qty", "received_qty", "cancelled",
    },
    "delivery_receipts": {"delivery_id", "idempotency_key", "quantity", "received_at", "response"},
}


class NotFound(Exception):
    """The requested persisted object does not exist."""


def database_path(path=None):
    return Path(path or os.getenv("BAKEPLAN_DB", "data/bakeplan.sqlite3"))


@contextmanager
def connection(path=None):
    con = sqlite3.connect(database_path(path), timeout=30)
    try:
        with con:
            yield con
    finally:
        con.close()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def dataset_metadata(dataset):
    return (
        str(dataset.get("schema_version", SCHEMA_VERSION)),
        dataset.get("source", "unknown"),
        dataset.get("content_hash")
        or dataset.get("version")
        or hashlib.sha256(
            json.dumps(dataset.get("rows", []), sort_keys=True).encode()
        ).hexdigest(),
        dataset.get("created_at") or timestamp(),
        dataset.get("row_count", len(dataset.get("rows", []))),
    )


def _backup_database(path, version):
    backup_path = path.with_name(path.name + f".pre-v{version}.bak")
    if backup_path.exists():
        raise RuntimeError(
            f"Migration backup already exists: {backup_path}; inspect it before retrying"
        )
    # The migration writer holds a reservation on the live database. A separate
    # reader snapshots its last committed state while other writers must wait.
    with connection(path) as source, connection(backup_path) as backup:
        source.backup(backup)


def initialize_database(path=None):
    """Apply additive migrations, preserving historical payload bytes and IDs.

    Existing databases receive a SQLite snapshot before each schema migration.
    Fresh databases need no backup. A failed migration is rolled back, and an
    existing backup is never silently overwritten on retry.
    """
    path = database_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as con:
        if con.execute("PRAGMA user_version").fetchone()[0] == SQLITE_SCHEMA_VERSION:
            return
        migrations = ((2, _migrate_v2), (3, _migrate_v3), (4, _migrate_v4))
        while True:
            con.execute("BEGIN IMMEDIATE")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > SQLITE_SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this application")
            if version == SQLITE_SCHEMA_VERSION:
                return
            has_tables = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone() is not None
            if version == 0 and not has_tables:
                for _, migrate in migrations:
                    migrate(con)
                return
            target, migrate = next(step for step in migrations if step[0] > version)
            _backup_database(path, target)
            migrate(con)
            con.commit()


def _migrate_v2(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS confirmations(plan_id TEXT,key TEXT,body TEXT,response TEXT,PRIMARY KEY(plan_id,key))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS revisions(plan_id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(plan_id,revision))"
    )
    columns = {
        "datasets": {
            "schema_version": "TEXT",
            "source": "TEXT",
            "content_hash": "TEXT",
            "created_at": "TEXT",
            "row_count": "INTEGER",
            "canonical_dataset_id": "TEXT",
        },
        "plans": {
            "schema_version": "TEXT",
            "source": "TEXT",
            "created_at": "TEXT",
            "target_date": "TEXT",
            "revision": "INTEGER",
            "status": "TEXT",
            "dataset_id": "TEXT",
        },
    }
    for table, additions in columns.items():
        existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, kind in additions.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    con.execute(
        "CREATE TABLE dataset_import_keys(source TEXT NOT NULL,schema_version TEXT NOT NULL,content_hash TEXT NOT NULL,dataset_id TEXT NOT NULL,PRIMARY KEY(source,schema_version,content_hash))"
    )
    for ident, payload in con.execute(
        "SELECT id,payload FROM datasets ORDER BY rowid"
    ).fetchall():
        meta = dataset_metadata(json.loads(payload))
        schema, source, content_hash, _, _ = meta
        con.execute(
            "INSERT OR IGNORE INTO dataset_import_keys VALUES (?,?,?,?)",
            (source, schema, content_hash, ident),
        )
        canonical = con.execute(
            "SELECT dataset_id FROM dataset_import_keys WHERE source=? AND schema_version=? AND content_hash=?",
            (source, schema, content_hash),
        ).fetchone()[0]
        con.execute(
            "UPDATE datasets SET schema_version=?,source=?,content_hash=?,created_at=?,row_count=?,canonical_dataset_id=? WHERE id=?",
            (*meta, canonical, ident),
        )
    for ident, payload in con.execute("SELECT id,payload FROM plans").fetchall():
        _update_plan_metadata(con, json.loads(payload), ident)
    con.execute("CREATE INDEX dataset_created ON datasets(created_at)")
    con.execute("CREATE INDEX plan_created ON plans(created_at)")
    con.execute("PRAGMA user_version=2")


def _migrate_v3(con):
    con.execute(
        "CREATE TABLE models("
        "id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,"
        "training_cutoff TEXT NOT NULL,protocol_version TEXT NOT NULL,"
        "payload TEXT NOT NULL,artifact_hash TEXT NOT NULL,"
        "UNIQUE(dataset_id,training_cutoff,protocol_version))"
    )
    con.execute(
        "CREATE TABLE training_requests("
        "dataset_id TEXT NOT NULL,key TEXT NOT NULL,body TEXT NOT NULL,"
        "model_id TEXT NOT NULL,PRIMARY KEY(dataset_id,key),"
        "FOREIGN KEY(model_id) REFERENCES models(id))"
    )
    con.execute("CREATE INDEX model_dataset_cutoff ON models(dataset_id,training_cutoff)")
    con.execute("PRAGMA user_version=3")


def _migrate_v4(con):
    con.execute("""
        CREATE TABLE deliveries(
            id TEXT PRIMARY KEY NOT NULL,
            bakery_id TEXT NOT NULL,
            number TEXT NOT NULL,
            product_id TEXT NOT NULL,
            arrival_date TEXT NOT NULL,
            initial_arrival_date TEXT NOT NULL,
            ordered_qty INTEGER NOT NULL CHECK(ordered_qty BETWEEN 1 AND 1000000000),
            received_qty INTEGER NOT NULL DEFAULT 0 CHECK(received_qty BETWEEN 0 AND ordered_qty),
            cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0, 1)),
            UNIQUE(bakery_id, number, product_id)
        )
    """)
    con.execute("CREATE INDEX deliveries_arrival ON deliveries(bakery_id, arrival_date)")
    con.execute("""
        CREATE TABLE delivery_receipts(
            delivery_id TEXT NOT NULL REFERENCES deliveries(id),
            idempotency_key TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),
            received_at TEXT NOT NULL,
            response TEXT NOT NULL,
            PRIMARY KEY(delivery_id, idempotency_key)
        )
    """)
    con.execute("PRAGMA user_version=4")


def fetch(con, table, ident):
    if table not in {"datasets", "plans"}:
        raise ValueError("Unsupported object table")
    row = con.execute(f"SELECT payload FROM {table} WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise NotFound(ident)
    return json.loads(row[0])


def get_dataset(ident):
    with connection() as con:
        return fetch(con, "datasets", ident)


def get_plan(ident, revision=None):
    with connection() as con:
        if revision is None:
            return fetch(con, "plans", ident)
        row = con.execute(
            "SELECT payload FROM revisions WHERE plan_id=? AND revision=?",
            (ident, revision),
        ).fetchone()
        if row is None:
            # Older databases can have a current plan without a history row.
            current = fetch(con, "plans", ident)
            if current.get("revision", 0) == revision:
                return current
            raise NotFound((ident, revision))
        return json.loads(row[0])


def save_dataset(dataset):
    schema, source, content_hash, created_at, count = dataset_metadata(dataset)
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT dataset_id FROM dataset_import_keys WHERE source=? AND schema_version=? AND content_hash=?",
            (source, schema, content_hash),
        ).fetchone()
        if existing:
            return fetch(con, "datasets", existing[0]), False
        ident = dataset["id"]
        con.execute(
            "INSERT INTO datasets(id,payload,schema_version,source,content_hash,created_at,row_count,canonical_dataset_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                ident,
                json.dumps(dataset),
                schema,
                source,
                content_hash,
                created_at,
                count,
                ident,
            ),
        )
        con.execute(
            "INSERT INTO dataset_import_keys VALUES (?,?,?,?)",
            (source, schema, content_hash, ident),
        )
        return dataset, True


def _update_plan_metadata(con, plan, ident):
    con.execute(
        "UPDATE plans SET schema_version=?,source=?,created_at=COALESCE(created_at,?),target_date=?,revision=?,status=?,dataset_id=? WHERE id=?",
        (
            str(plan.get("schema_version", "plan-1")),
            plan.get("source", "unknown"),
            plan.get("created_at") or timestamp(),
            plan.get("target_date"),
            plan.get("revision", 0),
            plan.get("status", "draft"),
            plan.get("dataset_id"),
            ident,
        ),
    )


def save_plan(plan):
    with connection() as con:
        insert_plan(con, plan)


def insert_plan(con, plan):
    payload = json.dumps(plan)
    con.execute("INSERT INTO plans(id,payload) VALUES (?,?)", (plan["id"], payload))
    _update_plan_metadata(con, plan, plan["id"])
    con.execute(
        "INSERT INTO revisions VALUES (?,?,?)",
        (plan["id"], plan["revision"], payload),
    )


def update_plan(con, plan):
    if (
        con.execute(
            "UPDATE plans SET payload=? WHERE id=?", (json.dumps(plan), plan["id"])
        ).rowcount
        != 1
    ):
        raise NotFound(plan["id"])
    _update_plan_metadata(con, plan, plan["id"])


def _summaries(table, columns, limit, offset, extras=None):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= MAX_SQLITE_INTEGER
    ):
        raise ValueError("offset must be a nonnegative SQLite integer")
    extras = extras or {}
    selections = [*columns, *(f"{expression} AS {name}" for name, expression in extras.items())]
    keys = [*columns, *extras]
    with connection() as con:
        rows = con.execute(
            f"SELECT {','.join(selections)} FROM {table} ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]


def list_datasets(limit=100, offset=0):
    return _summaries(
        "datasets",
        [
            "id",
            "schema_version",
            "source",
            "content_hash",
            "created_at",
            "row_count",
            "canonical_dataset_id",
        ],
        limit,
        offset,
        {"planning_kind": "CASE WHEN json_valid(payload) THEN COALESCE(json_extract(payload,'$.planning_kind'),'production') ELSE 'production' END"},
    )


def list_plans(limit=100, offset=0):
    return _summaries(
        "plans",
        [
            "id",
            "schema_version",
            "source",
            "created_at",
            "target_date",
            "revision",
            "status",
            "dataset_id",
        ],
        limit,
        offset,
        {
            "planning_kind": "CASE WHEN json_valid(payload) THEN COALESCE(json_extract(payload,'$.planning_kind'),'production') ELSE 'production' END",
            "order_date": "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.order_date') END",
            "lead_time_days": "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.lead_time_days') END",
        },
    )
