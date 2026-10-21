"""Configuration for a pair of accounts running Draft Duel together."""
from datetime import time
from enum import Enum

from pydantic import ConfigDict, Field, field_validator

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class TeamRole(str, Enum):
    UNASSIGNED = 'unassigned'
    LEADER = 'leader'
    MEMBER = 'member'


class FriendTab(str, Enum):
    FRIEND = '好友'
    RECENT = '最近'
    GUILD = '寮友'
    CROSS = '跨区'


class DraftDuelConfig(ConfigBase):
    model_config = ConfigDict(validate_assignment=True)

    user_status: TeamRole = Field(default=TeamRole.UNASSIGNED, description='draft_duel_role_help')
    partner_config: str = Field(default='', description='draft_duel_partner_config_help')
    teammate_name: str = Field(default='', description='draft_duel_teammate_name_help')
    friend_tab: FriendTab = Field(default=FriendTab.CROSS, description='draft_duel_friend_tab_help')
    challenge_limit: int = Field(default=1, ge=1, description='draft_duel_challenge_limit_help')
    limit_time: Time = Field(default=Time(hour=1), description='draft_duel_limit_time_help')
    coin_budget: int = Field(default=0, ge=0, description='draft_duel_coin_budget_help')
    free_only: bool = Field(default=True, description='draft_duel_free_only_help')
    partner_wait_seconds: int = Field(default=120, ge=10, description='draft_duel_partner_wait_seconds_help')
    phase_timeout_seconds: int = Field(default=120, ge=10, description='draft_duel_phase_timeout_seconds_help')
    matchmaking_timeout_seconds: int = Field(default=120, ge=10, description='matchmaking_timeout_seconds_help')

    @field_validator('partner_config', 'teammate_name')
    @classmethod
    def normalize_partner_config(cls, value: str) -> str:
        return value.strip()

    @field_validator('limit_time')
    @classmethod
    def positive_limit_time(cls, value: time) -> time:
        if value == time():
            raise ValueError('Draft Duel limit_time must be greater than zero')
        return value


class DraftDuel(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    draft_duel_config: DraftDuelConfig = Field(default_factory=DraftDuelConfig)
