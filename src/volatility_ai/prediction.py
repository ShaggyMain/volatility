"""Prediction records: build, validate, write.

A prediction file is the permanent record of what the system believed and why,
using only what it could see at ``data_cutoff``. Once written it is never
edited -- outcomes land in separate files (AGENTS.md rules 1, 2, 7).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from .features import FeatureSnapshot
from .ids import iso, prediction_id, resolution_due, utc_now
from .scoring import ScoreResult

FRAMEWORK_VERSION = "volatility_catalyst_v2.0"
PROMPT_VERSION = "volatility_catalyst_v2.0"
PREDICTIONS_ROOT = "predictions"
SCHEMA_PATH = "schemas/prediction.schema.json"


class ImmutabilityError(RuntimeError):
    """Raised on any attempt to overwrite an existing prediction file."""


def load_schema(path: str | Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(record: Mapping[str, Any], schema: Mapping[str, Any] | None = None) -> None:
    schema = schema or load_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(record), key=str)
    if errors:
        raise ValueError("; ".join(f"{list(e.path)}: {e.message}" for e in errors))


def check_probability_sum(record: Mapping[str, Any], tolerance: float = 0.001) -> None:
    probabilities = record["probabilities"]
    total = probabilities["up"] + probabilities["flat"] + probabilities["down"]
    if abs(total - 1.0) > tolerance:
        raise ValueError(f"Probabilities sum to {total}, outside tolerance {tolerance}")


def quant_thesis(snapshot: FeatureSnapshot, result: ScoreResult) -> str:
    """A factual, non-narrative thesis built only from computed values.

    Used when no analyst commentary exists. It deliberately describes the
    volatility state and says nothing about direction, so a machine-written
    record can never be mistaken for an analyst's directional call.
    """
    values = snapshot.values
    parts = [
        f"Deterministic record for {snapshot.ticker}: volatility score {result.scores.volatility:.0f}/100,"
        f" acceleration {result.scores.volatility_acceleration:.0f}/100."
    ]
    if values.get("iv30") is not None:
        parts.append(f"IV30 {values['iv30'] * 100:.1f}%.")
    if values.get("iv_rv20") is not None:
        parts.append(f"IV/RV20 {values['iv_rv20']:.2f}.")
    if values.get("term_slope") is not None:
        shape = "backwardation" if values["term_slope"] < 0 else "contango"
        parts.append(f"Term structure in {shape} ({values['term_slope'] * 100:+.1f} vol points).")
    parts.append("No analyst catalyst features were supplied, so no directional claim is made.")
    return " ".join(parts)


def build_record(
    snapshot: FeatureSnapshot,
    result: ScoreResult,
    *,
    horizon: str,
    run_identifier: str | None = None,
    thesis: str | None = None,
    key_catalyst: str | None = None,
    what_is_priced_in: str | None = None,
    invalidation_conditions: list[str] | None = None,
    llm_features: Mapping[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    calibration_version: str = "0.1",
    scoring_version: str = "0.1",
    event_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble a schema-valid prediction record.

    Fails closed rather than emitting a record with invented numbers: without a
    quotable expected move there is no prediction to make.
    """
    if result.expected_move_up is None or result.expected_move_down is None:
        raise ValueError(
            f"{snapshot.ticker}: no quotable expected move; refusing to write a prediction"
        )

    moment = now or utc_now()
    probability_up, probability_flat, probability_down = result.probabilities
    data_quality = _data_quality(snapshot, result)

    record: dict[str, Any] = {
        "prediction_id": prediction_id(snapshot.ticker, moment),
        "run_id": run_identifier,
        "ticker": snapshot.ticker,
        "timestamp": iso(moment),
        # Everything in the snapshot was public before it was retrieved; the
        # feed itself is delayed, which makes this cutoff conservative.
        "data_cutoff": snapshot.retrieved_at,
        "horizon": horizon,
        "event_id": event_id,
        "versions": {
            "framework": FRAMEWORK_VERSION,
            "prompt": PROMPT_VERSION,
            "scoring": scoring_version,
            "calibration": calibration_version,
            "normalization": result.diagnostics.get("normalization_version"),
        },
        "scores": result.scores.to_json(),
        "probabilities": {
            "up": round(probability_up, 4),
            "flat": round(probability_flat, 4),
            "down": round(probability_down, 4),
        },
        "expected_move": {
            "up": round(result.expected_move_up, 5),
            "down": round(result.expected_move_down, 5),
            "market_implied": (
                round(result.market_implied_move, 5) if result.market_implied_move is not None else None
            ),
        },
        "setup_type": result.setup_type,
        "decision": result.decision,
        "expected_value": round(result.expected_value, 5) if result.expected_value is not None else 0.0,
        "risk_reward": round(result.risk_reward, 4) if result.risk_reward is not None else None,
        "thesis": thesis or quant_thesis(snapshot, result),
        "thesis_source": "analyst" if thesis else "deterministic",
        "key_catalyst": key_catalyst,
        "what_is_priced_in": what_is_priced_in
        or "No analyst assessment of what is priced in was supplied for this record.",
        "invalidation_conditions": invalidation_conditions or _default_invalidations(snapshot, result),
        "features": snapshot.to_json(),
        "llm_features": dict(llm_features) if llm_features else None,
        "scoring_inputs": result.normalized_inputs,
        "diagnostics": result.diagnostics,
        "source_refs": source_refs or [],
        "data_quality": data_quality,
        "resolution_due": resolution_due(horizon, moment),
        "resolved_at": None,
    }
    return record


