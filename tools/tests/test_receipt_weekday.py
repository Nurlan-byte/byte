import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks/open-sales/scripts"))
from evaluate_receipt_weekday import BENCH, PROTOCOL, SPEC, verify, weekday_means  # noqa: E402


class BranchWeekdayForecast(unittest.TestCase):
    def test_report_rejects_an_artificial_improvement_on_the_original_week(self):
        report = json.loads((BENCH / "results/receipt_weekday.json").read_text(encoding="utf-8"))
        plan, source = (json.loads(path.read_text(encoding="utf-8")) for path in (PROTOCOL, SPEC))
        report["original_week"]["all"]["candidate_ae_sum"] = 0
        with self.assertRaisesRegex(ValueError, "breakdown"):
            verify(report, plan, source)
        row = report["fresh"]["cafe"]
        for key in ("n", "expected", "baseline_covered", "candidate_covered"):
            row[key] = 1
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            verify(report, plan, source)

    def test_only_jointly_observed_days_through_cutoff_affect_the_rate(self):
        known = {"a": {day: 1 for day in range(1, 58)},
                 "b": {day: 3 for day in range(1, 58)}}
        self.assertEqual(weekday_means(known, 56, 58), {"a": 1, "b": 3})
        known["a"][57] = 1000
        del known["b"][47]
        known["a"][47] = 1000
        self.assertEqual(weekday_means(known, 56, 58), {"a": 1, "b": 3})

    def test_weekday_uses_traffic_from_both_products(self):
        known = {"a": {day: 1 for day in range(1, 57)},
                 "b": {day: 7 if (58 - day) % 7 == 0 else 0 for day in range(1, 57)}}
        forecast = weekday_means(known, 56, 58)
        self.assertAlmostEqual(forecast["a"], (8 * 8 + 2) / (9 * 2))
        self.assertEqual(forecast["a"], forecast["b"])
        del known["b"]
        self.assertEqual(weekday_means(known, 56, 58), {})
