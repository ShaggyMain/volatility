"""Point-in-time feature extraction.

Every function here turns provider data into the raw feature snapshot that the
deterministic scorer consumes. Rule 8 of AGENTS.md applies throughout: when a
value cannot be computed from available data it is returned as ``None`` and the
scorer redistributes its weight. Nothing is inferred, interpolated from a peer,
or carried forward from an earlier session.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from .providers.cboe import Bar, Chain, OptionContract

TRADING_DAYS_PER_YEAR = 252

# Expiries closer than this are excluded from term-structure and skew readings.
TERM_STRUCTURE_MINIMUM_DTE = 7
# The far leg of the term-structure comparison.
TERM_STRUCTURE_BACK_DTE = 45


def _annualized_volatility(bars: Sequence[Bar], window: int) -> float | None:
    """Close-to-close realized volatility, annualized, as a decimal."""
    if len(bars) < window + 1:
        return None
    closes = [bar.close for bar in bars[-(window + 1) :]]
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _pct_return(bars: Sequence[Bar], window: int) -> float | None:
    if len(bars) < window + 1:
        return None
    start = bars[-(window + 1)].close
    if start <= 0:
        return None
    return bars[-1].close / start - 1.0


def _average_true_range_pct(bars: Sequence[Bar], window: int = 14) -> float | None:
    if len(bars) < window + 1:
        return None
    true_ranges: list[float] = []
    for index in range(len(bars) - window, len(bars)):
        current, previous = bars[index], bars[index - 1]
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    last_close = bars[-1].close
    if last_close <= 0 or not true_ranges:
        return None
    return (sum(true_ranges) / len(true_ranges)) / last_close


def _atm_contracts(
    contracts: Sequence[OptionContract], spot: float, expiry: date
) -> tuple[OptionContract | None, OptionContract | None]:
    """Return the call and put whose strike sits closest to spot for one expiry."""
    calls = [c for c in contracts if c.expiry == expiry and c.right == "C"]
    puts = [c for c in contracts if c.expiry == expiry and c.right == "P"]
    if not calls or not puts:
        return None, None
    call = min(calls, key=lambda c: abs(c.strike - spot))
    put = min(puts, key=lambda c: abs(c.strike - spot))
    return call, put


def _expiries(contracts: Sequence[OptionContract], as_of: date, minimum_dte: int = 1) -> list[date]:
    return sorted({c.expiry for c in contracts if (c.expiry - as_of).days >= minimum_dte})


def _atm_iv(contracts: Sequence[OptionContract], spot: float, expiry: date) -> float | None:
    call, put = _atm_contracts(contracts, spot, expiry)
    ivs = [c.iv for c in (call, put) if c is not None and c.iv > 0]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def _straddle_expected_move(
    contracts: Sequence[OptionContract], spot: float, expiry: date
) -> float | None:
    """Expected move implied by the at-the-money straddle, as a fraction of spot."""
    call, put = _atm_contracts(contracts, spot, expiry)
    if call is None or put is None:
        return None
    call_price = call.mid if call.mid is not None else (call.last_trade_price or None)
    put_price = put.mid if put.mid is not None else (put.last_trade_price or None)
    if not call_price or not put_price or spot <= 0:
        return None
    return (call_price + put_price) / spot


def _delta_bucket_iv(
    contracts: Sequence[OptionContract], expiry: date, right: str, target_delta: float
) -> float | None:
    candidates = [
        c
        for c in contracts
        if c.expiry == expiry and c.right == right and c.iv > 0 and c.delta != 0
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda c: abs(abs(c.delta) - abs(target_delta)))
    if abs(abs(best.delta) - abs(target_delta)) > 0.15:
        return None
    return best.iv


@dataclass
class FeatureSnapshot:
    """Raw, point-in-time features for one ticker.

    ``values`` holds numeric features (any of which may be ``None``);
    ``meta`` holds descriptive context that never feeds scoring directly.
    """

    ticker: str
    retrieved_at: str
    source_timestamp: str
    spot: float
    values: dict[str, float | None] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Fraction of numeric features that were actually computable."""
        if not self.values:
            return 0.0
        present = sum(1 for value in self.values.values() if value is not None)
        return present / len(self.values)

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "FeatureSnapshot":
        """Rebuild a snapshot from its stored form.

        The analyst step re-scores from the stored snapshot rather than
        re-fetching, so the prediction stays anchored to the original
        ``data_cutoff`` instead of quietly drifting to newer data.
        """
        return cls(
            ticker=str(payload["ticker"]),
            retrieved_at=str(payload["retrieved_at"]),
            source_timestamp=str(payload.get("source_timestamp", "")),
            spot=float(payload["spot"]),
            values=dict(payload.get("values") or {}),
            meta=dict(payload.get("meta") or {}),
            quality_flags=list(payload.get("quality_flags") or []),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "retrieved_at": self.retrieved_at,
            "source_timestamp": self.source_timestamp,
            "spot": self.spot,
            "coverage": round(self.coverage, 4),
            "values": self.values,
            "meta": self.meta,
            "quality_flags": self.quality_flags,
        }


