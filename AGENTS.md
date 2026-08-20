# AGENTS.md — Trading AI Prediction Repository

## Purpose
This repository implements a research-grade market opportunity prediction system.
It produces probabilistic predictions for earnings, volatility and catalyst-driven
setups, records point-in-time inputs, resolves outcomes after the horizon expires,
and evaluates/calibrates the system over historical predictions.

## Source of truth
- `docs/` = project knowledge and design decisions.
- `prompts/` = versioned LLM instructions.
- `config/` = scoring thresholds and weights.
- `schemas/` = immutable data contracts.
- `predictions/` = prediction records; historical records are append-only.
- `results/` = resolved outcomes and reports.
- `models/` = current and historical calibration artifacts.
- `src/` = implementation.

## Non-negotiable rules
1. Never overwrite a historical prediction.
2. Never modify a prediction's original timestamp or `data_cutoff`.
3. Never use information published after `data_cutoff` in a prediction.
4. Every prediction must have a unique `prediction_id`.
5. Every prediction must store `framework_version`, `prompt_version`, `scoring_version`, and `calibration_version`.
6. Every prediction must store the feature snapshot used to create it.
7. A resolver may add outcomes, but may not alter the original prediction payload.
8. Never infer missing market/options/news data. Use `null` plus a data-quality flag.
9. Never treat high implied volatility as a directional signal by itself.
10. Never treat social sentiment as factual confirmation of a market event.
11. No automatic trading/execution is implemented in v0.1.
12. No model-weight change is allowed from a single outcome.
13. Calibration changes must be reproducible and versioned.
14. Backtests must be point-in-time safe and must explicitly document data availability.
15. Keep secrets out of Git. Use environment variables and `.env` locally; commit only `.env.example`.

## Workflow
1. Scan market candidates.
2. Collect point-in-time inputs.
3. Run analyst LLM prompts.
4. Validate structured output against JSON Schema.
5. Calculate deterministic scores.
6. Save prediction as append-only JSON + database row.
7. After horizon/event expiry, resolve actual outcomes.
8. Run metrics and calibration periodically.
9. Propose scoring changes only after sufficient sample size and out-of-sample checks.
10. Human reviews any change to production scoring weights.

## Coding standards
- Python 3.12+.
- Type hints for public functions.
- Small, testable functions.
- Deterministic scoring logic separated from LLM calls.
- No network calls inside unit tests.
- Use UTC timestamps internally and ISO-8601 strings.
- Prefer decimal-safe handling for prices/returns when practical.
- Fail closed on missing critical data.

## Validation
Before proposing a merge:
- run unit tests;
- run schema validation fixtures;
- run an offline smoke test;
- verify no historical prediction files changed unexpectedly;
- show a concise summary of changed files and tests.

## Git discipline
- Do not rewrite history.
- Do not amend existing commits.
- Keep commits small and meaningful.
- Use branches/PRs for scoring or schema changes.
- Include a migration note whenever the prediction contract changes.

## Decision semantics
- `LONG` / `SHORT` are research signals only.
- `NO_TRADE` means the evidence is insufficient or asymmetric payoff is unattractive.
- `confidence` measures reliability of the analysis/data, not probability of direction.
- `probability_up + probability_flat + probability_down` must equal 1 within tolerance.

## Documentation map
Read `docs/architecture.md` before architectural changes.
Read `docs/prediction-lifecycle.md` before touching prediction/resolution flow.
Read `docs/calibration.md` before changing weights or calibration logic.
Read the relevant prompt file before changing LLM task behavior.

---

## Run layer (v0.2)

The v0.1 rules above are unchanged and remain binding. This section adds the rules that
govern recorded runs and the learning loop built on top of them.

### Two-command workflow
A run is `vol scan` followed by `vol predict`, never a single step.
- `vol scan` performs the quantitative pass and writes an immutable snapshot per ticker.
- `vol predict` supplies analyst features and re-scores **against the stored snapshot**.
  It must never fetch fresher market data: a prediction stays anchored to the scan's
  `data_cutoff` however long the analyst pass takes.

### Additional non-negotiable rules
16. Every run writes a manifest, including runs that produce no prediction. An empty run
    is evidence about the market, not a non-event.
17. Analyst features must be omitted when they cannot be supported by a dated source.
    Writing a neutral value in place of unknown evidence is fabrication, not caution.
18. The analyst layer never writes a score, probability, expected move or decision.
    If the engine looks wrong, record a lesson; do not overwrite a computed number.
19. IV history is append-only and capped at one observation per ticker per UTC day.
20. A lesson is a pattern with evidence across multiple predictions, never a single
    disappointing outcome.
21. Run reports state what was missing. Silently degrading data quality is a defect.

### Version compatibility
Schema changes in v0.2 are additive and optional only, so every v0.1 record still
validates. The v0.1 module paths (`scoring`, `generate_id`, `validate_prediction`,
`resolve_prediction`) remain importable as shims over the `volatility_ai` package.

`requires-python` was relaxed from 3.12 to 3.11 so the pipeline runs in the current
environment. No 3.12-only syntax is used.
