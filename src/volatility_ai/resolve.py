"""Outcome resolution.

Resolution reads market data published *after* a prediction was written and
records what actually happened. It never touches the prediction file: outcomes
live in their own artifacts (AGENTS.md rules 1, 7, 13). Nothing computed here is
allowed to flow back into a historical prediction.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .ids import iso, utc_now
from .providers.cboe import Bar, ProviderError, fetch_history

RESOLVED_ROOT = "predictions/resolved"
HORIZON_DAYS = {"1d": 1, "3d": 3, "5d": 5, "event": 3}


@dataclass(frozen=True)
class Outcome:
    actual_return_1d: float | None = None
    actual_return_3d: float | None = None
    actual_return_5d: float | None = None
    actual_event_return: float | None = None
    max_favorable_excursion: float | None = None
    max_adverse_excursion: float | None = None
    realized_volatility: float | None = None


@dataclass(frozen=True)
class Resolution:
    prediction_id: str
    ticker: str
    status: str
    outcome: Outcome | None = None
    detail: str | None = None
    path: str | None = None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _bars_after(bars: Sequence[Bar], prediction_time: datetime) -> list[Bar]:
    """Bars that closed strictly after the prediction was written.

    A prediction made during session D is resolved against D+1 onward. The
    session in progress is excluded even if its close later lands in the file,
    because part of it preceded the prediction.
    """
    cutoff = prediction_time.date()
    return [bar for bar in bars if bar.day > cutoff]


def compute_outcome(
    record: Mapping[str, Any],
    bars: Sequence[Bar],
    *,
    now: datetime | None = None,
) -> tuple[Outcome | None, str, str | None]:
    """Compute the outcome, or explain why it cannot be computed yet."""
    now = now or utc_now()
    entry = ((record.get("features") or {}).get("spot"))
    if not entry:
        return None, "unresolvable", "prediction stores no entry price"

    prediction_time = _parse_iso(record["timestamp"])
    future = _bars_after(bars, prediction_time)
    if not future:
        return None, "pending", "no sessions have closed since the prediction"

    horizon = str(record.get("horizon", "3d"))
    required = HORIZON_DAYS.get(horizon, 3)
    if len(future) < required:
        return (
            None,
            "pending",
            f"{len(future)}/{required} sessions closed since the prediction",
        )

    def horizon_return(days: int) -> float | None:
        if len(future) < days:
            return None
        return future[days - 1].close / float(entry) - 1.0

    window = future[:required]
    highs = [bar.high for bar in window]
    lows = [bar.low for bar in window]
    favorable = max(highs) / float(entry) - 1.0
    adverse = min(lows) / float(entry) - 1.0

    realized_volatility: float | None = None
    closes = [float(entry)] + [bar.close for bar in window]
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(returns) >= 2:
        realized_volatility = statistics.stdev(returns) * math.sqrt(252)

    outcome = Outcome(
        actual_return_1d=horizon_return(1),
        actual_return_3d=horizon_return(3),
        actual_return_5d=horizon_return(5),
        actual_event_return=horizon_return(required) if horizon == "event" else None,
        max_favorable_excursion=favorable,
        max_adverse_excursion=adverse,
        realized_volatility=realized_volatility,
    )
    return outcome, "resolved", None


def horizon_return(record: Mapping[str, Any], outcome: Mapping[str, Any] | Outcome) -> float | None:
    """The return that corresponds to the prediction's own horizon."""
    data = outcome if isinstance(outcome, Mapping) else asdict(outcome)
    horizon = str(record.get("horizon", "3d"))
    if horizon == "event":
        return data.get("actual_event_return") or data.get("actual_return_3d")
    return data.get(f"actual_return_{horizon}")


def outcome_path(prediction_id: str, root: str | Path = RESOLVED_ROOT) -> Path:
    return Path(root) / f"{prediction_id}.outcome.json"


def write_outcome(
    record: Mapping[str, Any],
    outcome: Outcome,
    *,
    root: str | Path = RESOLVED_ROOT,
    resolution_quality: str = "HIGH",
    source: str = "cboe_historical",
    now: datetime | None = None,
) -> Path:
    """Write the outcome artifact. Refuses to overwrite an existing one."""
    path = outcome_path(str(record["prediction_id"]), root)
    if path.exists():
        raise FileExistsError(f"Outcome already recorded and immutable: {path}")

    predicted = record.get("probabilities") or {}
    expected = record.get("expected_move") or {}
    realized = horizon_return(record, outcome)

    direction_correct: bool | None = None
    if realized is not None and predicted:
        highest = max(predicted, key=lambda key: predicted[key])
        if highest == "up":
            direction_correct = realized > 0
        elif highest == "down":
            direction_correct = realized < 0
        else:
            direction_correct = None

    within_range: bool | None = None
    if realized is not None and expected.get("up") is not None and expected.get("down") is not None:
        within_range = float(expected["down"]) <= realized <= float(expected["up"])

    payload = {
        "prediction_id": record["prediction_id"],
        "run_id": record.get("run_id"),
        "ticker": record["ticker"],
        "horizon": record.get("horizon"),
        "setup_type": record.get("setup_type"),
        "decision": record.get("decision"),
        "resolved_at": iso(now or utc_now()),
        "entry_price": (record.get("features") or {}).get("spot"),
        "outcome": asdict(outcome),
        "horizon_return": realized,
        "direction_correct": direction_correct,
        "move_within_expected_range": within_range,
        "resolution_quality": resolution_quality,
        "outcome_source": {"source_name": source, "source_type": "market_data"},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_outcomes(root: str | Path = RESOLVED_ROOT) -> dict[str, dict[str, Any]]:
    base = Path(root)
    if not base.exists():
        return {}
    outcomes: dict[str, dict[str, Any]] = {}
    for path in sorted(base.glob("*.outcome.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        prediction = payload.get("prediction_id")
        if prediction:
            outcomes[str(prediction)] = payload
    return outcomes


def resolve_due(
    records: Iterable[Mapping[str, Any]],
    *,
    root: str | Path = RESOLVED_ROOT,
    now: datetime | None = None,
    history_loader=fetch_history,
) -> list[Resolution]:
    """Resolve every prediction whose horizon has expired and is not yet resolved."""
    now = now or utc_now()
    already = load_outcomes(root)
    resolutions: list[Resolution] = []
    history_cache: dict[str, Sequence[Bar]] = {}

    for record in records:
        prediction = str(record.get("prediction_id", ""))
        ticker = str(record.get("ticker", ""))
        if prediction in already:
            continue

        due = record.get("resolution_due")
        if due and _parse_iso(due) > now:
            resolutions.append(Resolution(prediction, ticker, "not_due", detail=f"due {due}"))
            continue

        if ticker not in history_cache:
            try:
                history_cache[ticker] = history_loader(ticker)
            except ProviderError as error:
                resolutions.append(Resolution(prediction, ticker, "error", detail=str(error)))
                continue

        outcome, status, detail = compute_outcome(record, history_cache[ticker], now=now)
        if outcome is None:
            resolutions.append(Resolution(prediction, ticker, status, detail=detail))
            continue

        path = write_outcome(record, outcome, root=root, now=now)
        resolutions.append(
            Resolution(prediction, ticker, "resolved", outcome=outcome, path=str(path))
        )
    return resolutions
