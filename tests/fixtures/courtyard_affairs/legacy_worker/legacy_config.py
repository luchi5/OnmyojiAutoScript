from pydantic import Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class CourtyardAffairsConfig(ConfigBase):
    timeout_seconds: int = Field(default=60, ge=5, le=180,
                                 description='courtyard_affairs_timeout_help')
    max_complete_clicks: int = Field(default=3, ge=1, le=10,
                                    description='courtyard_affairs_click_limit_help')


class CourtyardAffairs(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    courtyard_affairs_config: CourtyardAffairsConfig = Field(default_factory=CourtyardAffairsConfig)
