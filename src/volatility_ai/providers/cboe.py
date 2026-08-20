"""Cboe delayed-quote adapters.

All endpoints are public and require no API key. Quotes are delayed by roughly
15 minutes, which makes them conservative for point-in-time work: every field a
snapshot contains was already public before the snapshot's ``retrieved_at``.

Endpoints used:
    options/{symbol}.json     full option chain + underlying quote + iv30
    charts/historical/{sym}   daily OHLCV back to 2004
    quotes/{symbol}.json      underlying quote only
    symbol_data/csv           previous session option volume per contract
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

CDN_BASE = "https://cdn.cboe.com/api/global/delayed_quotes"
SYMBOL_VOLUME_URL = "https://www.cboe.com/us/options/market_statistics/symbol_data/csv/?mkt=cone"
USER_AGENT = "volatility-ai-research/0.2 (point-in-time research client)"

# Cboe prefixes cash indices with an underscore.
INDEX_SYMBOLS = frozenset({"SPX", "SPXW", "VIX", "NDX", "RUT", "DJX", "XSP", "OEX", "VVIX"})


class ProviderError(RuntimeError):
    """Raised when a provider cannot return trustworthy data.

    The framework forbids inferring missing market data, so callers must treat
    this as a hard failure for the affected ticker rather than substituting a
    guess.
    """


def normalize_symbol(symbol: str) -> str:
    """Return the symbol spelling Cboe uses in its CDN paths."""
    clean = symbol.strip().upper()
    if clean in INDEX_SYMBOLS and not clean.startswith("_"):
        return f"_{clean}"
    return clean


def _http_get(url: str, timeout: float = 45.0, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise ProviderError(f"GET failed after {retries} attempts: {url} ({last_error})")


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_option_symbol(option_symbol: str) -> tuple[str, date, str, float]:
    """Split an OCC-style symbol such as ``AAPL260819C00205000``.

    Returns ``(root, expiry, right, strike)``. Parsing runs right-to-left because
    roots vary in length and may contain digits.
    """
    if len(option_symbol) < 16:
        raise ValueError(f"Not an OCC option symbol: {option_symbol!r}")
    strike = int(option_symbol[-8:]) / 1000.0
    right = option_symbol[-9].upper()
    if right not in ("C", "P"):
        raise ValueError(f"Unexpected right in {option_symbol!r}")
    # An expiry is a calendar date, not an instant: OCC symbols carry no time and
    # no zone. Building the date directly keeps it that way and still raises on a
    # malformed month or day.
    stamp = option_symbol[-15:-9]
    expiry = date(2000 + int(stamp[0:2]), int(stamp[2:4]), int(stamp[4:6]))
    root = option_symbol[:-15]
    return root, expiry, right, strike


@dataclass(frozen=True)
class OptionContract:
    option_symbol: str
    expiry: date
    right: str
    strike: float
    bid: float
    ask: float
    last_trade_price: float
    iv: float
    open_interest: float
    volume: float
    delta: float
    gamma: float
    vega: float
    theta: float

    @property
    def mid(self) -> float | None:
        """Mid price, or ``None`` when the quote is not two-sided."""
        if self.bid > 0 and self.ask > 0 and self.ask >= self.bid:
            return (self.bid + self.ask) / 2.0
        return None

    @property
    def spread_pct(self) -> float | None:
        mid = self.mid
        if mid is None or mid <= 0:
            return None
        return (self.ask - self.bid) / mid

    def days_to_expiry(self, as_of: date) -> int:
        return (self.expiry - as_of).days


@dataclass(frozen=True)
class Chain:
    symbol: str
    retrieved_at: str
    source_timestamp: str
    spot: float
    prev_close: float
    day_open: float | None
    day_high: float | None
    day_low: float | None
    stock_volume: int | None
    iv30: float | None
    iv30_change: float | None
    contracts: tuple[OptionContract, ...]

    @property
    def has_options(self) -> bool:
        return bool(self.contracts)


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fetch_chain(symbol: str, timeout: float = 60.0) -> Chain:
    """Fetch the full delayed option chain plus the underlying quote."""
    cboe_symbol = normalize_symbol(symbol)
    payload = json.loads(_http_get(f"{CDN_BASE}/options/{cboe_symbol}.json", timeout=timeout))
    retrieved_at = _utc_now_iso()
    data = payload.get("data") or {}
    raw_options = data.get("options") or []

    contracts: list[OptionContract] = []
    for row in raw_options:
        option_symbol = row.get("option") or ""
        try:
            _, expiry, right, strike = parse_option_symbol(option_symbol)
        except ValueError:
            continue
        contracts.append(
            OptionContract(
                option_symbol=option_symbol,
                expiry=expiry,
                right=right,
                strike=strike,
                bid=_as_float(row.get("bid")),
                ask=_as_float(row.get("ask")),
                last_trade_price=_as_float(row.get("last_trade_price")),
                iv=_as_float(row.get("iv")),
                open_interest=_as_float(row.get("open_interest")),
                volume=_as_float(row.get("volume")),
                delta=_as_float(row.get("delta")),
                gamma=_as_float(row.get("gamma")),
                vega=_as_float(row.get("vega")),
                theta=_as_float(row.get("theta")),
            )
        )

    spot = _as_float(data.get("current_price"))
    if spot <= 0:
        raise ProviderError(f"No usable spot price for {symbol}")

    iv30_raw = data.get("iv30")
    return Chain(
        symbol=symbol.strip().upper(),
        retrieved_at=retrieved_at,
        source_timestamp=str(payload.get("timestamp") or ""),
        spot=spot,
        prev_close=_as_float(data.get("prev_day_close")),
        day_open=_as_float(data.get("open")) or None,
        day_high=_as_float(data.get("high")) or None,
        day_low=_as_float(data.get("low")) or None,
        stock_volume=int(_as_float(data.get("volume"))) or None,
        # Cboe publishes iv30 in percent (24.5 == 24.5%); store it as a decimal.
        iv30=(_as_float(iv30_raw) / 100.0) if iv30_raw not in (None, "") else None,
        iv30_change=(_as_float(data.get("iv30_change")) / 100.0)
        if data.get("iv30_change") not in (None, "")
        else None,
        contracts=tuple(contracts),
    )


def fetch_history(symbol: str, timeout: float = 60.0) -> tuple[Bar, ...]:
    """Fetch daily OHLCV history (oldest first)."""
    cboe_symbol = normalize_symbol(symbol)
    payload = json.loads(
        _http_get(f"{CDN_BASE}/charts/historical/{cboe_symbol}.json", timeout=timeout)
    )
    bars: list[Bar] = []
    for row in payload.get("data") or []:
        try:
            day = date.fromisoformat(str(row["date"]))
        except (KeyError, ValueError):
            continue
        close = _as_float(row.get("close"))
        if close <= 0:
            continue
        bars.append(
            Bar(
                day=day,
                open=_as_float(row.get("open")),
                high=_as_float(row.get("high")),
                low=_as_float(row.get("low")),
                close=close,
                volume=_as_float(row.get("volume")),
            )
        )
    if not bars:
        raise ProviderError(f"No price history for {symbol}")
    bars.sort(key=lambda bar: bar.day)
    return tuple(bars)


def fetch_symbol_option_volume(timeout: float = 120.0) -> dict[str, dict[str, float]]:
    """Aggregate the previous session's Cboe option volume by underlying.

    The CSV is per contract, so this collapses it to one row per root symbol and
    also reports call/put split, which feeds the positioning features.

    Note: this covers Cboe's own C1 exchange, not consolidated OPRA volume. It is
    used as a *relative* activity ranking, never as an absolute market figure.
    """
    raw = _http_get(SYMBOL_VOLUME_URL, timeout=timeout).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"volume": 0.0, "call_volume": 0.0, "put_volume": 0.0, "contracts": 0.0}
    )
    for row in reader:
        symbol = (row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        volume = _as_float(row.get("Volume"))
        if volume <= 0:
            continue
        bucket = totals[symbol]
        bucket["volume"] += volume
        bucket["contracts"] += 1
        if (row.get("Call/Put") or "").strip().upper() == "P":
            bucket["put_volume"] += volume
        else:
            bucket["call_volume"] += volume
    return dict(totals)


@dataclass(frozen=True)
class Quote:
    """Underlying quote only -- roughly 0.5 KB per symbol.

    This is what makes a wide pre-screen affordable: hundreds of symbols can be
    checked for rising IV and unusual movement before committing to the ~1 MB
    chain download for the finalists.
    """

    symbol: str
    retrieved_at: str
    spot: float
    prev_close: float
    price_change_percent: float | None
    stock_volume: int | None
    iv30: float | None
    iv30_change: float | None
    iv30_change_percent: float | None
    security_type: str


def fetch_quote(symbol: str, timeout: float = 20.0) -> Quote:
    """Fetch the underlying quote and Cboe's own 30-day IV for one symbol."""
    cboe_symbol = normalize_symbol(symbol)
    payload = json.loads(_http_get(f"{CDN_BASE}/quotes/{cboe_symbol}.json", timeout=timeout, retries=2))
    data = payload.get("data") or {}
    spot = _as_float(data.get("current_price"))
    if spot <= 0:
        raise ProviderError(f"No usable quote for {symbol}")
    iv30_raw = data.get("iv30")
    return Quote(
        symbol=symbol.strip().upper(),
        retrieved_at=_utc_now_iso(),
        spot=spot,
        prev_close=_as_float(data.get("prev_day_close")),
        price_change_percent=(_as_float(data.get("price_change_percent")) / 100.0)
        if data.get("price_change_percent") not in (None, "")
        else None,
        stock_volume=int(_as_float(data.get("volume"))) or None,
        iv30=(_as_float(iv30_raw) / 100.0) if iv30_raw not in (None, "") else None,
        iv30_change=(_as_float(data.get("iv30_change")) / 100.0)
        if data.get("iv30_change") not in (None, "")
        else None,
        iv30_change_percent=(_as_float(data.get("iv30_change_percent")) / 100.0)
        if data.get("iv30_change_percent") not in (None, "")
        else None,
        security_type=str(data.get("security_type") or ""),
    )
