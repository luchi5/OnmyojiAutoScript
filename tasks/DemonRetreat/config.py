# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import timedelta
from pydantic import BaseModel, Field, model_validator, ConfigDict

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time, TimeDelta
from tasks.Component.guild_opening_config import GuildOpeningScheduler, migrate_opening_settings


class DemonRetreatTime(ConfigBase):
    model_config = ConfigDict(validate_assignment=True)
    opening_wait_minutes: int = Field(default=60, ge=1, le=1440,
                                      description='opening_wait_minutes_help')
    opening_retry_interval: TimeDelta = Field(default=TimeDelta(minutes=3),
                                             gt=timedelta(0), lt=timedelta(days=1),
                                             description='opening_retry_interval_help')
    # 自定义运行时间
    custom_run_time: Time = Field(default=Time(hour=10, minute=0, second=0),
                                  description='demon_retreat_opening_time_help')


class DemonRetreat(ConfigBase):
    scheduler: GuildOpeningScheduler = Field(default_factory=GuildOpeningScheduler)
    demon_retreat_time: DemonRetreatTime = Field(default_factory=DemonRetreatTime)
    general_battle: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)

    @model_validator(mode='before')
    @classmethod
    def preserve_legacy_opening(cls, data):
        return migrate_opening_settings(data, 'demon_retreat_time')
