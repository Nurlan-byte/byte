import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks/open-sales/scripts"))
import evaluate_receipts as replay  # noqa: E402


class TestReceiptQuality(unittest.TestCase):
    def test_holdout_cutoff_and_trailing_missing_sensitivity(self):
        target = replay.day("2025-09-15")
        origin = target - 2
        history = {("outlet", "tea"): {origin-1: 4, origin: 0, origin+1: 99, target: 2}}
        for pos, expected in [(False, [4, 0]), (True, [4])]:
            with self.subTest(pos=pos), patch.object(replay.chronos_order, "forecast_order",
                                                     return_value={"tea": {"forecast_mean": 1}}) as forecast:
                scores = replay.paired_rows(history, {"outlet": ["tea"]}, [replay.iso(target)] * 2,
                                            [], 2, pos_history=pos)
                data, target_date, cutoff_date, _ = forecast.call_args.args
                self.assertEqual([r["item_line_count"] for r in data["rows"]], expected)
                self.assertEqual((target_date.toordinal(), cutoff_date.toordinal()), (target, origin))
                self.assertEqual(scores[0]["actual"], 2)

    def test_report_detects_changed_gate_category_and_original_reference(self):
        spec = json.loads(replay.SPEC.read_text(encoding="utf-8"))
        report = json.loads((replay.BENCH / "results/receipt_quality.json").read_text(encoding="utf-8"))
        replay.verify_report(report, spec)
        broken = copy.deepcopy(report)
        broken["gate"]["passed"] = not broken["gate"]["passed"]
        with self.assertRaisesRegex(ValueError, "Decision"):
            replay.verify_report(broken, spec)
        broken = copy.deepcopy(report)
        broken["assortments"]["snacks"]["SHOP001"][0] = broken["assortments"]["cafe"]["SHOP001"][0]
        with self.assertRaisesRegex(ValueError, "disjoint"):
            replay.verify_report(broken, spec)
        broken = copy.deepcopy(report)
        week = broken["original_week"]
        for group in (week["all"], week["positive"], week["by_outlet"]["SHOP001"]):
            group["legacy_ae_sum"] += 1
        with self.assertRaisesRegex(ValueError, "regression was not reproduced"):
            replay.verify_report(broken, spec)
