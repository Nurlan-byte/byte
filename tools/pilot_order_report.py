"""Measure paired administrator order times from a local pilot log."""

import argparse
import csv
from dataclasses import dataclass, field
from fractions import Fraction
import json
import re
from statistics import median


COLUMNS = ("branch_code", "pair_id", "manual_seconds", "bakeplan_seconds", "manual_status", "bakeplan_status")
STATUSES = ("unaided", "assisted", "failed", "pending")
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
MIN_BRANCHES = 2
MIN_PAIRS = 10
MIN_UNAIDED_RATE = Fraction(9, 10)
MIN_REDUCTION_PERCENT = 30


def seconds(cell, status, line):
    if not cell and status in {"pending", "failed"}:
        return None
    if status == "pending" or not re.fullmatch(r"[0-9]{1,16}", cell):
        raise ValueError(f"Строка {line}: проверьте время и статус.")
    value = int(cell)
    if not 1 <= value <= 2**53 - 1:
        raise ValueError(f"Строка {line}: время должно быть положительным целым в секундах.")
    return value


@dataclass
class BranchOrders:
    ids: set[str] = field(default_factory=set)
    manual: dict = field(default_factory=lambda: dict.fromkeys(STATUSES, 0))
    bakeplan: dict = field(default_factory=lambda: dict.fromkeys(STATUSES, 0))
    comparable: list = field(default_factory=list)

    def add(self, pair, manual_status, bakeplan_status, manual_seconds, bakeplan_seconds, line):
        if pair in self.ids:
            raise ValueError(f"Строка {line}: повтор пары внутри филиала.")
        self.ids.add(pair)
        self.manual[manual_status] += 1
        self.bakeplan[bakeplan_status] += 1
        if manual_status == bakeplan_status == "unaided":
            self.comparable.append((manual_seconds, bakeplan_seconds))

    def report(self, source):
        planned = len(self.ids)
        rate = Fraction(self.bakeplan["unaided"], planned) if planned else None
        reduction = manual_time = bakeplan_time = None
        if self.comparable:
            manual_time = median(m for m, _ in self.comparable)
            bakeplan_time = median(b for _, b in self.comparable)
            reduction = median(Fraction(100 * (m - b), m) for m, b in self.comparable)
        if source == "synthetic":
            verdict = "demonstration"
        elif self.manual["pending"] or self.bakeplan["pending"] or len(self.comparable) < MIN_PAIRS:
            verdict = "insufficient"
        elif rate < MIN_UNAIDED_RATE or reduction < MIN_REDUCTION_PERCENT:
            verdict = "missed"
        else:
            verdict = "met"
        return {
            "verdict": verdict,
            "planned_pairs": planned,
            "comparable_pairs": len(self.comparable),
            "manual_status_counts": self.manual,
            "bakeplan_status_counts": self.bakeplan,
            "bakeplan_unaided_rate": float(rate) if rate is not None else None,
            "median_manual_seconds": manual_time,
            "median_bakeplan_seconds": bakeplan_time,
            "median_paired_reduction_percent": float(reduction) if reduction is not None else None,
        }


def build_report(stream, *, source, branches):
    if source not in ("pilot", "synthetic"):
        raise ValueError("Допустимые источники: pilot, synthetic.")
    if not branches or any(not CODE.fullmatch(code) for code in branches):
        raise ValueError("Укажите коды филиалов из допустимых символов.")
    if len(branches) != len(set(branches)):
        raise ValueError("Список филиалов содержит повторы.")
    orders = {code: BranchOrders() for code in branches}
    reader = csv.reader(stream, strict=True)
    try:
        if tuple(next(reader, ())) != COLUMNS:
            raise ValueError("Заголовок должен содержать шесть полей по шаблону.")
        for row in reader:
            line = reader.line_num
            if len(row) != len(COLUMNS):
                raise ValueError(f"Строка {line}: требуется шесть полей.")
            branch, pair, manual_cell, bakeplan_cell, manual_status, bakeplan_status = row
            if branch not in orders or not CODE.fullmatch(pair):
                raise ValueError(f"Строка {line}: проверьте код филиала или пары.")
            if manual_status not in STATUSES or bakeplan_status not in STATUSES:
                raise ValueError(f"Строка {line}: неизвестный статус.")
            orders[branch].add(
                pair, manual_status, bakeplan_status,
                seconds(manual_cell, manual_status, line), seconds(bakeplan_cell, bakeplan_status, line), line,
            )
    except csv.Error:
        raise ValueError(f"Строка {reader.line_num}: некорректный CSV.") from None

    reports = {code: branch_orders.report(source) for code, branch_orders in orders.items()}
    verdicts = {report["verdict"] for report in reports.values()}
    if source == "synthetic":
        verdict = "demonstration"
    elif len(reports) < MIN_BRANCHES or "insufficient" in verdicts:
        verdict = "insufficient"
    elif "missed" in verdicts:
        verdict = "missed"
    else:
        verdict = "met"
    return {
        "source": source, "verdict": verdict,
        "thresholds": {
            "min_branches": MIN_BRANCHES, "min_comparable_pairs_per_branch": MIN_PAIRS,
            "min_bakeplan_unaided_rate": float(MIN_UNAIDED_RATE),
            "min_median_paired_reduction_percent": MIN_REDUCTION_PERCENT,
        },
        "branches": reports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", help="локальный CSV с парами заказов")
    parser.add_argument("--source", required=True, metavar="{synthetic,pilot}")
    parser.add_argument("--branches", required=True, nargs="+", metavar="CODE")
    args = parser.parse_args()
    try:
        with open(args.csv_file, encoding="utf-8-sig", newline="") as stream:
            report = build_report(stream, source=args.source, branches=args.branches)
    except (OSError, UnicodeError):
        parser.exit(2, "Не удалось прочитать локальный CSV в кодировке UTF-8.\n")
    except ValueError as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
