import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from backend.imports import CsvValidationError, parse_csv
from tools.sales_adapter import build_daily, read_breadbasket


class SalesCliTests(unittest.TestCase):
    def test_clean_checkout_synthetic_command_and_reproducible_output(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / "absent" / "private" / "report.json"
            command = [
                sys.executable,
                str(root / "tools/evaluate_sales.py"),
                "--csv",
                str(root / "fixtures/synthetic_sales_lines.csv"),
                "--source",
                "synthetic-sales-only",
                "--history-mode",
                "rolling-one-day-ahead",
                "--test-end",
                "2017-03-09",
                "--output",
                str(output),
            ]
            self.assertFalse(output.parent.exists())
            result = subprocess.run(
                command, cwd=root, capture_output=True, text=True, timeout=120
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            original = output.read_bytes()
            report = json.loads(original)
            self.assertEqual(report["source"], "synthetic-sales-only")
            self.assertEqual(report["status"], "evaluated")
            result = subprocess.run(
                command, cwd=root, capture_output=True, text=True, timeout=120
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), original)

    def test_sales_schema_cannot_masquerade_as_operational_actuals(self):
        fixture = (
            Path(__file__).resolve().parents[2] / "fixtures/synthetic_sales_lines.csv"
        )
        with self.assertRaises(CsvValidationError):
            parse_csv(fixture.read_text("utf-8"))
        daily = build_daily(read_breadbasket(fixture), ["Bread"])
        self.assertEqual(
            set(asdict(daily[0])), {"date", "item", "item_line_count", "inferred_zero"}
        )


if __name__ == "__main__":
    unittest.main()
