import math
from datetime import date

import pytest

from volatility_ai.features import build_snapshot
from volatility_ai.scoring import ScoringEngine, coverage_weighted, interpolate, weighted_score

AS_OF = date(2026, 8, 20)


def test_interpolate_is_monotone_and_clamped():
    anchors = [[0.0, 0], [1.0, 50], [2.0, 100]]
    assert interpolate(anchors, -5) == 0
    assert interpolate(anchors, 0.5) == 25
    assert interpolate(anchors, 99) == 100


def test_missing_weights_are_redistributed_not_zero_filled():
    """A missing feature must not drag the score toward zero."""
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    score, coverage = coverage_weighted({"a": 80, "b": None, "c": 80}, weights)
    assert score == 80
    assert coverage == pytest.approx(0.7)


def test_weighted_score_matches_v01_contract():
    assert weighted_score({"a": 80, "b": 60}, {"a": 0.75, "b": 0.25}) == 75


def test_probabilities_sum_to_one(engine, chain, bars):
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    result = engine.score(snapshot, horizon="3d")
    assert sum(result.probabilities) == pytest.approx(1.0, abs=1e-9)


def test_bull_and_bear_are_independent_measures(engine):
    """Conflict must stay visible on both sides instead of collapsing to neutral.

    Two inputs pulling maximally against each other, and two inputs that are
    simply neutral, both produce a zero net edge. Keeping bull and bear separate
    is what distinguishes them: the conflict scores 50/50, the absence of
    evidence scores 0/0.
    """
    weights = {"x": 0.5, "y": 0.5}

    conflict_bull, conflict_bear, _ = ScoringEngine._bull_bear({"x": 100.0, "y": 0.0}, weights)
    assert conflict_bull == pytest.approx(50.0)
    assert conflict_bear == pytest.approx(50.0)

    silent_bull, silent_bear, _ = ScoringEngine._bull_bear({"x": 50.0, "y": 50.0}, weights)
    assert silent_bull == pytest.approx(0.0)
    assert silent_bear == pytest.approx(0.0)

    # Both cases net to zero edge; only the bull/bear pair tells them apart.
    assert (conflict_bull - conflict_bear) == (silent_bull - silent_bear) == 0.0

    unanimous_bull, unanimous_bear, _ = ScoringEngine._bull_bear({"x": 100.0, "y": 100.0}, weights)
    assert unanimous_bull == pytest.approx(100.0)
    assert unanimous_bear == pytest.approx(0.0)


def test_no_directional_evidence_never_produces_a_directional_call(engine, chain, bars):
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    result = engine.score(snapshot, horizon="3d")
    if result.diagnostics["direction_weight_coverage"] < 0.3:
        assert result.decision in {"NO_TRADE", "HIGH_VOLATILITY_NO_DIRECTION"}


def test_probabilities_stay_conservative_under_maximum_evidence(engine, chain, bars):
    """v0.1 priors deliberately cap confidence well below certainty."""
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    maximal = {
        "catalyst": {"score": 100, "type": "EARNINGS", "why_now": "x", "priced_in_score": 0},
        "sentiment": {"score": 100, "momentum": 100},
        "direction": {
            "catalyst_direction": 100,
            "analyst_revisions": 100,
            "valuation_expectations": 100,
            "squeeze": 100,
        },
        "market_context": {"sector_score": 100, "regime_score": 100},
        "news": {"velocity": 100},
        "risks": [],
        "contradictions": [],
    }
    result = engine.score(snapshot, horizon="3d", llm_features=maximal)
    assert result.probabilities[0] < 0.80, "extreme inputs must not yield near-certainty"
    assert result.probabilities[0] > result.probabilities[2]


def test_priced_in_catalyst_shrinks_the_edge(engine, chain, bars):
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)

    def edge(priced_in: int) -> float:
        features = {
            "catalyst": {"score": 80, "type": "EARNINGS", "why_now": "x", "priced_in_score": priced_in},
            "sentiment": {"score": 60, "momentum": 70},
            "direction": {"catalyst_direction": 70},
            "market_context": {"sector_score": 40},
            "risks": [],
            "contradictions": [],
        }
        return engine.score(snapshot, horizon="3d", llm_features=features).diagnostics["edge"]

    assert edge(100) < edge(0)


def test_expected_move_scales_with_horizon(engine, chain, bars):
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    one_day = engine.score(snapshot, horizon="1d").market_implied_move
    five_day = engine.score(snapshot, horizon="5d").market_implied_move
    assert five_day > one_day
    assert five_day / one_day == pytest.approx(math.sqrt(5), rel=0.05)
