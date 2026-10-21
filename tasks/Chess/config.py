# This Python file uses the following encoding: utf-8

from pydantic import Field, field_validator

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.Chess.strategy.lineup import LineupBond


class ChessConfig(ConfigBase):
    """百鬼棋局循环与结束条件。"""

    lineup_bond: LineupBond = Field(
        title='选择阵容羁绊',
        default=LineupBond.QIJIAOSHAN,
        description='选择阵容羁绊。',
    )

    @field_validator('lineup_bond', mode='before')
    @classmethod
    def migrate_removed_lineup(cls, value):
        raw = getattr(value, 'value', value)
        if raw in {'流火', '冥府', '大江山', '狐妖', '荒川',
                   'liuhuo', 'mingfu', 'dajiangshan', 'huyao', 'arakawa'}:
            return LineupBond.QIJIAOSHAN
        return value

    rank_protection: bool = Field(
        title='保段位',
        default=False,
        description='勾选启用保段位。',
    )

    run_count: int = Field(
        title='执行次数',
        default=1,
        ge=-1,
        description='填写局数；-1表示不限次数。',
    )
    matchmaking_timeout_seconds: int = Field(
        title='匹配限制时间（秒）',
        default=60,
        ge=10,
        description='单位：秒，至少10秒。',
    )
    limit_time: Time = Field(
        title='运行时间限制',
        default=Time(minute=30),
        description='格式：时:分:秒，例如00:30:00。',
    )
    coin_full_exit: bool = Field(
        title='刷满鼬乐币',
        default=False,
        description='勾选后刷满鼬乐币即停止。',
    )
    continue_grigri_refresh_below_nine: bool = Field(
        title='刷新非前40%符咒',
        default=True,
        description='勾选刷新收益排名前40%以外的符咒。',
    )


class Chess(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    chess_config: ChessConfig = Field(default_factory=ChessConfig)
