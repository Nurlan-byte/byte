import copy
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "benchmarks/open-sales/scripts"
sys.path.insert(0, str(SCRIPTS))
import evaluate_sparse_bias as experiment  # noqa: E402


class TestBiasCap(unittest.TestCase):
    def test_signed_error_and_seven_known_pairs(self):
        d = date(2025, 8, 18)
        days = experiment.bias_days(d)
        self.assertEqual((len(days), min(days), max(days)), (14, date(2025, 8, 3), date(2025, 8, 16)))
        sales = {t: 2 for t in days[:7]}
        pred = {t: 4 for t in days}
        pred[days[0]] = 0
        # Six positive residuals and one negative. Dropping the negative would overcorrect.
        self.assertAlmostEqual(experiment.bias_cap(days, sales, pred), 10 / 7)
        pred[days[1]] = None
        self.assertEqual(experiment.bias_cap(days, sales, pred), 0)
        self.assertEqual(experiment.bias_cap(days, sales, {d: 99} | {t: 0 for t in days}), 0)
        self.assertEqual(experiment.guarded_point(10, 2, 1), 9)
        self.assertEqual(experiment.guarded_point(10, 2, 100), 6)
        self.assertEqual(experiment.guarded_point(10, 12, 3), 10)

    def test_batch_prefix_date_local_bias_and_no_future_spike(self):
        target = date(2025, 8, 18)
        calls = []
        def forecast(data, day, cutoff, skus):
            self.assertEqual(skus, ["a", "b"])
            self.assertEqual(cutoff, day - timedelta(days=2))
            self.assertTrue(all(date.fromisoformat(r["date"]) <= cutoff for r in data["rows"]))
            calls.append(day)
            return {sku: {"forecast_mean": 3, "b1_mean": 2} for sku in skus}
        dates = [target - timedelta(days=i) for i in range(60)]
        products = {"a": dict.fromkeys(dates, 0), "b": dict.fromkeys(dates, 4)}
        products["a"][target - timedelta(days=1)] = 250
        products["a"][target] = 500
        pairs, _ = experiment.replay(products, [target], forecast)
        self.assertEqual(len(calls), 15)  # single combined shipping call per date for both SKUs
        self.assertEqual(len(set(calls)), 15)
        self.assertEqual([(p["actual"], p["cap"], bool(p["recent"])) for p in pairs],
                         [(500, 3, True), (4, 0, False)])
        del products["a"][target]
        pairs, _ = experiment.replay(products, [target], forecast)
        self.assertEqual([p["actual"] for p in pairs], [4])

    def test_stored_gates_and_adjustment_evidence_are_recomputed(self):
        report = json.loads((SCRIPTS.parent / "results/sparse_bias.json").read_text(encoding="utf-8"))
        experiment.verify(report)
        for change in ("gate", "baseline", "confirmation", "changed"):
            altered = copy.deepcopy(report)
            part = altered["partitions"]["confirmation"]
            if change == "gate":
                altered["gate"]["passed"] = not altered["gate"]["passed"]
            elif change == "confirmation":
                del part["sources"]["m5_TX_2"]
            elif change == "baseline":
                altered["partitions"]["regression"]["sources"]["vending"]["baseline_mae"] += 1
            else:
                part["sources"]["m5_TX_2"]["total_downward_units"] += 1
            with self.assertRaises(ValueError):
                experiment.verify(altered)

    def test_either_panel_can_independently_prevent_release(self):
        panels = {p: {"gate": {"passed": True, "reasons": []}} for p in experiment.PANELS}
        self.assertTrue(experiment.decision(panels)["passed"])
        for panel in panels:
            failed = copy.deepcopy(panels)
            failed[panel]["gate"] = {"passed": False, "reasons": ["mean_mae"]}
            self.assertEqual(experiment.decision(failed), {"passed": False, "reasons": [panel + "/mean_mae"]})
