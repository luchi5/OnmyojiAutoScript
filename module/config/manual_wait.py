"""Calendar-aware manual skip. Pure planning: never records task completion."""
from datetime import datetime, time, timedelta
from tasks.Component.guild_retry_schedule import plan_opening_check


def next_wait_run(task, settings, now=None):
    now = (now or datetime.now()).replace(microsecond=0)
    scheduler = settings.scheduler
    scheduled = scheduler.next_run
    # A manual wait skips the displayed occurrence, including a future one.
    anchor = max(now, scheduled)
    if task == 'guild_banquet':
        cfg = settings.guild_banquet_time
        weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        slots = [(weekdays.index(getattr(day, 'value', day)), at) for day, at in
                 ((cfg.day_1, cfg.run_time_1), (cfg.day_2, cfg.run_time_2))]
        return plan_opening_check(anchor, slots, timedelta(minutes=3), completed=True).target
    if task == 'abyss_shadows':
        cfg = settings.abyss_shadows_time
        slots = [(day, getattr(cfg, field)) for day, field in
                 ((4, 'custom_run_time_friday'), (5, 'custom_run_time_saturday'),
                  (6, 'custom_run_time_sunday'))]
        return plan_opening_check(anchor, slots, timedelta(minutes=3), completed=True).target
    if scheduler.server_update != time(9):
        # Preserve the scheduled calendar phase for multi-day tasks.
        candidate = datetime.combine(scheduled.date(), scheduler.server_update)
        period = timedelta(days=max(1, scheduler.delay_date))
    else:
        # 09:00 is the existing scheduler's interval-mode sentinel.
        candidate = scheduled
        period = scheduler.success_interval
        if period <= timedelta(0):
            raise ValueError('Success interval must be positive for manual wait')
    if candidate <= anchor:
        candidate += period * ((anchor - candidate) // period + 1)
    return candidate.replace(microsecond=0)
