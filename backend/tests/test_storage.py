import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from backend import storage
from backend.branch_access import branch_scope


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
        assert con.execute("PRAGMA user_version").fetchone()[0] == storage.SQLITE_SCHEMA_VERSION
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


@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_database_startup_preserves_snapshots(db, existing):
    if existing:
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE datasets(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
            con.execute("INSERT INTO datasets VALUES (?,?)", ("legacy", json.dumps(dataset("legacy"))))
    callers = 6
    start = Barrier(callers)

    def initialize(_):
        start.wait()
        storage.initialize_database(db)

    with ThreadPoolExecutor(callers) as pool:
        list(pool.map(initialize, range(callers)))
    with storage.connection(db) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == storage.SQLITE_SCHEMA_VERSION
    backups = list(db.parent.glob("*.bak"))
    if existing:
        assert {p.name for p in backups} == {f"{db.name}.pre-v{n}.bak" for n in (2, 3, 4, 5, 6, 7)}
        for backup in backups:
            with storage.connection(backup) as con:
                assert storage.fetch(con, "datasets", "legacy") == dataset("legacy")
    else:
        assert backups == []


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


def test_scoped_legacy_lists_and_revision_access(db):
    from scripts.sqlite_snapshot import inspect_database

    with storage.connection(db) as con:
        for migrate in (storage._migrate_v2, storage._migrate_v3, storage._migrate_v4,
                        storage._migrate_v5, storage._migrate_v6):
            migrate(con)
        for ident, owner in [("a", "North"), ("b", "north"), ("old", "legacy")]:
            record = dict(dataset(ident), bakery_id=owner, metadata_declared=ident != "old")
            con.execute("INSERT INTO datasets(id,payload) VALUES(?,?)", (ident, json.dumps(record)))
        before_payloads = {}
        for ident, source, owner in [("a1", "a", None), ("b1", "b", "north"), ("a2", "a", "North"),
                                     ("conflict", "b", "North"), ("unassigned", "old", None),
                                     ("mixed-history", "a", None), ("mixed-receipt", "a", None)]:
            plan = {"id": ident, "dataset_id": source, "revision": 1}
            if owner:
                plan["bakery_id"] = owner
            raw = json.dumps(plan, indent=2)
            before_payloads[ident] = raw
            con.execute("INSERT INTO plans(id,payload,dataset_id) VALUES(?,?,?)", (ident, raw, source))
            old = dict(plan, revision=0)
            if ident == "mixed-history":
                old["dataset_id"] = "b"
            con.execute("INSERT INTO revisions VALUES(?,?,?)", (ident, 0, json.dumps(old)))
            if ident == "mixed-receipt":
                receipt = json.dumps(dict(plan, bakery_id="north"))
                con.execute("INSERT INTO confirmations VALUES(?,?,?,?)", (ident, "once", "{}", receipt))
    before = inspect_database(db)
    storage.initialize_database()
    with branch_scope("North"):
        assert [p["id"] for p in storage.list_plans(1)] == ["a2"]
        assert [p["id"] for p in storage.list_plans(1, 1)] == ["a1"]
        assert storage.list_plans(1, 2) == []
        assert [d["id"] for d in storage.list_datasets()] == ["a"]
        assert storage.get_plan("a1", 0)["dataset_id"] == "a"
        for inaccessible in ["b1", "conflict", "unassigned", "mixed-history", "mixed-receipt"]:
            with pytest.raises(storage.NotFound):
                storage.get_plan(inaccessible, 0)
        with pytest.raises(storage.NotFound):
            storage.get_dataset("b")
    with branch_scope("north"):
        assert [p["id"] for p in storage.list_plans()] == ["b1"]
    assert inspect_database(db.with_name(db.name + ".pre-v7.bak")) == before
    with storage.connection(db) as con:
        assert dict(con.execute("SELECT id,payload FROM plans")) == before_payloads


def test_assigning_old_format_uploads_and_hash_retries_respects_branch(db):
    storage.initialize_database()
    for owner in ("A", "B"):
        with branch_scope(owner):
            supplied = dict(dataset(owner), bakery_id=owner, metadata_declared=False)
            with pytest.raises(storage.NotFound):
                storage.save_dataset(supplied)
            supplied["owner_bakery_id"] = owner
            assert storage.save_dataset(supplied) == (supplied, True)
            assert storage.save_dataset(dict(supplied, id="retry")) == (supplied, False)
            assert storage.get_dataset(owner)["metadata_declared"] is False
            storage.save_plan({"id": "plan-" + owner, "dataset_id": owner, "revision": 0})
    with branch_scope("B"):
        with pytest.raises(storage.NotFound):
            storage.save_plan({"id": "unrelated", "dataset_id": "A", "revision": 0})
        with pytest.raises(storage.NotFound), storage.connection() as con:
            storage.update_plan(con, {"id": "plan-A", "dataset_id": "B", "revision": 1})
        with pytest.raises(storage.NotFound), storage.connection() as con:
            storage.update_plan(con, {"id": "plan-B", "dataset_id": "A", "revision": 1})
        assert [d["id"] for d in storage.list_datasets()] == ["B"]
    assert storage.get_plan("plan-A")["revision"] == storage.get_plan("plan-B")["revision"] == 0
