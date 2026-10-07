"""Durable, per-account daily closeout proofs using the existing task queue.

A task moving its next_run or raising TaskEnd is not evidence of completion.
Only explicit game-confirmed terminal outcomes enter this coordinator. The
scheduler evaluates it between tasks; reporting an event never interrupts or
queues a task on its own.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
from pathlib import Path
from typing import Iterable

from filelock import FileLock
from module.config.atomicwrites import atomic_write


STATE_DIR = Path(__file__).resolve().parents[2] / 'config' / 'daily_closeout'


TASK_FIELDS = {
    'Dokan': 'dokan',
    'CollectiveMissions': 'collective_missions',
    'AbyssShadows': 'abyss_shadows',
    'GuildBanquet': 'guild_banquet',
    'DemonRetreat': 'demon_retreat',
}
TASK_LABELS = {
    'Dokan': '道馆', 'CollectiveMissions': '寮集体任务',
    'AbyssShadows': '峡间暗域', 'GuildBanquet': '寮宴会',
    'DemonRetreat': '首领退治',
}
TERMINAL_OUTCOMES = frozenset(('completed', 'expired', 'failed', 'skipped'))


@dataclass(frozen=True)
class CloseoutDecision:
    queued: bool = False
    ready: bool = False
    reason: str = ''
    required: tuple[str, ...] = ()
    waiting: tuple[str, ...] = ()
    incomplete: tuple[tuple[str, str], ...] = ()
    completed: tuple[str, ...] = ()
    due_tasks: tuple[str, ...] = ()


def _options(config):
    task = getattr(config, 'talisman_pass', None)
    return getattr(task, 'closeout_config', None)


def closeout_enabled(config) -> bool:
    task = getattr(config, 'talisman_pass', None)
    return bool(task is not None and getattr(task.scheduler, 'enable', False)
                and getattr(_options(config), 'enable', False))


def _enabled(config, task):
    value = getattr(config, TASK_FIELDS[task], None)
    return bool(value is not None and getattr(value.scheduler, 'enable', False))


def closeout_dependencies(config, now=None) -> tuple[str, ...]:
    """Only today's enabled closing anchors participate, never future loops."""
    now = now or datetime.now()
    weekday = now.weekday()
    if weekday <= 3:
        candidates = ['Dokan']
        missions = getattr(getattr(config, 'collective_missions', None), 'missions_config', None)
        if _enabled(config, 'Dokan') and getattr(missions, 'run_after_dokan', False):
            candidates.append('CollectiveMissions')
    elif weekday in (4, 6):
        candidates = ['AbyssShadows', 'GuildBanquet']
    else:
        candidates = ['AbyssShadows', 'DemonRetreat']
    return tuple(task for task in candidates if _enabled(config, task))


def _state_path(config):
    name = getattr(config, 'config_name', None)
    if not isinstance(name, str) or not name or name in ('.', '..'):
        raise ValueError('Daily closeout needs an account name')
    if any(char in name for char in '\\/:*?"<>|') or name[-1:] in (' ', '.'):
        raise ValueError('Unsafe daily closeout account name')
    if any(ord(char) < 32 for char in name):
        raise ValueError('Unsafe daily closeout account name')
    parent = STATE_DIR.resolve()
    target = (parent / (name + '.json')).resolve()
    if target.parent != parent:
        raise ValueError('Daily closeout path outside its state directory')
    parent.mkdir(parents=True, exist_ok=True)
    return target


