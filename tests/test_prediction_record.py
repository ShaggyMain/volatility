import json
from datetime import date
from pathlib import Path

import pytest

from volatility_ai import prediction as prediction_module
from volatility_ai.features import build_snapshot

AS_OF = date(2026, 8, 20)


def _record(engine, chain, bars, **kwargs):
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    result = engine.score(snapshot, horizon="3d")
    return prediction_module.build_record(snapshot, result, horizon="3d", **kwargs)


def test_record_validates_against_the_schema(engine, chain, bars):
    record = _record(engine, chain, bars, thesis="A" * 40)
    prediction_module.validate(record)
    prediction_module.check_probability_sum(record)


def test_v01_fixture_still_validates():
    """v0.2 schema changes are additive; a v0.1 record must remain valid."""
    fixture = json.loads(Path("tests/fixtures_prediction.json").read_text(encoding="utf-8"))
    prediction_module.validate(fixture)


def test_data_cutoff_comes_from_the_snapshot_not_write_time(engine, chain, bars):
    """The prediction is anchored to when data was pulled, not when it was saved.

    An analyst pass can take an hour; the cutoff must not drift with it.
    """
    from datetime import datetime, timezone

    written_an_hour_later = datetime(2026, 8, 20, 10, 5, tzinfo=timezone.utc)
    record = _record(engine, chain, bars, thesis="A" * 40, now=written_an_hour_later)
    assert record["data_cutoff"] == chain.retrieved_at == "2026-08-20T09:05:00Z"
    assert record["timestamp"] == "2026-08-20T10:05:00Z"
    assert record["timestamp"] > record["data_cutoff"]


def test_thesis_source_is_recorded(engine, chain, bars):
    analyst = _record(engine, chain, bars, thesis="Analyst-written thesis with enough length.")
    machine = _record(engine, chain, bars)
    assert analyst["thesis_source"] == "analyst"
    assert machine["thesis_source"] == "deterministic"
    assert "no directional claim" in machine["thesis"].lower()


def test_missing_expected_move_fails_closed(engine, chain, bars):
    """No quotable straddle means no prediction, never an invented one."""
    from volatility_ai.providers.cboe import Chain

    empty = Chain(**{**chain.__dict__, "contracts": (), "iv30": None})
    snapshot = build_snapshot(empty, bars, as_of=AS_OF)
    result = engine.score(snapshot, horizon="3d")
    with pytest.raises(ValueError, match="expected move"):
        prediction_module.build_record(snapshot, result, horizon="3d")


def test_predictions_are_immutable(engine, chain, bars, tmp_path):
    record = _record(engine, chain, bars, thesis="A" * 40)
    prediction_module.write(record, root=tmp_path)
    with pytest.raises(prediction_module.ImmutabilityError):
        prediction_module.write(record, root=tmp_path)


def test_probability_sum_is_enforced_on_write(engine, chain, bars, tmp_path):
    record = _record(engine, chain, bars, thesis="A" * 40)
    record["probabilities"]["up"] += 0.2
    with pytest.raises(ValueError, match="Probabilities sum"):
        prediction_module.write(record, root=tmp_path)


def test_data_quality_degrades_with_missing_inputs(engine, chain, bars):
    good = _record(engine, chain, bars, thesis="A" * 40)
    thin = build_snapshot(chain, bars[-30:], as_of=AS_OF)
    thin_record = prediction_module.build_record(
        thin, engine.score(thin, horizon="3d"), horizon="3d", thesis="A" * 40
    )
    order = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    assert order[thin_record["data_quality"]] <= order[good["data_quality"]]
