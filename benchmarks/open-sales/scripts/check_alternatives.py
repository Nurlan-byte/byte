"""Secondary B0 check using the two original files, existing forecasts and no fitting."""

import argparse
import csv
import hashlib
import io
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path


def french(source, predicted):
    with zipfile.ZipFile(source) as archive:
        content = archive.read("Bakery sales.csv")
        if (
            hashlib.sha256(content).hexdigest()
            != "af5eede55b6eb2efb6bebb0e6dd1a51568c7eb73554d2f8ad2563081025681b2"
        ):
            raise ValueError("Unexpected French bakery input file")
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        aggregate, total = Counter(), Counter()
        first = {}
        for row in reader:
            item, day = row["article"].strip(), date.fromisoformat(row["date"])
            if item in {"", ".", "UNKNOWN", "COUPE"}:
                continue
            quantity = float(row["Quantity"])
            assert quantity.is_integer()
            total[day] += int(quantity)
            aggregate[(item, day)] += int(quantity)
            if quantity > 0:
                first[item] = min(day, first.get(item, day))
    daysets = {"FR": {day for day, count in total.items() if count > 0}}
    raw = {
        ("FR", p): {d: aggregate[(p, d)] for d in daysets["FR"] if d >= first[p]}
        for p in {r["product"] for r in predicted}
    }
    return raw


def indonesian(source, predicted):
    keys = {(r["outlet"], r["product"]) for r in predicted}
    from_file = defaultdict(Counter)
    dates = defaultdict(set)
    with source.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            day, outlet = date.fromisoformat(row["Date"]), row["Outlet"]
            dates[outlet].add(day)
            occurrences = [v.strip() for v in row["Items"].split(",")]
            assert len(occurrences) == len(set(occurrences)) == int(row["TotalItem"])
            for p in occurrences:
                if (outlet, p) in keys:
                    from_file[outlet, p][day] += 1
    return {
        (o, p): {d: from_file[o, p][d] for d in dates[o] if d >= min(from_file[o, p])}
        for o, p in keys
    }


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def evaluate(predicted, history, bakery, native):
    rows = []
    for r in predicted:
        site = "FR" if bakery else r["outlet"]
        sku, day = r["product"], date.fromisoformat(r["date"])
        d0 = day - timedelta(days=7)
        h = history[(site, sku)]
        actual, b1, hybrid = [
            float(r[k])
            for k in (
                ("observed_net_units", "b1_forecast", "bakeplan_forecast")
                if bakery
                else ("actual", "b1", "bakeplan")
            )
        ]
        assert h[day] == actual
        cutoff = date.fromisoformat(r["data_cutoff" if bakery else "cutoff"])
        assert cutoff == day - timedelta(days=2)
        eligible = [
            {"date": d, "product_id": sku, "sold": q}
            for d, q in h.items()
            if day - timedelta(days=56) <= d <= cutoff
        ]
        for method in ["B0", "B1"]:
            independent = native(
                eligible,
                product_id=sku,
                target_date=day,
                cutoff=cutoff,
                method=method,
                eligible=lambda _: True,
            )["forecast_mean"]
            if method == "B1":
                assert independent == b1
            else:
                b0 = independent
                assert b0 == h.get(d0)
        rows.append((site, sku, actual, b0, b1, hybrid, day))
    return rows


def summarize(label, subset):
    common = [r for r in subset if r[3] is not None]
    n = len(common)
    sold = sum(r[2] for r in common)
    errors = [sum(abs(r[j] - r[2]) for r in common) for j in (3, 4, 5)]
    out = dict(
        sample=label,
        all_forecasts=len(subset),
        common_n=n,
        excluded_no_B0=len(subset) - n,
        observed_target_sum=int(sold),
    )
    for name, e in zip(("B0", "B1", "BakePlan"), errors):
        out[f"{name}_MAE"] = e / n
        out[f"{name}_WAPE_pct"] = 100 * e / sold
    out["BakePlan_error_reduction_vs_B0_pct"] = 100 * (1 - errors[2] / errors[0])
    out["BakePlan_error_reduction_vs_B1_pct"] = 100 * (1 - errors[2] / errors[1])
    byweek = defaultdict(lambda: [0.0, 0.0])
    start = min(r[6] for r in subset)
    for r in common:
        block = byweek[(r[6] - start).days // 7]
        block[0] += abs(r[3] - r[2])
        block[1] += abs(r[5] - r[2])
    leave_one_week_out = [
        100 * (1 - (errors[2] - h) / (errors[0] - b)) for b, h in byweek.values()
    ]
    out.update(
        weeks=len(byweek),
        weeks_hybrid_better_than_B0=sum(h < b for b, h in byweek.values()),
        leave_one_week_out_vs_B0_min_pct=min(leave_one_week_out),
        leave_one_week_out_vs_B0_max_pct=max(leave_one_week_out),
    )
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--french-source", required=True, type=Path)
    parser.add_argument("--french-forecasts", required=True, type=Path)
    parser.add_argument("--cafe-source", type=Path)
    parser.add_argument("--cafe-forecasts", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.cafe_source) != bool(args.cafe_forecasts):
        parser.error("--cafe-source and --cafe-forecasts must be provided together")
    if (
        args.cafe_source
        and hashlib.sha256(args.cafe_source.read_bytes()).hexdigest()
        != "36f2412969f8241e72b5f3eb395e5af13a7ba042de1e6ecdbe32bee23723ef90"
    ):
        raise ValueError("Unexpected Indonesian cafe input file")
    code = (
        (args.repo / "backend" / "forecasting.py").read_bytes().replace(b"\r\n", b"\n")
    )
    if (
        hashlib.sha256(code).hexdigest()
        != "82854f8841c836d222b9cae07d40c8eba26bcf7b52bdb1ccf1eb90da7ca16794"
    ):
        raise ValueError("Unexpected BakePlan baseline source")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.forecasting import baseline

    pf = read_csv(args.french_forecasts)
    rf = evaluate(pf, french(args.french_source, pf), True, baseline)
    groups = [
        summarize("French bakery, all six SKUs", rf),
        summarize(
            "French bakery, excluding TRADITIONAL BAGUETTE",
            [r for r in rf if r[1] != "TRADITIONAL BAGUETTE"],
        ),
    ]
    if args.cafe_forecasts:
        pc = read_csv(args.cafe_forecasts)
        rc = evaluate(pc, indonesian(args.cafe_source, pc), False, baseline)
        groups.append(summarize("Indonesian cafe, all three outlets", rc))
        for site in sorted({r[0] for r in rc}):
            groups.append(summarize(site, [r for r in rc if r[0] == site]))
    with args.out.open("w", encoding="utf-8-sig", newline="") as target:
        csvout = csv.DictWriter(target, fieldnames=list(groups[0].keys()))
        csvout.writeheader()
        csvout.writerows(groups)
    for row in groups:
        print(row)


if __name__ == "__main__":
    main()
