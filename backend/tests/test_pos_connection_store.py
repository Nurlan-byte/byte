"""Private credential, recovery, migration and fencing checks using real SQLite and AES-GCM."""

import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import pos_connection_store as store
from backend import pos_vault, storage
from backend.errors import ServiceError
from scripts import sqlite_snapshot as snapshots

NOW = datetime(2026, 11, 21, 1, 0, tzinfo=timezone.utc)
NEXT = NOW + timedelta(days=1)
TOKEN = "fixture-secret-exclusively-for-unit-tests"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite3"
    keyfile = tmp_path / "master.key"
    monkeypatch.setenv("BAKEPLAN_DB", str(db))
    monkeypatch.setenv(pos_vault.KEY_FILE_ENV, str(keyfile))
    pos_vault.init_key(keyfile)
    storage.initialize_database()
    for ident in ("first", "next"):
        storage.save_dataset(
            {"id": ident, "version": ident, "source": "local", "rows": [{"sold": 1}]}
        )
    return db, keyfile


def start(key=TOKEN, now=NOW, location="1", branch="poster-A"):
    return store.start_connection(
        "poster", location, "Тестовый филиал", branch, 56, None, key, now
    )


def forecast(ident, day):
    target = str(day + timedelta(days=1))
    cutoff = str(day - timedelta(days=1))
    return {
        "result": {"dataset_id": ident, "cutoff": cutoff, "target_date": target,
                   "items": [{"product_id": "p", "forecast_mean": 0}]},
        "targetDate": target, "cutoff": cutoff, "observed_through": cutoff,
    }


def test_vault_file_privacy_ciphertext_binding_and_no_plaintext(workspace, monkeypatch):
    db, keyfile = workspace
    assert pos_vault.available()
    before = keyfile.read_bytes()
    with pytest.raises(FileExistsError):
        pos_vault.init_key(keyfile)
    assert keyfile.read_bytes() == before
    claim = start()
    assert store.claim_key(claim) == TOKEN
    summary, saved = store.connection_result(claim["id"])
    assert summary["status"] == "syncing"
    assert store.list_connections() == [summary]
    assert summary["dataset_id"] is None and saved is None
    assert {"ciphertext", "lease_token", "token", "api_key"}.isdisjoint(summary)
    assert TOKEN not in json.dumps([summary, claim])
    with storage.connection() as conn:
        blob = conn.execute("SELECT ciphertext FROM pos_connections").fetchone()[0]
    assert TOKEN.encode() not in blob and TOKEN.encode() not in db.read_bytes()
    assert before.strip() not in db.read_bytes()
    arguments = (claim["id"], "poster", "poster-A", "1")
    assert pos_vault.seal(TOKEN, *arguments) != blob
    for i in range(len(arguments)):
        wrong = list(arguments)
        wrong[i] += "x"
        with pytest.raises(ServiceError) as error:
            pos_vault.unseal(blob, *wrong)
        assert error.value.status_code == 503 and TOKEN not in str(error.value)
    damaged = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(ServiceError):
        pos_vault.unseal(damaged, *arguments)
    other = keyfile.with_name("replacement.key")
    pos_vault.init_key(other)
    monkeypatch.setenv(pos_vault.KEY_FILE_ENV, str(other))
    with pytest.raises(ServiceError):
        store.claim_key(claim)
    assert store.list_connections()[0]["id"] == claim["id"]
    missing = keyfile.with_name("missing.key")
    monkeypatch.setenv(pos_vault.KEY_FILE_ENV, str(missing))
    assert not pos_vault.available()
    with pytest.raises(ServiceError):
        start(key="credential-cannot-overwrite")
    assert not missing.exists()
    monkeypatch.setenv(pos_vault.KEY_FILE_ENV, str(keyfile))
    assert store.claim_key(claim) == TOKEN


