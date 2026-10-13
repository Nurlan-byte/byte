import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from tools.compare_sales_ml import (
    FitBudget,
    candidate_score,
    compare_sales,
    load_protocol,
    select_candidate,
    summarize,
)
from tools.sales_adapter import SalesLine, SalesObservation


def score(candidate_error=1.0, coverage=1.0):
    protocol = load_protocol()
    records = []
    for offset in range(7):
        for item in protocol["cohort"]:
            records.append(
                {
                    "date": (date(2017, 2, 1) + timedelta(days=offset)).isoformat(),
                    "item": item,
                    "actual": 10,
                    "forecasts": {"candidate": 10 + candidate_error, "B1": 12},
                }
            )
    return {
        **summarize(records, protocol["cohort"], ("candidate", "B1")),
        "predictions": records,
        "raw_finite_coverage": coverage,
    }


def fixture_lines():
    lines = []
    for offset in range(68):
        day = date(2017, 1, 1) + timedelta(days=offset)
        for item in load_protocol()["cohort"]:
            lines.append(SalesLine(day, item, str(offset)))
    return lines


class ComparisonTests(unittest.TestCase):
    def test_frozen_protocol_budget_and_after_selection_folds(self):
        protocol = load_protocol()
        self.assertEqual(len(protocol["candidates"]), 4)
        self.assertEqual(len({s["family"] for s in protocol["candidates"]}), 2)
        for fold in protocol["cv_folds"]:
            self.assertGreater(fold["start"], protocol["train_end"])
            self.assertLess(fold["train_end"], fold["start"])
        budget = FitBudget(2)
        budget.record("a", "missing", date(2017, 1, 31))
        budget.record("b", "missing", date(2017, 1, 31))
        with self.assertRaises(ValueError):
            budget.record("c", "missing", date(2017, 1, 31))

    def test_gates_and_simpler_within_one_percent_tie(self):
        protocol = load_protocol()
        results = {
            spec["id"]: {
                "cv": [score() for _ in range(4)],
                "validation": score(0.995 if spec["complexity_order"] == 3 else 1.0),
                "missing_validation": score(),
            }
            for spec in protocol["candidates"]
        }
        decision = select_candidate(results, protocol)
        self.assertEqual(decision["selected"], "ridge_residual_a100")
        self.assertTrue(all(d["eligible"] for d in decision["decisions"].values()))
        for result in results.values():
            result["missing_validation"]["pooled"]["candidate"]["mae"] = 2.11
        decision = select_candidate(results, protocol)
        self.assertEqual(decision["selected"], "B1")
        self.assertTrue(
            all(
                not d["gates"]["missing_validation_guard"]
                for d in decision["decisions"].values()
            )
        )

    def test_coverage_and_per_item_degradation_gates(self):
        protocol = load_protocol()
        results = {
            spec["id"]: {
                "cv": [score() for _ in range(4)],
                "validation": score(),
                "missing_validation": score(),
            }
            for spec in protocol["candidates"]
        }
        for result in results.values():
            result["validation"]["raw_finite_coverage"] = 0.94
            result["validation"]["per_item"]["Bread"]["B1"]["mae"] = 0
            result["validation"]["per_item"]["Bread"]["candidate"]["mae"] = 0.26
        decision = select_candidate(results, protocol)
        self.assertEqual(decision["selected"], "B1")
        for report in decision["decisions"].values():
            self.assertFalse(report["gates"]["raw_finite_coverage"])
            self.assertFalse(report["per_item_gates"]["Bread"])

    def test_sparse_cv_fold_rejects_candidate(self):
        protocol = load_protocol()
        results = {
            spec["id"]: {
                "cv": [score() for _ in range(4)],
                "validation": score(),
                "missing_validation": score(),
            }
            for spec in protocol["candidates"]
        }
        for result in results.values():
            result["cv"][0]["paired_dates_per_item"]["Bread"] = 6
        decision = select_candidate(results, protocol)
        self.assertEqual(decision["selected"], "B1")
        self.assertTrue(
            all(not d["gates"]["paired_dates"] for d in decision["decisions"].values())
        )

    def test_failed_candidate_keeps_fixed_keys_using_flagged_b1(self):
        observation = SalesObservation(date(2017, 2, 1), "Bread", 10, False)
        references = [
            {
                "date": "2017-02-01",
                "item": "Bread",
                "actual": 10,
                "common": True,
                "forecasts": {"B0": 9, "B1": 12, "priorPoisson": 11},
            }
        ]
        failed = {
            "value": None,
            "raw_finite": False,
            "used_fallback": False,
            "reason": "injected failure",
            "b1": 12,
        }
        with patch("tools.compare_sales_ml.predict_candidate", return_value=failed):
            report = candidate_score(
                None, "fit failed", [observation], references, ["Bread"]
            )
        self.assertEqual(report["n"], 1)
        self.assertEqual(report["pooled"]["candidate"]["mae"], 2)
        self.assertEqual(report["raw_finite_coverage"], 0)
        self.assertEqual(report["fallback_n"], 1)
        self.assertEqual(report["predictions"][0]["fallback_reason"], "fit failed")

    def test_budget_no_test_fits_unselected_not_test_scored_and_future_immunity(self):
        lines = fixture_lines()
        args = dict(
            source="synthetic-sales-only",
            dataset_sha="synthetic-unit-fixture",
            history_mode="rolling-one-day-ahead",
        )
        failed_prediction = {
            "value": None,
            "raw_finite": False,
            "used_fallback": False,
            "reason": "synthetic mock fallback",
            "b1": None,
        }

        def fitted(spec, observations, fit_end, cohort):
            return SimpleNamespace(
                metadata={"id": spec["id"], "fit_end": fit_end.isoformat()}
            )

        with (
            patch("tools.compare_sales_ml.fit_candidate", side_effect=fitted),
            patch(
                "tools.compare_sales_ml.predict_candidate",
                return_value=failed_prediction,
            ),
        ):
            first = compare_sales(lines, **args)
            changed = lines + [
                SalesLine(date(2017, 3, 9), "Bread", f"future-{i}") for i in range(30)
            ]
            second = compare_sales(changed, **args)
        self.assertEqual(first["fits_n"], 30)
        self.assertEqual(first["selection"]["selected"], "B1")
        self.assertTrue(
            all(entry["fit_end"] < "2017-03-01" for entry in first["fit_log"])
        )
        self.assertEqual(first["candidate_results"], second["candidate_results"])
        self.assertEqual(first["selection"], second["selection"])
        self.assertNotIn("candidate", first["test"]["observed-store-zero"]["pooled"])
        self.assertEqual(first["test"]["observed-store-zero"]["fit_end"], "2017-01-31")

    def test_insufficient_training_is_refused_before_any_fit(self):
        report = compare_sales(
            fixture_lines()[:10],
            source="synthetic-sales-only",
            dataset_sha="unit",
            history_mode="rolling-one-day-ahead",
        )
        self.assertEqual(report["status"], "refused")
        self.assertEqual(report["fits_n"], 0)
        self.assertEqual(report["selected"], "B1")


if __name__ == "__main__":
    unittest.main()
