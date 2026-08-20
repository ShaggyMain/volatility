from datetime import date, timedelta

import pytest

from volatility_ai.features import build_snapshot
from volatility_ai.providers.cboe import parse_option_symbol

AS_OF = date(2026, 8, 20)


def test_parse_option_symbol_handles_numeric_roots():
    root, expiry, right, strike = parse_option_symbol("BRKB260918P00412500")
    assert (root, right, strike) == ("BRKB", "P", 412.5)
    assert expiry == date(2026, 9, 18)


def test_snapshot_computes_core_volatility_features(chain, bars):
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    values = snapshot.values
    assert values["iv30"] == 0.65
    assert values["rv20"] is not None and values["rv20"] > 0
    assert values["iv_rv20"] == values["iv30"] / values["rv20"]
    assert values["expected_move_front"] > 0


def test_term_slope_skips_the_nearest_expiry(chain, bars):
    """A two-day option must not set the term structure reading."""
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    assert snapshot.meta["front_dte"] == 2
    assert snapshot.meta["term_front_dte"] == 9
    # 0.42 back minus 0.62 term-front, not 0.42 minus 0.90.
    assert snapshot.values["term_slope"] == pytest.approx(-0.20, abs=1e-9)


def test_one_day_return_comes_from_the_live_quote(chain, bars):
    """The daily bar file lags; the quote is the fresher point-in-time fact."""
    snapshot = build_snapshot(chain, bars, as_of=AS_OF)
    assert snapshot.values["return_1d"] == pytest.approx(100.0 / 96.0 - 1.0)


def test_missing_data_becomes_none_and_is_flagged(chain):
    """Rule 8: never infer. Short history yields None, not an estimate."""
    snapshot = build_snapshot(chain, [], as_of=AS_OF)
    assert snapshot.values["rv20"] is None
    assert snapshot.values["iv_rv20"] is None
    assert snapshot.coverage < 1.0


def test_stale_history_is_flagged(chain, bars):
    snapshot = build_snapshot(chain, bars, as_of=AS_OF + timedelta(days=30))
    assert any(flag.startswith("STALE_PRICE_HISTORY") for flag in snapshot.quality_flags)
