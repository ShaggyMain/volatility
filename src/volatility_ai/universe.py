"""Dynamic universe construction and cheap pre-screening.

The scan is deliberately two-stage. Stage one asks the quote endpoint (~0.5 KB
per symbol) about a wide pool; stage two downloads full option chains (~1 MB
each) only for the finalists. That keeps a daily run to a few dozen megabytes
instead of several gigabytes, without narrowing the search to a fixed watchlist.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .providers.cboe import ProviderError, Quote, fetch_quote
from .scoring import interpolate

EARNINGS_CALENDAR_PATH = "data/earnings_calendar.json"


@dataclass
class Candidate:
    ticker: str
    sources: list[str] = field(default_factory=list)
    session_option_volume: float | None = None
    option_volume_percentile: float | None = None
    earnings_date: str | None = None
    earnings_session: str | None = None
    earnings_source: str | None = None
    quote: Quote | None = None
    prescreen_score: float | None = None
    rejected: str | None = None

    @property
    def price_move(self) -> float | None:
        if self.quote and self.quote.prev_close > 0:
            return self.quote.spot / self.quote.prev_close - 1.0
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "sources": self.sources,
            "session_option_volume": self.session_option_volume,
            "option_volume_percentile": (
                round(self.option_volume_percentile, 2)
                if self.option_volume_percentile is not None
                else None
            ),
            "earnings_date": self.earnings_date,
            "earnings_session": self.earnings_session,
            "iv30": self.quote.iv30 if self.quote else None,
            "iv30_change": self.quote.iv30_change if self.quote else None,
            "price_move": round(self.price_move, 4) if self.price_move is not None else None,
            "prescreen_score": (
                round(self.prescreen_score, 2) if self.prescreen_score is not None else None
            ),
            "rejected": self.rejected,
        }


def load_earnings_calendar(path: str | Path = EARNINGS_CALENDAR_PATH) -> dict[str, dict[str, Any]]:
    """Load the analyst-supplied earnings calendar.

    The file is written during the analyst step of a run and every entry must
    carry a ``source_url``. A missing file is not an error: it means the run has
    no event leg and will rank on movement alone, which the run report states
    explicitly rather than hiding.
    """
    file_path = Path(path)
    if not file_path.exists():
        return {}
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    calendar: dict[str, dict[str, Any]] = {}
    for entry in entries or []:
        ticker = str(entry.get("ticker", "")).strip().upper()
        if ticker:
            calendar[ticker] = entry
    return calendar


def _percentile_ranks(values: Mapping[str, float]) -> dict[str, float]:
    """Cross-sectional percentile of each symbol's session option volume."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    total = len(ordered)
    return {
        symbol: (index / (total - 1) * 100.0 if total > 1 else 100.0)
        for index, (symbol, _) in enumerate(ordered)
    }


def build_pool(
    config: Mapping[str, Any],
    option_volume: Mapping[str, Mapping[str, float]],
    earnings: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    as_of: date | None = None,
) -> list[Candidate]:
    """Assemble the candidate pool from the earnings leg and the movers leg."""
    as_of = as_of or datetime.now(UTC).date()
    movers_config = config.get("movers") or {}
    excluded = {str(root).upper() for root in movers_config.get("exclude_roots") or []}
    minimum_volume = float((config.get("filters") or {}).get("minimum_session_option_volume", 0))

    totals = {
        symbol: float(data.get("volume", 0.0))
        for symbol, data in option_volume.items()
        if symbol not in excluded and float(data.get("volume", 0.0)) >= minimum_volume
    }
    percentiles = _percentile_ranks(totals)

    candidates: dict[str, Candidate] = {}

    def ensure(ticker: str) -> Candidate:
        key = ticker.strip().upper()
        if key not in candidates:
            candidates[key] = Candidate(ticker=key)
        return candidates[key]

    # Leg 1: earnings inside the window.
    window = int(config.get("earnings_window_days", 10))
    for ticker, entry in (earnings or {}).items():
        raw_date = str(entry.get("date") or "")
        try:
            event_day = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if not (0 <= (event_day - as_of).days <= window):
            continue
        candidate = ensure(ticker)
        candidate.sources.append("earnings")
        candidate.earnings_date = raw_date
        candidate.earnings_session = entry.get("session")
        candidate.earnings_source = entry.get("source_url")

    # Leg 2: option-volume leaders from the previous session.
    top_n = int(movers_config.get("top_by_option_volume", 140))
    leaders = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:top_n]
    for symbol, volume in leaders:
        candidate = ensure(symbol)
        if "movers" not in candidate.sources:
            candidate.sources.append("movers")
        candidate.session_option_volume = volume

    # Leg 3: the optional standing watchlist.
    for ticker in config.get("always_consider") or []:
        candidate = ensure(str(ticker))
        if "watchlist" not in candidate.sources:
            candidate.sources.append("watchlist")

    for candidate in candidates.values():
        if candidate.session_option_volume is None:
            candidate.session_option_volume = totals.get(candidate.ticker)
        candidate.option_volume_percentile = percentiles.get(candidate.ticker)

    return list(candidates.values())


