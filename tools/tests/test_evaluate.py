import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tools.evaluate import baseline, evaluate, features, read_rows


def row(day, sold=10, product="001", **changes):
    return {"date": day, "product_id": product, "sold": sold, "is_open": True,
            "stockout_status": "none", "opening": 0, **changes}


class EvaluationTests(unittest.TestCase):
    def test_pipeline_on_later_synthetic_dates(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("Optional scikit-learn dependencies not installed")
        start = date(2026, 7, 1)
        rows = [row(start + timedelta(days=i), 10 + i % 7, product)
                for i in range(70) for product in ("001", "002")]
        report = evaluate(rows, "synthetic", "fixture-sha", start + timedelta(days=56))
        self.assertEqual(report["status"], "evaluated")
        self.assertEqual(report["train_n"], 112)
        self.assertEqual(report["holdout_n"], 28)
        self.assertEqual(report["metrics"]["Poisson"]["n"], 28)
        self.assertTrue(math.isfinite(report["metrics"]["Poisson"]["mae"]))

    def test_calendar_lag_preserves_missing_day(self):
        target = row(date(2026, 10, 12))
        index = {("001", date(2026, 10, 4)): row(date(2026, 10, 4), 999)}
        self.assertTrue(math.isnan(features(target, index)[2]))

    def test_baseline_exact_week_and_four_week_mean(self):
        target = row(date(2026, 10, 12))
        history = [row(date(2026, 8, 17) + timedelta(days=7*i), (i+1)*10) for i in range(8)]
        index = {(r["product_id"], r["date"]): r for r in history}
        self.assertEqual(baseline(target, index, "B0"), 80)
        self.assertEqual(baseline(target, index, "B1"), 65)
        index[("001", date(2026, 10, 5))]["stockout_status"] = "occurred"
        self.assertIsNone(baseline(target, index, "B0"))
        self.assertEqual(baseline(target, index, "B1"), 55)
        del index[("001", date(2026, 9, 21))]
        self.assertEqual(baseline(target, index, "B1"), 47.5)

    def test_target_and_future_cannot_change_lags(self):
        target = row(date(2026, 10, 12))
        past = row(date(2026, 10, 5), 0)
        index = {("001", past["date"]): past}
        expected = features(target, index)
        index[("001", target["date"])] = row(target["date"], 999999)
        index[("001", date(2026, 10, 19))] = row(date(2026, 10, 19), 999999)
        self.assertEqual(features(target, index)[:3], expected[:3])
        self.assertEqual(baseline(target, index, "B0"), 0)

    def test_insufficient_history_refuses_before_model_import(self):
        result = evaluate([row(date(2026, 10, 1))], "synthetic", "test-sha", date(2026, 10, 2))
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["source"], "synthetic")

    def test_csv_strict_validation_and_id(self):
        header = "date,product_id,is_open,opening,produced,sold,waste,closing,other_out,stockout_status\n"
        valid = "2026-10-01,001,1,0,100,80,20,0,0,none\n"
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2]) as directory:
            path = Path(directory) / "history.csv"
            path.write_text(header + valid, encoding="utf-8")
            self.assertEqual(read_rows(path)[0]["product_id"], "001")
            for invalid in (valid + valid, valid.replace(",100,", ",100.0,"), valid.replace(",80,", ",81,"), valid.replace(",80,", ",NaN,")):
                path.write_text(header + invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_rows(path)


    def test_documented_cli_creates_nested_output_directory(self):
        import json
        import subprocess
        import sys
        root = Path(__file__).parents[2]
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / "absent" / "private" / "local-report.json"
            self.assertFalse(output.parent.exists())
            result = subprocess.run([sys.executable, str(root / "tools/evaluate.py"), str(root / "fixtures/synthetic_demo.csv"), "--source", "synthetic", "--split-date", "2026-09-28", "--output", str(output)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "evaluated")


if __name__ == "__main__":
    unittest.main()