def _data_quality(snapshot: FeatureSnapshot, result: ScoreResult) -> str:
    coverage = snapshot.coverage
    weight_coverage = float(result.diagnostics.get("volatility_weight_coverage", 0.0))
    flags = len(snapshot.quality_flags)
    if coverage >= 0.85 and weight_coverage >= 0.75 and flags == 0:
        return "HIGH"
    if coverage >= 0.65 and weight_coverage >= 0.55 and flags <= 2:
        return "MEDIUM"
    return "LOW"


def _default_invalidations(snapshot: FeatureSnapshot, result: ScoreResult) -> list[str]:
    """Baseline invalidation conditions derived from the snapshot itself."""
    conditions = [
        "Implied volatility collapses without the expected move occurring (event passes or is cancelled).",
    ]
    values = snapshot.values
    if values.get("iv_rv20") is not None:
        conditions.append(
            f"IV/RV20 falls back below 1.0 from {values['iv_rv20']:.2f} without a realized move."
        )
    if values.get("term_slope") is not None and values["term_slope"] < 0:
        conditions.append("Term-structure backwardation flattens, indicating the dated event has passed.")
    if result.decision in {"LONG", "HIGH_CONVICTION_LONG", "LONG_BIAS_WATCH"}:
        conditions.append("Price closes below the pre-signal level on rising volume.")
    if result.decision in {"SHORT", "HIGH_CONVICTION_SHORT", "SHORT_BIAS_WATCH"}:
        conditions.append("Price closes above the pre-signal level on rising volume.")
    return conditions


def prediction_path(record: Mapping[str, Any], root: str | Path = PREDICTIONS_ROOT) -> Path:
    """``predictions/YYYY/MM/<prediction_id>.json``."""
    timestamp = str(record["timestamp"])
    year, month = timestamp[0:4], timestamp[5:7]
    return Path(root) / year / month / f"{record['prediction_id']}.json"


def write(record: Mapping[str, Any], root: str | Path = PREDICTIONS_ROOT) -> Path:
    """Validate and write a prediction. Refuses to overwrite anything."""
    validate(record)
    check_probability_sum(record)
    path = prediction_path(record, root)
    if path.exists():
        raise ImmutabilityError(f"Prediction already exists and is immutable: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_all(root: str | Path = PREDICTIONS_ROOT) -> list[dict[str, Any]]:
    """Load every prediction, newest last. Resolved outcome files are skipped."""
    base = Path(root)
    if not base.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*.json")):
        if "resolved" in path.parts or path.name.endswith(".outcome.json"):
            continue
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    records.sort(key=lambda record: str(record.get("timestamp", "")))
    return records
