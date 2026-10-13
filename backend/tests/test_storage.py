import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(path))
    return path


def dataset(ident="d1"):
    return {
        "id": ident,
        "source": "synthetic",
        "version": "samehash",
        "rows": [{"sold": 12}],
        "row_count": 1,
    }


def test_migrate_preserves_all_payloads_and_backup(db):
    original = {}
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE datasets(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        con.execute("CREATE TABLE plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        con.execute(
            "CREATE TABLE revisions(plan_id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(plan_id,revision))"
        )
        con.execute(
            "CREATE TABLE confirmations(plan_id TEXT,key TEXT,body TEXT,response TEXT,PRIMARY KEY(plan_id,key))"
        )
        for ident in ("d1", "d2"):
            con.execute(
                "INSERT INTO datasets VALUES (?,?)",
                (ident, json.dumps(dataset(ident), indent=2)),
            )
        payload = '{"id":"p1", "dataset_id":"d2", "revision":1,"status":"confirmed","source":"synthetic","target_date":"2026-10-12"}'
        con.execute("INSERT INTO plans VALUES (?,?)", ("p1", payload))
        con.execute("INSERT INTO revisions VALUES (?,?,?)", ("p1", 1, payload))
        con.execute(
            "INSERT INTO confirmations VALUES (?,?,?,?)",
            ("p1", "k", "original request", payload),
        )
        for table in ("datasets", "plans", "revisions", "confirmations"):
            original[table] = con.execute(f"SELECT * FROM {table}").fetchall()
    storage.initialize_database()
    with storage.connection() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        for table, rows in original.items():
            columns = "id,payload" if table in {"datasets", "plans"} else "*"
            assert con.execute(f"SELECT {columns} FROM {table}").fetchall() == rows
        assert (
            con.execute(
                "SELECT canonical_dataset_id FROM datasets WHERE id='d2'"
            ).fetchone()[0]
            == "d1"
        )
    with sqlite3.connect(str(db) + ".pre-v2.bak") as backup:
        for table, rows in original.items():
            assert backup.execute(f"SELECT * FROM {table}").fetchall() == rows
    storage.initialize_database()
    result, isnew = storage.save_dataset(dataset("d3"))
    assert not isnew and result["id"] == "d1"
    assert storage.get_plan("p1")["dataset_id"] == "d2"


def test_concurrent_import_retry_and_metadata_only(db):
    storage.initialize_database()
    with ThreadPoolExecutor(8) as pool:
        results = list(
            pool.map(lambda i: storage.save_dataset(dataset(f"d{i}")), range(8))
        )
    assert sum(isnew for _, isnew in results) == 1
    assert len({data["id"] for data, _ in results}) == 1
    summary = storage.list_datasets()[0]
    assert "rows" not in summary and "payload" not in summary
    assert summary["row_count"] == 1
    retry, isnew = storage.save_dataset(dataset("retry"))
    assert not isnew and retry["id"] == summary["id"]
    with storage.connection() as con:
        assert con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1
        assert (
            con.execute("SELECT COUNT(*) FROM dataset_import_keys").fetchone()[0] == 1
        )
        con.execute(
            "UPDATE datasets SET payload='malformed' WHERE id=?", (summary["id"],)
        )
    assert storage.list_datasets()[0] == summary


def test_plan_metadata_updates_share_atomic_transaction(db):
    storage.initialize_database()
    p = {
        "id": "p1",
        "dataset_id": "d1",
        "revision": 0,
        "status": "draft",
        "source": "local",
        "target_date": "2026-10-14",
    }
    storage.save_plan(p)
    p.update(revision=1, status="confirmed")
    with pytest.raises(RuntimeError):
        with storage.connection() as con:
            storage.update_plan(con, p)
            raise RuntimeError("rollback")
    assert storage.get_plan("p1")["revision"] == 0
    assert storage.list_plans()[0]["revision"] == 0
    with storage.connection() as con:
        storage.update_plan(con, p)
    assert storage.list_plans()[0]["revision"] == 1
    assert "items" not in storage.list_plans()[0]
    with pytest.raises(storage.NotFound):
        storage.get_plan("missing")
    with pytest.raises(ValueError):
        storage.list_datasets(101)
    with pytest.raises(ValueError):
        storage.list_plans(offset=-1)


def test_import_identity_includes_source_and_schema(db):
    storage.initialize_database()
    original, created = storage.save_dataset(dataset("original"))
    assert created
    other_source, created = storage.save_dataset(
        {**dataset("external"), "source": "external"}
    )
    assert created
    other_schema, created = storage.save_dataset(
        {**dataset("schema2"), "schema_version": "history-2"}
    )
    assert created
    assert len({original["id"], other_source["id"], other_schema["id"]}) == 3
    retry, created = storage.save_dataset(
        {**dataset("retry"), "schema_version": "history-2"}
    )
    assert not created and retry["id"] == other_schema["id"]
