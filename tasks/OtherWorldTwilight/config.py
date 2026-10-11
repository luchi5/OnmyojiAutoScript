# This Python file uses the following encoding: utf-8
# @author AzurTian
from datetime import timedelta, time

from pydantic import Field
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralInvite.config_invite import InviteConfig
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase, Time, dynamic_hide
from tasks.Component.config_scheduler import Scheduler
from enum import Enum


class UserStatus(str, Enum):
    LEADER = 'leader'
    MEMBER = 'member'
    ALONE = 'alone'


class OtherWorldTwilightConfig(ConfigBase):
    # 身份
    user_status: UserStatus = Field(default=UserStatus.LEADER, description='user_status_help')
    # 限制时间
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 限制次数
    limit_count: int = Field(default=30, description='limit_count_help', ge=1)

    @property
    def limit_time_v(self) -> timedelta:
        if isinstance(self.limit_time, time):
            return timedelta(hours=self.limit_time.hour, minutes=self.limit_time.minute,
                             seconds=self.limit_time.second)
        return self.limit_time


class OWTBattleConfig(GeneralBattleConfig):
    hide_fields = dynamic_hide('preset_enable', 'preset_group', 'preset_team', 'green_mark')

    # 彼世逢魔的站位可变，绿标仅按名称识别；不改变其它任务的战斗配置。
    green_mark_name: str = Field(default='', description='green_mark_name_help')


class OtherWorldTwilight(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    other_world_twilight_config: OtherWorldTwilightConfig = Field(default_factory=OtherWorldTwilightConfig)
    invite_config: InviteConfig = Field(default_factory=InviteConfig)
    general_battle_config: OWTBattleConfig = Field(default_factory=OWTBattleConfig)
    switch_soul: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
