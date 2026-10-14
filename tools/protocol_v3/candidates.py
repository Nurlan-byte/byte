"""Fast candidate comparison with the same as-of rules as ml_core (features at t-2).

Dev seeds choose; confirm seeds (disjoint) only report. Selection rule fixed up front:
lowest mean MAE ratio vs B1 across scenarios, subject to worst-scenario ratio <= 1.02.
"""

import hashlib
import json
import subprocess
import math
import sys
from collections import defaultdict
from datetime import timedelta

import numpy as np

from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]

from backend import ml_core as core  # noqa: E402
from gen import START, make_dataset  # noqa: E402
import reference_v2  # noqa: E402

H = 2
SCEN = {
    "stationary": {},
    "trend": {"scenario": "trend"},
    "shift+30": {"scenario": "level_shift"},
    "drop-30": {"scenario": "drop"},
    "wave": {"scenario": "slow_wave"},
    "closed5%": {"closed_prob": 0.05},
    "missing10%": {"missing_prob": 0.10},
    "orders2%": {"outlier_prob": 0.02},
    "lowvol": {"bases": (2, 3, 4, 5, 6)},
    "stockouts": {"stockout_mode": "exclude", "stockout_slack": 0.1},
    "mixed": {
        "scenario": "trend",
        "closed_prob": 0.03,
        "outlier_prob": 0.01,
        "missing_prob": 0.05,
        "stockout_mode": "exclude",
        "stockout_slack": 0.2,
    },
}


def level_ratios(history, target, cutoff):
    """mean(value_i / B1_i) over recent observed days i <= cutoff; B1_i uses only days < i."""
    end = min(cutoff.toordinal(), target.toordinal() - 1)
    out = []
    for window in (7, 14):
        ratios = []
        for o in range(end - window + 1, end + 1):
            if o not in history:
                continue
            prev = [history[o - k] for k in range(7, 57, 7) if (o - k) in history][:4]
            if not prev:
                continue
            b = sum(prev) / len(prev)
            ratios.append((history[o] + 1.0) / (b + 1.0))
        out.append(sum(ratios) / len(ratios) if ratios else math.nan)
    return out


def prepare(kw, seed, days=126):
    ds, truth = make_dataset(seed=seed, days=days, **kw)
    _, records, _ = core._snapshot(ds, START + timedelta(days=days - 1))
    hist = core._history(records)
    demand = {
        (r["date"], r["product_id"]): r["_demand"] for r in ds["rows"] if r["is_open"]
    }
    samples = defaultdict(dict)  # product -> day_idx -> (features, b1, label)
    for r in records:
        day = core._day(r["date"])
        idx = (day - START).days
        f, b = core._features(hist[r["product_id"]], day, day - timedelta(days=H))
        if f is None or not core._safe_features(f):
            continue
        lr = level_ratios(hist[r["product_id"]], day, day - timedelta(days=H))
        samples[r["product_id"]][idx] = (
            f + lr,
            b["b1_mean"],
            r["value"],
            demand.get((r["date"], r["product_id"])),
        )
    return samples


# ---------- candidate feature maps ----------
def rel(f):
    f = f[:11]
    b1 = f[0]
    g = (
        [0.0]
        + [f[i] - b1 if not math.isnan(f[i]) else math.nan for i in range(1, 7)]
        + list(f[7:])
    )
    return g


def ratio(f):
    f = f[:11]
    b1 = f[0]
    s = max(b1, 1.0)
    return [
        (f[i] - b1) / s if not math.isnan(f[i]) else math.nan for i in range(1, 7)
    ] + [f[7] / 6.0, f[8] / 27.0, f[9], f[10]]


def ratio2(f):
    return ratio(f) + [x - 1.0 if not math.isnan(x) else math.nan for x in f[11:13]]


class Ridge:
    """Same preprocessing as ml_core._fit (median impute + missing flags + standardize)."""

    def __init__(self, alpha, weighted=False):
        self.alpha, self.weighted = alpha, weighted

    def fit(self, X, y, w=None):
        X = np.asarray(X, float)
        miss = np.isnan(X)
        med = np.array(
            [np.median(c[~np.isnan(c)]) if np.any(~np.isnan(c)) else 0.0 for c in X.T]
        )
        Z = np.concatenate([np.where(miss, med, X), miss], axis=1).astype(float)
        mu, sd = Z.mean(0), Z.std(0)
        sd[sd < 1e-12] = 1
        Z = (Z - mu) / sd
        y = np.asarray(y, float)
        w = np.ones(len(y)) if w is None or not self.weighted else np.asarray(w, float)
        b0 = np.average(y, weights=w)
        W = w[:, None]
        coef = np.linalg.solve(
            Z.T @ (W * Z) + self.alpha * np.eye(Z.shape[1]), Z.T @ (w * (y - b0))
        )
        self.p = (med, mu, sd, coef, b0)
        return self

    def predict(self, x):
        med, mu, sd, coef, b0 = self.p
        x = np.asarray(x, float)
        miss = np.isnan(x)
        z = (np.concatenate([np.where(miss, med, x), miss]) - mu) / sd
        return float(b0 + z @ coef)


