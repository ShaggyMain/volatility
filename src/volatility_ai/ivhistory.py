"""Self-built implied-volatility history.

No free provider publishes historical IV, so IV Rank and IV Percentile -- the
highest-weighted input in ``config/scoring.yaml`` -- cannot be sourced on day
one. Rather than substitute a proxy and pretend it is IV Rank (AGENTS.md rule
8), the system records one IV observation per ticker per run and starts
reporting IV Rank only once it owns enough history to mean something.

Until then ``iv_rank`` is ``None``, its 0.15 weight redistributes across the
other volatility inputs, and the snapshot carries an explicit quality flag.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

HEADER = ["observed_at_utc", "iv30", "spot", "rv20", "option_volume", "source"]


@dataclass(frozen=True)
class IVObservation:
    observed_at_utc: str
    iv30: float
    spot: float | None
    rv20: float | None
    option_volume: float | None
    source: str


@dataclass(frozen=True)
class IVStats:
    iv_rank: float | None
    iv_percentile: float | None
    observations: int
    relative_option_volume: float | None


class IVHistoryStore:
    def __init__(self, directory: str | Path = "data/iv_history", minimum_observations: int = 60):
        self.directory = Path(directory)
        self.minimum_observations = minimum_observations

    def _path(self, ticker: str) -> Path:
        return self.directory / f"{ticker.strip().upper()}.csv"

    def load(self, ticker: str) -> list[IVObservation]:
        path = self._path(ticker)
        if not path.exists():
            return []
        observations: list[IVObservation] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    iv30 = float(row["iv30"])
                except (KeyError, TypeError, ValueError):
                    continue
                observations.append(
                    IVObservation(
                        observed_at_utc=row.get("observed_at_utc", ""),
                        iv30=iv30,
                        spot=_optional_float(row.get("spot")),
                        rv20=_optional_float(row.get("rv20")),
                        option_volume=_optional_float(row.get("option_volume")),
                        source=row.get("source", ""),
                    )
                )
        return observations

    def append(
        self,
        ticker: str,
        *,
        iv30: float,
        spot: float | None = None,
        rv20: float | None = None,
        option_volume: float | None = None,
        source: str = "cboe_delayed",
        observed_at_utc: str | None = None,
    ) -> None:
        """Append one observation. History is append-only, like predictions."""
        path = self._path(ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        stamp = observed_at_utc or datetime.now(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if is_new:
                writer.writerow(HEADER)
            writer.writerow(
                [
                    stamp,
                    f"{iv30:.6f}",
                    "" if spot is None else f"{spot:.4f}",
                    "" if rv20 is None else f"{rv20:.6f}",
                    "" if option_volume is None else f"{option_volume:.0f}",
                    source,
                ]
            )

    def already_recorded_today(self, ticker: str, day: str) -> bool:
        """True when an observation for that UTC date already exists.

        Guards against a re-run of the same session double-counting history and
        quietly distorting a future IV Rank.
        """
        return any(o.observed_at_utc.startswith(day) for o in self.load(ticker))

    def stats(self, ticker: str, current_iv: float, current_option_volume: float | None = None) -> IVStats:
        """Compute IV Rank / Percentile from owned history, or report why not."""
        observations = self.load(ticker)
        series = [o.iv30 for o in observations if o.iv30 > 0]
        count = len(series)

        relative_volume: float | None = None
        volumes = [o.option_volume for o in observations if o.option_volume]
        if current_option_volume and len(volumes) >= 20:
            average = sum(volumes[-20:]) / len(volumes[-20:])
            if average > 0:
                ratio = current_option_volume / average
                relative_volume = max(0.0, min(100.0, (ratio - 0.5) / 3.5 * 100.0))

        if count < self.minimum_observations:
            return IVStats(None, None, count, relative_volume)

        low, high = min(series), max(series)
        iv_rank = None if high <= low else max(0.0, min(100.0, (current_iv - low) / (high - low) * 100.0))
        below = sum(1 for value in series if value < current_iv)
        iv_percentile = below / count * 100.0
        return IVStats(iv_rank, iv_percentile, count, relative_volume)


def _optional_float(raw: str | None) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
