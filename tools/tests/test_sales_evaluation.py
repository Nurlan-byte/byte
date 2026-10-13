import json
import math
import unittest
from datetime import date, timedelta

from backend.forecasting import baseline
from tools.evaluate_sales import evaluate_sales, metric, sales_features
from tools.sales_adapter import SalesLine, SalesObservation

ITEMS = ("Bread", "Pastry", "Medialuna", "Cookies", "Cake")


def fixture_lines():
    lines = []
    start = date(2017, 1, 1)
    for offset in range(68):
        day = start + timedelta(days=offset)
        for item in ITEMS:
            if day == date(2017, 2, 8) and item == "Cake":
                continue
            count = 2 + offset % 3
            lines.extend(
                SalesLine(day, item, f"{offset}-{item}-{n}") for n in range(count)
            )
    return lines


class SalesEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = fixture_lines()
        cls.args = dict(
            dataset_sha="synthetic-unit-fixture",
            history_mode="rolling-one-day-ahead",
            source="synthetic-sales-only",
            test_end=date(2017, 3, 9),
        )
        cls.report = evaluate_sales(cls.lines, **cls.args)

    def test_calendar_lags_and_cutoff(self):
        target = SalesObservation(date(2017, 3, 8), "Bread", 9999, False)
        previous = SalesObservation(date(2017, 3, 1), "Bread", 7, False)
        wrong_day = SalesObservation(date(2017, 2, 28), "Bread", 800, False)
        index = {(o.item, o.date): o for o in (target, previous, wrong_day)}
        feature = sales_features(target, index, date(2017, 3, 7))
        self.assertEqual(feature[:3], ["Bread", 2, 7])
        self.assertTrue(math.isnan(feature[3]))
        self.assertTrue(math.isnan(sales_features(target, index, date(2017, 2, 28))[2]))

    def test_zero_safe_metrics(self):
        self.assertIsNone(metric([(0, 3.0), (0, 0.0)])["wape"])
        self.assertEqual(metric([(0, 3.0), (0, 0.0)])["mae"], 1.5)
        self.assertIsNone(metric([])["mae"])
        self.assertEqual(metric([(2, 3.0), (2, 1.0)])["wape"], 0.5)

    def test_shared_baseline_parity_without_operational_fields(self):
        observations = [
            SalesObservation(
                date(2017, 1, 2) + timedelta(days=7 * i), "Bread", 10 * (i + 1), False
            )
            for i in range(8)
        ]
        rows = [
            {"date": o.date, "product_id": o.item, "item_line_count": o.item_line_count}
            for o in observations
        ]
        target = date(2017, 2, 27)
        b0 = baseline(
            rows,
            product_id="Bread",
            target_date=target,
            cutoff=target - timedelta(days=1),
            method="B0",
            eligible=lambda _: True,
            value_key="item_line_count",
        )
        b1 = baseline(
            rows,
            product_id="Bread",
            target_date=target,
            cutoff=target - timedelta(days=1),
            method="B1",
            eligible=lambda _: True,
            value_key="item_line_count",
        )
        self.assertEqual(b0["forecast_mean"], 80)
        self.assertEqual(b1["forecast_mean"], 65)
        self.assertEqual(b1["n"], 4)
        primary = self.report["policies"]["observed-store-zero"]
        record = primary["periods"]["test"]["predictions"][0]
        item, day = record["item"], date.fromisoformat(record["date"])
        actual_past = [
            r
            for r in primary["periods"]["validation"]["predictions"]
            if r["item"] == item and r["date"] == (day - timedelta(days=7)).isoformat()
        ]
        self.assertEqual(record["predictions"]["B0"], actual_past[0]["actual"])

    def test_identical_intersection_and_sensitivity_coverage(self):
        self.assertEqual(self.report["status"], "evaluated")
        for policy in self.report["policies"].values():
            for period in policy["periods"].values():
                for method in ("Poisson", "B0", "B1"):
                    self.assertEqual(
                        period["pooled"][method]["n"], period["comparison_n"]
                    )
                for record in period["predictions"]:
                    self.assertEqual(
                        record["compared"],
                        all(
                            value is not None and math.isfinite(value)
                            for value in record["predictions"].values()
                        ),
                    )
        primary = self.report["policies"]["observed-store-zero"]["periods"][
            "validation"
        ]
        sensitivity = self.report["policies"]["missing"]["periods"]["validation"]
        self.assertEqual(primary["absent_item_unknown_excluded_n"], 0)
        self.assertEqual(sensitivity["absent_item_unknown_excluded_n"], 1)
        self.assertLess(sensitivity["comparison_n"], primary["comparison_n"])

    def test_deterministic_report(self):
        import hashlib

        self.assertEqual(self.report["source"], "synthetic-sales-only")
        protocol_payload = {
            key: self.report[key]
            for key in ("protocol", "selected_items", "source", "history_mode")
        }
        expected_hash = hashlib.sha256(
            json.dumps(
                protocol_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(self.report["protocol_sha256"], expected_hash)
        repeated = evaluate_sales(self.lines, **self.args)
        self.assertEqual(
            json.dumps(self.report, sort_keys=True, allow_nan=False),
            json.dumps(repeated, sort_keys=True, allow_nan=False),
        )

    def test_later_observations_cannot_change_earlier_predictions_or_selection(self):
        future = date(2017, 3, 9)
        changed = self.lines + [
            SalesLine(future, "Bread", f"future-{i}") for i in range(200)
        ]
        report = evaluate_sales(changed, **self.args)
        self.assertEqual(report["selected_items"], self.report["selected_items"])
        for name, original in self.report["policies"].items():
            for period in ("validation", "test"):
                before = [
                    r
                    for r in original["periods"][period]["predictions"]
                    if r["date"] < future.isoformat()
                ]
                after = [
                    r
                    for r in report["policies"][name]["periods"][period]["predictions"]
                    if r["date"] < future.isoformat()
                ]
                self.assertEqual(before, after)

    def test_insufficient_shared_dates_refuses_comparison(self):
        sparse = [
            line
            for line in self.lines
            if line.date < date(2017, 2, 1)
            or date(2017, 2, 1) <= line.date <= date(2017, 2, 7)
            or date(2017, 3, 1) <= line.date <= date(2017, 3, 7)
        ]
        report = evaluate_sales(sparse, **self.args)
        self.assertEqual(report["status"], "refused")
        for policy in report["policies"].values():
            period = policy["periods"]["test"]
            self.assertEqual(period["status"], "refused")
            self.assertEqual(period["comparison_n"], 0)
            self.assertIsNone(period["pooled"]["Poisson"]["mae"])
            self.assertIn("diagnostic", period["metrics_interpretation"])

    def test_cli_hash_and_csv_errors_are_clean(self):
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=root) as directory:
            csv_path = Path(directory) / "bad.csv"
            output = Path(directory) / "report.json"
            csv_path.write_text("bad header\n", encoding="utf-8")
            command = [
                sys.executable,
                str(root / "tools/evaluate_sales.py"),
                "--csv",
                str(csv_path),
                "--output",
                str(output),
                "--history-mode",
                "rolling-one-day-ahead",
                "--source",
                "synthetic-sales-only",
            ]
            for suffix, expected in (
                (["--expected-sha256", "0" * 64], "SHA256 mismatch"),
                ([], "Expected corrected BreadBasket columns"),
            ):
                result = subprocess.run(
                    command + suffix, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(output.exists())

    def test_explicit_history_and_refusal(self):
        with self.assertRaises(ValueError):
            evaluate_sales(
                self.lines,
                dataset_sha="test",
                history_mode="rolling-one-day-ahead",
                source="unlabeled",
            )
        with self.assertRaises(ValueError):
            evaluate_sales(self.lines, dataset_sha="test", history_mode="fixed")
        result = evaluate_sales(self.lines[:5], **self.args)
        self.assertEqual(result["status"], "refused")
        self.assertNotIn("policies", result)


if __name__ == "__main__":
    unittest.main()