def build_snapshot(
    chain: Chain,
    bars: Sequence[Bar],
    *,
    as_of: date,
    benchmark_bars: Sequence[Bar] | None = None,
    option_volume: dict[str, float] | None = None,
    option_volume_percentile: float | None = None,
    iv_rank: float | None = None,
    iv_percentile: float | None = None,
    iv_history_observations: int = 0,
) -> FeatureSnapshot:
    """Assemble every quantitative feature the scorer needs for one ticker."""
    snapshot = FeatureSnapshot(
        ticker=chain.symbol,
        retrieved_at=chain.retrieved_at,
        source_timestamp=chain.source_timestamp,
        spot=chain.spot,
    )
    values = snapshot.values
    meta = snapshot.meta

    # --- implied volatility -------------------------------------------------
    values["iv30"] = chain.iv30
    values["iv30_change"] = chain.iv30_change
    values["iv_rank"] = iv_rank
    values["iv_percentile"] = iv_percentile
    meta["iv_history_observations"] = iv_history_observations
    if iv_rank is None:
        snapshot.quality_flags.append(
            f"IV_RANK_UNAVAILABLE (own IV history: {iv_history_observations} obs)"
        )

    # --- realized volatility ------------------------------------------------
    rv5 = _annualized_volatility(bars, 5)
    rv20 = _annualized_volatility(bars, 20)
    rv60 = _annualized_volatility(bars, 60)
    rv252 = _annualized_volatility(bars, 252)
    values["rv5"] = rv5
    values["rv20"] = rv20
    values["rv60"] = rv60
    values["rv252"] = rv252
    values["iv_rv20"] = (chain.iv30 / rv20) if (chain.iv30 and rv20) else None
    values["iv_rv252"] = (chain.iv30 / rv252) if (chain.iv30 and rv252) else None
    values["rv_acceleration"] = (rv5 / rv20) if (rv5 and rv20) else None
    values["rv_trend"] = (rv20 / rv60) if (rv20 and rv60) else None

    # --- option structure ---------------------------------------------------
    expiries = _expiries(chain.contracts, as_of)
    if expiries:
        # The nearest expiry prices the next event, so it is the right straddle
        # to read an expected move from.
        front = expiries[0]
        meta["front_expiry"] = front.isoformat()
        meta["front_dte"] = (front - as_of).days
        values["iv_front"] = _atm_iv(chain.contracts, chain.spot, front)
        values["expected_move_front"] = _straddle_expected_move(chain.contracts, chain.spot, front)

        # Term structure needs a different front. An option with one or two days
        # left carries pin risk and a wide, jumpy implied volatility that says
        # more about its own expiry mechanics than about event risk, so the slope
        # is measured from the first expiry at least a week out.
        term_front_candidates = [e for e in expiries if (e - as_of).days >= TERM_STRUCTURE_MINIMUM_DTE]
        back_candidates = [e for e in expiries if (e - as_of).days >= TERM_STRUCTURE_BACK_DTE]
        if term_front_candidates and back_candidates:
            term_front = term_front_candidates[0]
            back = back_candidates[0]
            if term_front != back:
                iv_term_front = _atm_iv(chain.contracts, chain.spot, term_front)
                iv_back = _atm_iv(chain.contracts, chain.spot, back)
                meta["term_front_expiry"] = term_front.isoformat()
                meta["term_front_dte"] = (term_front - as_of).days
                meta["back_expiry"] = back.isoformat()
                meta["back_dte"] = (back - as_of).days
                values["iv_term_front"] = iv_term_front
                values["iv_back"] = iv_back
                if iv_term_front and iv_back:
                    # Negative slope (backwardation) is the classic event-risk marker.
                    values["term_slope"] = iv_back - iv_term_front

        if "term_slope" not in values:
            values["iv_term_front"] = None
            values["iv_back"] = None
            values["term_slope"] = None
            snapshot.quality_flags.append("TERM_STRUCTURE_UNAVAILABLE")

        # Skew is read from the same stable expiry as the term slope where one
        # exists, for the same reason.
        skew_expiry = term_front_candidates[0] if term_front_candidates else front
        call_iv = _delta_bucket_iv(chain.contracts, skew_expiry, "C", 0.25)
        put_iv = _delta_bucket_iv(chain.contracts, skew_expiry, "P", 0.25)
        values["skew_25d"] = (put_iv - call_iv) if (call_iv and put_iv) else None
        meta["skew_expiry"] = skew_expiry.isoformat()
    else:
        snapshot.quality_flags.append("NO_TRADEABLE_EXPIRIES")

    call_volume = sum(c.volume for c in chain.contracts if c.right == "C")
    put_volume = sum(c.volume for c in chain.contracts if c.right == "P")
    call_oi = sum(c.open_interest for c in chain.contracts if c.right == "C")
    put_oi = sum(c.open_interest for c in chain.contracts if c.right == "P")
    total_volume = call_volume + put_volume
    total_oi = call_oi + put_oi
    values["option_volume"] = total_volume or None
    values["option_open_interest"] = total_oi or None
    values["put_call_volume"] = (put_volume / call_volume) if call_volume > 0 else None
    values["put_call_oi"] = (put_oi / call_oi) if call_oi > 0 else None
    # Volume well above resting open interest is the standard "unusual activity" tell.
    values["volume_oi_ratio"] = (total_volume / total_oi) if total_oi > 0 else None
    values["relative_option_volume"] = option_volume_percentile
    if option_volume:
        meta["session_option_volume"] = option_volume

    # --- liquidity ----------------------------------------------------------
    if expiries:
        near = [c for c in chain.contracts if c.expiry == expiries[0]]
        spreads = [c.spread_pct for c in near if c.spread_pct is not None]
        if spreads:
            median_spread = statistics.median(spreads)
            values["median_spread_pct"] = median_spread
            if median_spread > 0.25:
                snapshot.quality_flags.append("WIDE_OPTION_SPREADS")

    # --- price action -------------------------------------------------------
    # Prefer the live quote for the one-day return: the daily bar file can lag
    # the chain by a session, and the quote is the fresher point-in-time fact.
    if chain.prev_close > 0:
        values["return_1d"] = chain.spot / chain.prev_close - 1.0
    else:
        values["return_1d"] = _pct_return(bars, 1)
    values["return_5d"] = _pct_return(bars, 5)
    values["return_20d"] = _pct_return(bars, 20)
    values["return_60d"] = _pct_return(bars, 60)
    values["atr14_pct"] = _average_true_range_pct(bars)

    if len(bars) >= 252:
        window = bars[-252:]
        high_52w = max(bar.high for bar in window)
        low_52w = min(bar.low for bar in window)
        if high_52w > 0:
            values["pct_from_52w_high"] = chain.spot / high_52w - 1.0
        if low_52w > 0:
            values["pct_from_52w_low"] = chain.spot / low_52w - 1.0

    if len(bars) >= 21:
        avg_volume = sum(bar.volume for bar in bars[-21:-1]) / 20
        values["avg_stock_volume_20d"] = avg_volume or None
        if avg_volume > 0 and chain.stock_volume:
            values["stock_volume_ratio"] = chain.stock_volume / avg_volume

    # --- relative strength --------------------------------------------------
    if benchmark_bars:
        stock_20d = _pct_return(bars, 20)
        bench_20d = _pct_return(benchmark_bars, 20)
        if stock_20d is not None and bench_20d is not None:
            values["relative_strength_20d"] = stock_20d - bench_20d
        stock_5d = _pct_return(bars, 5)
        bench_5d = _pct_return(benchmark_bars, 5)
        if stock_5d is not None and bench_5d is not None:
            values["relative_strength_5d"] = stock_5d - bench_5d

    if bars:
        meta["last_bar"] = bars[-1].day.isoformat()
        # The chain is delayed, so the newest bar may lag; record it rather than patch it.
        if (as_of - bars[-1].day).days > 5:
            snapshot.quality_flags.append(f"STALE_PRICE_HISTORY (last bar {bars[-1].day})")

    return snapshot
