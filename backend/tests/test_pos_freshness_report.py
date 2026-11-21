import hashlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from backend import pos_monitoring, storage
from scripts import sqlite_snapshot as snapshots
from scripts.sqlite_snapshot import SnapshotError, inspect_database
from tools.pos_freshness_report import report


def test_v5_monitoring_migration_preserves_legacy_backup(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("BAKEPLAN_DB", str(path))
    with storage.connection(path) as db:
        storage._migrate_v2(db)
        storage._migrate_v3(db)
        storage._migrate_v4(db)
        storage._migrate_v5(db)
    storage.save_dataset({"id": "data", "rows": [], "version": "saved"})
    legacy_forecast = '{"result":{"dataset_id":"data"},"targetDate":"2026-01-03"}'
    with storage.connection(path) as db:
        db.execute("""INSERT INTO pos_connections
            (id,provider,bakery_id,location_id,location_name,days,ciphertext,updated_at,last_attempt_at,attempt_day,
             last_success_day,target_date,dataset_id,forecast)
            VALUES('old','poster','branch','1','Branch',7,X'AA','2026-01-02','2026-01-02','2026-01-02',
                   '2026-01-02','2026-01-03','data',?)
        """, (legacy_forecast,))
    before = inspect_database(path)
    storage.initialize_database(path)
    assert before["schema_version"] == 5 and inspect_database(Path(str(path) + ".pre-v6.bak")) == before
    backup = Path(str(path) + ".pre-v6.bak")
    manifest = {"format": snapshots.FORMAT, "schema_version": 5, "created_at": "2026-01-02T15:00:00Z",
                "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(), "table_counts": before["table_counts"]}
    snapshots.manifest_path(backup).write_text(json.dumps(manifest), encoding="utf-8")
    assert snapshots.verify_backup(backup) == manifest
    if sys.platform == "linux":
        restored = tmp_path / "restored.db"
        assert snapshots.restore_backup(backup, restored, app_stopped=True) == manifest
        assert restored.read_bytes() == backup.read_bytes()
        storage.initialize_database(restored)
        assert inspect_database(restored) == inspect_database(path)
    counts = inspect_database(path)["table_counts"]
    assert counts["pos_fresh_days"] == 0 and counts["pos_monitoring_epochs"] == counts["pos_connections"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,ciphertext,forecast,error FROM pos_connections").fetchone() == (
            "old", bytes.fromhex("AA"), legacy_forecast, "freshness_unverified")
        db.execute("INSERT INTO pos_fresh_days (connection_id,business_day,observed_through,fresh_at) VALUES('old','2026-01-03','2026-01-01','2026-01-03T05:00:00+00:00')")
    with pytest.raises(SnapshotError, match="freshness"):
        inspect_database(path)


def test_deadline_cli_counts_silent_days_and_eligible_working_days(tmp_path):
    path = tmp_path / "report.db"
    storage.initialize_database(path)
    with storage.connection(path) as db:
        db.execute("INSERT INTO pos_monitoring_epochs VALUES('branch','2026-10-11T12:00:00+00:00','2026-10-16T02:00:00+00:00')")
        db.executemany("""INSERT INTO pos_fresh_days (connection_id,business_day,observed_through,fresh_at)
            VALUES('branch',?,?,?)""", [
            ("2026-10-12", "2026-10-11", "2026-10-12T04:00:00+00:00"),  # 09:00 local = on time
            ("2026-10-13", "2026-10-12", "2026-10-13T04:00:01+00:00"),  # late
        ])
    args = ["--db", str(path), "--connection", "branch", "--start", "2026-10-09", "--end", "2026-10-22",
            "--deadline", "09:00", "--weekdays", "1,2,3,4,5", "--as-of", "2026-10-22T00:00:00Z"]
    result = subprocess.run([sys.executable, "-m", "tools.pos_freshness_report", *args, "--target-percent", "95"],
                            cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, check=True)
    measured = json.loads(result.stdout)
    assert [r["status"] for r in measured["days"]] == ["on_time", "late", "missing", "missing"]
    assert measured["expected_days"] == 4 and measured["on_time_pct"] == 25
    assert measured["objective"] == {"target_percent": "95", "status": "missed_to_date",
                                     "required_on_time_days": 4, "allowed_misses": 0}
    partial = report(path, "branch", date(2026,10,12), date(2026,10,14), time(9), {1,2,3}, datetime(2026,10,13,4,tzinfo=UTC))
    assert partial["late"] == 0 and partial["missing"] == partial["on_time"] == 1
    assert "objective" not in partial
    day = date(2026, 10, 12)
    inputs = (path, "branch", day, date(2026, 10, 13), time(9), {1, 2})
    as_of = datetime(2026, 10, 13, 5, tzinfo=UTC)
    assert report(*inputs, as_of, target_percent="50")["objective"]["status"] == "met_to_date"
    assert report(*inputs, as_of, target_percent="50.0001")["objective"]["status"] == "missed_to_date"
    before_open = datetime(2026, 10, 12, 3, tzinfo=UTC)
    assert report(*inputs, before_open, target_percent="95")["objective"]["status"] == "no_due_days"
    for invalid in ("N/A", "NaN", "0", "101"):
        with pytest.raises(ValueError, match="Target percent"):
            report(*inputs, as_of, target_percent=invalid)


@pytest.mark.parametrize(("ack", "accepted"), [(b"OK", True), (b"OK (not found)", False),
                                                (b"OK (rate limited)", False)])
def test_uuid_only_heartbeat_sends_no_payload_or_redirect(monkeypatch, ack, accepted):
    from urllib.request import Request
    from uuid import uuid4

    check_id = str(uuid4())

    class Check:
        def open(self, req, timeout):
            assert req.full_url == f"https://hc-ping.com/{check_id}"
            assert req.get_method() == "POST" and req.data == b"" and not req.headers
            assert timeout == 4
            class Response:
                status = 200
                def read(self, limit): return ack[:limit]
                def __enter__(self): return self
                def __exit__(self, *exc): pass
            return Response()

    monkeypatch.setattr(pos_monitoring, "build_opener", lambda *args: Check())
    assert pos_monitoring._send_success(check_id) is accepted
    with pytest.raises(ValueError):
        pos_monitoring._send_success("https://example.invalid/")
    assert pos_monitoring._NoRedirect().redirect_request(Request("https://hc-ping.com"),
        None, 302, "", {}, "https://example.invalid") is None
