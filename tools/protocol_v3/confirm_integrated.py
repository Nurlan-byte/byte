"""Integrated train_model/forecast_model on FRESH seeds 200-229 (never used for design)."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]

import backtest  # noqa: E402
from backend import ml_core as core  # noqa: E402

SEEDS = range(200, 230)


def main(tag):
    out = {"protocol": core.PROTOCOL_VERSION, "seeds": [SEEDS.start, SEEDS.stop - 1]}
    for name, kw in backtest.SCENARIOS.items():
        s = backtest.summarize([backtest.run(name, kw, seed) for seed in SEEDS])
        out[name] = s
        print(
            f"{name:24s} B1={s['MAE_B1']:.2f} ML={s['MAE_ML']:.2f} "
            f"ratio={s['ML/B1 ratio']:.3f} wins={s['ML wins %']:.0f}% "
            f"biasML={s['bias_ML_vs_true']:+.2f} "
            f"holdout_agrees={s['holdout verdict agrees with future %']:.0f}%",
            flush=True,
        )
    (HERE / f"confirm_{tag}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run")
