import importlib
from pathlib import Path
import tempfile
import unittest

exporter = importlib.import_module("benchmarks.open-sales.scripts.export_results")
verify = importlib.import_module("benchmarks.open-sales.scripts.verify_results")


class OpenSalesVerificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / "bakery"
        self.rows = [
            {"date": "2022-07-02", "site": "shop", "product": "bread", "cutoff": "2022-06-30",
             "training_cutoff": "2022-06-29", "actual": 2, "b1": "3", "bakeplan": "5", "method": "ML"},
            {"date": "2022-07-03", "site": "shop", "product": "roll", "cutoff": "2022-07-01",
             "training_cutoff": "2022-06-29", "actual": 0, "b1": "1", "bakeplan": "1", "method": "B1"},
        ]
        exporter.export("bakery", self.rows, self.root)

    def test_reports_worse_than_baseline_and_matches_all_paired_rows(self):
        result = verify.verify_sample("bakery", self.rows[::-1], self.folder)
        self.assertEqual((result["candidates"], result["scored"], result["ridge_count"]), (2, 2, 1))
        self.assertEqual((result["b1_mae"], result["bakeplan_mae"], result["error_reduction_pct"]), (1, 2, -100))

    def test_rejects_duplicates_missing_pairs_future_data_and_invalid_fallback(self):
        for rows, message in [
            (self.rows * 2, "duplicate"),
            (self.rows[:1], "keys differ"),
            ([dict(self.rows[0], cutoff="2022-07-01"), self.rows[1]], "cutoff mismatch"),
            ([dict(self.rows[0], training_cutoff="2022-07-01"), self.rows[1]], "cutoff mismatch"),
            ([dict(self.rows[0], method="B1"), self.rows[1]], "B1 fallback"),
            ([dict(self.rows[0], bakeplan="nan"), self.rows[1]], "invalid outcome or forecast"),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                verify.verify_sample("bakery", rows, self.folder)

    def test_rejects_altered_observations_and_public_predictions(self):
        with self.assertRaisesRegex(ValueError, "published units_observed differs"):
            verify.verify_sample("bakery", [dict(self.rows[0], actual=1), self.rows[1]], self.folder)
        records = exporter.load_rows(self.folder, "forecasts.csv")
        records[0]["bakeplan"] = "4.99"
        exporter.write_csv(self.folder / "forecasts.csv", records)
        with self.assertRaisesRegex(ValueError, "published forecast differs"):
            verify.verify_sample("bakery", self.rows, self.folder)

    def test_rejects_wrong_sign_and_wrong_score_for_one_product(self):
        for product, field, value in [("ALL", "error_reduction_pct", "100"), ("bread", "bakeplan_mae", "2")]:
            exporter.export("bakery", self.rows, self.root)
            metrics = exporter.load_rows(self.folder, "metrics.csv")
            next(row for row in metrics if row["product"] == product)[field] = value
            exporter.write_csv(self.folder / "metrics.csv", metrics)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                verify.verify_sample("bakery", self.rows, self.folder)
