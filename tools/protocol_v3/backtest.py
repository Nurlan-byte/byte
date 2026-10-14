"""Ground-truth backtests: frozen model, rolling 2-day horizon, many seeds."""

import json
import sys
from collections import defaultdict
from datetime import timedelta

import numpy as np

from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]

from backend import ml_core as core  # noqa: E402
from backend.planning import nearest_allowed_quantity  # noqa: E402
from gen import START, make_dataset  # noqa: E402

TRAIN_CUTOFF = 83
EVAL = range(88, 126)
BATCH = 6

SCENARIOS = {
    "stationary": {},
    "trend +1.5%/wk": {"scenario": "trend"},
    "level shift +30%": {"scenario": "level_shift"},
    "drop -30%": {"scenario": "drop"},
    "slow wave ±20%": {"scenario": "slow_wave"},
    "closed days 5%": {"closed_prob": 0.05},
    "missing rows 10%": {"missing_prob": 0.10},
    "big orders 2%": {"outlier_prob": 0.02},
    "low volume (2-6/day)": {"bases": (2, 3, 4, 5, 6)},
    "stockouts flagged": {"stockout_mode": "exclude", "stockout_slack": 0.1},
    "stockouts NOT flagged": {"stockout_mode": "hide", "stockout_slack": 0.1},
}


def target_rows(dataset, eval_days=EVAL):
    """Observed open target rows, including excluded stockout training labels."""
    dates = {(START + timedelta(days=t)).isoformat() for t in eval_days}
    return {(r["date"], r["product_id"]): r for r in dataset["rows"]
            if r["is_open"] and r["date"] in dates and r.get("_demand") is not None}


def forecast_rows(ds, implementation, horizon=2, train_cutoff=TRAIN_CUTOFF):
    model = implementation.train_model(ds, START + timedelta(days=train_cutoff))
    predictions = {}
    for key, row in target_rows(ds).items():
        target = implementation._day(row["date"])
        predictions[key] = implementation.forecast_model(
            model, ds, row["product_id"], target, target - timedelta(days=horizon)
        )
    return model, predictions


def score(ds, truth, model, predictions, cohort=None):
    targets = target_rows(ds)
    eligible = set(targets)
    usable = {k for k, f in predictions.items()
              if f.get("forecast_mean") is not None and f.get("b1_mean") is not None}
    cohort = usable if cohort is None else usable & set(cohort)
    out = defaultdict(list)
    for key in sorted(cohort):
        row, f = targets[key], predictions[key]
        mu, demand = truth[key], row["_demand"]
        for tag, pred in (("b1", f["b1_mean"]), ("ml", f["forecast_mean"]),
                          ("conditional_mean", mu)):
            out[f"{tag}_ae_demand"].append(abs(demand - pred))
            out[f"{tag}_err_mu"].append(pred - mu)
            q = nearest_allowed_quantity(pred, BATCH, 10_000)
            out[f"{tag}_waste"].append(max(q - demand, 0))
            out[f"{tag}_short"].append(max(demand - q, 0))
        out["demand"].append(demand)
    res = {k: float(np.mean(v)) for k, v in out.items() if v}
    ev = model["evaluation"]
    res.update(n=len(cohort), eligible_n=len(eligible), numeric_n=len(usable),
               ml_n=sum(f["method"] == "ML" for f in predictions.values()),
               fallback_n=sum(f["method"] != "ML" for f in predictions.values()),
               missing_prediction_n=len(eligible - usable),
               excluded_label_target_n=sum(core._label(r, ds["target"])[0] is None
                                           for r in targets.values()),
               fallback=sum(f["method"] != "ML" for f in predictions.values()) / len(eligible)
                        if eligible else 0.0,
               internal_b1_mae=ev.get("b1_mae"), internal_ml_mae=ev.get("ml_mae"),
               internal_n=ev.get("paired_n"))
    return res


def run(name, kw, seed, horizon=2, train_cutoff=TRAIN_CUTOFF, implementation=core):
    ds, truth = make_dataset(seed=seed, **kw)
    model, predictions = forecast_rows(ds, implementation, horizon, train_cutoff)
    return score(ds, truth, model, predictions)


def summarize(results):
    def m(k):
        return float(np.mean([r[k] for r in results]))

    ratio = np.array([r["ml_ae_demand"] / r["b1_ae_demand"] for r in results])
    internal = [
        (
            r["internal_ml_mae"] < r["internal_b1_mae"],
            r["ml_ae_demand"] < r["b1_ae_demand"],
        )
        for r in results
        if r["internal_ml_mae"] is not None
    ]
    agree = np.mean([a == b for a, b in internal]) if internal else float("nan")
    return {
        "seeds": len(results),
        "eligible_n": sum(r["eligible_n"] for r in results),
        "scored_n": sum(r["n"] for r in results),
        "ml_n": sum(r["ml_n"] for r in results),
        "missing_prediction_n": sum(r["missing_prediction_n"] for r in results),
        "excluded_label_target_n": sum(r["excluded_label_target_n"] for r in results),
        "mean_demand": m("demand"),
        "MAE_B1": m("b1_ae_demand"),
        "MAE_ML": m("ml_ae_demand"),
        "MAE_conditional_mean_reference": m("conditional_mean_ae_demand"),
        "ML/B1 ratio": float(ratio.mean()),
        "ML wins %": float((ratio < 1).mean() * 100),
        "bias_B1_vs_conditional_mean": m("b1_err_mu"),
        "bias_ML_vs_conditional_mean": m("ml_err_mu"),
        "waste_B1": m("b1_waste"),
        "short_B1": m("b1_short"),
        "waste_ML": m("ml_waste"),
        "short_ML": m("ml_short"),
        "fallback %": m("fallback") * 100,
        "holdout verdict agrees with future %": float(agree * 100),
    }


if __name__ == "__main__":
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    report = {}
    for name, kw in SCENARIOS.items():
        report[name] = summarize([run(name, kw, s) for s in range(seeds)])
        print(
            name,
            json.dumps({k: round(v, 3) for k, v in report[name].items()}),
            flush=True,
        )
    # horizon sensitivity: model evaluated for h=2, used at h=3 and h=1
    for h in (1, 3, 4):
        res = summarize([run("stationary", {}, s, horizon=h) for s in range(seeds)])
        report[f"stationary, horizon {h}"] = res
        print(
            f"horizon {h}",
            json.dumps({k: round(v, 3) for k, v in res.items()}),
            flush=True,
        )
    # stale model: trained at day 41, used on days 85..125
    res = summarize(
        [run("trend", {"scenario": "trend"}, s, train_cutoff=41) for s in range(seeds)]
    )
    report["trend, stale model (trained 6 wks earlier)"] = res
    print("stale", json.dumps({k: round(v, 3) for k, v in res.items()}), flush=True)
    json.dump(report, open(HERE / "backtest_report.json", "w"), indent=1)
