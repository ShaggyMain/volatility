# Architecture

## Principle
The system separates **LLM interpretation** from **deterministic scoring**.

### LLM layer
Produces structured features such as:
- catalyst quality;
- priced-in score;
- sentiment and sentiment momentum;
- event-chain hypotheses;
- narrative conflicts;
- risk flags.

### Quant layer
Calculates:
- volatility score;
- volatility acceleration;
- bull/bear score;
- probability distribution;
- expected move;
- expected value;
- opportunity score.

### Storage layer
Stores immutable prediction snapshots and resolved outcomes.

### Evaluation layer
Computes accuracy, calibration, volatility hit rate, expected-move coverage and setup-level performance.

## Data flow

`raw data -> normalized snapshot -> LLM feature extraction -> deterministic scoring -> prediction contract -> outcome resolver -> evaluation -> calibration proposal`

## Anti-leakage
The prediction uses only records with `published_at <= data_cutoff`.
Outcome resolution happens separately and never feeds back into historical predictions.
