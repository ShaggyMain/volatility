"""Shared fixtures. No test in this suite performs a network call."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from volatility_ai.providers.cboe import Bar, Chain, OptionContract
from volatility_ai.scoring import load_engine

AS_OF = date(2026, 8, 20)


def _contract(expiry: date, right: str, strike: float, iv: float, **overrides) -> OptionContract:
    defaults = {
        "option_symbol": f"TEST{expiry:%y%m%d}{right}{int(strike * 1000):08d}",
        "expiry": expiry,
        "right": right,
        "strike": strike,
        "bid": max(0.05, abs(100 - strike) * 0.1 + 2.0),
        "ask": max(0.10, abs(100 - strike) * 0.1 + 2.4),
        "last_trade_price": 2.2,
        "iv": iv,
        "open_interest": 1200.0,
        "volume": 400.0,
        "delta": 0.5 if right == "C" else -0.5,
        "gamma": 0.02,
        "vega": 0.1,
        "theta": -0.05,
    }
    defaults.update(overrides)
    return OptionContract(**defaults)


@pytest.fixture
def chain() -> Chain:
    """A synthetic chain in backwardation, i.e. a dated event ahead."""
    front = AS_OF + timedelta(days=2)
    term_front = AS_OF + timedelta(days=9)
    back = AS_OF + timedelta(days=52)
    contracts: list[OptionContract] = []
    for expiry, iv in ((front, 0.90), (term_front, 0.62), (back, 0.42)):
        for strike in (90.0, 95.0, 100.0, 105.0, 110.0):
            contracts.append(_contract(expiry, "C", strike, iv, delta=0.25 if strike == 105 else 0.5))
            contracts.append(_contract(expiry, "P", strike, iv + 0.03, delta=-0.25 if strike == 95 else -0.5))
    return Chain(
        symbol="TEST",
        retrieved_at="2026-08-20T09:05:00Z",
        source_timestamp="2026-08-20 08:50:00",
        spot=100.0,
        prev_close=96.0,
        day_open=97.0,
        day_high=101.0,
        day_low=96.5,
        stock_volume=9_000_000,
        iv30=0.65,
        iv30_change=0.05,
        contracts=tuple(contracts),
    )


@pytest.fixture
def bars() -> tuple[Bar, ...]:
    """300 sessions of a gently trending, mildly volatile series."""
    series: list[Bar] = []
    price = 70.0
    day = AS_OF - timedelta(days=430)
    index = 0
    while len(series) < 300:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        index += 1
        price *= 1.0 + 0.0012 + 0.02 * math.sin(index / 5.0)
        series.append(
            Bar(
                day=day,
                open=price * 0.995,
                high=price * 1.015,
                low=price * 0.985,
                close=price,
                volume=5_000_000,
            )
        )
    return tuple(series)


@pytest.fixture
def engine():
    return load_engine()
