"""Local snapshot and configuration contracts; these do NOT run Docker/Caddy."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from backend import model_storage, storage
from scripts import check_demo, sqlite_snapshot as snapshots

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def populated(tmp_path, monkeypatch):
    if sys.platform != "linux":
        pytest.skip(
            "Linux/container snapshot operations run in the Linux deployment CI job"
        )
    database = tmp_path / "live.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(database))
    storage.initialize_database()
    dataset = {
        "id": "synthetic-dataset",
        "source": "synthetic",
        "schema_version": "history-1",
        "version": "immutable-fixture-hash",
        "rows": [{"date": "2026-10-01", "product_id": "p1", "sold_units": 12}],
    }
    storage.save_dataset(dataset)
    plan = {
        "id": "synthetic-plan",
        "dataset_id": dataset["id"],
        "revision": 0,
        "status": "draft",
        "source": "synthetic",
        "target_date": "2026-10-02",
        "items": [],
    }
    storage.save_plan(plan)
    confirmed = {**plan, "revision": 1, "status": "confirmed"}
    with storage.connection() as con:
        storage.update_plan(con, confirmed)
        con.execute(
            "INSERT INTO revisions VALUES (?,?,?)",
            (plan["id"], 1, json.dumps(confirmed)),
        )
        con.execute(
            "INSERT INTO confirmations VALUES (?,?,?,?)",
            (plan["id"], "confirm-once", '{"revision":0}', json.dumps(confirmed)),
        )
    model = {
        "id": "synthetic-model",
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
        "bakery_id": "synthetic-only",
        "training_cutoff": "2026-10-01",
        "protocol_version": "ridge-test",
        "target": "sold_units",
        "unit": "physical_unit",
        "status": "ready",
        "products": {"p1": {"coefficients": [1.25], "intercept": 2.0}},
        "evaluation": {"synthetic": True},
    }
    model_storage.save_training(model, "train-once", '{"cutoff":"2026-10-01"}')
    return database, dataset, confirmed, model


def dump_tables(path):
    with sqlite3.connect(path) as con:
        return {
            table: con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in snapshots.COLUMNS
        }


def test_backup_restore_preserves_all_state_and_retry_keys(
    populated, tmp_path, monkeypatch
):
    database, dataset, plan, model = populated
    backup = tmp_path / "snapshots" / "demo.sqlite3"
    summary = snapshots.create_backup(database, backup)
    assert summary["table_counts"] == {
        "datasets": 1,
        "plans": 1,
        "models": 1,
        "revisions": 2,
        "dataset_import_keys": 1,
        "training_requests": 1,
        "confirmations": 1,
    }
    assert snapshots.verify_backup(backup) == summary
    restored = tmp_path / "restored.sqlite3"
    snapshots.restore_backup(backup, restored, app_stopped=True)
    assert dump_tables(database) == dump_tables(restored)
    assert restored.read_bytes() == backup.read_bytes()
    monkeypatch.setenv("BAKEPLAN_DB", str(restored))
    storage.initialize_database()
    assert storage.get_dataset(dataset["id"]) == dataset
    assert storage.get_plan(plan["id"]) == plan
    assert model_storage.get_model(model["id"]) == model
    assert storage.save_dataset(dataset) == (dataset, False)
    assert (
        model_storage.previous_training(
            dataset["id"], "train-once", '{"cutoff":"2026-10-01"}'
        )
        == model
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.storage import initialize_database, get_plan; "
            "initialize_database(); print(get_plan('synthetic-plan')['revision'])",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert process.stdout.strip() == "1"
    assert backup.stat().st_mode & 0o777 == 0o600
    assert snapshots.manifest_path(backup).stat().st_mode & 0o777 == 0o600


def test_online_backup_includes_committed_wal_and_excludes_pending_changes(
    populated, tmp_path
):
    database, _, _, _ = populated
    backup = tmp_path / "wal-backup.sqlite3"
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE plans SET status='committed-marker'")
        writer.commit()
        writer.execute("UPDATE plans SET status='uncommitted-marker'")
        assert Path(str(database) + "-wal").exists()
        snapshots.create_backup(database, backup)
        with sqlite3.connect(backup) as saved:
            assert (
                saved.execute("SELECT status FROM plans").fetchone()[0]
                == "committed-marker"
            )
        writer.rollback()
    assert not Path(str(backup) + "-wal").exists()
    snapshots.verify_backup(backup)


@pytest.mark.parametrize("existing", ["database", "manifest"])
def test_backup_never_overwrites_existing_files(populated, tmp_path, existing):
    database, *_ = populated
    backup = tmp_path / "existing.sqlite3"
    path = backup if existing == "database" else snapshots.manifest_path(backup)
    path.write_text("preserve-me")
    with pytest.raises(snapshots.SnapshotError, match="overwrite"):
        snapshots.create_backup(database, backup)
    assert path.read_text() == "preserve-me"


def test_restore_requires_stop_acknowledgement_and_new_destination(populated, tmp_path):
    database, *_ = populated
    backup = tmp_path / "snapshot.sqlite3"
    snapshots.create_backup(database, backup)
    with pytest.raises(snapshots.SnapshotError, match="Stop API"):
        snapshots.restore_backup(backup, tmp_path / "new.sqlite3")
    before = database.read_bytes()
    with pytest.raises(snapshots.SnapshotError, match="overwrite"):
        snapshots.restore_backup(backup, database, app_stopped=True)
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "bytes",
        "manifest",
        "missing_manifest",
        "model",
        "schema",
        "tables",
        "reference",
        "sidecar",
    ],
)
def test_invalid_backup_refused_without_creating_destination(
    populated, tmp_path, damage
):
    database, *_ = populated
    backup = tmp_path / "snapshot.sqlite3"
    snapshots.create_backup(database, backup)
    manifest = snapshots.manifest_path(backup)
    if damage == "bytes":
        backup.write_bytes(b"not a SQLite database")
    elif damage == "manifest":
        manifest.write_text("[]")
    elif damage == "missing_manifest":
        manifest.unlink()
    elif damage == "sidecar":
        Path(str(backup) + "-wal").write_bytes(b"unexpected")
    else:
        with sqlite3.connect(backup) as con:
            if damage == "model":
                con.execute("UPDATE models SET payload=payload || ' '")
            elif damage == "schema":
                con.execute("PRAGMA user_version=99")
            elif damage == "tables":
                con.execute("DROP TABLE confirmations")
            elif damage == "reference":
                con.execute("UPDATE models SET dataset_id='missing'")
        # A matching checksum does not let corrupt application structure pass.
        record = json.loads(manifest.read_text())
        record["sha256"] = snapshots._sha256(backup)
        manifest.write_text(json.dumps(record))
    restored = tmp_path / "must-not-exist.sqlite3"
    with pytest.raises((snapshots.SnapshotError, FileNotFoundError)):
        snapshots.restore_backup(backup, restored, app_stopped=True)
    assert not restored.exists()
    assert not list(tmp_path.glob(".bakeplan-snapshot-*"))


def test_backup_rejects_missing_source_without_creating_empty_database(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(snapshots.SnapshotError):
        snapshots.create_backup(missing, tmp_path / "backup.sqlite3")
    assert not missing.exists()


def test_restore_refuses_symlink_or_sidecar_destination(populated, tmp_path):
    database, *_ = populated
    backup = tmp_path / "snapshot.sqlite3"
    snapshots.create_backup(database, backup)
    target = tmp_path / "link.sqlite3"
    target.symlink_to(tmp_path / "missing-target")
    with pytest.raises(snapshots.SnapshotError, match="overwrite"):
        snapshots.restore_backup(backup, target, app_stopped=True)
    target.unlink()
    Path(str(target) + "-journal").write_bytes(b"stale")
    with pytest.raises(snapshots.SnapshotError, match="sidecar"):
        snapshots.restore_backup(backup, target, app_stopped=True)


def test_cli_reports_failure_without_traceback(tmp_path):
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.sqlite_snapshot",
            "verify",
            str(tmp_path / "absent.sqlite3"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 1
    assert "Snapshot refused:" in process.stderr
    assert "Traceback" not in process.stderr


@pytest.mark.parametrize(
    "url",
    [
        "http://demo.example",
        "ftp://localhost",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?key=secret",
    ],
)
def test_gateway_probe_refuses_unsafe_origins(url):
    with pytest.raises(check_demo.CheckError):
        check_demo.validate_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://demo.example",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
    ],
)
def test_gateway_probe_accepts_https_or_loopback_only(url):
    assert check_demo.validate_base_url(url) == url


def test_deployment_configuration_contracts():
    """Text guards are supplementary, not container or proxy integration tests."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "deploy/compose.yaml").read_text()
    routes = (ROOT / "deploy/routes.caddy").read_text()
    https = (ROOT / "deploy/Caddyfile.https").read_text()
    assert "node:24.19.0-bookworm-slim" in dockerfile
    assert "python:3.14.8-slim-bookworm" in dockerfile
    assert "npm ci" in dockerfile and "backend/requirements.lock" in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert "USER 10001:10001" in dockerfile
    api_service = compose.split("  gateway:")[0]
    assert "ports:" not in api_service
    assert "sqlite_data:/data" in api_service
    assert '"127.0.0.1:8080:80"' in compose
    assert "BAKEPLAN_AUTH_FILE:?" in compose
    assert "internal: true" in compose
    assert routes.index("basic_auth {") < routes.index("reverse_proxy api:8000")
    assert routes.index("basic_auth {") < routes.index("file_server")
    assert "not header X-BakePlan-Request 1" in routes
    assert "Sec-Fetch-Site cross-site" in routes
    assert "header Strict-Transport-Security" in https
    assert "bind 127.0.0.1" in https
    assert "ports: !override" in (ROOT / "deploy/compose.https.yaml").read_text()
    # No usable credential or default account is checked in.
    assert not (ROOT / "deploy/secrets").exists()
    assert "BAKEPLAN_AUTH_FILE=\n" in (ROOT / "deploy/.env.example").read_text()