def prescreen(
    candidates: Sequence[Candidate],
    config: Mapping[str, Any],
    *,
    workers: int = 6,
    limit: int | None = None,
) -> list[Candidate]:
    """Fetch quotes for the pool and rank it for the expensive scan.

    Candidates that fail a hard filter keep a ``rejected`` reason so the run
    record can show what was excluded and why.
    """
    filters = config.get("filters") or {}
    minimum_price = float(filters.get("minimum_price", 0))
    allowed_types = {str(t).lower() for t in filters.get("allowed_security_types") or []}
    weights = config.get("prescreen_weights") or {}
    maximum = limit or int((config.get("sizing") or {}).get("prescreen_maximum", 180))

    ordered = sorted(
        candidates,
        key=lambda c: (
            0 if "earnings" in c.sources else 1,
            -(c.session_option_volume or 0.0),
        ),
    )[:maximum]

    def load(candidate: Candidate) -> Candidate:
        try:
            candidate.quote = fetch_quote(candidate.ticker)
        except ProviderError as error:
            candidate.rejected = f"quote_unavailable: {error}"
        return candidate

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(load, candidate) for candidate in ordered]
        for future in as_completed(futures):
            future.result()

    for candidate in ordered:
        if candidate.rejected:
            continue
        quote = candidate.quote
        if quote is None:
            candidate.rejected = "no_quote"
            continue
        if quote.spot < minimum_price:
            candidate.rejected = f"price_below_{minimum_price}"
            continue
        if allowed_types and quote.security_type.lower() not in allowed_types:
            candidate.rejected = f"security_type_{quote.security_type or 'unknown'}"
            continue
        if quote.iv30 is None:
            candidate.rejected = "no_iv30"
            continue

        parts = {
            "option_volume_rank": candidate.option_volume_percentile,
            "iv_change": interpolate(
                [[-0.06, 0], [-0.02, 15], [0.0, 35], [0.02, 62], [0.05, 85], [0.10, 100]],
                quote.iv30_change if quote.iv30_change is not None else 0.0,
            ),
            "absolute_price_move": interpolate(
                [[0.0, 0], [0.02, 30], [0.05, 60], [0.10, 85], [0.20, 100]],
                abs(candidate.price_move) if candidate.price_move is not None else 0.0,
            ),
            "earnings_soon": 100.0 if "earnings" in candidate.sources else 0.0,
        }
        available = {k: v for k, v in parts.items() if v is not None and k in weights}
        total_weight = sum(float(weights[k]) for k in available) or 1.0
        candidate.prescreen_score = sum(
            float(available[k]) * float(weights[k]) for k in available
        ) / total_weight

    survivors = [c for c in ordered if c.rejected is None and c.prescreen_score is not None]
    survivors.sort(key=lambda c: c.prescreen_score or 0.0, reverse=True)
    return survivors
