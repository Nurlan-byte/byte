"""Reproducible protocol checks on generated data, never real accuracy evidence."""

from copy import deepcopy
from datetime import date, timedelta
import unittest
import tempfile
import json
from pathlib import Path

from backend.tests.test_ml_core import fixture
from tools.evaluate_forward import evaluate, main


class ForwardEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture(100, products=("p1", "p2"))
        self.cutoff = date(2026, 3, 11)
        self.start = self.cutoff + timedelta(days=2)
        self.end = date(2026, 4, 10)

    def test_paired_dates_horizon_and_denominator(self):
        report = evaluate(self.data, self.cutoff, self.start, self.end)
        self.assertEqual(report["model_status"], "ready")
        self.assertEqual(report["horizon_days"], 2)
        self.assertEqual(report["aggregate"]["paired_n"], 58)
        self.assertEqual(report["aggregate"]["coverage"], 1.0)
        for product in report["products"].values():
            self.assertEqual(product["paired_n"], len(product["pairs"]))
            for pair in product["pairs"]:
                self.assertEqual(
                    date.fromisoformat(pair["date"])
                    - date.fromisoformat(pair["cutoff"]),
                    timedelta(days=2),
                )
        self.assertIn("unverified", report["independence"])

    def test_future_labels_do_not_change_frozen_training(self):
        original = evaluate(self.data, self.cutoff, self.start, self.end)
        changed = deepcopy(self.data)
        for row in changed["rows"]:
            if row["date"] > str(self.cutoff):
                row["sold"] *= 2
        report = evaluate(changed, self.cutoff, self.start, self.end)
        self.assertEqual(
            report["training_snapshot_hash"], original["training_snapshot_hash"]
        )
        self.assertEqual(
            report["aggregate"]["paired_n"], original["aggregate"]["paired_n"]
        )
        self.assertNotEqual(
            report["aggregate"]["ml_mae"], original["aggregate"]["ml_mae"]
        )

    def test_no_hidden_zero_for_missing_or_unfit_products(self):
        changed = deepcopy(self.data)
        changed["rows"] = [
            row
            for row in changed["rows"]
            if row["product_id"] == "p1" or row["date"] >= str(self.start)
        ]
        report = evaluate(changed, self.cutoff, self.start, self.end)
        self.assertEqual(report["products"]["p2"]["paired_n"], 0)
        self.assertIsNone(report["products"]["p2"]["ml_mae"])
        self.assertEqual(report["aggregate"]["coverage"], 0.5)

    def test_sales_cli_can_select_a_window_from_longer_history(self):
        data = fixture(100, target="item_line_count")
        text = "date,product_id,item_line_count\n" + "".join(
            f"{row['date']},{row['product_id']},{row['item_line_count']}\n"
            for row in data["rows"]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.csv"
            output = Path(directory) / "report.json"
            path.write_text(text, encoding="utf-8")
            main(
                [
                    str(path),
                    "--source",
                    "synthetic",
                    "--provenance",
                    "Generated test",
                    "--target",
                    "item_line_count",
                    "--training-cutoff",
                    str(self.cutoff),
                    "--start",
                    str(self.start),
                    "--end",
                    "2026-04-01",
                    "--output",
                    str(output),
                ]
            )
            report = json.loads(output.read_text("utf-8"))
            self.assertEqual(report["unit"], "item_line")
            self.assertEqual(report["end"], "2026-04-01")
            self.assertEqual(report["aggregate"]["paired_n"], 20)

    def test_refuse_label_fit_after_first_issue_cutoff(self):
        with self.assertRaises(ValueError):
            evaluate(self.data, self.cutoff, self.cutoff + timedelta(days=1), self.end)
        with self.assertRaises(ValueError):
            evaluate(self.data, self.cutoff, date(2027, 1, 1), date(2027, 2, 1))


if __name__ == "__main__":
    unittest.main()
