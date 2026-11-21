"""Check the published open-sales forecasts and scores against local paired outcomes."""

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path

if __package__:
    from .export_results import FORMATS, grouped_metrics, load_rows, replay_rows
else:
    from export_results import FORMATS, grouped_metrics, load_rows, replay_rows


RESULTS = Path(__file__).resolve().parents[1] / "results"
HISTORICAL_KERNEL = "ac3c2af5a4535c6fcd35ccd7ebdd41c4f7bac4cb"
WINDOWS = {
    "bakery": (date(2022, 7, 2), date(2022, 9, 30)),
    "cafe": (date(2025, 8, 6), date(2025, 9, 30)),
    "vending": (date(2022, 10, 2), date(2022, 12, 31)),
    "grocery": (date(2017, 11, 6), date(2017, 12, 31)),
}


def keyed(rows, fields, label):
    index = {}
    for row in rows:
        key = tuple(row[field] for field in fields)
        if not all(key) or key in index:
            raise ValueError(f"{label}: empty or duplicate key {key}")
        index[key] = row
    return index


def verify_sample(sample, rows, folder):
    start, end = WINDOWS[sample]
    key_fields = ("date", "site", "product")
    detailed = keyed(rows, key_fields, sample)
    public = keyed(load_rows(folder, "forecasts.csv"), key_fields, f"{sample}/forecasts.csv")
    if not detailed or detailed.keys() != public.keys():
        raise ValueError(f"{sample}: published forecast keys differ from detailed outcomes")
    for key, row in detailed.items():
        day, cutoff, trained = (date.fromisoformat(row[field]) for field in ("date", "cutoff", "training_cutoff"))
        if not start <= day <= end or day - cutoff != timedelta(days=2) or trained > cutoff:
            raise ValueError(f"{sample}: test window or cutoff mismatch at {key}")
        b1, model = float(row["b1"]), float(row["bakeplan"])
        if row["actual"] < 0 or not all(math.isfinite(value) and value >= 0 for value in (b1, model)):
            raise ValueError(f"{sample}: invalid outcome or forecast at {key}")
        if row["method"] not in {"B1", "ML"} or (row["method"] == "B1" and b1 != model):
            raise ValueError(f"{sample}: invalid method or B1 fallback at {key}")
        # Compare unrounded values and dates, independent of CSV row ordering.
        if public[key] != {field: value for field, value in row.items() if field != "actual"}:
            raise ValueError(f"{sample}: published forecast differs at {key}")

    observed = keyed(load_rows(folder, "metrics.csv"), ("site", "product"), f"{sample}/metrics.csv")
    expected = keyed(grouped_metrics(rows), ("site", "product"), sample)
    if observed.keys() != expected.keys():
        raise ValueError(f"{sample}: published metric groups differ")
    for key, metrics in expected.items():
        stored = observed[key]
        if stored.keys() != metrics.keys():
            raise ValueError(f"{sample}: published metric fields differ at {key}")
        for field, value in metrics.items():
            if isinstance(value, float):
                matches = math.isclose(float(stored[field]), value, rel_tol=1e-12, abs_tol=1e-12)
                if field == "error_reduction_pct" and matches:
                    number = float(stored[field])
                    matches = (number > 0) == (value > 0) and (number < 0) == (value < 0)
            else:
                matches = stored[field] == ("" if value is None else str(value))
            if not matches:
                raise ValueError(f"{sample}: published {field} differs at {key}")

    summary = expected["ALL", "ALL"]
    return {k: v for k, v in summary.items() if k not in {"site", "product"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for sample in FORMATS:
        parser.add_argument(f"--{sample}", required=True, type=Path)
    parser.add_argument("--published", default=RESULTS, type=Path, help="Directory containing public results")
    args = parser.parse_args()
    try:
        samples = {
            name: verify_sample(name, replay_rows(name, getattr(args, name)), args.published / name)
            for name in FORMATS
        }
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"Benchmark verification failed: {exc}\n")
    print(json.dumps({"historical_kernel": HISTORICAL_KERNEL, "samples": samples}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
