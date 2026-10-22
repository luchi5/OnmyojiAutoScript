# This Python file uses the following encoding: utf-8

from pydantic import Field, field_validator

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.Chess.strategy.lineup import LineupBond


class ChessConfig(ConfigBase):
    """百鬼棋局循环与结束条件。"""

    hyakki_draft_enabled: bool = Field(
        title='百鬼投掷试行', default=True,
    )

    lineup_bond: LineupBond = Field(
        title='选择阵容羁绊',
        default=LineupBond.QIJIAOSHAN,
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
    )

    run_count: int = Field(
        title='执行次数',
        default=1,
        ge=-1,
    )
    matchmaking_timeout_seconds: int = Field(
        title='匹配限制时间（秒）',
        default=60,
        ge=10,
    )
    limit_time: Time = Field(
        title='运行时间限制',
        default=Time(minute=30),
    )
    coin_full_exit: bool = Field(
        title='刷满鼬乐币',
        default=False,
    )
    continue_grigri_refresh_below_nine: bool = Field(
        title='刷新非前40%符咒',
        default=True,
    )


class Chess(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    chess_config: ChessConfig = Field(default_factory=ChessConfig)
