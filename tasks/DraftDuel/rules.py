"""Opening hours verified in the game on 2026-10-18 (Asia/Shanghai).

This is a versioned observation, not proof that a future UI is open. Admission
always also requires positive screen evidence and both accounts' cost checks.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone


GAME_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
VERIFIED_ON = "2026-10-18"


@dataclass(frozen=True)
class OpeningWindow:
    is_scheduled_open: bool
    opens_at: datetime
    closes_at: datetime
    next_opens_at: datetime


def opening_window(now: datetime) -> OpeningWindow:
    """Require an aware instant so the host's timezone cannot shift the hours."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("An aware datetime is required")
    local = now.astimezone(GAME_TIMEZONE)
    hour = 8 if local.weekday() >= 5 else 10
    opening = datetime.combine(local.date(), time(hour), GAME_TIMEZONE)
    closing = datetime.combine(local.date(), time(23), GAME_TIMEZONE)
    is_open = opening <= local < closing
    if local < opening:
        upcoming = opening
    else:
        day = local.date() + timedelta(days=1)
        upcoming = datetime.combine(day, time(8 if day.weekday() >= 5 else 10), GAME_TIMEZONE)
    return OpeningWindow(is_open, opening, closing, upcoming)


def next_closed_retry(now: datetime) -> datetime:
    """Unexpected maintenance during nominal hours gets a bounded retry."""
    window = opening_window(now)
    if not window.is_scheduled_open:
        return window.next_opens_at
    candidate = now.astimezone(GAME_TIMEZONE) + timedelta(minutes=15)
    return min(candidate, window.closes_at) if candidate < window.closes_at else window.next_opens_at
