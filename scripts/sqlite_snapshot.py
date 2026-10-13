"""Create consistent SQLite backups and restore only verified, fresh copies.

Run as ``python -m scripts.sqlite_snapshot`` from the repository/image root.
No SQL dump, executable serialization, credentials, or extra packages are used.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend import model_storage

FORMAT = "bakeplan-sqlite-snapshot-1"
SCHEMA_VERSION = 3
COLUMNS = {
    "datasets": {
        "id",
        "payload",
        "schema_version",
        "source",
        "content_hash",
        "created_at",
        "row_count",
        "canonical_dataset_id",
    },
    "plans": {
        "id",
        "payload",
        "schema_version",
        "source",
        "created_at",
        "target_date",
        "revision",
        "status",
        "dataset_id",
    },
    "models": {
        "id",
        "dataset_id",
        "training_cutoff",
        "protocol_version",
        "payload",
        "artifact_hash",
    },
    "training_requests": {"dataset_id", "key", "body", "model_id"},
    "revisions": {"plan_id", "revision", "payload"},
    "confirmations": {"plan_id", "key", "body", "response"},
    "dataset_import_keys": {"source", "schema_version", "content_hash", "dataset_id"},
}


class SnapshotError(ValueError):
    """A snapshot is incomplete, invalid, or unsafe to publish/restore."""


def manifest_path(path):
    return Path(str(path) + ".manifest.json")


def _sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _regular_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SnapshotError(f"Expected an existing, regular nonsymlink file: {path}")
    return path


def _no_sidecars(path):
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise SnapshotError(
                f"Refusing SQLite sidecar {sidecar}; use a clean snapshot"
            )


def _connect(path, *, snapshot=False):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    if snapshot:
        uri += "&immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.execute("PRAGMA trusted_schema=OFF")
    return con


def inspect_database(path):
    """Validate a standalone v3 BakePlan DB without creating or migrating it."""
    path = _regular_file(path)
    _no_sidecars(path)
    try:
        with closing(_connect(path, snapshot=True)) as con:
            if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise SnapshotError("SQLite integrity_check failed")
            if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise SnapshotError(
                    f"Expected BakePlan schema {SCHEMA_VERSION}; no implicit migrations"
                )
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if tables != set(COLUMNS):
                raise SnapshotError(
                    "Snapshot does not have the expected BakePlan tables"
                )
            if con.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1"
            ).fetchone():
                raise SnapshotError("Unexpected database views or triggers")
            for table, required in COLUMNS.items():
                columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                if columns != required:
                    raise SnapshotError(f"Unexpected schema for {table}")
            if con.execute("PRAGMA foreign_key_check").fetchone():
                raise SnapshotError("Snapshot contains invalid foreign keys")
            references = (
                ("plans", "dataset_id", "datasets", "id"),
                ("models", "dataset_id", "datasets", "id"),
                ("training_requests", "model_id", "models", "id"),
                ("training_requests", "dataset_id", "datasets", "id"),
                ("revisions", "plan_id", "plans", "id"),
                ("confirmations", "plan_id", "plans", "id"),
                ("dataset_import_keys", "dataset_id", "datasets", "id"),
            )
            for table, column, target, key in references:
                if con.execute(
                    f"SELECT 1 FROM {table} a LEFT JOIN {target} b ON a.{column}=b.{key} "
                    f"WHERE b.{key} IS NULL LIMIT 1"
                ).fetchone():
                    raise SnapshotError(f"Broken reference in {table}.{column}")
            for table in ("datasets", "plans"):
                for ident, payload in con.execute(f"SELECT id,payload FROM {table}"):
                    decoded = json.loads(payload)
                    if not isinstance(decoded, dict) or decoded.get("id") != ident:
                        raise SnapshotError(f"Invalid object identity in {table}")
            for plan_id, payload in con.execute(
                "SELECT plan_id,payload FROM revisions"
            ):
                decoded = json.loads(payload)
                if not isinstance(decoded, dict) or decoded.get("id") != plan_id:
                    raise SnapshotError("Invalid revision identity")
            for plan_id, body, response in con.execute(
                "SELECT plan_id,body,response FROM confirmations"
            ):
                if not isinstance(json.loads(body), dict):
                    raise SnapshotError("Invalid confirmation request")
                decoded = json.loads(response)
                if not isinstance(decoded, dict) or decoded.get("id") != plan_id:
                    raise SnapshotError("Invalid confirmation response")
            for row in con.execute(
                "SELECT id,dataset_id,training_cutoff,protocol_version,payload,artifact_hash FROM models"
            ):
                model_storage._decode(row)
            counts = {
                table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in sorted(COLUMNS)
            }
    except (
        sqlite3.Error,
        json.JSONDecodeError,
        TypeError,
        model_storage.InvalidArtifact,
    ) as exc:
        raise SnapshotError(f"Invalid BakePlan snapshot: {exc}") from exc
    return {"schema_version": SCHEMA_VERSION, "table_counts": counts}


def _fresh_destination(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise SnapshotError(f"Refusing to overwrite existing destination: {path}")
    _no_sidecars(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _temp_path(parent):
    fd, name = tempfile.mkstemp(prefix=".bakeplan-snapshot-", dir=parent)
    os.close(fd)
    return Path(name)


def _sync_file(path):
    with Path(path).open("rb") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(temp, destination):
    # Same-directory hard link is atomic and refuses a competing destination.
    os.link(temp, destination)
    _sync_directory(destination.parent)


def create_backup(database, output):
    """SQLite's backup API includes committed WAL data while the app is online."""
    database = _regular_file(database)
    output = _fresh_destination(output)
    manifest = _fresh_destination(manifest_path(output))
    temporary = _temp_path(output.parent)
    temporary_manifest = _temp_path(output.parent)
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > 60:
            raise SnapshotError(
                "Backup exceeded 60 seconds; retry during a quieter period"
            )

    try:
        with (
            closing(_connect(database)) as source,
            closing(sqlite3.connect(temporary)) as target,
        ):
            source.backup(target, pages=256, progress=progress, sleep=0.1)
            target.execute("PRAGMA journal_mode=DELETE")
        summary = inspect_database(temporary)
        record = {
            "format": FORMAT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha256": _sha256(temporary),
            **summary,
        }
        temporary_manifest.write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        _sync_file(temporary)
        _sync_file(temporary_manifest)
        _publish(temporary, output)
        _publish(temporary_manifest, manifest)
        return record
    finally:
        temporary.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)


