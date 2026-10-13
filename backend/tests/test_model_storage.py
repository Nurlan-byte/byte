import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from backend import model_storage, storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "models.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(path))
    return path


@pytest.fixture
def initialized(db):
    storage.initialize_database()
    return db


def model(ident="m1", dataset_id="d1", cutoff="2026-10-01", protocol="ridge-1"):
    return {
        "id": ident,
        "dataset_id": dataset_id,
        "dataset_version": "immutable-data-hash",
        "bakery_id": "bakery-one",
        "target": "sold",
        "unit": "pieces",
        "training_cutoff": cutoff,
        "protocol_version": protocol,
        "status": "ready",
        "products": {
            "p1": {"coefficients": [1.2, -0.5], "intercept": 12.0, "n_rows": 42}
        },
        "evaluation": {"mae": 1.5, "label": "Тест", "passed": True},
    }


def test_canonical_artifact_and_read_after_process_restart(initialized):
    original = model()
    saved, created = model_storage.save_training(
        original, "key", '{"cutoff":"2026-10-01"}'
    )
    assert created and saved == original
    assert model_storage.get_model(original["id"]) == original
    with storage.connection() as con:
        payload, digest = con.execute(
            "SELECT payload,artifact_hash FROM models"
        ).fetchone()
    assert payload == json.dumps(
        original,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert digest == hashlib.sha256(payload.encode()).hexdigest()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from backend import storage, model_storage; "
            "storage.initialize_database(); print(json.dumps(model_storage.get_model('m1')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == original
    saved["products"]["p1"]["intercept"] = 999
    original["evaluation"]["mae"] = 999
    assert model_storage.get_model("m1") == model()
    assert not list(initialized.parent.glob("*.bak"))


def test_same_key_retry_preserves_original_and_conflicts_on_changed_body(initialized):
    original, created = model_storage.save_training(model(), "key", "body")
    assert created
    assert model_storage.previous_training("d1", "absent", "body") is None
    assert model_storage.previous_training("d1", "key", "body") == original
    changed = model("different-id")
    changed["products"]["p1"]["intercept"] = 400
    assert model_storage.save_training(changed, "key", "body") == (original, False)
    with pytest.raises(model_storage.TrainingConflict):
        model_storage.previous_training("d1", "key", "different-body")
    with pytest.raises(model_storage.TrainingConflict):
        model_storage.save_training(changed, "key", "different-body")
    assert model_storage.list_models("d1") == [original]


def test_existing_scope_with_new_key_returns_immutable_artifact(initialized):
    original, _ = model_storage.save_training(model(), "first", "first-body")
    changed = model("another-model")
    changed["evaluation"]["mae"] = 999
    assert model_storage.save_training(changed, "second", "second-body") == (
        original,
        False,
    )
    assert model_storage.previous_training("d1", "second", "second-body") == original
    with storage.connection() as con:
        assert con.execute("SELECT count(*) FROM models").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM training_requests").fetchone()[0] == 2


def test_lookup_filters_dataset_cutoff_and_protocol(initialized):
    items = [
        model("latest", cutoff="2026-10-03"),
        model("early", cutoff="2026-10-01"),
        model("other-protocol", cutoff="2026-10-02", protocol="ridge-2"),
        model("other-dataset", dataset_id="d2", cutoff="2026-10-04"),
    ]
    for item in items:
        model_storage.save_training(item, "key-" + item["id"], "body")
    assert [m["id"] for m in model_storage.list_models("d1")] == [
        "latest",
        "other-protocol",
        "early",
    ]
    assert model_storage.find_model("d1", "2026-10-02")["id"] == "other-protocol"
    assert model_storage.find_model("d1", date(2026, 10, 2), "ridge-1")["id"] == "early"
    assert model_storage.find_model("d1", "2026-10-03", "ridge-1")["id"] == "latest"
    assert model_storage.find_model("d1", "2026-09-30") is None
    assert model_storage.find_model("missing", "2026-10-31") is None
    assert model_storage.find_model("d1", "2026-10-31", "missing") is None
    assert model_storage.list_models("missing") == []
    with pytest.raises(storage.NotFound):
        model_storage.get_model("missing")
    with pytest.raises(ValueError):
        model_storage.find_model("d1", "20261003")


def test_training_request_keys_are_dataset_scoped(initialized):
    first, _ = model_storage.save_training(model(), "shared-key", "body-one")
    second, created = model_storage.save_training(
        model("m2", "d2"), "shared-key", "body-two"
    )
    assert created
    assert model_storage.previous_training("d1", "shared-key", "body-one") == first
    assert model_storage.previous_training("d2", "shared-key", "body-two") == second


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_training_saves_one_artifact(initialized, same_key):
    def save(index):
        return model_storage.save_training(
            model(f"model-{index}"), "key" if same_key else f"key-{index}", "body"
        )

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(save, range(8)))
    assert sum(created for _, created in results) == 1
    assert len({saved["id"] for saved, _ in results}) == 1
    with storage.connection() as con:
        assert con.execute("SELECT count(*) FROM models").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM training_requests").fetchone()[0] == (
            1 if same_key else 8
        )


