from module.base.timer import Timer
from module.logger import logger


class CourtyardAffairsMixin:
    """Claim courtyard affairs through the existing mainline assets and controls."""

    def harvest_courtyard_affairs(self, timeout_seconds=60, max_complete_clicks=3):
        if not self.ui_click_multi_scale(self.I_NOTE, self.I_PAGE, timeout=3,
                                         scale_range=(0.8, 1.2)):
            logger.warning('Courtyard affairs entry was not found')
            return False
        timer = Timer(timeout_seconds).start()
        complete_clicks = 0
        while True:
            self.screenshot()
            if self.appear(self.I_NO_TASKS):
                logger.info('Courtyard affairs completed')
                return True
            if timer.reached():
                logger.warning('Courtyard affairs timed out; keep the task unfinished')
                return False
            if self.appear_then_click(self.I_HARVEST_SOUL_2, interval=1) or \
                    self.appear_then_click(self.I_HARVEST_SOUL_3, interval=1):
                continue
            if self.ui_reward_appear_click():
                continue
            if self.appear_then_click(self.I_UI_AWARD, interval=0.2):
                continue
            if self.appear_then_click(self.I_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_DAILY, interval=1):
                continue
            if self.appear_then_click(self.I_SUCCESS_CLAIMED, interval=1):
                continue
            if self.appear_then_click(self.I_SKIP, interval=1):
                continue
            if self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=1):
                continue
            if complete_clicks >= max_complete_clicks:
                logger.warning('Courtyard affairs click limit reached without a completion screen')
                return False
            if self.appear_then_click(self.I_COMPLETE_TASKS, interval=2.3):
                complete_clicks += 1
