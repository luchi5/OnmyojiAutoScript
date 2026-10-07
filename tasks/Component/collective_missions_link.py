"""Per-account Dokan/CollectiveMissions link, using the existing task queue."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from tasks.Component.guild_retry_schedule import retry_delay


LINK_IDLE_TARGET = datetime(2099, 1, 1)


@dataclass(frozen=True)
class LinkResult:
    queued: bool
    reason: str


@dataclass(frozen=True)
class CollectiveGate:
    allowed: bool
    reason: str
    verify_only: bool = False


def linked_mode(config) -> bool:
    missions = config.collective_missions.missions_config
    return bool(getattr(missions, 'run_after_dokan', False))


def restricted_days(config) -> bool:
    missions = config.collective_missions.missions_config
    return linked_mode(config) or bool(getattr(missions, 'monday_to_thursday', False))


def collective_gate(config, now=None) -> CollectiveGate:
    now = now or datetime.now()
    missions = config.collective_missions.missions_config
    if restricted_days(config) and now.weekday() >= 4:
        return CollectiveGate(False, 'outside_days')
    today = now.date().isoformat()
    if getattr(missions, 'completed_date', '') == today:
        return CollectiveGate(False, 'already_completed')
    if not linked_mode(config):
        return CollectiveGate(True, 'independent')
    if getattr(missions, 'dokan_finished_date', '') != today:
        return CollectiveGate(False, 'waiting_dokan')
    if getattr(missions, 'attempted_date', '') == today:
        # After a crash, inspect the game counter but never submit twice.
        return CollectiveGate(True, 'verify_previous_attempt', verify_only=True)
    return CollectiveGate(True, 'linked_ready')


def queue_collective_after_dokan(config, *, confirmed: bool, now=None) -> LinkResult:
    """Persist a final-Dokan proof together with one existing queue entry.

    The caller must pass confirmed=False for unopened/unknown/skip-day exits.
    Counts/date provide another guard against an early first-challenge callback.
    No game task is run here; Config.task_call persists proof and next_run in
    the same configuration save, so restart does not create a second job.
    """
    now = now or datetime.now()
    task = getattr(config, 'collective_missions', None)
    if task is None or not getattr(task.scheduler, 'enable', False):
        return LinkResult(False, 'disabled')
    if not linked_mode(config):
        return LinkResult(False, 'independent')
    if not confirmed:
        return LinkResult(False, 'dokan_unconfirmed')
    if now.weekday() >= 4:
        return LinkResult(False, 'outside_days')
    today = now.date().isoformat()
    attack = config.dokan.attack_count_config
    if getattr(attack, 'attack_date', '') != today:
        return LinkResult(False, 'dokan_date_unconfirmed')
    remaining = getattr(attack, 'remain_attack_count', None)
    daily = getattr(attack, 'daily_attack_count', None)
    if type(remaining) is not int or type(daily) is not int or daily not in (1, 2):
        return LinkResult(False, 'dokan_count_unconfirmed')
    if remaining < 0 or remaining > (0 if daily == 2 else 1):
        return LinkResult(False, 'dokan_challenges_pending')
    missions = task.missions_config
    # Old in-memory config classes must keep independent behavior until their
    # schema is reloaded; they cannot safely store link proof or guards.
    state_names = ('dokan_finished_date', 'attempted_date', 'completed_date', 'pending_kind', 'pending_until')
    if not all(hasattr(missions, name) for name in state_names):
        return LinkResult(False, 'link_state_unavailable')
    if missions.completed_date == today:
        return LinkResult(False, 'already_completed')
    if missions.attempted_date == today:
        return LinkResult(False, 'already_attempted')
    if missions.dokan_finished_date == today:
        return LinkResult(False, 'already_queued')
    previous = missions.dokan_finished_date
    previous_pending = missions.pending_kind
    previous_until = missions.pending_until
    missions.dokan_finished_date = today
    missions.pending_kind = ''
    missions.pending_until = ''
    try:
        queued = config.task_call('CollectiveMissions', force_call=False)
    except Exception:
        missions.dokan_finished_date = previous
        missions.pending_kind = previous_pending
        missions.pending_until = previous_until
        raise
    if not queued:
        missions.dokan_finished_date = previous
        missions.pending_kind = previous_pending
        missions.pending_until = previous_until
        return LinkResult(False, 'disabled')
    return LinkResult(True, 'queued')


def begin_collective_attempt(config, now=None) -> bool:
    """Save the submission guard before linked mode selects or submits anything."""
    now = now or datetime.now()
    gate = collective_gate(config, now)
    if not gate.allowed or gate.verify_only:
        return False
    if linked_mode(config):
        missions = config.collective_missions.missions_config
        if not hasattr(missions, 'attempted_date'):
            return False
        missions.attempted_date = now.date().isoformat()
        if hasattr(missions, 'pending_kind'):
            missions.pending_kind = ''
        if hasattr(missions, 'pending_until'):
            missions.pending_until = ''
        config.save()
    return True


def complete_collective(config, now=None) -> None:
    """Only callers with a confirmed full game counter may write completion."""
    now = now or datetime.now()
    missions = config.collective_missions.missions_config
    if hasattr(missions, 'completed_date'):
        missions.completed_date = now.date().isoformat()
        if hasattr(missions, 'pending_kind'):
            missions.pending_kind = ''
        if hasattr(missions, 'pending_until'):
            missions.pending_until = ''
        config.save()


def next_collective_target(config, *, success=False, waiting=False, now=None):
    """Preserve independent intervals while moving restricted runs off Fri-Sun."""
    now = now or datetime.now()
    scheduler = config.collective_missions.scheduler
    if linked_mode(config) and waiting:
        # The script framework opens the game before task.run. Do not create a
        # daily no-op wake; only a confirmed final Dokan task_call wakes us.
        return LINK_IDLE_TARGET
    else:
        interval = scheduler.success_interval if success or waiting else scheduler.failure_interval
        if waiting:
            interval = max(retry_delay(interval), timedelta(days=1))
        candidate = now + retry_delay(interval)
        # Preserve legacy independent forced-clock scheduling, without letting
        # a common server override put a restricted task on a forbidden day.
        at = getattr(scheduler, 'server_update', time(9))
        if not linked_mode(config) and at != time(9):
            days = max(1, int(getattr(scheduler, 'delay_date', 1)))
            candidate = datetime.combine(now.date() + timedelta(days=days), at)
    if linked_mode(config) and candidate.date() != now.date():
        return LINK_IDLE_TARGET
    if restricted_days(config):
        while candidate.weekday() >= 4:
            candidate += timedelta(days=1)
    return candidate.replace(microsecond=0)


def collective_continuation_target(config, target, now=None):
    """Keep explicit linked continuation on its proven day; never wake next day."""
    now = now or datetime.now()
    if linked_mode(config):
        if target.date() != now.date() or now.weekday() >= 4:
            return LINK_IDLE_TARGET
        if getattr(config.collective_missions.missions_config, 'dokan_finished_date', '') != now.date().isoformat():
            return LINK_IDLE_TARGET
    if restricted_days(config):
        while target.weekday() >= 4:
            target += timedelta(days=1)
    return target.replace(microsecond=0)


def pending_collective_check(config, now=None):
    """Recover an already-saved child-task wait after a crash, without requeueing it."""
    now = now or datetime.now()
    gate = collective_gate(config, now)
    if not linked_mode(config) or not gate.allowed or not gate.verify_only:
        return None
    missions = config.collective_missions.missions_config
    if getattr(missions, 'pending_kind', '') != 'bondling_reward':
        return None
    try:
        target = datetime.fromisoformat(getattr(missions, 'pending_until', ''))
        if target <= now:
            return None
    except (ValueError, TypeError):
        return None
    return collective_continuation_target(config, target, now)
