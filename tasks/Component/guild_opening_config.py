"""Visible guild opening settings, with migration from mainline schedulers."""
from datetime import timedelta, time
from pydantic import BaseModel, field_serializer

from tasks.Component.config_base import format_timedelta
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.guild_retry_schedule import retry_delay


class GuildOpeningScheduler(Scheduler):
    # These tasks use explicit calendar targets and their own retry settings.
    # Showing the generic force-time/retry controls alongside them is misleading.
    @field_serializer(
        'server_update', 'failure_interval', 'success_interval', 'delay_date', 'float_time',
    )
    def hide_opening_fields(self, value, info):
        if info.context and info.context.get('hide', False):
            return 0xABCDEF
        if isinstance(value, timedelta):
            return format_timedelta(value)
        if isinstance(value, time):
            return value.strftime('%H:%M:%S')
        return value


def migrate_opening_settings(data, group_name, dokan=False):
    """Keep legacy account times/intervals when adding the visible controls."""
    if not isinstance(data, dict):
        return data
    result = dict(data)
    group = result.get(group_name, {})
    scheduler = result.get('scheduler', {})
    if isinstance(group, BaseModel):
        group = group.model_dump()
    if isinstance(scheduler, BaseModel):
        scheduler = scheduler.model_dump()
    if not isinstance(group, dict) or not isinstance(scheduler, dict):
        return data
    group = dict(group)
    if 'opening_retry_interval' not in group and 'failure_interval' in scheduler:
        previous_interval = retry_delay(scheduler['failure_interval'])
        # Legacy disabled tasks often contain the generic one-day default.
        # The frontend's duration endpoint supports sub-day retry intervals.
        group['opening_retry_interval'] = (previous_interval if previous_interval < timedelta(days=1)
                                            else timedelta(minutes=3))
    if dokan and 'dokan_run_time' not in group:
        previous = scheduler.get('server_update')
        # 09:00 is the mainline sentinel, rather than a real evening opening.
        if previous is not None and str(previous) != '09:00:00':
            group['dokan_run_time'] = previous
    result[group_name] = group
    return result
