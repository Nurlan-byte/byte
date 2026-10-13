"""Synthetic-only acceptance tests for the sales proxy adapter."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from tools.sales_adapter import SalesLine, build_daily, read_breadbasket, select_items


class SalesAdapterTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BreadBasket_DMS.csv"
            path.write_text(text, encoding="utf-8")
            return read_breadbasket(path)

    def test_duplicate_transaction_lines_are_legitimate_counts(self):
        lines = self.parse(
            "Transaction,Item,date_time,period_day,weekday_weekend\n001,Bread,01/01/2017 9:00,morning,weekend\n001,Bread,01/01/2017 9:00,morning,weekend\n"
        )
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].transaction_id, "001")
        observations = build_daily(lines, ["Bread"])
        self.assertEqual(observations[0].item_line_count, 2)
        self.assertFalse(observations[0].inferred_zero)

    def test_bad_dates_headers_and_empty_identifiers_rejected(self):
        header = "Transaction,Item,date_time,period_day,weekday_weekend\n"
        for text in (
            "Date,Time,Transaction,Item\n2017-01-01,09:00:00,1,Bread\n",
            header + "1,Bread,02/30/2017 9:00,morning,weekday\n",
            header + "1,Bread,2017-01-01 9:00,morning,weekday\n",
            header + ",Bread,01/01/2017 9:00,morning,weekend\n",
            header + "1,,01/01/2017 9:00,morning,weekend\n",
            header + "1,Bread,01/01/2017 9:00,morning,weekend,extra\n",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.parse(text)

    def test_zero_only_on_observed_store_days_and_after_product_launch(self):
        lines = [
            SalesLine(date(2017, 1, 1), "Coffee", "1"),
            SalesLine(date(2017, 1, 2), "Bread", "2"),
            SalesLine(date(2017, 1, 4), "Coffee", "3"),
        ]
        daily = build_daily(lines, ["Bread"], zero_policy="observed-store-zero")
        self.assertEqual(
            [(r.date, r.item_line_count, r.inferred_zero) for r in daily],
            [(date(2017, 1, 2), 1, False), (date(2017, 1, 4), 0, True)],
        )
        conservative = build_daily(lines, ["Bread"], zero_policy="missing")
        self.assertEqual(
            [(r.date, r.item_line_count) for r in conservative], [(date(2017, 1, 2), 1)]
        )

    def test_food_selection_frozen_at_training_cutoff(self):
        foods = ["Bread", "Pastry", "Medialuna", "Cookies", "Cake"]
        lines = [
            SalesLine(date(2017, 1, day), item, f"{day}-{item}")
            for day in range(1, 29)
            for item in foods
        ]
        lines += [
            SalesLine(date(2017, 1, day), "Coffee", f"coffee-{day}")
            for day in range(1, 29)
        ]
        cutoff = date(2017, 1, 31)
        selected = select_items(lines, train_end=cutoff, n=5, min_coverage=0.5)
        self.assertEqual(set(selected), set(foods))
        future = [
            SalesLine(date(2017, 2, day), "Toast", f"future-{day}")
            for day in range(1, 28)
        ]
        self.assertEqual(
            select_items(lines + future, train_end=cutoff, n=5, min_coverage=0.5),
            selected,
        )

    def test_insufficient_training_coverage_does_not_promote_sparse_item(self):
        lines = [
            SalesLine(date(2017, 1, day), "Coffee", str(day)) for day in range(1, 29)
        ]
        lines += [
            SalesLine(date(2017, 1, day), "Cake", f"cake-{day}") for day in range(1, 29)
        ]
        lines.append(SalesLine(date(2017, 1, 4), "Bread", "bread"))
        selected = select_items(
            lines, train_end=date(2017, 1, 31), n=1, min_coverage=0.5
        )
        self.assertNotIn("Bread", selected)


if __name__ == "__main__":
    unittest.main()
