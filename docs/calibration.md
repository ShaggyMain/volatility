# Calibration policy

## What learning means in v0.1
Learning is not prompt accumulation. It is:

1. probability calibration;
2. feature-weight evaluation;
3. setup-specific performance analysis;
4. error analysis;
5. out-of-sample validation.

## Rules
- Never recalibrate on a single trade.
- Minimum suggested sample: 250 resolved observations for a production weight change.
- Keep a holdout set.
- Compare old vs proposed scoring with the same evaluation window.
- Require improvement in calibration and/or decision quality without unacceptable degradation elsewhere.
- Version every weight set.

## Metrics
- Brier score;
- log loss;
- calibration by probability bucket;
- directional accuracy;
- high-volatility detection precision/recall;
- expected-move coverage;
- average/median realized move by score bucket;
- max favorable excursion;
- max adverse excursion.