def _load_state(path, now):
    today = now.date().isoformat()
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {}
    # A damaged proof must not be silently erased and mistaken for a new day.
    if not isinstance(state, dict):
        raise ValueError('Invalid closeout state')
    if state.get('date') != today:
        return {'date': today, 'outcomes': {}, 'queued_date': '',
                'completed_date': '', 'queue_reason': '', 'queued_at': '',
                'queued_next_run': '', 'queue_previous_next_run': '', 'queue_stage': '',
                'manual_date': '', 'manual_next_run': ''}
    outcomes = state.get('outcomes', {})
    if not isinstance(outcomes, dict):
        outcomes = {}
    state['outcomes'] = {name: proof for name, proof in outcomes.items()
                         if name in TASK_FIELDS and isinstance(proof, dict)
                         and proof.get('outcome') in TERMINAL_OUTCOMES
                         and proof.get('date') == today}
    return state


def _write_state(path, state):
    with atomic_write(str(path), overwrite=True, encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, sort_keys=True, indent=2)


def closeout_state(config, now=None) -> dict:
    """Read today's independent state, never a stale main config snapshot."""
    now = now or datetime.now()
    try:
        path = _state_path(config)
        with FileLock(str(path) + '.lock', timeout=5):
            return _load_state(path, now)
    except Exception:
        _warn('state read')
        return {'date': now.date().isoformat(), 'outcomes': {}, 'queued_date': '',
                'completed_date': '', 'queue_reason': '', 'unavailable': True}


def _warn(operation):
    # No provider/config URLs or exception bodies enter logs here.
    try:
        from module.logger import logger
        logger.warning(f'Daily closeout {operation} unavailable; keeping normal task scheduling')
    except Exception:
        pass


def _clock(value, default):
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return default
    return value if isinstance(value, time) else default


def _decision(config, state, now, due_tasks=()):
    options = _options(config)
    outcomes = state['outcomes']
    required = closeout_dependencies(config, now)
    waiting, incomplete, completed = [], [], []
    dokan = outcomes.get('Dokan', {}).get('outcome')
    for name in required:
        status = outcomes.get(name, {}).get('outcome')
        if status == 'completed':
            completed.append(name)
        elif status in TERMINAL_OUTCOMES:
            incomplete.append((name, status))
        elif name == 'CollectiveMissions' and dokan in ('expired', 'failed', 'skipped'):
            # Linked collective cannot start without confirmed final Dokan.
            # It is terminally blocked, not completed or silently omitted.
            incomplete.append((name, 'blocked_by_dokan'))
        else:
            waiting.append(name)
    due = tuple(dict.fromkeys(name for name in due_tasks if name != 'TalismanPass'))
    today = now.date().isoformat()
    if state.get('completed_date') == today:
        reason, ready = 'already_completed', False
    elif state.get('queued_date') == today:
        reason, ready = 'already_queued', False
    elif due:
        reason, ready = 'waiting_due_tasks', False
    elif now.time() >= _clock(getattr(options, 'fallback_time', time(23)), time(23)):
        reason, ready = 'fallback', True
    elif waiting:
        reason, ready = 'waiting_dependencies', False
    elif not required and now.time() < _clock(getattr(options, 'earliest_time', time(18)), time(18)):
        reason, ready = 'before_earliest_time', False
    else:
        reason, ready = 'dependencies_terminal', True
    return CloseoutDecision(ready=ready, reason=reason, required=required,
                            waiting=tuple(waiting), incomplete=tuple(incomplete),
                            completed=tuple(completed), due_tasks=due)


def report_closeout_outcome(config, task_name, outcome, *, now=None, detail='') -> CloseoutDecision:
    """Record a real terminal result; never infer one from generic TaskEnd.

    Reporting is safe inside a game task: a coordinator storage failure cannot
    turn an otherwise successful task into an error/restart. Queueing is only
    performed by evaluate_daily_closeout at the scheduler's safe boundary.
    """
    if task_name not in TASK_FIELDS or outcome not in TERMINAL_OUTCOMES:
        raise ValueError('Unsupported daily closeout task/outcome')
    if not closeout_enabled(config):
        return CloseoutDecision(reason='disabled')
    now = now or datetime.now()
    try:
        path = _state_path(config)
        with FileLock(str(path) + '.lock', timeout=5):
            state = _load_state(path, now)
            outcomes = state['outcomes']
            previous = outcomes.get(task_name, {}).get('outcome')
            # A later manual failed check cannot erase confirmed completion.
            if previous != 'completed' or outcome == 'completed':
                outcomes[task_name] = {'outcome': outcome, 'date': now.date().isoformat(),
                                       'at': now.replace(microsecond=0).isoformat(), 'detail': str(detail)[:160]}
                _write_state(path, state)
            return _decision(config, state, now)
    except Exception:
        _warn('event save')
        return CloseoutDecision(reason='state_unavailable')