def test_gateway_entrypoint_fails_closed_without_operator_secret():
    # A local shell check only: this is not a running Caddy auth test.
    if sys.platform != "linux":
        pytest.skip("Linux container entrypoint runs in the Linux deployment CI job")
    if Path("/run/secrets/demo_users").exists():
        pytest.skip("Host already provides a secret; do not inspect or use it")
    process = subprocess.run(
        [
            "sh",
            str(ROOT / "deploy/gateway-entrypoint.sh"),
            "sh",
            "-c",
            "echo should-not-run",
        ],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 1
    assert "Refusing to serve" in process.stderr
    assert not process.stdout


def test_health_rejects_missing_persistence_tables(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import app

    database = tmp_path / "health.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(database))
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        with sqlite3.connect(database) as con:
            con.execute("DROP TABLE models")
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["error"]["message"] == "Хранилище недоступно"


def test_health_never_recreates_a_missing_database(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import app

    database = tmp_path / "health.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(database))
    with TestClient(app) as client:
        database.unlink()
        assert client.get("/api/health").status_code == 503
        assert not database.exists()


def test_container_fixture_ml_flow_against_api_and_restored_snapshot(
    tmp_path, monkeypatch
):
    """Exercise the script's application assertions, without claiming proxy QA."""
    if sys.platform != "linux":
        pytest.skip("Linux snapshot + scripted ML flow runs in deployment CI")
    from fastapi.testclient import TestClient
    from backend.app import app
    from scripts import container_fixture

    database = tmp_path / "scripted.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(database))
    state = tmp_path / "state.json"

    def local_request(path, body=None, *, expected=200, text=False):
        response = client.get(path) if body is None else client.post(path, json=body)
        assert response.status_code == expected, response.text
        return response.text if text else response.json()

    monkeypatch.setattr(container_fixture, "request", local_request)
    with TestClient(app) as client:
        container_fixture.seed(state)
        container_fixture.verify(state)
    backup = tmp_path / "scripted-backup.sqlite3"
    snapshots.create_backup(database, backup)
    restored = tmp_path / "scripted-restored.sqlite3"
    snapshots.restore_backup(backup, restored, app_stopped=True)
    monkeypatch.setenv("BAKEPLAN_DB", str(restored))
    with TestClient(app) as client:
        container_fixture.verify(state)
