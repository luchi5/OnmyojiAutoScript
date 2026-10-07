from pydantic import ConfigDict, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler
from tasks.CourtyardAffairs.themes import CourtyardSkin


class CourtyardAffairsConfig(ConfigBase):
    model_config = ConfigDict(validate_assignment=True)
    courtyard_skin: CourtyardSkin = Field(default=CourtyardSkin.AUTO,
                                         description='courtyard_skin_help')
    timeout_seconds: int = Field(default=60, ge=5, le=180,
                                 description='courtyard_affairs_timeout_help')
    max_complete_clicks: int = Field(default=3, ge=1, le=10,
                                    description='courtyard_affairs_click_limit_help')


class CourtyardAffairs(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    courtyard_affairs_config: CourtyardAffairsConfig = Field(default_factory=CourtyardAffairsConfig)