def evaluate_daily_closeout(config, *, now=None, due_tasks: Iterable[str] = ()) -> CloseoutDecision:
    """At a scheduler safe point, queue one enabled TalismanPass for this day.

    due_tasks is the caller's list of already-due daily jobs, excluding future
    repeated/temporary tasks. Fallback queues an honest incomplete report; it
    never interrupts the running game task.
    """
    now = now or datetime.now()
    if not closeout_enabled(config):
        return CloseoutDecision(reason='disabled')
    try:
        path = _state_path(config)
        with FileLock(str(path) + '.lock', timeout=5):
            state = _load_state(path, now)
            decision = _decision(config, state, now, due_tasks)
            if _manual_matches(config, state, now):
                return CloseoutDecision(**{**decision.__dict__, 'queued': False,
                                          'ready': False, 'reason': 'manual_requested'})
            if decision.reason == 'already_queued':
                # Recover only an unfinished intent. A subsequent explicit
                # next_run change is manual scheduling; never overwrite it.
                running = getattr(config, 'running_task', '')
                if state.get('queue_stage') in ('intent', 'retry_intent'):
                    current_target = _timestamp(config.talisman_pass.scheduler.next_run)
                    valid_targets = {state.get('queue_previous_next_run'), state.get('queued_next_run')}
                    if current_target not in valid_targets:
                        return CloseoutDecision(**{**decision.__dict__, 'reason': 'schedule_changed'})
                if state.get('queue_stage') == 'retry_intent' and running != 'TalismanPass':
                    target = datetime.fromisoformat(state['queued_next_run'])
                    if target.date() != now.date():
                        return CloseoutDecision(reason='retry_outside_day')
                    config.talisman_pass.scheduler.next_run = target
                    config.save()
                    state['queue_stage'] = 'queued'
                    _write_state(path, state)
                    return CloseoutDecision(**{**decision.__dict__, 'queued': target <= now,
                                              'reason': 'retry_recovered'})
                if state.get('queue_stage') == 'intent' and running != 'TalismanPass':
                    queued = bool(config.task_call('TalismanPass', force_call=False))
                    if queued:
                        state['queue_stage'] = 'queued'
                        state['queued_next_run'] = _timestamp(config.talisman_pass.scheduler.next_run)
                        _write_state(path, state)
                    return CloseoutDecision(**{**decision.__dict__, 'queued': queued,
                                              'reason': 'queue_recovered' if queued else 'disabled'})
                return decision
            if not decision.ready:
                return decision
            previous = dict(state)
            state['queued_date'] = now.date().isoformat()
            state['queue_reason'] = decision.reason
            state['queued_at'] = now.replace(microsecond=0).isoformat()
            state['queue_previous_next_run'] = _timestamp(config.talisman_pass.scheduler.next_run)
            state['queued_next_run'] = _timestamp(now)
            state['queue_stage'] = 'intent'
            # Durable intent precedes task_call; the above recovery handles a
            # process crash between the two independent atomic file writes.
            _write_state(path, state)
            try:
                queued = config.task_call('TalismanPass', force_call=False)
            except Exception:
                _write_state(path, previous)
                raise
            if not queued:
                _write_state(path, previous)
                return CloseoutDecision(reason='disabled')
            state['queue_stage'] = 'queued'
            state['queued_next_run'] = _timestamp(config.talisman_pass.scheduler.next_run)
            _write_state(path, state)
            return CloseoutDecision(**{**decision.__dict__, 'queued': True})
    except Exception:
        _warn('queue')
        return CloseoutDecision(reason='state_unavailable')


