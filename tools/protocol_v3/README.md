# Protocol v3: synthetic design evidence

Generated data only. No bakery history was used to choose or tune protocol v3.
These scripts show *why* the design changed; they do not prove accuracy for a real bakery.

- `gen.py` — demand generator with a known true mean (weekly profile, negative-binomial noise,
  trend, level shift/drop, slow wave, closed days, missing rows, large orders, low volume,
  stockouts flagged or hidden).
- `candidates.py dev|confirm <names> [refit_days]` — fast comparison with the same as-of rules
  (features at t−2). Selection rule fixed before running: lowest mean MAE ratio to B1 across
  scenarios with worst scenario ≤ 1.02. Design chosen on seeds 0–11, reported on seeds 100–129.
- `confirm_integrated.py <tag>` — runs the real `train_model`/`forecast_model` on fresh seeds
  200–229 (never used for design): model frozen at day 83, rolling 2-day horizon on days 88–125,
  MAE against the true demand.
- `backtest.py` — scenario runner used by `confirm_integrated.py`.

Run from the repository root, e.g. `python tools/protocol_v3/confirm_integrated.py v3`.