def test_concurrent_same_key_different_body_has_one_winner(initialized):
    def save(index):
        try:
            model_storage.save_training(model(f"m{index}"), "key", f"body-{index}")
            return "created"
        except model_storage.TrainingConflict:
            return "conflict"

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(save, range(8)))
    assert results.count("created") == 1
    assert results.count("conflict") == 7


def test_failed_request_insert_rolls_back_new_artifact(initialized):
    with storage.connection() as con:
        con.execute(
            "CREATE TRIGGER fail_request BEFORE INSERT ON training_requests "
            "BEGIN SELECT RAISE(ABORT,'injected request failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected request failure"):
        model_storage.save_training(model(), "key", "body")
    assert model_storage.list_models("d1") == []
    assert model_storage.previous_training("d1", "key", "body") is None
    with storage.connection() as con:
        con.execute("DROP TRIGGER fail_request")
    assert model_storage.save_training(model(), "key", "body")[1]


def test_id_collision_never_overwrites_or_records_retry(initialized):
    original, _ = model_storage.save_training(model(), "key-one", "body")
    with pytest.raises(model_storage.TrainingConflict):
        model_storage.save_training(model(dataset_id="d2"), "key-two", "body")
    assert model_storage.get_model("m1") == original
    assert model_storage.previous_training("d2", "key-two", "body") is None


@pytest.mark.parametrize(
    "bad_value", [float("nan"), float("inf"), float("-inf"), object(), (1, 2), {1: 2}]
)
def test_invalid_json_values_are_rejected_without_writes(initialized, bad_value):
    candidate = model()
    candidate["products"]["p1"]["coefficients"] = [bad_value]
    with pytest.raises(model_storage.InvalidArtifact):
        model_storage.save_training(candidate, "key", "body")
    assert model_storage.list_models("d1") == []
    assert model_storage.previous_training("d1", "key", "body") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("dataset_id", ""),
        ("dataset_version", 42),
        ("training_cutoff", "not-a-date"),
        ("training_cutoff", "20261001"),
        ("products", None),
        ("products", []),
        ("products", {"p1": 42}),
        ("evaluation", []),
    ],
)
def test_missing_or_invalid_artifact_fields_rejected(initialized, field, value):
    candidate = model()
    candidate[field] = value
    with pytest.raises(model_storage.InvalidArtifact):
        model_storage.save_training(candidate, "key", "body")


def test_hash_corruption_detected_in_every_read_path(initialized):
    model_storage.save_training(model(), "key", "body")
    model_storage.save_training(model("m2", "d2"), "other-key", "body")
    with storage.connection() as con:
        con.execute("UPDATE models SET payload=payload || ' ' WHERE id='m1'")
    for read in (
        lambda: model_storage.get_model("m1"),
        lambda: model_storage.list_models("d1"),
        lambda: model_storage.find_model("d1", "2026-10-31"),
        lambda: model_storage.previous_training("d1", "key", "body"),
        lambda: model_storage.save_training(model(), "key", "body"),
        lambda: model_storage.save_training(model("m3"), "new-key", "body"),
    ):
        with pytest.raises(model_storage.InvalidArtifact):
            read()
    assert model_storage.get_model("m2")["dataset_id"] == "d2"
    assert model_storage.previous_training("d1", "new-key", "body") is None


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"id":"m1","id":"m1"}',
        json.dumps(model()).replace("1.5", "NaN"),
        json.dumps(model()).replace("1.5", "Infinity"),
        json.dumps(model()).replace("1.5", "1e309"),
        json.dumps({**model(), "id": "other"}),
        json.dumps({**model(), "dataset_id": "other"}),
        json.dumps({**model(), "training_cutoff": "2026-10-02"}),
        json.dumps({**model(), "protocol_version": "other"}),
    ],
)
def test_malformed_payload_rejected_even_with_matching_hash(initialized, payload):
    model_storage.save_training(model(), "key", "body")
    with storage.connection() as con:
        con.execute(
            "UPDATE models SET payload=?,artifact_hash=? WHERE id='m1'",
            (
                payload,
                hashlib.sha256(payload.encode()).hexdigest(),
            ),
        )
    with pytest.raises(model_storage.InvalidArtifact):
        model_storage.get_model("m1")


