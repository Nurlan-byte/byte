"""Quality gates must reject a locally worse candidate even when its pooled score improves."""
import json
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks/open-sales/scripts"))
from evaluate_volume_quality import FORMATS, PROTOCOL_FILE, gate, metrics, verify_report
from volume_candidates import correct_bias, previous_examples


class TestVolumeQuality(unittest.TestCase):
    def test_committed_report_and_stale_or_tampered_results(self):
        path = PROTOCOL_FILE.parent / "results/volume_quality.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        verify_report(report)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            verify_report({**report, "fingerprints": {}})
        with self.assertRaisesRegex(ValueError, "gate"):
            verify_report({**report, "gate": {**report["gate"], "passed": not report["gate"]["passed"]}})

    def test_dates_hidden_during_residual_calibration(self):
        values = np.arange(35, dtype=np.float32)
        values[-2] = np.nan
        examples = previous_examples(values, horizon=2)
        self.assertEqual(len(examples), 14)
        for history_length, label in examples:
            last_known = history_length - 1
            self.assertEqual(last_known + 2, int(label))
        self.assertEqual(previous_examples(np.full(35, np.nan), 2), [])

    def test_shrunk_correction_needs_consistent_residuals(self):
        self.assertEqual(correct_bias(8, [10] * 14), 10)
        self.assertEqual(correct_bias(8, [-2] * 14), 7)
        self.assertEqual(correct_bias(8, [-4] * 7 + [4] * 7), 8)
        self.assertEqual(correct_bias(0.1, [-1] * 14), 0)
        self.assertEqual(correct_bias(8, [2] * 13), 8)

    def test_paired_volume_bias_and_absolute_error(self):
        summary = metrics([{"actual": 0, "baseline": 1, "candidate": 0},
                           {"actual": 2, "baseline": 1, "candidate": 1}])
        self.assertEqual(summary["baseline_mae"], 1)
        self.assertEqual(summary["candidate_mae"], 0.5)
        self.assertEqual(summary["baseline_bias_pct"], 0)
        self.assertEqual(summary["candidate_bias_pct"], -50)

    def test_gate_blocks_known_bad_and_accepts_uniform_improvement(self):
        expected = {"baseline_mae": 10, "candidate_mae": 9, "baseline_bias_pct": -2, "candidate_bias_pct": 0}
        sources = {name: dict(expected) for name in [*FORMATS, "bakery_new_skus"]}
        limits = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))["gate"]
        self.assertTrue(gate(sources, limits)["passed"])
        sources["cafe"].update(candidate_mae=10.1, candidate_bias_pct=4.5)
        verdict = gate(sources, limits)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasons"], ["cafe:mae", "cafe:volume_bias"])
        # A missing difficult source must never produce a passing report.
        del sources["cafe"]
        with self.assertRaises(ValueError):
            gate(sources, limits)
