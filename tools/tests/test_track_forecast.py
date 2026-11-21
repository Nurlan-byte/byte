import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from importlib.util import find_spec

from tools.track_forecast import record_run, summary_fields


def report():
    scores = dict(n=5, actual_sum=10, baseline_mae=1.0, candidate_mae=2.0,
                  baseline_bias_pct=0.0, candidate_bias_pct=-3.0, data_sha256="f" * 64)
    return {"schema_version": 1, "candidate": "example", "baseline_protocol": "current",
            "gate": {"passed": False, "mean_relative_mae_improvement_pct": -100.0,
                     "reasons": ["mean_relative_mae"], "thresholds": {"mean_relative_mae_improvement_min_pct": 1.0}},
            "partitions": {split: {"sources": {"cafe": {**scores, "start": day, "end": day}}}
                           for split, day in (("development", "2025-01-01"), ("holdout", "2025-02-01"))}}


class ForecastTracking(unittest.TestCase):
    def test_holdout_cannot_include_the_last_development_date(self):
        data = report()
        data["partitions"]["holdout"]["sources"]["cafe"]["start"] = "2025-01-01"
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            summary_fields(data)

    def test_existing_business_database_is_untouched(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            database = path / "orders.sqlite3"
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE plans (id INTEGER)")
            file = path / "report.json"
            file.write_text(json.dumps(report()), encoding="utf-8")
            previous = database.read_bytes()
            with self.assertRaisesRegex(ValueError, "separate MLflow database"):
                record_run(file, database)
            self.assertEqual(database.read_bytes(), previous)

    @unittest.skipUnless(find_spec("mlflow") is not None, "MLflow runs in the tracking environment")
    def test_rejected_forecast_is_recorded_as_a_completed_experiment(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            file = path / "report.json"
            file.write_text(json.dumps(report()), encoding="utf-8")
            database = path / "tracking.sqlite3"
            process = subprocess.run(
                [sys.executable, "-m", "tools.track_forecast", str(file), "--database", str(database)],
                capture_output=True, text=True, check=True,
            )
            saved = json.loads(process.stdout)
            with closing(sqlite3.connect(database)) as db:
                status, = db.execute("SELECT status FROM runs WHERE run_uuid=?", (saved["run_id"],)).fetchone()
                improvement, = db.execute(
                    "SELECT value FROM metrics WHERE run_uuid=? AND key='holdout.mean_relative_mae_improvement_pct'",
                    (saved["run_id"],),
                ).fetchone()
            self.assertEqual(status, "FINISHED")
            self.assertEqual(improvement, -100.0)
            self.assertEqual(saved["gate"], "rejected")
            self.assertEqual(saved["metrics_verified"], 15)
