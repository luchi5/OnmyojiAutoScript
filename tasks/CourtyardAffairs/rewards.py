"""AZ's sign-in/reward order, shared by courtyard skins and their exit flow."""
from module.base.timer import Timer
from module.logger import logger


class CourtyardRewardMixin:
    def _click_courtyard_overlay(self, rule, interval=1, action=None, threshold=None):
        if rule is None or not self.appear(rule, threshold=threshold):
            return False
        kwargs = {'interval': interval}
        if action is not None:
            kwargs['action'] = action
        if threshold is not None:
            kwargs['threshold'] = threshold
        self.appear_then_click(rule, **kwargs)
        # A throttled click still means the overlay is present. Do not click
        # the daily tab/claim/return controls beneath it on the same frame.
        return True

    def handle_courtyard_reward(self):
        rules = getattr(self, '_longyi_rules', None)
        for rule in (rules['reward'] if rules else None,
                     getattr(self, 'I_BLUE_SUCCESS_CLAIMED', None)):
            if self._click_courtyard_overlay(
                    rule, action=getattr(self, 'C_BLUE_REWARD_CLOSE', None)):
                return True
        reward = getattr(self, 'I_UI_REWARD', None)
        if reward is not None:
            if self._click_courtyard_overlay(
                    reward, interval=.4, action=self.C_UI_REWARD, threshold=.6):
                return True
        elif self.ui_reward_appear_click():
            return True
        if self._click_courtyard_overlay(getattr(self, 'I_UI_AWARD', None), interval=.2):
            return True
        # Preserve AZ's ordinary sign, special signs, claim, and 999-day bag.
        for name, interval in (
                ('I_HARVEST_SIGN', 1.5), ('I_HARVEST_SIGN_3', .7),
                ('I_HARVEST_SIGN_4', 1), ('I_HARVEST_SIGN_2', 1.5),
                ('I_HARVEST_SIGN_999', 1.5),
                ('I_HARVEST_SOUL_2', 1), ('I_HARVEST_SOUL_3', 1),
                ('I_CONFIRM', 1), ('I_SKIP', 1), ('I_LOGIN_RED_CLOSE', 1)):
            if self._click_courtyard_overlay(getattr(self, name, None), interval):
                logger.info(f'Courtyard reward handled: {name}')
                return True
        return False

    def finish_courtyard_rewards(self, timeout_seconds=60):
        """Drain delayed sign-in/rewards, then require five quiet seconds at main."""
        timer = Timer(timeout_seconds).start()
        quiet_timer = Timer(min(5, max(1, timeout_seconds - 1))).start()
        while not timer.reached():
            self.screenshot()
            if self.handle_courtyard_reward():
                quiet_timer.reset()
                continue
            if self.appear(self.I_PAGE):
                self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1.5)
                quiet_timer.reset()
                continue
            if not self.appear(self.I_CHECK_MAIN):
                quiet_timer.reset()
                continue
            if quiet_timer.reached():
                logger.info('Courtyard sign-in and rewards finished; main page stable')
                return True
        logger.warning('Courtyard sign-in/reward cleanup timed out')
        return False
