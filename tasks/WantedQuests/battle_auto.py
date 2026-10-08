"""Recover a wanted-quest battle's manual mode without toggling auto off."""
from time import monotonic

from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.exception import GameStuckError
from module.logger import logger


class WantedBattleAuto:
    CHECK_INTERVAL = 2.0
    MAX_ATTEMPTS = 3

    def __init__(self):
        self.last_check = -float('inf')
        self.attempts = 0
        self.pending = False
        # The lower-left mode label stays in this area across battle themes.
        self.labels = [
            RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                    keyword='', name='wanted_battle_mode')
            for roi in ((18, 635, 98, 62), (35, 644, 52, 34))
        ]
        self.button = RuleClick(roi_front=(40, 650, 40, 28),
                                roi_back=(40, 650, 40, 28),
                                name='wanted_battle_auto_switch')

    def inspect(self, task) -> bool:
        """Inspect the current screenshot; True only when a switch was sent."""
        now = monotonic()
        if now - self.last_check < self.CHECK_INTERVAL:
            return False
        self.last_check = now
        if task.is_in_prepare(False) or not task.is_in_real_battle(False):
            return False

        mode = ''
        for label in self.labels:
            result = label.ocr(task.device.image)
            if isinstance(result, str):
                mode = ''.join(result.split())
            if mode in ('手动', '自动'):
                break
        if mode == '自动':
            if self.pending:
                logger.info('悬赏封印：已确认自动战斗')
            self.pending = False
            self.attempts = 0
            return False
        if mode != '手动':
            return False
        if self.attempts >= self.MAX_ATTEMPTS:
            raise GameStuckError('悬赏封印：切换自动战斗后仍显示手动，请检查控制连接')
        if task.click(self.button, interval=self.CHECK_INTERVAL):
            self.attempts += 1
            self.pending = True
            logger.info(f'悬赏封印：检测到手动战斗，切换自动 ({self.attempts}/{self.MAX_ATTEMPTS})')
            return True
        return False
