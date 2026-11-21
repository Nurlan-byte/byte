import copy
from datetime import date, timedelta
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "benchmarks/open-sales/scripts"
sys.path.insert(0, str(SCRIPTS))
import evaluate_sparse_tsb as experiment  # noqa: E402


class SparseTsbTests(unittest.TestCase):
    @unittest.skipUnless(find_spec("statsforecast"), "Offline StatsForecast environment required")
    def test_pinned_statsforecast_worker_on_known_prefixes(self):
        response = subprocess.run([sys.executable, str(SCRIPTS / "evaluate_sparse_tsb.py"), "--tsb-worker"],
                                  input=json.dumps([[0, 0, 1, 0, 1, 0], [0] * 28, [4] * 28]),
                                  text=True, capture_output=True, check=True)
        answer = json.loads(response.stdout)
        self.assertEqual(answer["statsforecast_version"], "2.0.3")
        self.assertAlmostEqual(answer["forecasts"][0], 0.1629)
        self.assertEqual(answer["forecasts"][1:], [0, 4])

    def test_changed_panel_does_not_reuse_frozen_benchmark(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "sales.csv"
            path.write_text("site,product,date,sold\nCA_1,FOODS_1,2016-05-01,3\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen input"):
                experiment.read_panel(path)

    def test_strictly_past_observed_zeros_and_independent_holdout(self):
        start = date(2020, 1, 1)
        days = [start + timedelta(days=i) for i in range(90)]
        history = {day: 0 for day in days}
        history[days[15]] = 3
        history[days[-1]] = 9999
        del history[days[21]]
        recent = experiment.recent_values(history, days[60])
        self.assertEqual(len(recent), 55)
        self.assertEqual(sum(recent), 3)
        self.assertTrue(experiment.eligible(recent))
        self.assertFalse(experiment.eligible([0, 2] * 20))
        self.assertFalse(experiment.eligible([0] * 27))
        dev, hold = experiment.split_dates(days)
        self.assertEqual((len(dev), len(hold)), (21, 28))
        self.assertLessEqual(dev[-1] + timedelta(days=2), hold[0])

    def test_sparse_candidate_preserves_dense_and_never_imputes_sales(self):
        self.assertEqual(experiment.point("tsb", 4, None, []), 4)
        self.assertEqual(experiment.point("half_cap_above_recent_mean", 4, 0, [0, 2]), 2.5)
        self.assertEqual(experiment.point("half_cap_tsb", 1, 3, [0, 2]), 1)

    def test_gate_requires_gain_without_local_regression_or_missing_evidence(self):
        record = {"candidate_mae": 0.8, "baseline_mae": 1, "candidate_bias_pct": 1,
                  "baseline_bias_pct": 5, "coverage_pct": 100,
                  "eligible": {"candidate_mae": 0.3, "baseline_mae": 0.4, "n": 35, "positive_days": 9}}
        groups = {name: copy.deepcopy(record) for name in experiment.GROUPS}
        self.assertTrue(experiment.decision(groups)["passed"])
        groups["vending"]["candidate_bias_pct"] = -9
        groups["m5_CA_1"]["eligible"]["positive_days"] = 4
        self.assertEqual(experiment.decision(groups)["reasons"], ["m5_CA_1:eligible_evidence", "vending:bias"])

    def test_checked_in_report_rejects_flipped_gate_and_forged_metrics(self):
        path = SCRIPTS.parent / "results/sparse_tsb.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        experiment.verify(original)
        for field in ("gate", "mae", "coverage", "statsforecast_version", "baseline_protocol"):
            changed = copy.deepcopy(original)
            group = changed["partitions"]["holdout"]["sources"]["vending"]
            if field == "gate":
                changed["gate"]["passed"] = not changed["gate"]["passed"]
            elif field == "mae":
                group["candidate_mae"] += 1
            elif field == "coverage":
                group["coverage_pct"] = 0
            else:
                changed[field] = "unknown-version"
            with self.assertRaises(ValueError):
                experiment.verify(changed)
