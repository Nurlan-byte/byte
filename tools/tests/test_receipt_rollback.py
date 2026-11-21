import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks/open-sales/scripts"))
import evaluate_receipt_rollback as replay  # noqa: E402


class TestReceiptRollback(unittest.TestCase):
    def test_verify_rejects_different_observations_even_with_same_decision(self):
        spec = json.loads(replay.POLICY.read_text(encoding="utf-8"))
        report = json.loads((replay.BENCH / "results/receipt_rollback.json").read_text(encoding="utf-8"))
        replay.verify(report, spec)
        for group, field, wrong in [
            ("cafe", "start", "2025-09-04"), ("snacks", "end", "2025-09-28"),
            ("cafe", "data_sha256", "0" * 64), ("snacks", "n", 729),
            ("uci", "actual_sum", 939), ("uci", "data_sha256", "1" * 64),
            ("uci", "baseline_covered", 143), ("uci", "n", 138),
            ("original_week", "zero_actuals", 58),
        ]:
            with self.subTest(group=group, field=field):
                broken = copy.deepcopy(report)
                scores = broken["original_week"] if group == "original_week" else broken["holdout"][group]
                scores[field] = wrong
                if field == "n":
                    scores.update(expected=wrong, baseline_covered=wrong, candidate_covered=wrong)
                    for model in ("baseline", "candidate"):
                        scores[model + "_ae_sum"] = scores[model + "_mae"] * wrong
                self.assertEqual(broken["gate"], replay.decision(broken["original_week"], broken["holdout"], spec["gate"]))
                with self.assertRaisesRegex(ValueError, group):
                    replay.verify(broken, spec)

    def test_training_ends_on_monday_and_prediction_sees_only_its_issue_history(self):
        from datetime import date

        monday = date(2025, 9, 22).toordinal()
        source = {("outlet", "tea"): {monday: 3, monday+1: 0, monday+2: 4, monday+4: 999}}
        rows = [{"site": "outlet", "sku": "tea", "origin": replay.iso(monday+i),
                 "date": replay.iso(monday+i+2)} for i in [1, 2]]
        with patch.object(replay, "train_model") as train, patch.object(replay, "prepare_forecast") as prepare:
            prepare.return_value.predict.return_value = {"forecast_mean": 1.25, "method": "ml"}
            replay.add_legacy_forecasts(source, {"outlet": ["tea"]}, rows)
        train.assert_called_once()
        train_data, train_day = train.call_args.args
        self.assertEqual(train_day.toordinal(), monday)
        self.assertEqual([r["item_line_count"] for r in train_data["rows"]], [3])
        for i, call in enumerate(prepare.call_args_list, 1):
            _, projection, target, cutoff = call.args
            self.assertEqual(target.toordinal() - cutoff.toordinal(), 2)
            self.assertEqual([r["item_line_count"] for r in projection["rows"]], [3, 0, 4][:i+1])

    def test_original_match_alone_does_not_release_the_rollback(self):
        spec = json.loads(replay.POLICY.read_text(encoding="utf-8"))
        week = {"n": 126, "expected": 126, "candidate_mae": 1, "published_legacy_mae": 1,
                "maximum_abs_prediction_difference": 0}
        cohorts = {k: {"expected": 10, "n": 10, "candidate_mae": 0.98, "baseline_mae": 1,
                       "candidate_bias_pct": -0.1, "baseline_bias_pct": 0} for k in spec["gate"]["all_required"]}
        self.assertTrue(replay.decision(week, cohorts, spec["gate"])["passed"])
        broken = copy.deepcopy(cohorts)
        broken["uci"].update(candidate_mae=1.1, candidate_bias_pct=-3, n=9)
        result = replay.decision(week, broken, spec["gate"])
        self.assertFalse(result["passed"])
        self.assertEqual(set(result["reasons"]), {"uci:mae", "uci:volume_bias", "uci:coverage"})
