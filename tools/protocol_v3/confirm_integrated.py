"""Audit rerun on previously inspected seeds 200–229; not fresh confirmation."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]

import backtest  # noqa: E402
import reference_v2  # noqa: E402
from backend import ml_core as core  # noqa: E402
from gen import make_dataset  # noqa: E402

SEEDS = range(200, 230)
IMPLEMENTATIONS = {"v2": reference_v2, "v3": core}


def provenance():
    files = [HERE / n for n in ("reference_v2.py", "gen.py", "backtest.py", "confirm_integrated.py")]
    files.append(HERE.parents[1] / "backend/ml_core.py")
    return {
        "historical_reference": json.loads((HERE / "reference_v2_provenance.json").read_text()),
        "source_sha256": {str(p.relative_to(HERE.parents[1])): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=HERE, text=True).strip()),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=HERE, text=True).strip(),
        "protocols": {name: mod.PROTOCOL_VERSION for name, mod in IMPLEMENTATIONS.items()},
        "alpha": {name: mod.ALPHA for name, mod in IMPLEMENTATIONS.items()},
    }


def main(model="both", tag="audit"):
    selected = list(IMPLEMENTATIONS) if model == "both" else [model]
    out = {"interpretation": "Corrected audit rerun; seeds previously inspected, not fresh confirmation.",
           "seeds": list(SEEDS), "model_selection": model, "provenance": provenance(),
           "training_cutoff_day": backtest.TRAIN_CUTOFF, "eval_days": list(backtest.EVAL),
           "horizon": 2, "cohort": "Common numeric predictions on observed open targets, including excluded training labels; B1 fallbacks included and counted.",
           "bias_reference": "Generator conditional mean including large-order mixture; not an MAE-optimal oracle.",
           "scenarios": {}}
    for name, kw in backtest.SCENARIOS.items():
        results = {m: [] for m in selected}
        per_seed = []
        for seed in SEEDS:
            ds, truth = make_dataset(seed=seed, **kw)
            fitted = {m: backtest.forecast_rows(ds, IMPLEMENTATIONS[m]) for m in selected}
            common = set(backtest.target_rows(ds))
            common_ml = set(common)
            for _, predictions in fitted.values():
                common &= {k for k, f in predictions.items() if f.get("forecast_mean") is not None and f.get("b1_mean") is not None}
                common_ml &= {k for k, f in predictions.items() if f["method"] == "ML"}
            entry = {"seed": seed, "common_numeric_n": len(common), "common_ml_n": len(common_ml), "models": {}}
            for m, (artifact, predictions) in fitted.items():
                r = backtest.score(ds, truth, artifact, predictions, common)
                results[m].append(r)
                entry["models"][m] = r
            if len(selected) == 2:
                a, b = entry["models"]["v2"], entry["models"]["v3"]
                entry["v3_minus_v2_MAE"] = b["ml_ae_demand"] - a["ml_ae_demand"]
                entry["v3_over_v2_MAE"] = b["ml_ae_demand"] / a["ml_ae_demand"]
            per_seed.append(entry)
        scenario = {"models": {m: backtest.summarize(rs) for m, rs in results.items()}, "per_seed": per_seed}
        if len(selected) == 2:
            scenario["paired"] = {
                "mean_v3_minus_v2_MAE": float(np.mean([r["v3_minus_v2_MAE"] for r in per_seed])),
                "mean_v3_over_v2_MAE": float(np.mean([r["v3_over_v2_MAE"] for r in per_seed])),
                "v3_seed_wins": sum(r["v3_minus_v2_MAE"] < 0 for r in per_seed),
                "common_numeric_n": sum(r["common_numeric_n"] for r in per_seed),
                "common_ml_n": sum(r["common_ml_n"] for r in per_seed),
            }
        out["scenarios"][name] = scenario
        print(name, json.dumps(scenario.get("paired", scenario["models"])), flush=True)
    path = HERE / f"confirm_{tag}.json"
    path.write_text(json.dumps(out, indent=2, allow_nan=False) + "\n")
    print(path, flush=True)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("v2", "v3", "both"), required=True)
    parser.add_argument("--tag", default="audit")
    args = parser.parse_args()
    main(args.model, args.tag)
