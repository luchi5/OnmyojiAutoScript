# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from pydantic import Field
from typing import ClassVar

from tasks.Component.config_base import ConfigBase, TimeDelta, DateTime, Time, dynamic_hide

class Scheduler(ConfigBase):
    enable: bool = Field(default=False, description='enable_help')
    next_run: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"), description='next_run_help')
    priority: int = Field(default=5, description='priority_help')

    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(days=1), description='failure_interval_help')
    server_update: Time = Field(default=Time(hour=9, minute=0, second=0), description='server_update_help')
    delay_date: int = Field(default=1, description='delay_date_help', ge=1, le=31)
    float_time: Time = Field(default=Time(hour=0, minute=0, second=0), description='float_time_help')


class DedicatedTimeScheduler(Scheduler):
    """Calendar targets belong to the task, never to legacy force-time fields."""
    dedicated_time: ClassVar[bool] = True
    next_run: DateTime = Field(default=DateTime.fromisoformat('2023-01-01 00:00:00'),
                               description='dedicated_next_run_help')
    hide_calendar_fields = dynamic_hide(
        'server_update', 'failure_interval', 'success_interval', 'delay_date', 'float_time',
    )


class PartialDedicatedTimeScheduler(Scheduler):
    """Keep real repeat intervals; completed raids have their own clock time."""
    dedicated_time: ClassVar[bool] = True
    hide_force_fields = dynamic_hide('server_update', 'delay_date', 'float_time')


if __name__ == "__main__":
    dict_s = {
        "enable": False,
        "next_run": "2026-07-19T14:15:37",
        "priority": 5,
        "success_interval": "10 00:00:01",
        "failure_interval": "10 00:00:01",
        "server_update": "09:03:00",
        "float_time": "02:00:05"
    }
    s = Scheduler(**dict_s)
    print(s.model_dump())



