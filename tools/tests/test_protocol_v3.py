"""Synthetic protocol audit integrity and shared-cohort regression checks."""
import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from tools.protocol_v3 import backtest, candidates, gen, reference_v2


class ProtocolAuditTests(unittest.TestCase):
    def test_frozen_v2_integrity(self):
        path = candidates.HERE / "reference_v2.py"
        provenance = json.loads((candidates.HERE / "reference_v2_provenance.json").read_text())
        self.assertEqual(provenance["source_commit"], "3cbfbfae14bfd617d162cbe955fd8090c40b53e0")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "b9f126601244c5ad0bab32a870f8c4a6e5348188089a5a613229c26445272935")
        self.assertEqual(provenance["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(reference_v2.ALPHA, 100.0)

    def test_v2_candidate_isolated_from_current_fit_and_predict(self):
        samples = candidates.prepare({}, 200)
        with patch.object(candidates.core, "_fit", side_effect=AssertionError("current fit used")), \
             patch.object(candidates.core, "_predict", side_effect=AssertionError("current predict used")):
            result = candidates.run_candidate("v2", samples, 83, range(88, 90))
        self.assertEqual(len(result), 10)
        self.assertTrue(all(p >= 0 for p, _, _, _ in result))

    def test_excluded_labels_remain_in_common_target_cohort(self):
        kw = {"stockout_mode": "exclude", "stockout_slack": 0.1}
        ds, truth = gen.make_dataset(seed=200, **kw)
        samples = candidates.prepare(kw, 200)
        pairs = candidates.run_candidate("B1", samples, 83, backtest.EVAL)
        targets = backtest.target_rows(ds)
        self.assertEqual(len(pairs), len(targets))
        self.assertGreater(sum(y is None for _, _, y, _ in pairs), 0)
        expected = sorted(r["_demand"] for r in targets.values())
        self.assertEqual(sorted(d for _, _, _, d in pairs), expected)
        artifact, forecasts = backtest.forecast_rows(ds, reference_v2)
        result = backtest.score(ds, truth, artifact, forecasts)
        self.assertEqual(result["n"], len(pairs))
        self.assertEqual(result["excluded_label_target_n"], sum(y is None for _, _, y, _ in pairs))

    def test_b1_cohort_and_predictions_match_integrated(self):
        kw = {"stockout_mode": "exclude", "stockout_slack": 0.1}
        ds, _ = gen.make_dataset(seed=200, **kw)
        _, forecasts = backtest.forecast_rows(ds, backtest.core)
        _, historical = backtest.forecast_rows(ds, reference_v2)
        samples = candidates.prepare(kw, 200)
        pairs = candidates.run_candidate("B1", samples, 83, backtest.EVAL)
        keys = [((gen.START + timedelta(days=t)).isoformat(), product)
                for t in backtest.EVAL for product, days in samples.items()
                if t in days and days[t][3] is not None]
        self.assertEqual(len(pairs), len(keys))
        for (pred, b1, _, demand), key in zip(pairs, keys):
            self.assertEqual(pred, b1)
            self.assertEqual(b1, forecasts[key]["b1_mean"])
            self.assertEqual(b1, historical[key]["b1_mean"])
            self.assertEqual(demand, backtest.target_rows(ds)[key]["_demand"])

    def test_training_never_includes_missing_labels(self):
        samples = candidates.prepare({"stockout_mode": "exclude"}, 201)
        original = candidates.reference_v2._fit
        seen = []
        def fit(rows):
            self.assertTrue(all(r["value"] is not None for r in rows))
            seen.extend(rows)
            return original(rows)
        with patch.object(candidates.reference_v2, "_fit", side_effect=fit):
            candidates.run_candidate("v2", samples, 83, range(88, 90))
        self.assertTrue(seen)

    def test_conditional_mean_includes_large_order_mixture(self):
        ds, truth = gen.make_dataset(seed=204, outlier_prob=0.02)
        for day in (0, 88, 125):
            date = gen.START + timedelta(days=day)
            mu = 30 * gen.WEEKLY[date.weekday()]
            self.assertAlmostEqual(truth[(date.isoformat(), "p1")], mu * 1.06 + 0.4)
        # Changing the recorded truth calculation must not change generated rows.
        fingerprint = hashlib.sha256(json.dumps(ds["rows"], sort_keys=True).encode()).hexdigest()
        self.assertEqual(fingerprint, "650fa8beb82b116dd5fa0dea603a4c3aebd84008275860bdeeb95826627509b1")

    def test_missing_and_closed_targets_are_excluded(self):
        ds, _ = gen.make_dataset(seed=200, closed_prob=0.1, missing_prob=0.1)
        targets = backtest.target_rows(ds)
        self.assertLess(len(targets), 190)
        self.assertTrue(all(r["is_open"] for r in targets.values()))


if __name__ == "__main__":
    unittest.main()
