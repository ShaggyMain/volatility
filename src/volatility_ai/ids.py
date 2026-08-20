"""Identifier and calendar helpers.

Identifiers are timestamp-prefixed so that a directory listing sorts
chronologically, and carry a random suffix so two runs in the same second can
never collide (AGENTS.md rule 4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(moment: datetime | None = None) -> str:
    return (moment or utc_now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


def prediction_id(ticker: str, now: datetime | None = None) -> str:
    now = now or utc_now()
    return f"{now.strftime(STAMP_FORMAT)}-{ticker.upper()}-{uuid.uuid4().hex[:8]}"


def run_id(run_type: str = "daily", now: datetime | None = None) -> str:
    now = now or utc_now()
    return f"RUN-{now.strftime('%Y%m%dT%H%M%SZ')}-{run_type.upper()}-{uuid.uuid4().hex[:6]}"


def is_trading_day(day: date) -> bool:
    """Weekday check only.

    Exchange holidays are deliberately not modelled here: the resolver reads
    actual bars, so a holiday simply produces no bar and resolution waits. Using
    a hardcoded holiday list would risk silently resolving against a day that
    never traded.
    """
    return day.weekday() < 5


def add_trading_days(start: date, count: int) -> date:
    day = start
    remaining = count
    while remaining > 0:
        day += timedelta(days=1)
        if is_trading_day(day):
            remaining -= 1
    return day


def resolution_due(horizon: str, from_moment: datetime | None = None) -> str:
    """Earliest UTC moment at which a horizon can honestly be resolved."""
    moment = from_moment or utc_now()
    horizon_days = {"1d": 1, "3d": 3, "5d": 5, "event": 5}.get(horizon, 3)
    due_day = add_trading_days(moment.date(), horizon_days)
    return iso(datetime.combine(due_day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=23))
