"""Replay the public synthetic fixture through the real in-process API.

No network, browser, credentials or supplied database are used. Each run creates
fresh temporary databases and a new output directory; existing output is refused.
This proves program behavior on synthetic data, not real bakery accuracy.
"""

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from backend.app import app
from backend.storage import connection

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = "2026-10-11"
TARGET = "2026-10-13"
BUSINESS_DATE = date(2026, 10, 14)
TABLES = (
    "datasets",
    "dataset_import_keys",
    "models",
    "training_requests",
    "plans",
    "revisions",
    "confirmations",
)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def table_counts():
    with connection() as con:
        return {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }


def source_provenance():
    files = [*sorted((ROOT / "backend").glob("*.py")), Path(__file__).resolve()]
    hashes = {
        path.relative_to(ROOT).as_posix(): digest(path.read_bytes()) for path in files
    }
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    changes = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {
        "git_head": head,
        "working_tree_dirty": bool(changes),
        "working_tree_status_note": "Dirty includes tracked and untracked files; source hashes below identify the executed code. Private file names are not collected into this report.",
        "source_file_sha256": hashes,
        "source_manifest_sha256": digest(json.dumps(hashes, sort_keys=True).encode()),
        "python": sys.version.split()[0],
        "package_versions": {
            name: importlib.metadata.version(name)
            for name in ("fastapi", "numpy", "httpx")
        },
    }


