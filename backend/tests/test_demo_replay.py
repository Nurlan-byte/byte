"""Exact synthetic defense evidence; never contact a host or reuse a user DB."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import replay_demo


def test_exact_demo_api_replay_preserves_external_database(tmp_path, monkeypatch):
    existing_db = tmp_path / "user.sqlite3"
    sentinel = b"Never open, overwrite or migrate this external database"
    existing_db.write_bytes(sentinel)
    monkeypatch.setenv("BAKEPLAN_DB", str(existing_db))
    output = tmp_path / "new-evidence"
    report = replay_demo.replay(output)
    assert os.environ["BAKEPLAN_DB"] == str(existing_db)
    assert existing_db.read_bytes() == sentinel
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "new-evidence",
        "user.sqlite3",
    ]
    assert (
        json.loads((output / "api-replay.json").read_text(encoding="utf-8")) == report
    )
    fixture = report["fixture"]
    assert (
        fixture["sha256"]
        == hashlib.sha256(
            (replay_demo.ROOT / "fixtures" / "synthetic_demo.csv").read_bytes()
        ).hexdigest()
    )
    assert (
        fixture["canonical_lf_sha256"]
        == "3811e52ebd6b3bebebafc8b4c479446e8cf263cdd9d056303394778f00d985ce"
    )
    assert fixture["rows"] == 280 and fixture["observed_dates"] == 56
    assert report["request"]["target_date"] == "2026-10-13"
    assert report["request"]["training_cutoff"] == "2026-10-11"
    assert report["request"]["horizon_days"] == 2
    metrics = report["holdout_evaluation"]
    assert metrics["paired_n"] == metrics["coverage_denominator_n"] == 60
    assert metrics["coverage"] == 1.0
    assert metrics["b1_mae"] == pytest.approx(0.4166666666666667)
    assert metrics["ml_mae"] == pytest.approx(0.7672253798564735)
    assert metrics["ml_mae"] > metrics["b1_mae"]
    assert metrics["first_holdout_cutoff"] == "2026-09-28"
    assert metrics["holdout_start"] == "2026-09-30"
    assert metrics["holdout_end"] == "2026-10-11"
    for product, values in metrics["per_product"].items():
        assert values["paired_n"] == values["coverage_denominator_n"] == 12
        assert values["paired_dates"] == metrics["paired_dates"]
        assert values["coverage"] == 1
        if product != "p1":
            assert values["b1_mae"] == values["ml_mae"] == 0
    assert report["forecast"]["target_actual_available"] is False
    assert report["forecast"]["target_mae"] is None
    assert all(item["method"] == "ML" for item in report["forecast"]["ml"]["items"])
    p1 = next(
        item for item in report["confirmed_plan"]["items"] if item["product_id"] == "p1"
    )
    assert p1["forecast_mean"] == pytest.approx(29.7621242495623)
    assert p1["recommended_qty"] == 24 and p1["confirmed_qty"] == 84
    assert p1["batch_size"] == 12 and p1["max_daily_qty"] == 100
    assert report["invalid_balance"]["status"] == 422
    assert (
        report["invalid_balance"]["counts_before"]
        == report["invalid_balance"]["counts_after"]
    )
    assert report["untrained_fallback"]["separate_new_database"] is True
    assert report["untrained_fallback"]["counts"]["models"] == 0
    assert all(
        item["method"] == "B1"
        for item in report["untrained_fallback"]["forecast"]["items"]
    )
    assert report["persistence"]["app_lifespan_reopened"] is True
    assert report["persistence"]["revision_count"] == 2
    assert report["persistence"]["table_counts"]["confirmations"] == 1
    assert (
        report["export"]["sha256"]
        == hashlib.sha256((output / "confirmed-plan.csv").read_bytes()).hexdigest()
    )
    assert report["export"]["exact_bytes_after_reopen_verified"] is True
    assert len(report["source"]["git_head"]) == 40
    assert isinstance(report["source"]["working_tree_dirty"], bool)
    source_path = Path(replay_demo.__file__)
    assert (
        report["source"]["source_file_sha256"]["scripts/replay_demo.py"]
        == hashlib.sha256(source_path.read_bytes()).hexdigest()
    )
    assert "Synthetic fixture only" in report["warning"]
    assert "no browser" in report["verification_surface"]


@pytest.mark.parametrize("existing_kind", ["directory", "file", "dangling_symlink"])
def test_replay_refuses_any_existing_output_before_using_api(
    tmp_path, existing_kind, monkeypatch
):
    output = tmp_path / "existing-output"
    if existing_kind == "directory":
        output.mkdir()
        (output / "keep.txt").write_text("keep")
    elif existing_kind == "file":
        output.write_text("keep")
    else:
        try:
            output.symlink_to(tmp_path / "absent-target", target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation unavailable on this platform")
    monkeypatch.setattr(
        replay_demo,
        "source_provenance",
        lambda: pytest.fail("Should refuse before reading source or opening API"),
    )
    with pytest.raises(FileExistsError):
        replay_demo.replay(output)
    if existing_kind == "directory":
        assert (output / "keep.txt").read_text() == "keep"
    elif existing_kind == "file":
        assert output.read_text() == "keep"
    else:
        assert output.is_symlink() and not (tmp_path / "absent-target").exists()


def test_replay_refuses_optimized_python_before_creating_output(tmp_path):
    output = tmp_path / "optimized-evidence"
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-m",
            "scripts.replay_demo",
            "--output-dir",
            str(output),
        ],
        cwd=replay_demo.ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "evidence assertions are required" in result.stderr
    assert not output.exists()
