"""Export public forecasts and grouped errors from the four benchmark replays."""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


FORMATS = {
    "bakery": ("bakery_forecasts.csv", "observed_net_units", "French bakery"),
    "cafe": ("cafe_predictions.csv", "actual", None),
    "vending": ("vending_forecasts.csv", "observed_units", "GuttenPlans x1367"),
    "grocery": ("completejourney_predictions.csv", "actual", None),
}


def load_rows(folder, filename):
    with (folder / filename).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def replay_rows(sample, folder):
    filename, actual, site = FORMATS[sample]
    if sample == "grocery":
        info = json.loads((folder / "completejourney_results.json").read_text(encoding="utf-8"))
        site = str(info["store_id"])
    rows = []
    for source in load_rows(folder, filename):
        rows.append({
            "date": source["date"], "site": site or source["outlet"],
            "product": source.get("product") or source["product_id"],
            "cutoff": source.get("data_cutoff") or source["cutoff"],
            "training_cutoff": info["training_cutoff"] if sample == "grocery" else source["training_cutoff"],
            "method": source["method"], "b1": source.get("b1_forecast", source.get("b1")),
            "bakeplan": source.get("bakeplan_forecast", source.get("bakeplan")),
            "actual": int(source[actual]),
        })
    if not rows:
        raise ValueError(f"No forecasts for {sample}")
    return rows


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def summary(site, product, rows):
    valid = [row for row in rows if row["b1"] != "" and row["bakeplan"] != ""]
    total = sum(row["actual"] for row in valid)
    out = {
        "site": site,
        "product": product,
        "candidates": len(rows),
        "scored": len(valid),
        "units_observed": total,
        "ridge_count": sum(row["method"] == "ML" for row in valid),
    }
    errors = {}
    for key in ("b1", "bakeplan"):
        errors[key] = math.fsum(abs(float(row[key]) - row["actual"]) for row in valid)
        out[key + "_mae"] = errors[key] / len(valid) if valid else None
        out[key + "_wape_pct"] = 100 * errors[key] / total if total else None
    out["error_reduction_pct"] = (
        100 * (1 - errors["bakeplan"] / errors["b1"]) if errors["b1"] else None
    )
    return out


def grouped_metrics(rows):
    groups = defaultdict(list)
    sites = defaultdict(list)
    for row in rows:
        sites[row["site"]].append(row)
        groups[row["site"], row["product"]].append(row)
    metrics = [summary("ALL", "ALL", rows)]
    if len(sites) > 1:
        metrics.extend(
            summary(site, "ALL", group) for site, group in sorted(sites.items())
        )
    metrics.extend(
        summary(site, product, group)
        for (site, product), group in sorted(groups.items())
    )
    return metrics


def export(sample, rows, root):
    folder = root / sample
    folder.mkdir(parents=True, exist_ok=True)
    write_csv(folder / "metrics.csv", grouped_metrics(rows))
    public = [{k: v for k, v in row.items() if k != "actual"} for row in rows]
    write_csv(folder / "forecasts.csv", public)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (*FORMATS, "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    for sample in FORMATS:
        export(sample, replay_rows(sample, getattr(args, sample)), args.out)


if __name__ == "__main__":
    main()
