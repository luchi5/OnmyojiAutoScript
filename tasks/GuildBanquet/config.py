# This Python file uses the following encoding: utf-8
# @author ohspecial
# github https://github.com/ohspecial
from enum import Enum  
from datetime import timedelta

from pydantic import Field, BaseModel, SerializationInfo, field_serializer, model_validator, ConfigDict

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time, TimeDelta, dynamic_hide
from tasks.Component.guild_opening_config import GuildOpeningScheduler, migrate_opening_settings


class Weekday(str,Enum):
    Monday: str = "星期一"
    Tuesday: str = "星期二" 
    Wednesday: str = "星期三"
    Thursday: str = "星期四"
    Friday: str = "星期五"
    Saturday: str = "星期六"
    Sunday: str = "星期日"


class GuildBanquetTime(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    opening_wait_minutes: int = Field(default=60, ge=1, le=1440,
                                      description='opening_wait_minutes_help')
    opening_retry_interval: TimeDelta = Field(default=TimeDelta(minutes=3),
                                             gt=timedelta(0), lt=timedelta(days=1),
                                             description='opening_retry_interval_help')
    auto_switch_shikigami: bool = Field(default=False, description='guild_auto_switch_shikigami_help')
    # 自定义运行时间
    day_1: Weekday = Field(
        default=Weekday.Wednesday,
        description="每周第1次运行时间设置，注意第一次时间要比第二次时间早",
    )
    run_time_1: Time = Field(default=Time(hour=19, minute=0, second=0))
    day_2: Weekday = Field(
        default=Weekday.Saturday,
        description="每周第2次运行时间设置",
    )
    run_time_2: Time = Field(
        default=Time(hour=19, minute=0, second=0), 
        description="每周第2次运行时间设置"
    )



class GuildBanquet(ConfigBase):
    scheduler: GuildOpeningScheduler = Field(default_factory=GuildOpeningScheduler)
    guild_banquet_time: GuildBanquetTime = Field(default_factory=GuildBanquetTime)

    @model_validator(mode='before')
    @classmethod
    def preserve_legacy_opening(cls, data):
        return migrate_opening_settings(data, 'guild_banquet_time')

