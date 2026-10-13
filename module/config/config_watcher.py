# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import os
from datetime import datetime

from module.config.utils import filepath_config, DEFAULT_TIME
from module.logger import logger

class ConfigWatcher:
    """
    监视特定配置文件修改的功能。
    它跟踪了初始修改时间,(start_mtime)
    并提供了方法来检查文件是否被修改(should_reload)
    和获取当前修改时间(get_mtime)。
    """
    config_name = 'script'
    start_mtime = DEFAULT_TIME

    def start_watching(self) -> None:
        stat = os.stat(filepath_config(self.config_name))
        self._start_signature = self._signature(stat)
        self.start_mtime = datetime.fromtimestamp(stat.st_mtime)

    @staticmethod
    def _signature(stat):
        return (stat.st_mtime_ns, stat.st_size, stat.st_dev, stat.st_ino)

    def get_mtime(self) -> datetime:
        """
        Last modify time of the file
        """
        timestamp = os.stat(filepath_config(self.config_name)).st_mtime
        mtime = datetime.fromtimestamp(timestamp)
        return mtime

    def should_reload(self) -> bool:
        """
        Returns:
            bool: Whether the file has been modified and configs should reload
        """
        stat = os.stat(filepath_config(self.config_name))
        mtime = datetime.fromtimestamp(stat.st_mtime)
        previous = getattr(self, '_start_signature', None)
        changed = self._signature(stat) != previous if previous is not None else mtime != self.start_mtime
        if changed:
            logger.info(f'Config "{self.config_name}" changed at {mtime.replace(microsecond=0)}')
            return True
        else:
            return False