def run_candidate(name, samples, train_end, eval_days, refit_every=None):
    """Returns list of (pred, b1, label, demand) for eval days."""
    out = []
    models = {}

    def fit(cut):
        tr = {
            p: [(i, s) for i, s in d.items() if i <= cut and s[2] is not None]
            for p, d in samples.items()
        }
        if name.startswith("v2"):  # immutable historical per-product absolute fit
            return {
                p: reference_v2._fit(
                    [
                        {"features": s[0][:11], "b1": s[1], "value": s[2]}
                        for _, s in rows
                    ]
                )
                for p, rows in tr.items()
                if len(rows) >= core.MIN_TRAIN_ROWS
            }
        if name.startswith("rel_pp"):
            a = float(name.split("_a")[1])
            return {
                p: Ridge(a).fit(
                    [rel(s[0]) for _, s in rows], [s[2] - s[1] for _, s in rows]
                )
                for p, rows in tr.items()
                if len(rows) >= core.MIN_TRAIN_ROWS
            }
        if name.startswith("pool"):
            a = float(name.split("_a")[1])
            fm = ratio2 if name.startswith("pool2") else ratio
            X, y, w = [], [], []
            for p, rows in tr.items():
                for _, s in rows:
                    X.append(fm(s[0]))
                    y.append((s[2] - s[1]) / max(s[1], 1.0))
                    w.append(max(s[1], 1.0))
            return {"*": Ridge(a, weighted="w" in name.split("_a")[0]).fit(X, y, w)}
        return {}

    def predict(m, p, s):
        f, b1 = s[0], s[1]
        if name == "B1":
            return b1
        if name.startswith("v2"):
            return reference_v2._predict(m[p], f[:11], b1) if p in m else b1
        if name.startswith("rel_pp"):
            return max(0.0, b1 + m[p].predict(rel(f))) if p in m else b1
        if name == "B2":
            lr = f[12] if not math.isnan(f[12]) else 1.0
            return max(0.0, (b1 + 1.0) * min(max(lr, 0.5), 2.0) - 1.0)
        if name.startswith("pool"):
            fm = ratio2 if name.startswith("pool2") else ratio
            r = m["*"].predict(fm(f))
            r = min(max(r, -0.8), 2.0)
            return max(0.0, b1 + r * max(b1, 1.0))
        raise ValueError(name)

    last_fit = None
    for t in eval_days:
        cut = (
            train_end
            if refit_every is None
            else (t - H) - ((t - H - train_end) % refit_every)
        )
        if cut != last_fit:
            models = fit(cut)
            last_fit = cut
        for p, d in samples.items():
            s = d.get(t)
            if s is None or s[3] is None:
                continue
            out.append((predict(models, p, s), s[1], s[2], s[3]))
    return out


def evaluate(cands, seeds, refit_every=None):
    res = {c: defaultdict(list) for c in cands}
    per_seed = []
    for sc, kw in SCEN.items():
        for seed in seeds:
            samples = prepare(kw, seed)
            evidence = {"scenario": sc, "seed": seed, "models": {}}
            for c in cands:
                pairs = run_candidate(c, samples, 83, range(88, 126), refit_every)
                mae = np.mean([abs(d - p) for p, b, y, d in pairs])  # vs true demand
                b1 = np.mean([abs(d - b) for p, b, y, d in pairs])
                res[c][sc].append(mae / b1)
                evidence["models"][c] = {"n": len(pairs), "MAE": float(mae), "MAE_B1": float(b1), "ratio": float(mae / b1),
                                          "excluded_label_target_n": sum(y is None for _, _, y, _ in pairs)}
            assert len({v["n"] for v in evidence["models"].values()}) == 1
            per_seed.append(evidence)
    table = {}
    for c in cands:
        per = {sc: float(np.mean(v)) for sc, v in res[c].items()}
        table[c] = {
            "mean": float(np.mean(list(per.values()))),
            "worst": max(per.values()),
            **per,
        }
    return {"summary": table, "per_seed": per_seed,
            "seeds": list(seeds), "refit_every": refit_every,
            "interpretation": "Corrected audit rerun on previously inspected seeds, not fresh confirmation.",
            "provenance": {
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=HERE, text=True).strip(),
                "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=HERE, text=True).strip()),
                "source_sha256": {str(p.relative_to(HERE.parents[1])): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in [HERE / "candidates.py", HERE / "gen.py", HERE / "reference_v2.py", HERE.parents[1] / "backend/ml_core.py"]}},
            "reference_v2": json.loads((HERE / "reference_v2_provenance.json").read_text()),
            "cohort": "Feature-eligible observed open targets with latent demand, including excluded training labels. All candidates use identical targets."}


if __name__ == "__main__":
    mode = sys.argv[1]
    cands = sys.argv[2].split(",")
    refit = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != "-" else None
    seeds = range(0, 12) if mode == "dev" else range(100, 130)
    t = evaluate(cands, seeds, refit)
    for c, row in sorted(t["summary"].items(), key=lambda kv: kv[1]["mean"]):
        print(f"{c:18s}", " ".join(f"{k}={v:.3f}" for k, v in row.items()), flush=True)
    json.dump(t, open(HERE / f"cand_{mode}_{refit}.json", "w"), indent=1)
