"""XY-style target planning, daily checkpoints and opening retries.

Based on xylolit-mu/OnmyojiAutoScript, self@00c083e8, AbyssShadows:
get_next / get_next_dt / saved_params. Runtime navigation stays in script_task.
"""
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import re

from filelock import FileLock
from module.config.atomicwrites import atomic_write


QUOTAS = {'BOSS': 2, 'GENERAL': 4, 'ELITE': 6}
ALL_TARGETS = tuple(f'{area}-{number}' for area in 'ABCD' for number in range(1, 7))


def enemy_type(code):
    if code not in ALL_TARGETS:
        raise ValueError(f'Invalid Abyss target: {code!r}')
    number = int(code[-1])
    return 'BOSS' if number == 1 else 'GENERAL' if number < 4 else 'ELITE'


def parse_order(value):
    result = []
    for token in str(value).split(';'):
        token = token.strip().upper()
        if not token:
            continue
        if token in ALL_TARGETS:
            expanded = [token]
        elif token in ('A', 'B', 'C', 'D'):
            expanded = [f'{token}-{n}' for n in (4, 5, 6, 2, 3, 1)]
        elif token in ('1', '2', '3', '4', '5', '6'):
            expanded = [f'{a}-{token}' for a in 'ABCD']
        else:
            raise ValueError(f'Invalid Abyss attack order: {token!r}')
        for code in expanded:
            if code not in result:
                result.append(code)
    return result


def legacy_order(smart):
    # Keep the user's existing normal/smart ordering when no order is specified.
    if smart:
        return [f'{a}-{n}' for numbers in ((1,), (2, 3), (4, 5, 6))
                for a in 'AB' for n in numbers]
    return [f'{a}-{n}' for a in 'AB' for n in range(1, 7)]


class TargetPlan:
    def __init__(self, order, done=(), unavailable=(), complete=True):
        self.order = list(dict.fromkeys(order))
        self.done = list(dict.fromkeys(done))
        self.unavailable = set(unavailable)
        if any(code not in ALL_TARGETS for code in (*self.order, *self.done, *self.unavailable)):
            raise ValueError('Invalid Abyss target in checkpoint')
        self.reward_exhausted = set()
        self.complete = complete
        self.current_type = enemy_type(self.done[-1]) if self.done else None

    @property
    def counts(self):
        return {kind: sum(enemy_type(code) == kind for code in self.done) for kind in QUOTAS}

    def next_target(self):
        pending = next((code for code in self.order if self.available(code)), None)
        counts = self.counts
        kind = self.current_type
        # XY fills the current general/elite group before changing presets/types.
        if (self.complete and kind in ('GENERAL', 'ELITE')
                and (pending is None or enemy_type(pending) != kind)
                and counts[kind] < QUOTAS[kind]):
            same_kind = next((code for code in ALL_TARGETS
                              if self.available(code) and enemy_type(code) == kind), None)
            if same_kind is not None:
                return same_kind
        if pending is not None:
            return pending
        if self.complete:
            return next((code for code in ALL_TARGETS if self.available(code)
                         and counts[enemy_type(code)] < QUOTAS[enemy_type(code)]), None)
        return None

    def available(self, code):
        return (code not in self.done and code not in self.unavailable
                and enemy_type(code) not in self.reward_exhausted)

    def mark_reward_exhausted(self, kind):
        if kind not in QUOTAS:
            raise ValueError(kind)
        self.reward_exhausted.add(kind)

    def mark_done(self, code):
        enemy_type(code)
        if code not in self.done:
            self.done.append(code)
        self.unavailable.discard(code)

    def mark_unavailable(self, code):
        enemy_type(code)
        if code not in self.done:
            self.unavailable.add(code)

    def seal_area(self, area):
        for code in ALL_TARGETS:
            if code[0] == area:
                self.mark_unavailable(code)


def opening_slots(settings):
    result = {}
    for day, field in ((4, 'custom_run_time_friday'), (5, 'custom_run_time_saturday'),
                       (6, 'custom_run_time_sunday')):
        at = getattr(settings, field)
        result[day] = time.fromisoformat(at) if isinstance(at, str) else at
    return result


def retry_interval(value):
    if isinstance(value, str):
        match = re.fullmatch(r'(\d+)\s+(\d+):(\d+):(\d+)', value.strip())
        if match:
            days, hours, minutes, seconds = map(int, match.groups())
            value = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    # A legacy one-day failure default cannot retry in a one-hour opening window.
    if not isinstance(value, timedelta) or not timedelta(0) < value < timedelta(hours=1):
        return timedelta(minutes=3)
    return value


def next_run(now, settings, interval, success=False):
    slots = opening_slots(settings)
    target_time = slots.get(now.weekday())
    if target_time is not None:
        start = datetime.combine(now.date(), target_time)
        if not success and now < start:
            return start
        if not success and now < start + timedelta(hours=1):
            return now + retry_interval(interval)
    for offset in range(1, 8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() in slots:
            return datetime.combine(day, slots[day.weekday()])
    raise ValueError('No Abyss opening configured')


class ProgressStore:
    """Account-scoped daily XY done/unavailable lists, separate from GUI config."""
    def __init__(self, root, account, day):
        if (not isinstance(account, str) or not account or account in ('.', '..')
                or any(c in account for c in '\\/:*?"<>|')
                or any(ord(c) < 32 for c in account) or account[-1:] in (' ', '.')):
            raise ValueError('Invalid Abyss account name')
        self.root = Path(root).resolve()
        self.path = self.root / f'{account}.json'
        if self.path.resolve().parent != self.root:
            raise ValueError('Abyss checkpoint is outside its directory')
        self.day = day.isoformat() if isinstance(day, date) else str(day)

    def load(self):
        self.reward_exhausted = set()
        if not self.path.exists():
            return [], []
        with FileLock(str(self.path) + '.lock'):
            data = json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('date') != self.day:
            return [], []
        if data.get('version') != 1:
            raise ValueError('Unsupported Abyss checkpoint version')
        done, unavailable = data.get('done'), data.get('unavailable')
        if not isinstance(done, list) or not isinstance(unavailable, list):
            raise ValueError('Invalid Abyss checkpoint lists')
        if any(code not in ALL_TARGETS for code in done + unavailable):
            raise ValueError('Invalid Abyss checkpoint targets')
        exhausted = data.get("reward_exhausted", [])
        if not isinstance(exhausted, list) or any(k not in QUOTAS for k in exhausted):
            raise ValueError("Invalid exhausted reward types")
        self.reward_exhausted = set(exhausted)
        return done, unavailable

    def save(self, plan):
        self.root.mkdir(parents=True, exist_ok=True)
        data = {'version': 1, 'date': self.day, 'done': plan.done,
                'unavailable': sorted(plan.unavailable),
                'reward_exhausted': sorted(plan.reward_exhausted)}
        with FileLock(str(self.path) + '.lock'):
            with atomic_write(str(self.path), overwrite=True, encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)

    def clear(self):
        self.save(TargetPlan([]))
