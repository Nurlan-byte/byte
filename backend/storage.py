"""Versioned SQLite persistence. Initialize once before serving requests."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.branch_access import current_branch

SCHEMA_VERSION = "history-1"
MAX_SQLITE_INTEGER = 2**63 - 1
SQLITE_SCHEMA_VERSION = 7
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
SQLITE_V4_COLUMNS = {
    **SQLITE_V3_COLUMNS,
    "deliveries": {
        "id", "bakery_id", "number", "product_id", "arrival_date", "initial_arrival_date",
        "ordered_qty", "received_qty", "cancelled",
    },
    "delivery_receipts": {"delivery_id", "idempotency_key", "quantity", "received_at", "response"},
}
SQLITE_V5_COLUMNS = {
    **SQLITE_V4_COLUMNS,
    "pos_connections": {
        "id", "provider", "bakery_id", "location_id", "location_name", "days", "city", "ciphertext",
        "updated_at", "last_attempt_at", "attempt_day", "last_success_day", "error", "retry_at",
        "lease_token", "lease_until", "dataset_id", "forecast", "target_date",
    },
}
SQLITE_V6_COLUMNS = {
    **SQLITE_V5_COLUMNS,
    "pos_monitoring_epochs": {"connection_id", "started_at", "ended_at"},
    "pos_fresh_days": {"connection_id", "business_day", "observed_through", "fresh_at",
                       "pinged_at", "ping_token", "next_ping_at"},
}
SQLITE_SCHEMA_COLUMNS = {
    **SQLITE_V6_COLUMNS,
    "datasets": SQLITE_V6_COLUMNS["datasets"] | {"branch_id"},
    "plans": SQLITE_V6_COLUMNS["plans"] | {"branch_id"},
    "branch_accounts": {"username", "bakery_id", "password_hash"},
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
        migrations = ((2, _migrate_v2), (3, _migrate_v3), (4, _migrate_v4), (5, _migrate_v5),
                      (6, _migrate_v6), (7, _migrate_v7))
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


def _migrate_v5(con):
    con.execute("""
        CREATE TABLE pos_connections(
            id TEXT PRIMARY KEY NOT NULL, provider TEXT NOT NULL, bakery_id TEXT NOT NULL,
            location_id TEXT NOT NULL, location_name TEXT NOT NULL,
            days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 120), city TEXT,
            ciphertext BLOB NOT NULL, updated_at TEXT NOT NULL, last_attempt_at TEXT NOT NULL,
            attempt_day TEXT NOT NULL, last_success_day TEXT, error TEXT, retry_at TEXT,
            lease_token TEXT, lease_until TEXT, dataset_id TEXT REFERENCES datasets(id),
            forecast TEXT, target_date TEXT,
            UNIQUE(provider, bakery_id, location_id),
            CHECK ((lease_token IS NULL) = (lease_until IS NULL)),
            CHECK ((dataset_id IS NULL) = (forecast IS NULL)),
            CHECK ((dataset_id IS NULL) = (last_success_day IS NULL)),
            CHECK ((dataset_id IS NULL) = (target_date IS NULL))
        )
    """)
    con.execute("PRAGMA user_version=5")


def _migrate_v6(con):
    con.execute("""
        CREATE TABLE pos_monitoring_epochs (
            connection_id TEXT PRIMARY KEY NOT NULL, started_at TEXT NOT NULL, ended_at TEXT
        )
    """)
    con.execute("""
        CREATE TABLE pos_fresh_days (
            connection_id TEXT NOT NULL REFERENCES pos_monitoring_epochs(connection_id),
            business_day TEXT NOT NULL, observed_through TEXT NOT NULL, fresh_at TEXT NOT NULL,
            pinged_at TEXT, ping_token TEXT, next_ping_at TEXT,
            PRIMARY KEY(connection_id, business_day)
        )
    """)
    # Legacy snapshots have no evidence of timely, fresh publications.
    con.execute("INSERT INTO pos_monitoring_epochs SELECT id, ?, NULL FROM pos_connections", (timestamp(),))
    con.execute("""
        UPDATE pos_connections SET error='freshness_unverified'
        WHERE last_success_day IS NOT NULL AND error IS NULL AND lease_token IS NULL
    """)
    con.execute("PRAGMA user_version=6")


def _dataset_owner(dataset):
    label = dataset.get("bakery_id")
    assigned = dataset.get("owner_bakery_id")
    if not isinstance(label, str) or not label.strip():
        return None
    if assigned is not None:
        return label if assigned == label else None
    if dataset.get("metadata_declared") is False or (
        label == "legacy" and dataset.get("metadata_declared") is not True
    ):
        return None
    return label


def _plan_owner(con, plan):
    row = con.execute("SELECT branch_id FROM datasets WHERE id=?", (plan.get("dataset_id"),)).fetchone()
    if row and ("bakery_id" not in plan or plan["bakery_id"] == row[0]):
        return row[0]
    return None


def _migrate_v7(con):
    con.execute("""CREATE TABLE branch_accounts(
        username TEXT PRIMARY KEY NOT NULL, bakery_id TEXT NOT NULL, password_hash TEXT
    )""")
    con.execute("ALTER TABLE datasets ADD COLUMN branch_id TEXT")
    con.execute("ALTER TABLE plans ADD COLUMN branch_id TEXT")
    for ident, raw in con.execute("SELECT id,payload FROM datasets WHERE json_valid(payload)"):
        dataset = json.loads(raw)
        if isinstance(dataset, dict) and dataset.get("id") == ident:
            con.execute("UPDATE datasets SET branch_id=? WHERE id=?", (_dataset_owner(dataset), ident))
    for ident, dataset_id, raw in con.execute("SELECT id,dataset_id,payload FROM plans WHERE json_valid(payload)"):
        plan = json.loads(raw)
        if isinstance(plan, dict) and plan.get("id") == ident and plan.get("dataset_id") == dataset_id:
            con.execute("UPDATE plans SET branch_id=? WHERE id=?", (_plan_owner(con, plan), ident))
    # A contradictory historical revision or receipt makes the entire order ambiguous.
    for table, column in (("revisions", "payload"), ("confirmations", "response")):
        con.execute(f"""
            UPDATE plans SET branch_id=NULL WHERE branch_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM {table} h WHERE h.plan_id=plans.id AND CASE WHEN json_valid(h.{column}) THEN
                    json_extract(h.{column},'$.id') IS NOT plans.id
                    OR (json_type(h.{column},'$.bakery_id') IS NOT NULL
                        AND json_extract(h.{column},'$.bakery_id') IS NOT plans.branch_id)
                    OR (json_type(h.{column},'$.dataset_id') IS NOT NULL
                        AND json_extract(h.{column},'$.dataset_id') IS NOT plans.dataset_id)
                ELSE 1 END
            )
        """)
    con.execute("CREATE INDEX datasets_branch ON datasets(branch_id)")
    con.execute("CREATE INDEX plans_branch ON plans(branch_id)")
    # Old imports could share a canonical ID assigned before branches existed.
    con.execute("""
        UPDATE datasets SET canonical_dataset_id=id WHERE branch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM datasets canonical
            WHERE canonical.id=datasets.canonical_dataset_id AND canonical.branch_id=datasets.branch_id
        )
    """)
    con.execute("PRAGMA user_version=7")


def fetch(con, table, ident):
    if table not in {"datasets", "plans"}:
        raise ValueError("Unsupported object table")
    branch = current_branch()
    if branch is None:
        row = con.execute(f"SELECT payload FROM {table} WHERE id=?", (ident,)).fetchone()
    else:
        row = con.execute(f"SELECT payload FROM {table} WHERE id=? AND branch_id=?", (ident, branch)).fetchone()
    if row is None:
        raise NotFound(ident)
    return json.loads(row[0])


def get_dataset(ident):
    with connection() as con:
        return fetch(con, "datasets", ident)


def get_plan(ident, revision=None):
    with connection() as con:
        current = fetch(con, "plans", ident)
        if revision is None:
            return current
        row = con.execute(
            "SELECT payload FROM revisions WHERE plan_id=? AND revision=?",
            (ident, revision),
        ).fetchone()
        if row is None:
            # Older databases can have a current plan without a history row.
            if current.get("revision", 0) == revision:
                return current
            raise NotFound((ident, revision))
        return json.loads(row[0])


def save_dataset(dataset):
    schema, source, content_hash, created_at, count = dataset_metadata(dataset)
    owner = _dataset_owner(dataset)
    branch = current_branch()
    if branch is not None and branch != owner:
        raise NotFound(dataset["id"])
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            """SELECT k.dataset_id, d.branch_id FROM dataset_import_keys k
               JOIN datasets d ON d.id=k.dataset_id
               WHERE k.source=? AND k.schema_version=? AND k.content_hash=?""",
            (source, schema, content_hash),
        ).fetchone()
        if existing:
            if existing[1] == owner:
                return fetch(con, "datasets", existing[0]), False
            # Current imports hash the branch into their content. Older hashes can collide.
            match = con.execute("""
                SELECT id FROM datasets WHERE branch_id IS ? AND source=?
                AND schema_version=? AND content_hash=? ORDER BY rowid LIMIT 1
            """, (owner, source, schema, content_hash)).fetchone()
            if match:
                return fetch(con, "datasets", match[0]), False
        ident = dataset["id"]
        con.execute(
            "INSERT INTO datasets(id,payload,schema_version,source,content_hash,created_at,row_count,canonical_dataset_id,branch_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ident,
                json.dumps(dataset),
                schema,
                source,
                content_hash,
                created_at,
                count,
                ident,
                owner,
            ),
        )
        con.execute(
            "INSERT OR IGNORE INTO dataset_import_keys VALUES (?,?,?,?)",
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
    owner = _plan_owner(con, plan)
    branch = current_branch()
    if branch is not None and branch != owner:
        raise NotFound(plan["dataset_id"])
    con.execute("INSERT INTO plans(id,payload,branch_id) VALUES (?,?,?)", (plan["id"], payload, owner))
    _update_plan_metadata(con, plan, plan["id"])
    con.execute(
        "INSERT INTO revisions VALUES (?,?,?)",
        (plan["id"], plan["revision"], payload),
    )


def update_plan(con, plan):
    branch = current_branch()
    owner = _plan_owner(con, plan)
    params = (json.dumps(plan), plan["id"])
    if branch is None:
        changed = con.execute(
            "UPDATE plans SET payload=? WHERE id=? AND (branch_id IS NULL OR (branch_id=? AND dataset_id=?))",
            (*params, owner, plan.get("dataset_id")),
        )
    else:
        if owner != branch:
            raise NotFound(plan["id"])
        changed = con.execute(
            "UPDATE plans SET payload=? WHERE id=? AND branch_id=? AND dataset_id=?",
            (*params, branch, plan["dataset_id"]),
        )
    if changed.rowcount != 1:
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
    branch = current_branch()
    condition = "" if branch is None else "WHERE branch_id=?"
    scope = () if branch is None else (branch,)
    with connection() as con:
        rows = con.execute(
            f"SELECT {','.join(selections)} FROM {table} {condition} ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (*scope, limit, offset),
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
        {
            "planning_kind": "CASE WHEN json_valid(payload) THEN COALESCE(json_extract(payload,'$.planning_kind'),'production') ELSE 'production' END",
            "pos_provider": "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.pos_provider') END",
            "pos_location_name": "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.pos_location.name') END",
        },
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
