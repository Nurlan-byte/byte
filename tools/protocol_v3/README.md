# Protocol v3: synthetic design audit

Generated data only. No bakery history was used to choose or tune protocol v3.
These scripts explain the design change and audit its evaluation; they do not prove
accuracy for a real bakery.

## Seed history and interpretation

- Seeds 0–11 were used for development; seeds 100–129 were used for reporting.
- Seeds 200–229 were already inspected in the earlier integrated comparison. The
  corrected rerun on those seeds is an audit rerun, not fresh confirmation or new
  independent evidence. Changing the implementation does not make inspected seeds fresh.
- The historical candidate-selection rule was lowest mean MAE ratio to B1 across
  scenarios with the worst scenario at most 1.02. Do not present this corrected audit
  as a new preregistered test of that rule.
- Scenario summaries average across seeds. A better scenario-average MAE does not
  mean a win on every seed; inspect seed win rates and coverage too.

## Procedure

Models are frozen at day 83 (84 training dates); forecasts use a rolling two-day
horizon on days 88–125. Features use only history available through target date t−2.
Compare frozen historical v2 and current v3 against B1 on the same observed, open
product/date cohort with numeric forecasts from both models. B1 fallbacks remain
in that common forecast cohort; report fallback counts and the smaller common
ML-only cohort separately. Missing rows and closed targets are excluded. Observed
open stockout targets with latent demand remain eligible even when their sales
labels are excluded from training.
The internal simulation holdout has 17 dates out of 84. The separate 56-date demo
fixture has a 12-date holdout; these are not interchangeable experiments.

Demand MAE uses realized latent demand, including demand hidden by stockouts, rather
than censored sales. The generator's conditional mean includes the large-order
mixture: for open days, base mean μ and order probability p give
`E[demand] = (1 + 3p)μ + 20p`. This mean is useful for measuring bias but is not an
MAE-optimal oracle: the conditional median minimizes expected absolute error.

## Scripts

- `gen.py` — known demand process with weekly profile, negative-binomial noise,
  trend, level shift/drop, slow wave, closed days, missing rows, large orders,
  low volume, and flagged or hidden stockouts.
- `candidates.py dev|confirm <names> [refit_days]` — historical fast candidate
  comparison using features at t−2. Its `confirm` command name does not establish
  independence of already inspected seeds.
- `confirm_integrated.py --model v2|v3|both --tag <tag>` — integrated audit on
  previously inspected seeds 200–229. Explicit model selection is required; `both`
  compares the frozen historical v2 implementation with current v3. The report
  records frozen-source provenance, code hashes, model parameters, seeds, per-seed
  metrics and coverage, and paired v3/v2 differences and wins.
- `backtest.py` — scenario runner used by the integrated audit.

Run from the repository root:

```sh
.venv/bin/python tools/protocol_v3/confirm_integrated.py --model both --tag audit
```

The report is written to `tools/protocol_v3/confirm_audit.json`. Conditional-mean
metrics use the name `conditional_mean`, replacing the misleading `oracle` label.