def verify_backup(backup):
    backup = _regular_file(backup)
    manifest = _regular_file(manifest_path(backup))
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("Unreadable snapshot manifest") from exc
    if not isinstance(record, dict) or record.get("format") != FORMAT:
        raise SnapshotError("Unknown snapshot manifest format")
    if record.get("sha256") != _sha256(backup):
        raise SnapshotError("Snapshot SHA-256 does not match its manifest")
    summary = inspect_database(backup)
    if any(record.get(key) != value for key, value in summary.items()):
        raise SnapshotError("Snapshot schema/counts do not match its manifest")
    return record


def restore_backup(backup, database, *, app_stopped=False):
    """Restore into a NEW filename; preserve the old database for rollback."""
    if not app_stopped:
        raise SnapshotError(
            "Stop API and gateway first; pass --app-stopped to acknowledge"
        )
    record = verify_backup(backup)
    database = _fresh_destination(database)
    temporary = _temp_path(database.parent)
    try:
        shutil.copyfile(backup, temporary)
        if _sha256(temporary) != record["sha256"]:
            raise SnapshotError("Snapshot changed while restoring")
        inspect_database(temporary)
        _sync_file(temporary)
        _publish(temporary, database)
        return record
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser(
        "backup", help="Consistent live backup plus SHA-256 manifest"
    )
    backup.add_argument(
        "--database", default=os.getenv("BAKEPLAN_DB", "data/bakeplan.sqlite3")
    )
    backup.add_argument("--output", required=True)
    verify = commands.add_parser(
        "verify", help="Verify a saved backup; never migrate it"
    )
    verify.add_argument("backup")
    restore = commands.add_parser(
        "restore", help="Restore to a new database filename only"
    )
    restore.add_argument("backup")
    restore.add_argument("--database", required=True)
    restore.add_argument("--app-stopped", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.database, args.output)
        elif args.command == "verify":
            result = verify_backup(args.backup)
        else:
            result = restore_backup(
                args.backup, args.database, app_stopped=args.app_stopped
            )
    except (SnapshotError, OSError, sqlite3.Error) as exc:
        print(f"Snapshot refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
