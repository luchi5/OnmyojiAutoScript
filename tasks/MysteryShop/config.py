# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import timedelta, datetime, time
from pydantic import BaseModel, Field, model_validator

from tasks.Component.config_scheduler import DedicatedTimeScheduler as Scheduler
from tasks.Component.config_base import ConfigBase, Time

class ShopConfig(BaseModel):
    time_of_mystery: Time = Field(default=Time(hour=9), description='time_of_mystery_help')
    mystery_amulet: bool = Field(default=False)
    black_daruma_scrap: bool = Field(default=False)
    shop_kaiko_3: bool = Field(default=False)
    shop_kaiko_4: bool = Field(default=False)




class ShareConfig(BaseModel):
    enable: bool = Field(default=False)
    share_friend_1: str = Field(title='Share Friend 1', default='', description='share_friend_1_help')
    share_friend_2: str = Field(title='Share Friend 2', default='')
    share_friend_3: str = Field(title='Share Friend 3', default='')
    share_friend_4: str = Field(title='Share Friend 4', default='')
    share_friend_5: str = Field(title='Share Friend 5', default='')

class MysteryShop(ConfigBase):
    @model_validator(mode='before')
    @classmethod
    def migrate_legacy_clock(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        group = dict(data.get('shop_config') or {})
        # 00:00 was the old "use generic scheduler" sentinel, not midnight.
        if group.get('time_of_mystery') in ('00:00:00', time(0)):
            scheduler = data.get('scheduler') or {}
            group['time_of_mystery'] = scheduler.get('server_update', time(9))
        data['shop_config'] = group
        return data
    scheduler: Scheduler = Field(default_factory=Scheduler)
    shop_config: ShopConfig = Field(default_factory=ShopConfig)
    share_config: ShareConfig = Field(default_factory=ShareConfig)

