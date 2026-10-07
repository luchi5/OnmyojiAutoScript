"""Task-local skin values independent of cached account configuration models."""

from enum import Enum


class CourtyardSkin(str, Enum):
    AUTO = 'courtyard_affairs_auto'
    DEFAULT = 'courtyard_affairs_default'
    BLUE = 'courtyard_affairs_blue'
