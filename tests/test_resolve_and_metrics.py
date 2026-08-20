from datetime import date, datetime, timedelta, timezone

import pytest

from volatility_ai import metrics as metrics_module
from volatility_ai import resolve as resolve_module
from volatility_ai.providers.cboe import Bar

BASE = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)


def _prediction(**overrides):
    record = {
        "prediction_id": "20260820T130000Z-TEST-abcd1234",
        "ticker": "TEST",
        "timestamp": "2026-08-20T13:00:00Z",
        "data_cutoff": "2026-08-20T12:55:00Z",
        "horizon": "3d",
        "resolution_due": "2026-08-25T23:00:00Z",
        "probabilities": {"up": 0.55, "flat": 0.25, "down": 0.20},
        "expected_move": {"up": 0.06, "down": -0.05, "market_implied": 0.05},
        "scores": {"volatility": 85, "opportunity": 70},
        "decision": "LONG",
        "setup_type": "EVENT_IV",
        "features": {"spot": 100.0},
    }
    record.update(overrides)
    return record


def _bars(closes, start=date(2026, 8, 20)):
    bars = []
    day = start
    for close in closes:
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        bars.append(Bar(day=day, open=close, high=close * 1.02, low=close * 0.98, close=close, volume=1e6))
    return bars


def test_resolution_waits_for_enough_closed_sessions():
    outcome, status, detail = resolve_module.compute_outcome(_prediction(), _bars([101, 102]))
    assert outcome is None
    assert status == "pending"
    assert "2/3" in detail


def test_session_in_progress_is_excluded():
    """A bar dated the day of the prediction partly precedes it, so it cannot count."""
    same_day = Bar(day=date(2026, 8, 20), open=100, high=120, low=99, close=118, volume=1e6)
    outcome, status, _ = resolve_module.compute_outcome(
        _prediction(), [same_day] + _bars([101, 102, 103])
    )
    assert status == "resolved"
    # 103/100 - 1, not the 118 print from the prediction's own session.
    assert outcome.actual_return_3d == pytest.approx(0.03)


def test_outcome_captures_excursions():
    outcome, status, _ = resolve_module.compute_outcome(_prediction(), _bars([105, 95, 103]))
    assert status == "resolved"
    assert outcome.max_favorable_excursion == pytest.approx(105 * 1.02 / 100 - 1)
    assert outcome.max_adverse_excursion == pytest.approx(95 * 0.98 / 100 - 1)
    assert outcome.realized_volatility > 0


def test_resolution_never_touches_the_prediction(tmp_path):
    record = _prediction()
    before = dict(record)
    outcome, _, _ = resolve_module.compute_outcome(record, _bars([101, 102, 103]))
    resolve_module.write_outcome(record, outcome, root=tmp_path)
    assert record == before
    assert (tmp_path / f"{record['prediction_id']}.outcome.json").exists()


def test_outcome_is_written_once(tmp_path):
    record = _prediction()
    outcome, _, _ = resolve_module.compute_outcome(record, _bars([101, 102, 103]))
    resolve_module.write_outcome(record, outcome, root=tmp_path)
    with pytest.raises(FileExistsError):
        resolve_module.write_outcome(record, outcome, root=tmp_path)


# --- metrics ---------------------------------------------------------------

CALIBRATION = {"outcome": {"flat_band_vs_implied": 0.5, "flat_band_absolute": 0.01}}


def _pair(realized, probability_up=0.55, implied=0.05, **overrides):
    record = _prediction(
        probabilities={"up": probability_up, "flat": 0.2, "down": round(0.8 - probability_up, 4)},
        expected_move={"up": 0.06, "down": -0.05, "market_implied": implied},
        **overrides,
    )
    outcome = {"prediction_id": record["prediction_id"], "horizon_return": realized}
    return record, outcome


def test_flat_band_scales_with_implied_move():
    """A 1% move is flat for a volatile name and directional for a quiet one."""
    loud, loud_outcome = _pair(0.01, implied=0.10)
    loud_outcome["prediction_id"] = "loud"
    loud["prediction_id"] = "loud"
    quiet, quiet_outcome = _pair(0.01, implied=0.005)
    quiet_outcome["prediction_id"] = "quiet"
    quiet["prediction_id"] = "quiet"

    pairs = metrics_module.join(
        [loud, quiet], {"loud": loud_outcome, "quiet": quiet_outcome}, CALIBRATION
    )
    buckets = {pair.prediction["prediction_id"]: pair.bucket for pair in pairs}
    assert buckets == {"loud": "flat", "quiet": "up"}


def test_brier_skill_is_zero_for_a_base_rate_forecast():
    """A forecast that only repeats the base rate has learned nothing, by definition.

    Outcomes here are 70% up / 30% down and never flat, so the base-rate forecast
    is exactly (0.7, 0.0, 0.3). Skill must come out at zero.
    """
    records, outcomes = [], {}
    for index in range(20):
        realized = 0.10 if index < 14 else -0.10
        record, outcome = _pair(realized)
        record["probabilities"] = {"up": 0.7, "flat": 0.0, "down": 0.3}
        record["prediction_id"] = outcome["prediction_id"] = f"p{index}"
        records.append(record)
        outcomes[f"p{index}"] = outcome

    report = metrics_module.compute(records, outcomes, CALIBRATION)
    assert report.sample_size == 20
    assert report.metrics["brier_skill"] == pytest.approx(0.0, abs=1e-9)


def test_brier_skill_rewards_a_forecast_that_separates_outcomes():
    """Confident *and* correct per case must beat the base rate."""
    records, outcomes = [], {}
    for index in range(20):
        goes_up = index < 14
        record, outcome = _pair(0.10 if goes_up else -0.10)
        record["probabilities"] = (
            {"up": 0.9, "flat": 0.0, "down": 0.1} if goes_up else {"up": 0.1, "flat": 0.0, "down": 0.9}
        )
        record["prediction_id"] = outcome["prediction_id"] = f"p{index}"
        records.append(record)
        outcomes[f"p{index}"] = outcome

    report = metrics_module.compute(records, outcomes, CALIBRATION)
    assert report.metrics["brier_skill"] > 0.5


def test_volatility_detection_counts_real_events():
    record, outcome = _pair(0.12, implied=0.05)
    record["prediction_id"] = outcome["prediction_id"] = "hit"
    pairs = metrics_module.join([record], {"hit": outcome}, CALIBRATION)
    detection = metrics_module.volatility_detection(pairs, CALIBRATION)
    assert detection["true_positive"] == 1
    assert detection["precision"] == 1.0
