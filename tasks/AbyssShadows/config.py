# This Python file uses the following encoding: utf-8
# @brief    Configurations for Ryou Dokan Toppa (阴阳竂道馆突破配置)
# @author   jackyhwei
# @note     draft version without full test
# github    https://github.com/roarhill/oas

from pydantic import BaseModel, Field, model_validator, ConfigDict
# from pygments.lexer import default
# from scripts.regsetup import description

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase, Time, TimeDelta
from tasks.Component.config_scheduler import DedicatedTimeScheduler as Scheduler
from tasks.Component.guild_opening_config import migrate_opening_settings
from datetime import timedelta


class AbyssProcessManage(ConfigBase):
    attack_order: str = Field(default='', description='abyss_attack_order_help')
    try_complete_enemy_count: bool = Field(default=True, description='abyss_complete_enemy_count_help')

class AbyssShadowsTime(ConfigBase):
    model_config = ConfigDict(validate_assignment=True)
    opening_retry_interval: TimeDelta = Field(default=timedelta(minutes=3),
                                             gt=timedelta(0), lt=timedelta(days=1),
                                             description='opening_retry_interval_help')
    # 自定义运行时间
    custom_run_time_friday: Time = Field(default=Time(hour=19, minute=0, second=0))
    custom_run_time_saturday: Time = Field(default=Time(hour=19, minute=0, second=0))
    custom_run_time_sunday: Time = Field(default=Time(hour=19, minute=0, second=0))

class AbyssShadowsCombatTime(BaseModel):
    # 设置战斗时间
    CombatTime_enable: bool = Field(default=False, description="开启智能伤害")
    boss_combat_time: int = Field(default=60, description="以秒为单位设置首领战斗时间，请合理设置")
    general_combat_time: int = Field(default=60, description="以秒为单位设置副将战斗时间，请合理设置")
    elite_combat_time: int = Field(default=60, description="以秒为单位设置精英战斗时间，请合理设置")

class AbyssShadows(ConfigBase):
    @model_validator(mode='before')
    @classmethod
    def migrate_schedule(cls, data):
        return migrate_opening_settings(data, 'abyss_shadows_time')

    scheduler: Scheduler = Field(default_factory=Scheduler)
    abyss_shadows_time: AbyssShadowsTime = Field(default_factory=AbyssShadowsTime)
    abyss_shadows_combat_time: AbyssShadowsCombatTime = Field(default_factory=AbyssShadowsCombatTime)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
    process_manage: AbyssProcessManage = Field(default_factory=AbyssProcessManage)
