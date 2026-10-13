"""Versioned SQLite persistence. Initialize once before serving requests."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "history-1"


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


def initialize_database(path=None):
    """Backup existing v0/v1 databases before an atomic, non-destructive v2 migration."""
    path = database_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise RuntimeError("Database schema is newer than this application")
        if version == 2:
            return
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if tables:
            backup_path = path.with_name(path.name + ".pre-v2.bak")
            if backup_path.exists():
                raise RuntimeError(
                    f"Migration backup already exists: {backup_path}; inspect it before retrying"
                )
            with sqlite3.connect(backup_path) as backup:
                con.backup(backup)
        con.execute("BEGIN IMMEDIATE")
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


def get_plan(ident):
    with connection() as con:
        return fetch(con, "plans", ident)


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


def _summaries(table, columns, limit, offset):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    with connection() as con:
        rows = con.execute(
            f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(zip(columns, row, strict=True)) for row in rows]


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
    )