@pytest.mark.parametrize("missing", [True, False])
def test_corrupt_training_reference_cannot_leak_other_dataset(initialized, missing):
    model_storage.save_training(model(), "key", "body")
    model_storage.save_training(model("m2", "d2"), "key", "body")
    with storage.connection() as con:
        con.execute(
            "UPDATE training_requests SET model_id=? WHERE dataset_id='d1'",
            ("missing" if missing else "m2",),
        )
    with pytest.raises(model_storage.InvalidArtifact):
        model_storage.previous_training("d1", "key", "body")


def _legacy_database(path, version):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE datasets(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        con.execute("CREATE TABLE plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        con.execute(
            "CREATE TABLE revisions(plan_id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(plan_id,revision))"
        )
        con.execute(
            "CREATE TABLE confirmations(plan_id TEXT,key TEXT,body TEXT,response TEXT,PRIMARY KEY(plan_id,key))"
        )
        for ident in ("d1", "d2"):
            payload = json.dumps(
                {"id": ident, "rows": [], "version": "same", "source": "legacy"},
                indent=3,
            )
            con.execute("INSERT INTO datasets VALUES (?,?)", (ident, payload))
        plan = '{ "id":"p1", "revision":2, "dataset_id":"d2", "status":"confirmed" }'
        con.execute("INSERT INTO plans VALUES (?,?)", ("p1", plan))
        con.execute("INSERT INTO revisions VALUES (?,?,?)", ("p1", 2, plan))
        con.execute(
            "INSERT INTO confirmations VALUES (?,?,?,?)",
            ("p1", "key", " unchanged request ", plan),
        )
        if version == 2:
            storage._migrate_v2(con)
        else:
            con.execute(f"PRAGMA user_version={version}")
        return {
            table: con.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("datasets", "plans", "revisions", "confirmations")
        }


@pytest.mark.parametrize("version", [0, 1, 2])
def test_additive_v3_migration_preserves_legacy_bytes_and_backups(db, version):
    original = _legacy_database(db, version)
    storage.initialize_database()
    with storage.connection() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
        for table, rows in original.items():
            current = con.execute(f"SELECT * FROM {table}").fetchall()
            assert [row[: len(rows[0])] for row in current] == rows
        assert con.execute("SELECT count(*) FROM models").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM training_requests").fetchone()[0] == 0
    backup_path = db.with_name(db.name + ".pre-v3.bak")
    backup_bytes = backup_path.read_bytes()
    with sqlite3.connect(backup_path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        for table, rows in original.items():
            current = con.execute(f"SELECT * FROM {table}").fetchall()
            assert [row[: len(rows[0])] for row in current] == rows
    assert db.with_name(db.name + ".pre-v2.bak").exists() == (version < 2)
    model_storage.save_training(model(), "new-key", "body")
    storage.initialize_database()
    assert backup_path.read_bytes() == backup_bytes
    assert model_storage.get_model("m1") == model()
    assert storage.get_plan("p1")["dataset_id"] == "d2"


def test_newer_schema_rejected_without_migration_or_backup(db):
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA user_version=4")
    with pytest.raises(RuntimeError, match="newer"):
        storage.initialize_database()
    assert not list(db.parent.glob("*.bak"))
    with sqlite3.connect(db) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 4


def test_existing_migration_backup_is_never_overwritten(db):
    original = _legacy_database(db, 2)
    backup_path = db.with_name(db.name + ".pre-v3.bak")
    backup_path.write_bytes(b"previous backup")
    with pytest.raises(RuntimeError, match="backup already exists"):
        storage.initialize_database()
    assert backup_path.read_bytes() == b"previous backup"
    with sqlite3.connect(db) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert con.execute("SELECT * FROM datasets").fetchall() == original["datasets"]
        assert (
            con.execute("SELECT name FROM sqlite_master WHERE name='models'").fetchone()
            is None
        )


def test_v3_schema_failure_rolls_back_additions(db):
    _legacy_database(db, 2)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE training_requests(already_exists TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        storage.initialize_database()
    with sqlite3.connect(db) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            con.execute("SELECT name FROM sqlite_master WHERE name='models'").fetchone()
            is None
        )
    assert db.with_name(db.name + ".pre-v3.bak").exists()
