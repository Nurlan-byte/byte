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


def receipt_report():
    path = Path(__file__).resolve().parents[2] / "benchmarks/open-sales/results/receipt_quality.json"
    return json.loads(path.read_bytes())


def track_using_cli(source, database):
    process = subprocess.run(
        [sys.executable, "-m", "tools.track_forecast", str(source), "--database", str(database)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(process.stdout)


class ForecastTracking(unittest.TestCase):
    def test_new_stores_are_logged_as_independent_confirmation(self):
        data = report()
        gate = data["gate"]
        development, holdout = (data["partitions"][p] for p in ("development", "holdout"))
        data["partitions"] = {
            "regression": {**development, "gate": gate},
            "confirmation": {"sources": {"another_store": holdout["sources"]["cafe"]}, "gate": gate},
        }
        data["gate"] = {"passed": False, "reasons": ["regression", "confirmation"]}
        fields, metrics = summary_fields(data)
        self.assertEqual(fields["confirmation.gate.passed"], "False")
        self.assertIn("confirmation.another_store.baseline_mae", metrics)
        self.assertNotIn("holdout.mean_relative_mae_improvement_pct", metrics)
        data["partitions"]["confirmation"]["sources"] = development["sources"]
        with self.assertRaisesRegex(ValueError, "distinct sources"):
            summary_fields(data)

    def test_sparse_experiment_tracks_coverage_and_errors_against_b1(self):
        data = report()
        record = data["partitions"]["holdout"]["sources"]["cafe"]
        record.update(b1_mae=0.75, coverage_pct=100.0, baseline_bias_units=3, candidate_bias_units=-2, b1_bias_units=1)
        _, metrics = summary_fields(data)
        for name in ("b1_mae", "coverage_pct", "baseline_bias_units", "candidate_bias_units", "b1_bias_units"):
            self.assertEqual(metrics[f"holdout.cafe.{name}"], record[name])
        record["coverage_pct"] = 101
        with self.assertRaises(ValueError):
            summary_fields(data)

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

    def test_inconsistent_original_week_groups_are_rejected_before_storage(self):
        for key, message in (("n", "counts"), ("legacy_ae_sum", "errors")):
            with self.subTest(key=key), TemporaryDirectory() as folder:
                data = receipt_report()
                data["original_week"]["zero"][key] += 1
                source = Path(folder) / "report.json"
                source.write_text(json.dumps(data), encoding="utf-8")
                database = Path(folder) / "tracking.sqlite3"
                with self.assertRaisesRegex(ValueError, message):
                    record_run(source, database)
                self.assertFalse(database.exists())

    def test_perfect_reference_has_no_relative_error_denominator(self):
        data = receipt_report()
        for group in ("all", "zero", "positive"):
            data["original_week"][group]["legacy_ae_sum"] = 0
        _, metrics = summary_fields(data)
        self.assertEqual(metrics["original_week.all.legacy_mae"], 0.0)
        self.assertFalse(any("vs_legacy_pct" in key for key in metrics))

    @unittest.skipUnless(find_spec("mlflow") is not None, "MLflow runs in the tracking environment")
    def test_rejected_forecast_is_recorded_as_a_completed_experiment(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            file = path / "report.json"
            file.write_text(json.dumps(report()), encoding="utf-8")
            database = path / "tracking.sqlite3"
            saved = track_using_cli(file, database)
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

    @unittest.skipUnless(find_spec("mlflow") is not None, "MLflow runs in the tracking environment")
    def test_receipt_root_cause_is_retrievable_separately_from_purchase_quality(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            database = path / "tracking.sqlite3"
            source = path / "report.json"
            source.write_text(json.dumps(report()), encoding="utf-8")
            purchase = track_using_cli(source, database)
            source.write_text(json.dumps(receipt_report()), encoding="utf-8")
            receipt = track_using_cli(source, database)
            with closing(sqlite3.connect(database)) as db:
                experiments = dict(db.execute("SELECT e.name, r.run_uuid FROM experiments e JOIN runs r USING(experiment_id)"))
                self.assertEqual(experiments, {"BakePlan purchase forecasts": purchase["run_id"],
                                               "BakePlan receipt counts": receipt["run_id"]})
                self.assertIsNone(db.execute(
                    "SELECT 1 FROM metrics WHERE run_uuid=? AND key LIKE 'original_week.%'", (purchase["run_id"],),
                ).fetchone())
                params = dict(db.execute("SELECT key, value FROM params WHERE run_uuid=?", (receipt["run_id"],)))
                metrics = dict(db.execute("SELECT key, value FROM metrics WHERE run_uuid=?", (receipt["run_id"],)))
            self.assertEqual(params["original_week.start"], "2025-09-24")
            self.assertEqual(params["original_week.end"], "2025-09-30")
            self.assertEqual([metrics[f"original_week.{g}.n"] for g in ("all", "zero", "positive")],
                             [126, 59, 67])
            for model, expected in (("baseline", 4.27), ("candidate", 0.22), ("legacy", 0.0)):
                self.assertAlmostEqual(metrics[f"original_week.all.{model}_mae_change_vs_legacy_pct"],
                                       expected, places=2)
            zero = metrics["original_week.zero.baseline_ae_sum"] - metrics["original_week.zero.legacy_ae_sum"]
            total = metrics["original_week.all.baseline_ae_sum"] - metrics["original_week.all.legacy_ae_sum"]
            self.assertAlmostEqual(100 * zero / total, 97.65, places=2)
            self.assertEqual(receipt["metrics_verified"], 40)