def _timestamp(value):
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    try:
        return datetime.fromisoformat(str(value)).replace(microsecond=0).isoformat()
    except (ValueError, TypeError):
        return ''


def _manual_matches(config, state, now, scheduled_at=None):
    if scheduled_at is None:
        scheduled_at = config.talisman_pass.scheduler.next_run
    return bool(state.get('manual_date') == now.date().isoformat()
                and state.get('manual_next_run')
                and state.get('manual_next_run') == _timestamp(scheduled_at))


def request_manual_talisman(config, scheduled_at, *, now=None) -> bool:
    """External next_run writes explicitly identify an immediate manual run.

    The timestamp can be in the past, while the marker's ownership date is
    today. Scheduler/coordinator internal writes do not create this marker.
    """
    now = now or datetime.now()
    if not closeout_enabled(config):
        return False
    target_text = _timestamp(scheduled_at)
    if not target_text:
        return False
    try:
        target = datetime.fromisoformat(target_text)
        if target > now:
            return False
        path = _state_path(config)
        with FileLock(str(path) + '.lock', timeout=5):
            state = _load_state(path, now)
            state['manual_date'] = now.date().isoformat()
            state['manual_next_run'] = target_text
            _write_state(path, state)
        return True
    except Exception:
        _warn('manual request save')
        return False


def is_manual_talisman(config, *, scheduled_at=None, now=None) -> bool:
    now = now or datetime.now()
    if not closeout_enabled(config):
        return False
    state = closeout_state(config, now)
    return bool(not state.get('unavailable') and _manual_matches(config, state, now, scheduled_at))


def clear_manual_talisman_request(config, *, now=None, scheduled_at=None) -> bool:
    now = now or datetime.now()
    if not closeout_enabled(config):
        return False
    try:
        path = _state_path(config)
        with FileLock(str(path) + '.lock', timeout=5):
            state = _load_state(path, now)
            if scheduled_at is not None and state.get('manual_next_run') != _timestamp(scheduled_at):
                return False
            if not state.get('manual_date') and not state.get('manual_next_run'):
                return False
            state['manual_date'] = ''
            state['manual_next_run'] = ''
            _write_state(path, state)
        return True
    except Exception:
        _warn('manual request clear')
        return False


def is_automatic_closeout(config, *, scheduled_at=None, now=None) -> bool:
    """Identify the coordinator's queue slot, distinct from a manual next_run."""
    now = now or datetime.now()
    if not closeout_enabled(config):
        return False
    state = closeout_state(config, now)
    today = now.date().isoformat()
    scheduled_at = (config.talisman_pass.scheduler.next_run if scheduled_at is None else scheduled_at)
    return bool(not state.get('unavailable') and state.get('queued_date') == today
                and state.get('completed_date') != today
                and state.get('queue_stage') == 'queued'
                and state.get('queue_reason') in ('dependencies_terminal', 'fallback')
                and not _manual_matches(config, state, now, scheduled_at)
                and state.get('queued_next_run') == _timestamp(scheduled_at))


def should_hold_talisman(config, *, now=None, manual=False) -> bool:
    """An explicit manual run bypasses the dynamic automatic-run gate."""
    now = now or datetime.now()
    if manual or not closeout_enabled(config):
        return False
    return not is_automatic_closeout(config, now=now)


def next_closeout_check(config, now=None) -> datetime:
    now = now or datetime.now()
    at = _clock(getattr(_options(config), 'fallback_time', time(23)), time(23))
    target = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
    state = closeout_state(config, now)
    if target <= now or state.get('completed_date') == now.date().isoformat():
        target += timedelta(days=1)
    return target.replace(microsecond=0)


