"""Pure calendar planning for guild opening checks; no runtime or device imports."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import re
from typing import Iterable


@dataclass(frozen=True)
class OpeningPlan:
    status: str
    target: datetime
    start: datetime
    deadline: datetime

    @property
    def in_window(self) -> bool:
        return self.status == 'retry'


def retry_delay(value) -> timedelta:
    """Keep configured positive intervals; invalid/zero values cannot busy-loop."""
    if isinstance(value, str):
        match = re.fullmatch(r'\s*(\d+)\s+(\d{1,2}):(\d{1,2}):(\d{1,2})\s*', value)
        if match:
            days, hours, minutes, seconds = map(int, match.groups())
            value = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    if not isinstance(value, timedelta) or value <= timedelta(0):
        return timedelta(minutes=3)
    return value


def plan_opening_check(
    now: datetime,
    slots: Iterable[tuple[int, time]],
    retry_interval,
    completed: bool = False,
    window: timedelta = timedelta(hours=1),
) -> OpeningPlan:
    """Plan a yielding check in [start, deadline), or the next weekly opening.

    This controls waiting for an opening. An activity already entered must
    complete normally; callers use completed=True after that activity ends.
    A final target at the deadline only advances the calendar, without another
    late game check. Explicit targets must be scheduled with server=False.
    """
    if window <= timedelta(0) or window > timedelta(days=1):
        raise ValueError('Opening window must be positive and no more than one day')
    slots = tuple(slots)
    if not slots or any(day not in range(7) or not isinstance(at, time) for day, at in slots):
        raise ValueError('Opening slots require weekdays 0..6 and clock times')
    starts = set()
    for day, at in slots:
        for offset in range(-7, 15):
            date = now.date() + timedelta(days=offset)
            if date.weekday() == day:
                starts.add(datetime.combine(date, at, tzinfo=now.tzinfo))
    future = min(start for start in starts if start > now)
    if completed:
        return OpeningPlan('completed', future, future, future + window)
    active = [start for start in starts if start <= now < start + window]
    if active:
        start = max(active)
        deadline = start + window
        target = min(now + retry_delay(retry_interval), deadline)
        return OpeningPlan('retry', target, start, deadline)
    recent = [start for start in starts if start <= now]
    previous = max(recent) if recent else None
    expired = previous is not None and previous + window <= now < previous + timedelta(days=1)
    status = 'window_expired' if expired else 'before_start'
    return OpeningPlan(status, future, future, future + window)
