"""Wednesday/Saturday shop schedule, independent of generic force times."""
from datetime import datetime, timedelta


def next_shop_run(now, at, completed=False):
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() not in (2, 5) or (completed and offset == 0):
            continue
        candidate = datetime.combine(day, at)
        if candidate > now:
            return candidate
    raise ValueError('No future shop date')
