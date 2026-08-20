from datetime import date

import pytest

from volatility_ai.ivhistory import IVHistoryStore
from volatility_ai.universe import build_pool, prescreen

# --- IV history ------------------------------------------------------------


def test_iv_rank_is_none_until_enough_history_exists(tmp_path):
    """Rule 8: report the gap, never a proxy dressed up as IV Rank."""
    store = IVHistoryStore(tmp_path, minimum_observations=10)
    for index in range(9):
        store.append("TEST", iv30=0.20 + index * 0.01, observed_at_utc=f"2026-08-{index + 1:02d}T12:00:00Z")
    stats = store.stats("TEST", 0.30)
    assert stats.iv_rank is None
    assert stats.iv_percentile is None
    assert stats.observations == 9


def test_iv_rank_unlocks_and_is_computed_from_owned_history(tmp_path):
    store = IVHistoryStore(tmp_path, minimum_observations=10)
    for index in range(10):
        store.append("TEST", iv30=0.10 + index * 0.02, observed_at_utc=f"2026-08-{index + 1:02d}T12:00:00Z")
    # History spans 0.10 to 0.28; 0.19 sits halfway.
    stats = store.stats("TEST", 0.19)
    assert stats.iv_rank == pytest.approx(50.0)
    assert stats.observations == 10


def test_history_is_capped_at_one_observation_per_day(tmp_path):
    """Re-running a scan must not inflate the series a future IV Rank rests on."""
    store = IVHistoryStore(tmp_path, minimum_observations=1)
    store.append("TEST", iv30=0.25, observed_at_utc="2026-08-20T12:00:00Z")
    assert store.already_recorded_today("TEST", "2026-08-20") is True
    assert store.already_recorded_today("TEST", "2026-08-21") is False


def test_history_is_append_only(tmp_path):
    store = IVHistoryStore(tmp_path, minimum_observations=1)
    store.append("TEST", iv30=0.25, observed_at_utc="2026-08-20T12:00:00Z")
    store.append("TEST", iv30=0.31, observed_at_utc="2026-08-21T12:00:00Z")
    observations = store.load("TEST")
    assert [round(o.iv30, 2) for o in observations] == [0.25, 0.31]


# --- universe --------------------------------------------------------------

CONFIG = {
    "earnings_window_days": 10,
    "movers": {"top_by_option_volume": 3, "exclude_roots": ["SPXW"]},
    "filters": {"minimum_session_option_volume": 100},
    "sizing": {"prescreen_maximum": 50},
    "always_consider": [],
}

VOLUME = {
    "SPXW": {"volume": 900_000.0},
    "AAA": {"volume": 50_000.0},
    "BBB": {"volume": 20_000.0},
    "CCC": {"volume": 5_000.0},
    "DDD": {"volume": 50.0},
}


def test_index_roots_and_thin_names_are_excluded():
    pool = build_pool(CONFIG, VOLUME, {}, as_of=date(2026, 8, 20))
    tickers = {candidate.ticker for candidate in pool}
    assert "SPXW" not in tickers, "index roots are not single-name equities"
    assert "DDD" not in tickers, "volume below the floor is not a mover"
    assert {"AAA", "BBB", "CCC"} <= tickers


def test_earnings_inside_the_window_join_the_pool():
    earnings = {
        "EEE": {"date": "2026-08-24", "session": "AMC", "source_url": "https://example.test"},
        "FFF": {"date": "2026-12-01", "session": "BMO", "source_url": "https://example.test"},
    }
    pool = build_pool(CONFIG, VOLUME, earnings, as_of=date(2026, 8, 20))
    by_ticker = {candidate.ticker: candidate for candidate in pool}
    assert "EEE" in by_ticker and "earnings" in by_ticker["EEE"].sources
    assert "FFF" not in by_ticker, "an event three months out is not this run's catalyst"


def test_prescreen_rejects_and_explains(monkeypatch):
    from volatility_ai import universe as universe_module
    from volatility_ai.providers.cboe import Quote

    def fake_quote(symbol, timeout=20.0):
        prices = {"AAA": 120.0, "BBB": 1.5, "CCC": 40.0}
        return Quote(
            symbol=symbol,
            retrieved_at="2026-08-20T12:00:00Z",
            spot=prices[symbol],
            prev_close=prices[symbol] * 0.95,
            price_change_percent=None,
            stock_volume=1_000_000,
            iv30=None if symbol == "CCC" else 0.5,
            iv30_change=0.02,
            iv30_change_percent=0.04,
            security_type="stock",
        )

    monkeypatch.setattr(universe_module, "fetch_quote", fake_quote)
    config = dict(CONFIG)
    config["filters"] = {"minimum_price": 3.0, "allowed_security_types": ["stock"], "minimum_session_option_volume": 100}
    config["prescreen_weights"] = {"option_volume_rank": 0.5, "iv_change": 0.3, "absolute_price_move": 0.2}

    pool = [c for c in build_pool(CONFIG, VOLUME, {}, as_of=date(2026, 8, 20))]
    survivors = prescreen(pool, config, workers=2)

    assert [c.ticker for c in survivors] == ["AAA"]
    rejected = {c.ticker: c.rejected for c in pool if c.rejected}
    assert "price_below" in rejected["BBB"]
    assert rejected["CCC"] == "no_iv30"