def replay(output_dir):
    """Write evidence only into a newly created directory; preserve caller's DB."""
    if not __debug__:
        raise ValueError(
            "Run without Python optimization; evidence assertions are required"
        )
    output_dir = Path(output_dir)
    # mkdir is exclusive even for dangling symlinks; never reuse existing output.
    output_dir.mkdir(exist_ok=False)
    source = source_provenance()
    fixture_path = ROOT / "fixtures" / "synthetic_demo.csv"
    fixture_bytes = fixture_path.read_bytes()
    csv_text = fixture_bytes.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert max(row["date"] for row in rows) < TARGET
    import_body = {
        "csv_text": csv_text,
        "source": "synthetic",
        "schema_version": "history-1",
        "bakery_id": "demo",
        "target": "sold_units",
        "unit": "physical_unit",
        "provenance": "Синтетическая демонстрация BakePlan",
    }
    audit = []

    def request(client, method, path, *, body=None, expected=200, raw=False):
        response = (
            client.request(method, path, json=body)
            if body is not None
            else client.request(method, path)
        )
        audit.append({"method": method, "path": path, "status": response.status_code})
        if response.status_code != expected:
            raise ValueError(
                f"{method} {path}: expected {expected}, got {response.status_code}: {response.text}"
            )
        return response.content if raw else response.json()

    def forecast_body(dataset, method="ML", **extra):
        return {
            "dataset_id": dataset["id"],
            "cutoff": CUTOFF,
            "target_date": TARGET,
            "method": method,
            **extra,
        }

    with tempfile.TemporaryDirectory(prefix="bakeplan-api-replay-") as directory:
        main_db = Path(directory) / "trained.sqlite3"
        fallback_db = Path(directory) / "untrained.sqlite3"
        assert not main_db.exists() and not fallback_db.exists()
        with (
            patch.dict(os.environ, {"BAKEPLAN_DB": str(fallback_db)}),
            patch("backend.app.business_today", return_value=BUSINESS_DATE),
        ):
            with TestClient(app) as client:
                fallback_dataset = request(
                    client, "POST", "/api/datasets", body=import_body, expected=201
                )
                fallback = request(
                    client,
                    "POST",
                    "/api/forecasts",
                    body=forecast_body(fallback_dataset),
                )
                fallback_b1 = request(
                    client,
                    "POST",
                    "/api/forecasts",
                    body=forecast_body(fallback_dataset, "B1"),
                )
                assert fallback["model_id"] is None
                assert table_counts()["models"] == 0
                for actual, baseline in zip(
                    fallback["items"], fallback_b1["items"], strict=True
                ):
                    assert actual["product_id"] == baseline["product_id"]
                    assert actual["method"] == "B1" and actual["fallback_reason"]
                    assert actual["forecast_mean"] == baseline["forecast_mean"]
                    assert actual["used_dates"] == baseline["used_dates"]
                fallback_evidence = {
                    "separate_new_database": True,
                    "counts": table_counts(),
                    "forecast": fallback,
                    "b1_equality_verified": True,
                }
        with (
            patch.dict(os.environ, {"BAKEPLAN_DB": str(main_db)}),
            patch("backend.app.business_today", return_value=BUSINESS_DATE),
        ):
            with TestClient(app) as client:
                request(client, "GET", "/api/health")
                dataset = request(
                    client, "POST", "/api/datasets", body=import_body, expected=201
                )
                assert (
                    request(client, "POST", "/api/datasets", body=import_body)
                    == dataset
                )
                before = table_counts()
                invalid_bytes = (ROOT / "fixtures" / "invalid_balance.csv").read_bytes()
                error = request(
                    client,
                    "POST",
                    "/api/datasets",
                    body={**import_body, "csv_text": invalid_bytes.decode("utf-8-sig")},
                    expected=422,
                )
                after = table_counts()
                assert before == after
                invalid = {
                    "fixture": "fixtures/invalid_balance.csv",
                    "fixture_sha256": digest(invalid_bytes),
                    "status": 422,
                    "error": error,
                    "counts_before": before,
                    "counts_after": after,
                    "atomic_rejection_verified": True,
                }
                train_body = {
                    "cutoff": CUTOFF,
                    "idempotency_key": "synthetic-replay-train",
                }
                model = request(
                    client,
                    "POST",
                    f"/api/datasets/{dataset['id']}/train",
                    body=train_body,
                    expected=201,
                )
                assert model["status"] == "ready"
                ml = request(
                    client,
                    "POST",
                    "/api/forecasts",
                    body=forecast_body(dataset, model_id=model["id"]),
                )
                b1 = request(
                    client, "POST", "/api/forecasts", body=forecast_body(dataset, "B1")
                )
                assert ml["evaluation_applicability"]["applicable"] is True
                for actual, baseline in zip(ml["items"], b1["items"], strict=True):
                    assert actual["product_id"] == baseline["product_id"]
                    assert actual["method"] == "ML"
                    assert actual["b1_mean"] == baseline["forecast_mean"]
                    assert actual["used_dates"] == baseline["used_dates"]
                plan = request(
                    client,
                    "POST",
                    "/api/plans",
                    body=forecast_body(dataset, model_id=model["id"], mode="replay"),
                    expected=201,
                )
                quantities = {
                    item["product_id"]: item["recommended_qty"]
                    for item in plan["items"]
                }
                quantities["p1"] = 84
                confirmation = {
                    "revision": plan["revision"],
                    "idempotency_key": "synthetic-replay-confirm",
                    "quantities": quantities,
                }
                confirmed = request(
                    client,
                    "POST",
                    f"/api/plans/{plan['id']}/confirm",
                    body=confirmation,
                )
                assert confirmed["status"] == "confirmed"
                assert (
                    next(
                        item
                        for item in confirmed["items"]
                        if item["product_id"] == "p1"
                    )["confirmed_qty"]
                    == 84
                )
                exported = request(
                    client, "GET", f"/api/plans/{plan['id']}/export", raw=True
                )
                export_rows = list(
                    csv.DictReader(io.StringIO(exported.decode("utf-8-sig")))
                )
                assert len(export_rows) == 5
                assert (
                    next(row for row in export_rows if row["product_id"] == "p1")[
                        "confirmed_qty"
                    ]
                    == "84"
                )
                assert all(
                    row["source"] == "synthetic" and row["model_id"] == model["id"]
                    for row in export_rows
                )
                counts_before_reopen = table_counts()
            # End the app lifespan and all request connections, then reopen its DB.
            # This is in-process persistence evidence, not a browser/OS restart.
            assert main_db.is_file()
            with TestClient(app) as client:
                assert (
                    request(client, "POST", "/api/datasets", body=import_body)
                    == dataset
                )
                assert request(
                    client, "GET", f"/api/datasets/{dataset['id']}/models"
                ) == [model]
                assert (
                    request(
                        client,
                        "POST",
                        f"/api/datasets/{dataset['id']}/train",
                        body=train_body,
                    )
                    == model
                )
                assert (
                    request(
                        client,
                        "POST",
                        "/api/forecasts",
                        body=forecast_body(dataset, model_id=model["id"]),
                    )
                    == ml
                )
                assert request(client, "GET", f"/api/plans/{plan['id']}") == confirmed
                assert (
                    request(
                        client,
                        "POST",
                        f"/api/plans/{plan['id']}/confirm",
                        body=confirmation,
                    )
                    == confirmed
                )
                revisions = request(client, "GET", f"/api/plans/{plan['id']}/revisions")
                assert len(revisions) == 2 and revisions[-1] == confirmed
                assert (
                    request(client, "GET", f"/api/plans/{plan['id']}/export", raw=True)
                    == exported
                )
                assert table_counts() == counts_before_reopen
        report = {
            "report_version": "synthetic-inprocess-api-replay-1",
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
            "verification_surface": "FastAPI TestClient; in-process API and app lifespan close/reopen; no browser, hosted URL or OS restart",
            "warning": "Synthetic fixture only. Metrics are reproducibility evidence, not real bakery accuracy, independent validation, demand recovery or economic benefit.",
            "source": source,
            "fixture": {
                "path": "fixtures/synthetic_demo.csv",
                "sha256": digest(fixture_bytes),
                "canonical_lf_sha256": digest(
                    csv_text.replace("\r\n", "\n").replace("\r", "\n").encode()
                ),
                "imported_csv_text_sha256": digest(csv_text.encode()),
                "rows": len(rows),
                "date_start": min(row["date"] for row in rows),
                "date_end": max(row["date"] for row in rows),
                "observed_dates": len({row["date"] for row in rows}),
                "product_ids": sorted({row["product_id"] for row in rows}),
            },
            "request": {
                "training_cutoff": CUTOFF,
                "forecast_cutoff": CUTOFF,
                "target_date": TARGET,
                "horizon_days": 2,
                "mode": "replay",
                "fixed_business_date_for_validation": str(BUSINESS_DATE),
                "import_metadata": {
                    key: value
                    for key, value in import_body.items()
                    if key != "csv_text"
                },
            },
            "dataset": dataset,
            "model": model,
            "holdout_evaluation": model["evaluation"],
            "forecast": {
                "ml": ml,
                "b1": b1,
                "same_input_and_baseline_dates_verified": True,
                "target_actual_available": False,
                "target_mae": None,
            },
            "draft_plan": plan,
            "confirmed_plan": confirmed,
            "invalid_balance": invalid,
            "untrained_fallback": fallback_evidence,
            "persistence": {
                "app_lifespan_reopened": True,
                "same_dataset_model_forecast_plan_verified": True,
                "idempotent_retries_verified": True,
                "revision_count": len(revisions),
                "table_counts": counts_before_reopen,
            },
            "export": {
                "file": "confirmed-plan.csv",
                "sha256": digest(exported),
                "row_count": len(export_rows),
                "exact_bytes_after_reopen_verified": True,
                "rows": export_rows,
            },
            "api_calls": audit,
        }
    assert source["source_file_sha256"] == source_provenance()["source_file_sha256"]
    with (output_dir / "confirmed-plan.csv").open("xb") as handle:
        handle.write(exported)
    with (output_dir / "api-replay.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="New non-existing directory under an existing parent; default: a unique ignored test-results directory",
    )
    args = parser.parse_args(argv)
    try:
        if args.output_dir is None:
            parent = ROOT / "test-results"
            parent.mkdir(exist_ok=True)
            args.output_dir = parent / ("api-replay-" + uuid.uuid4().hex)
        report = replay(args.output_dir)
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Replay refused or failed: {exc}\n")
    metrics = report["holdout_evaluation"]
    print(
        json.dumps(
            {
                "output": str(args.output_dir / "api-replay.json"),
                "git_head": report["source"]["git_head"],
                "fixture_sha256": report["fixture"]["sha256"],
                "holdout": {
                    key: metrics[key]
                    for key in (
                        "b1_mae",
                        "ml_mae",
                        "paired_n",
                        "coverage",
                        "holdout_start",
                        "holdout_end",
                    )
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
