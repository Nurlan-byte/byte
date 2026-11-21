from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks/open-sales/scripts"))
from receipt_candidates import estimate


class TestReceiptCandidates(unittest.TestCase):
    def test_cutoff_includes_observed_zero_and_excludes_future(self):
        origin = 170
        target = origin + 2
        # Target-weekday observations at 165 and 158. Day 142 is outside the 28-day window.
        history = {142: 20, 158: 5, 165: 2, 166: 0, 167: 0, 168: 0, 169: 0, 170: 0,
                   171: 1000, 172: 1000}
        self.assertEqual(estimate("recent_mean", 3, history, origin, target), 1)
        self.assertEqual(estimate("blended_mean", 3, history, origin, target), 2)
        pooled = (7 + 7 * (27 / 8)) / (2 + 7)
        self.assertAlmostEqual(estimate("blended_weekday", 3, history, origin, target), (3 + pooled) / 2)

    def test_insufficient_history_and_missing_forecast(self):
        history = {day: 0 for day in range(201, 207)}
        history.update({199: None, 200: -3, 207: 90})
        for name in ("recent_mean", "blended_mean", "blended_weekday"):
            with self.subTest(candidate=name):
                self.assertEqual(estimate(name, 2.5, history, 206, 208), 2.5)
                self.assertIsNone(estimate(name, None, history, 207, 209))

    def test_unrecognized_estimator_is_an_error(self):
        with self.assertRaises(ValueError):
            estimate("unregistered", 0, {}, 206, 208)