def test_master_file_must_be_private(workspace):
    _, keyfile = workspace
    if os.name == "nt":
        import win32security as security
        import ntsecuritycon

        sd = security.GetNamedSecurityInfo(
            str(keyfile), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
        )
        dacl = sd.GetSecurityDescriptorDacl()
        world = security.CreateWellKnownSid(security.WinWorldSid)
        dacl.AddAccessAllowedAce(
            security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ, world
        )
        security.SetNamedSecurityInfo(
            str(keyfile),
            security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
    else:
        assert keyfile.stat().st_mode & 0o777 == 0o600
        keyfile.chmod(0o644)
    assert not pos_vault.available()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows ACL errors")
@pytest.mark.parametrize(
    "stage", ["principal", "GetSecurityInfo", "GetSecurityDescriptorDacl"]
)
def test_native_acl_failure_is_safe_and_does_not_leak(workspace, monkeypatch, stage):
    import pywintypes
    import win32security

    _, keyfile = workspace

    def access_denied(*args):
        raise pywintypes.error(5, stage, f"{keyfile}: {TOKEN}")

    if stage == "principal":
        monkeypatch.setattr(pos_vault, "_windows_principals", access_denied)
    elif stage == "GetSecurityInfo":
        monkeypatch.setattr(win32security, stage, access_denied)
    else:

        class DeniedDescriptor:
            GetSecurityDescriptorDacl = access_denied

        monkeypatch.setattr(
            win32security, "GetSecurityInfo", lambda *args: DeniedDescriptor()
        )
    assert not pos_vault.available()
    with pytest.raises(ServiceError) as error:
        start()
    assert error.value.status_code == 503
    assert str(error.value) == pos_vault.UNAVAILABLE
    assert not store.has_connections()


def test_malformed_key_file_fails_closed(workspace):
    _, keyfile = workspace
    keyfile.write_bytes(b"x" * 127)
    assert not pos_vault.available()
    with pytest.raises(ServiceError):
        start()
    assert not store.list_connections()


def test_concurrent_claim_recovery_retry_and_rotation(workspace):
    claim = start()
    ident = claim["id"]
    result = forecast("first", NOW.date())
    assert store.finish(claim, "first", result, NOW.date(), NOW)
    assert store.connection_result(ident) == (store.list_connections()[0], result)
    assert store.claim_next(NOW.date(), NOW + timedelta(hours=4)) is None
    with ThreadPoolExecutor(max_workers=8) as threads:
        claims = [
            v
            for v in threads.map(
                lambda _: store.claim_next(NEXT.date(), NEXT), range(12)
            )
            if v
        ]
    assert len(claims) == 1
    stale = claims[0]
    assert store.claim_key(stale) == TOKEN
    recovered = store.claim_next(NEXT.date(), NEXT + store.LEASE_DURATION)
    assert recovered["token"] != stale["token"] and recovered["day"] == str(NEXT.date())
    with pytest.raises(storage.NotFound):
        store.claim_key(stale)
    assert not store.finish(
        stale, "next", forecast("next", NEXT.date()), NEXT.date(), NEXT
    )
    assert not store.fail(stale, "forecast_unavailable", NEXT)
    failed_at = NEXT + store.LEASE_DURATION
    assert store.fail(recovered, TOKEN, failed_at)
    metadata, saved = store.connection_result(ident)
    assert metadata["error"] == "sync_failed"
    assert (metadata["dataset_id"], saved) == ("first", result)
    assert (
        store.claim_next(
            NEXT.date(), failed_at + store.RETRY_DELAY - timedelta(seconds=1)
        )
        is None
    )
    retry = store.claim_next(NEXT.date(), failed_at + store.RETRY_DELAY)
    replacement = start("replacement-token", NEXT)
    assert (
        store.claim_key(replacement) == "replacement-token"
        and replacement["id"] == ident
    )
    assert not store.fail(retry, "unauthorized", NEXT)
    newer = forecast("next", NEXT.date())
    assert store.finish(replacement, "next", newer, NEXT.date(), NEXT)
    metadata, saved = store.connection_result(ident)
    assert (metadata["dataset_id"], saved) == ("next", newer)
    assert store.claim_next(NEXT.date(), NEXT + timedelta(hours=1)) is None
    assert store.claim_next(NOW.date(), NOW) is None
    with pytest.raises(storage.NotFound):
        store.claim_key(replacement)
    store.revoke(ident)
    store.revoke(ident)
    assert not store.fail(replacement, "no_sales", NEXT)
    assert store.list_connections() == []
    with pytest.raises(storage.NotFound):
        store.connection_result(ident)
    assert storage.get_dataset("next")["id"] == "next"
    assert start(now=NEXT)["id"] != ident


@pytest.mark.parametrize("failure", ["interrupted", "error"])
def test_pre_schedule_registration_retries_for_its_local_day(workspace, failure):
    # 02:00 local on Nov 22, scheduler's latest due period is Nov 21.
    early = datetime(2026, 11, 21, 21, tzinfo=timezone.utc)
    day = early.astimezone(store.BUSINESS_TZ).date()
    old_period = day - timedelta(days=1)
    first = start(now=early)
    if failure == "error":
        assert store.fail(first, "vendor_unavailable", early)
        due = early + store.RETRY_DELAY
    else:
        due = early + store.LEASE_DURATION
    assert store.claim_next(old_period, due - timedelta(seconds=1)) is None
    retry = store.claim_next(old_period, due)
    assert retry["day"] == str(day)
    assert store.finish(retry, "first", forecast("first", day), day, due)
    assert store.claim_next(old_period, due + timedelta(minutes=1)) is None


def test_v4_migration_preserves_rows_backup_and_legacy_verification(
    tmp_path, monkeypatch
):
    db = tmp_path / "old.sqlite3"
    monkeypatch.setenv("BAKEPLAN_DB", str(db))
    with storage.connection() as conn:
        storage._migrate_v2(conn)
        storage._migrate_v3(conn)
        storage._migrate_v4(conn)
    storage.save_dataset(
        {"id": "old", "version": "v4", "source": "local", "rows": [{"sold": 3}]}
    )
    assert snapshots.inspect_database(db)["schema_version"] == 4
    storage.initialize_database()
    backup = Path(str(db) + ".pre-v5.bak")
    assert not store.list_connections()
    assert snapshots.inspect_database(backup)["schema_version"] == 4
    with sqlite3.connect(backup) as old, sqlite3.connect(db) as new:
        for table in storage.SQLITE_V4_COLUMNS:
            old_rows = old.execute(f"SELECT * FROM {table}").fetchall()
            assert old_rows == new.execute(f"SELECT * FROM {table}").fetchall()


@pytest.mark.skipif(
    sys.platform != "linux", reason="Linux snapshot publication and fsync contract"
)
def test_encrypted_connection_survives_real_snapshot_restore(
    workspace, tmp_path, monkeypatch
):
    db, _ = workspace
    claim = start()
    result = forecast("first", NOW.date())
    assert store.finish(claim, "first", result, NOW.date(), NOW)
    backup = tmp_path / "backup" / "sales.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    manifest = snapshots.create_backup(db, backup)
    assert manifest["table_counts"]["pos_connections"] == 1
    assert TOKEN.encode() not in backup.read_bytes()
    snapshots.restore_backup(backup, restored, app_stopped=True)
    monkeypatch.setenv("BAKEPLAN_DB", str(restored))
    storage.initialize_database()
    metadata, saved = store.connection_result(claim["id"])
    assert (metadata["dataset_id"], saved) == ("first", result)
    lease = store.claim_next(NEXT.date(), NEXT)
    assert store.claim_key(lease) == TOKEN
    store.revoke(lease["id"])
    assert (
        snapshots.create_backup(restored, tmp_path / "post-revocation.sqlite3")[
            "table_counts"
        ]["pos_connections"]
        == 0
    )
