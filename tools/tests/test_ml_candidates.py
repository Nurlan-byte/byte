"""Synthetic-only checks for frozen residual candidates and past-only features."""
import json
import math
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tools.ml_candidates import enriched_features, fit_candidate, predict_candidate
from tools.sales_adapter import SalesObservation


def observations():
    start = date(2016, 12, 1)
    return [SalesObservation(start + timedelta(days=day), item, 10 + day % 7, False)
            for day in range(90) for item in ('Bread', 'Cake')]


def index_for(rows):
    return {(row.item, row.date): row for row in rows}


class CandidateTests(unittest.TestCase):
    def test_target_and_future_values_cannot_change_features(self):
        rows = observations()
        target = SalesObservation(date(2017, 2, 1), 'Bread', 12, False)
        cutoff = target.date - timedelta(days=1)
        original = enriched_features(target, index_for(rows), cutoff)
        altered = [SalesObservation(r.date, r.item, 999999 if r.date >= target.date else r.item_line_count, r.inferred_zero) for r in rows]
        revised_target = SalesObservation(target.date, 'Bread', 999999, False)
        result = enriched_features(revised_target, index_for(altered), cutoff)
        self.assertEqual(len(original), 13)
        for before, after in zip(original, result):
            if isinstance(before, float) and math.isnan(before):
                self.assertTrue(math.isnan(after))
            else:
                self.assertEqual(before, after)

    def test_calendar_lags_respect_earlier_cutoff(self):
        rows = observations()
        target = SalesObservation(date(2017, 2, 1), 'Bread', 12, False)
        features = enriched_features(target, index_for(rows), date(2017, 1, 30))
        self.assertTrue(math.isnan(features[2]))  # lag1 is Jan31, unavailable
        self.assertEqual(features[3], index_for(rows)[('Bread', date(2017, 1, 25))].item_line_count)

    def test_fit_excludes_future_labels_and_categories(self):
        spec = json.loads((Path(__file__).parents[1] / 'ml_improvement_protocol.json').read_text(encoding='utf-8'))['candidates'][0]
        rows = observations()
        fit_end = date(2017, 1, 31)
        original = fit_candidate(spec, rows, fit_end, ['Bread', 'Cake'])
        altered = [SalesObservation(r.date, r.item, 999999 if r.date > fit_end else r.item_line_count, r.inferred_zero) for r in rows]
        altered.append(SalesObservation(date(2017, 2, 2), 'FUTURE_CATEGORY', 999999, False))
        changed = fit_candidate(spec, altered, fit_end, ['Bread', 'Cake'])
        self.assertIsNotNone(original)
        self.assertEqual(original.metadata, changed.metadata)
        original_transform = original.pipeline.named_steps['preprocess']
        changed_transform = changed.pipeline.named_steps['preprocess']
        for step, attribute in [('impute', 'statistics_'), ('scale', 'mean_'), ('scale', 'scale_')]:
            before_stats = getattr(original_transform.named_transformers_['numeric'].named_steps[step], attribute)
            after_stats = getattr(changed_transform.named_transformers_['numeric'].named_steps[step], attribute)
            self.assertEqual(before_stats.tolist(), after_stats.tolist())
        before_categories = original_transform.named_transformers_['category'].categories_
        after_categories = changed_transform.named_transformers_['category'].categories_
        self.assertEqual([v.tolist() for v in before_categories], [v.tolist() for v in after_categories])
        target = SalesObservation(date(2017, 2, 1), 'Bread', 12, False)
        before = predict_candidate(original, target, index_for(rows), fit_end)
        after = predict_candidate(changed, target, index_for(rows), fit_end)
        self.assertAlmostEqual(before['value'], after['value'])
        self.assertNotIn('recommended_qty', before)
        self.assertNotIn('confirmed_qty', before)

    def test_failed_or_nonfinite_model_keeps_b1_and_flags_fallback(self):
        rows = observations()
        target = SalesObservation(date(2017, 2, 1), 'Bread', 12, False)
        index = index_for(rows)
        cutoff = target.date - timedelta(days=1)
        for value in (float('nan'), float('inf')):
            fitted = SimpleNamespace(pipeline=Mock(predict=Mock(return_value=[value])), spec={}, metadata={})
            result = predict_candidate(fitted, target, index, cutoff)
            self.assertTrue(result['used_fallback'])
            self.assertFalse(result['raw_finite'])
            self.assertEqual(result['value'], result['b1'])
        fitted = SimpleNamespace(pipeline=Mock(predict=Mock(side_effect=ValueError('synthetic failure'))), spec={}, metadata={})
        result = predict_candidate(fitted, target, index, cutoff)
        self.assertTrue(result['used_fallback'])
        self.assertEqual(result['value'], result['b1'])
        result = predict_candidate(None, target, index, cutoff)
        self.assertTrue(result['used_fallback'])
        self.assertEqual(result['value'], result['b1'])


if __name__ == '__main__':
    unittest.main()

