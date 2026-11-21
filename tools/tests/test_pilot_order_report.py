"""Acceptance checks for paired order times and branch-level pilot decisions."""

import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.pilot_order_report import build_report


HEADER = ("branch_code", "pair_id", "manual_seconds", "bakeplan_seconds",
          "manual_status", "bakeplan_status")


def csv_input(rows, header=HEADER):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows(rows)
    stream.seek(0)
    return stream


def unaided(branch, n=10, manual=100, bakeplan=70):
    return [(branch, f"private-pair-{i}", manual, bakeplan, "unaided", "unaided")
            for i in range(n)]


def report(rows, *, branches=("A", "B"), source="pilot"):
    return build_report(csv_input(rows), source=source, branches=list(branches))


class PilotOrderReportTests(unittest.TestCase):
    def test_durations_are_paired_and_all_enrolled_attempts_count(self):
        rows = [
            ("A", "r1", 100, 50, "unaided", "unaided"),
            ("A", "r2", 200, 180, "unaided", "unaided"),
            ("A", "r3", 1000, 600, "unaided", "unaided"),
            ("A", "r4", 10, 2, "unaided", "assisted"),
            ("A", "r5", 10, "", "unaided", "pending"),
            ("A", "r6", "", 8, "failed", "failed"),
            ("A", "r7", 360, 20, "assisted", "unaided"),
        ]
        result = report(rows)
        branch = result["branches"]["A"]
        self.assertEqual(branch["planned_pairs"], 7)
        self.assertEqual(branch["comparable_pairs"], 3)
        self.assertEqual(branch["bakeplan_status_counts"],
                         {"unaided": 4, "assisted": 1, "pending": 1, "failed": 1})
        self.assertEqual(branch["manual_status_counts"]["unaided"], 5)
        self.assertAlmostEqual(branch["bakeplan_unaided_rate"], 4 / 7)
        self.assertEqual(branch["median_manual_seconds"], 200)
        self.assertEqual(branch["median_bakeplan_seconds"], 180)
        # Individual reductions are 50%, 10%, 40%. The ratio of medians would give 10%.
        self.assertEqual(branch["median_paired_reduction_percent"], 40)
        self.assertEqual(branch["verdict"], "insufficient")
        absent = result["branches"]["B"]
        self.assertEqual(absent["comparable_pairs"], 0)
        self.assertIsNone(absent["median_paired_reduction_percent"])

    def test_per_branch_thresholds_accept_equality_and_reject_a_reversal(self):
        a = unaided("A", 18) + [("A", "a19", 100, "", "unaided", "failed"),
                                ("A", "a20", 100, 70, "unaided", "assisted")]
        passing = report(a + unaided("B"))
        self.assertEqual(passing["source"], "pilot")
        self.assertEqual(passing["verdict"], "met")
        self.assertEqual(passing["branches"]["A"]["bakeplan_unaided_rate"], 0.9)
        self.assertEqual(passing["branches"]["A"]["median_paired_reduction_percent"], 30)
        # A high-performing branch cannot hide a slower branch.
        missed = report(a + unaided("B", bakeplan=110))
        self.assertEqual(missed["branches"]["A"]["verdict"], "met")
        self.assertEqual(missed["branches"]["B"]["median_paired_reduction_percent"], -10)
        self.assertEqual(missed["branches"]["B"]["verdict"], "missed")
        self.assertEqual(missed["verdict"], "missed")
        low_success = report(a + [("A", "a21", 100, "", "unaided", "failed")] + unaided("B"))
        self.assertEqual(low_success["branches"]["A"]["verdict"], "missed")
        self.assertEqual(low_success["verdict"], "missed")

    def test_incomplete_coverage_and_unfinished_pairs_keep_pilot_open(self):
        a = unaided("A")
        self.assertEqual(report(a, branches=("A",))["verdict"], "insufficient")
        for b in ([], unaided("B", 9),
                  unaided("B") + [("B", "unfinished", "", 10, "pending", "unaided")],
                  unaided("B") + [("B", "unfinished", 10, "", "unaided", "pending")]):
            with self.subTest(b_rows=len(b), last=b[-1:] if b else None):
                result = report(a + b)
                self.assertEqual(result["branches"]["A"]["verdict"], "met")
                self.assertEqual(result["branches"]["B"]["verdict"], "insufficient")
                self.assertEqual(result["verdict"], "insufficient")

    def test_synthetic_results_remain_demonstrations_without_raw_observations(self):
        rows = unaided("A") + unaided("B")
        # Same local observation IDs in separate branches are valid.
        result = report(rows, source="synthetic")
        self.assertEqual(result["verdict"], "demonstration")
        self.assertEqual(result["source"], "synthetic")
        self.assertEqual([result["branches"][b]["verdict"] for b in ("A", "B")],
                         ["demonstration", "demonstration"])
        self.assertNotIn("private-pair-", json.dumps(result))

    def test_duplicate_rows_unknown_branches_and_extra_columns_are_rejected(self):
        row = ("A", "private-pair-1", 10, 5, "unaided", "unaided")
        fixtures = [
            csv_input([row, row]),
            csv_input([("unknown-branch", *row[1:])]),
            csv_input([row + ("personal@example.com",)], HEADER + ("email",)),
            csv_input([row + ("personal@example.com",)]),
            csv_input([row[:-1]]),
            csv_input([row], HEADER[:-1] + (HEADER[0],)),
            io.StringIO('branch_code,pair_id,manual_seconds,bakeplan_seconds,manual_status,bakeplan_status\nA,"unclosed'),
        ]
        for index, stream in enumerate(fixtures):
            with self.subTest(index=index), self.assertRaises(ValueError):
                build_report(stream, source="pilot", branches=["A", "B"])

    def test_status_and_duration_must_describe_the_same_attempt(self):
        for seconds, status in [("", "unaided"), ("0", "unaided"), ("-5", "unaided"),
                                ("1.5", "unaided"), ("nan", "unaided"), ("１２", "unaided"),
                                ("", "assisted"), ("5", "pending"), ("", "unknown")]:
            for manual_side in (True, False):
                durations = (seconds, "10") if manual_side else ("10", seconds)
                statuses = (status, "unaided") if manual_side else ("unaided", status)
                with self.subTest(seconds=seconds, status=status, manual=manual_side):
                    with self.assertRaises(ValueError):
                        report([("A", "private-id", *durations, *statuses)])

    def test_cli_reports_aggregates_and_returns_failure_without_modifying_input(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.csv"
            command = [sys.executable, "-m", "tools.pilot_order_report", str(path),
                       "--source", "pilot", "--branches", "A", "B"]
            for valid in (True, False):
                rows = unaided("A") + unaided("B")
                if not valid:
                    rows.append(rows[0])
                contents = csv_input(rows).getvalue().encode("utf-8")
                path.write_bytes(contents)
                process = subprocess.run(command, cwd=root, capture_output=True, timeout=15)
                self.assertEqual(path.read_bytes(), contents)
                self.assertNotIn(b"private-pair-", process.stdout + process.stderr)
                if valid:
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertEqual(json.loads(process.stdout)["verdict"], "met")
                else:
                    self.assertEqual(process.returncode, 2)
                    self.assertEqual(process.stdout, b"")


if __name__ == "__main__":
    unittest.main()