def arm_daily_closeout(config, now=None) -> bool:
    """Park fixed-time scheduling, preserving an already-queued dynamic run.

    Use for migration, after successful closeout, and when holding a fixed-time
    task. Today's completed closeout parks tomorrow's fallback checkpoint.
    """
    now = now or datetime.now()
    if not closeout_enabled(config):
        return False
    try:
        state = closeout_state(config, now)
        if state.get('unavailable'):
            return False
        if _manual_matches(config, state, now):
            return False
        today = now.date().isoformat()
        if state.get('queued_date') == today and state.get('completed_date') != today:
            return False
        target = next_closeout_check(config, now)
        scheduler = config.talisman_pass.scheduler
        if scheduler.next_run == target:
            return False
        scheduler.next_run = target
        config.save()
        return True
    except Exception:
        _warn('checkpoint save')
        return False


def hold_closeout_until(config, now=None) -> bool:
    return arm_daily_closeout(config, now)


def schedule_closeout_retry(config, *, now=None, delay=timedelta(minutes=3)) -> bool:
    """Retry a known automatic closeout without mistaking it for manual work.

    This uses the existing single scheduler slot. Durable intent allows a
    crash between state/config writes to recover the requested retry time.
    Never carry yesterday's closeout proof into a new calendar day.
    """
    now = now or datetime.now()
    if not closeout_enabled(config):
        return False
    if not isinstance(delay, timedelta) or delay <= timedelta(0) or delay >= timedelta(days=1):
        delay = timedelta(minutes=3)
    target = (now + delay).replace(microsecond=0)
    if target.date() != now.date():
        _warn('retry outside the proven day')
        return False
    try:
        path = _state_path(config)
        with FileLock(str(path) + '.lock', timeout=5):
            state = _load_state(path, now)
            if _manual_matches(config, state, now):
                return False
            today = now.date().isoformat()
            if state.get('queued_date') != today or state.get('completed_date') == today:
                return False
            previous = dict(state)
            previous_target = config.talisman_pass.scheduler.next_run
            state['queue_stage'] = 'retry_intent'
            state['queue_previous_next_run'] = _timestamp(previous_target)
            state['queued_next_run'] = _timestamp(target)
            _write_state(path, state)
            try:
                config.talisman_pass.scheduler.next_run = target
                config.save()
            except Exception:
                config.talisman_pass.scheduler.next_run = previous_target
                _write_state(path, previous)
                raise
            state['queue_stage'] = 'queued'
            _write_state(path, state)
            return True
    except Exception:
        _warn('retry save')
        return False


def mark_closeout_completed(config, now=None) -> bool:
    """Call after real TalismanPass reward/feedback success, not generic TaskEnd."""
    now = now or datetime.now()
    today = now.date().isoformat()
    try:
        path = _state_path(config)
        with FileLock(str(path) + '.lock', timeout=5):
            state = _load_state(path, now)
            if state.get('queued_date') != today or state.get('completed_date') == today:
                return False
            state['completed_date'] = today
            _write_state(path, state)
            return True
    except Exception:
        _warn('completion save')
        return False


def closeout_summary(config, now=None) -> dict:
    """Feedback data with incomplete/unconfirmed items, no completion claims."""
    now = now or datetime.now()
    state = closeout_state(config, now)
    if state.get('unavailable'):
        return {'date': now.date().isoformat(), 'reason': 'state_unavailable', 'completed': [], 'incomplete': []}
    decision = _decision(config, state, now)
    incomplete = list(decision.incomplete) + [(name, 'unconfirmed') for name in decision.waiting]
    return {'date': now.date().isoformat(), 'reason': state.get('queue_reason') or decision.reason,
            'completed': [{'task': name, 'label': TASK_LABELS[name]} for name in decision.completed],
            'incomplete': [{'task': name, 'label': TASK_LABELS[name], 'outcome': status}
                           for name, status in incomplete]}
