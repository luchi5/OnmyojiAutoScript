# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Component.config_scheduler import Scheduler
from tasks.Utils.config_enum import ShikigamiClass


class CardType(str, Enum):
    FISH = '斗鱼'
    TAIKO = '太鼓'
    

class ActivationScheduler(Scheduler):
    priority: int = Field(default=2, description='priority_help')
    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(hours=10), description='failure_interval_help')


class ActivationConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    card_type: CardType = Field(default=CardType.TAIKO, description='card_rule_help')
    min_taiko_num: int = Field(default=8, ge=0, description='min_taiko_num_help')
    max_taiko_num: int = Field(default=0, ge=0, description='max_taiko_num_help')
    min_fish_num: int = Field(default=16, ge=0, description='min_fish_num_help')
    max_fish_num: int = Field(default=0, ge=0, description='max_fish_num_help')
    exchange_before: bool = Field(default=True, description='exchange_before_help')
    exchange_max: bool = Field(default=True, description='exchange_max_help')
    auto_fill: bool = Field(default=False, description='auto_fill_help')
    shikigami_class: ShikigamiClass = Field(default=ShikigamiClass.N, description='shikigami_class_help')
    card_not_found_count: int = Field(default=0, description='未发现卡次数')

    @field_validator('min_taiko_num', 'max_taiko_num', 'min_fish_num', 'max_fish_num')
    @classmethod
    def validate_reward_range(cls, value: int, info: ValidationInfo) -> int:
        kind = 'taiko_num' if 'taiko' in info.field_name else 'fish_num'
        minimum = value if info.field_name.startswith('min_') else info.data.get(f'min_{kind}', 0)
        maximum = value if info.field_name.startswith('max_') else info.data.get(f'max_{kind}', 0)
        if maximum > 0 and maximum < minimum:
            label = '太鼓' if kind == 'taiko_num' else '斗鱼'
            raise ValueError(f'{label}收益上限不能低于下限；上限0表示不限')
        return value


class KekkaiActivation(ConfigBase):
    scheduler: ActivationScheduler = Field(default_factory=ActivationScheduler)
    activation_config: ActivationConfig = Field(default_factory=ActivationConfig)
