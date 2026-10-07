"""Selected skin execution, independent of legacy worker module caches."""

import cv2
import numpy as np
from copy import copy

from module.atom.image import RuleImage
from module.base.timer import Timer
from module.logger import logger
from tasks.CourtyardAffairs.themes import CourtyardSkin
from tasks.CourtyardAffairs.selected_assets import (
    BlueCourtyardAssets, CourtyardSkinRule, SkinAwareCourtyardAffairsMixin)
from tasks.GameUi.action import conditional_action


def _clone_rule(rule):
    if rule is None:
        return None
    result = copy(rule)
    result.roi_front = list(rule.roi_front)
    return result


def _skin_rule(*rules, skin=CourtyardSkin.AUTO, daily_inactive=None):
    # Copy inputs as well as rule internals: matching localizes front ROIs.
    return CourtyardSkinRule(*(_clone_rule(rule) for rule in rules),
                            skin=skin, daily_inactive=_clone_rule(daily_inactive))


class BlueCompletionAssets:
    I_BLUE_DISABLED_COMPLETE = RuleImage(
        roi_front=(1124, 599, 77, 75), roi_back=(1100, 570, 142, 128),
        threshold=.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_disabled_complete.png')
    I_BLUE_COMPLETED_STAMP_FIRST = RuleImage(
        roi_front=(1025, 180, 90, 56), roi_back=(1000, 165, 145, 88),
        threshold=.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_completed_stamp.png')
    I_BLUE_COMPLETED_STAMP_SECOND = RuleImage(
        roi_front=(1025, 310, 90, 56), roi_back=(1000, 295, 145, 88),
        threshold=.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_completed_stamp.png')
    I_BLUE_COMPLETED_STAMP_THIRD = RuleImage(
        roi_front=(1025, 440, 90, 56), roi_back=(1000, 425, 145, 88),
        threshold=.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_completed_stamp.png')
    I_BLUE_COMPLETED_STAMP_FOURTH = RuleImage(
        roi_front=(1025, 570, 90, 56), roi_back=(1000, 555, 145, 88),
        threshold=.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_completed_stamp.png')


class CourtyardEmptyTasksRule(RuleImage):
    """Keep a confirmed daily context while a reward covers its tab.

    A missing daily marker is insufficient to prove a special page. Clear
    the context only on a positively recognized inactive daily tab or a
    different game page. The common empty popup remains a positive rule.
    """

    def __init__(self, empty_rule, default_page, blue_page, daily_selected,
                 daily_inactive, other_pages=(), skin=CourtyardSkin.AUTO):
        super().__init__(tuple(empty_rule.roi_front), empty_rule.roi_back,
                         empty_rule.method, empty_rule.threshold, empty_rule.file)
        self.empty_rule = _clone_rule(empty_rule)
        self.default_page = _clone_rule(default_page)
        self.blue_page = _clone_rule(blue_page)
        self.daily_selected = _clone_rule(daily_selected)
        self.daily_inactive = _clone_rule(daily_inactive)
        self.other_pages = tuple(_clone_rule(rule) for rule in other_pages)
        self.daily_confirmed = False
        self.skin = CourtyardSkin(skin)

    def match(self, image, threshold=None):
        if self.blue_page.match(image):
            # Explicit inactive evidence takes precedence if both templates
            # happen to match; an ambiguous page must not count as completed.
            if self.skin == CourtyardSkin.DEFAULT or self.daily_inactive.match(image):
                self.daily_confirmed = False
            elif self.daily_selected.match(image):
                self.daily_confirmed = True
        elif self.default_page.match(image):
            self.daily_confirmed = self.skin != CourtyardSkin.BLUE
        elif any(rule.match(image) for rule in self.other_pages):
            self.daily_confirmed = False
        if not self.daily_confirmed or not self.empty_rule.match(image, threshold=threshold):
            return False
        self.roi_front = list(self.empty_rule.roi_front)
        return True


class BlueCompletionDetector:
    """Positive done state: daily page, disabled button, four completed rows.

    CCOEFF template matching is invariant to brightness: the bright enabled
    button also matches the disabled template at 0.995. Verify its actual
    luminance and that the white task card is not under a reward shade.
    """

    def __init__(self, page=None, daily=None, disabled=None, stamps=None, inactive=None):
        self.page = _clone_rule(page if page is not None else BlueCourtyardAssets.I_BLUE_PAGE)
        self.daily = _clone_rule(daily if daily is not None else BlueCourtyardAssets.I_BLUE_DAILY_SELECTED)
        self.inactive = _clone_rule(inactive if inactive is not None else BlueCourtyardAssets.I_BLUE_DAILY_INACTIVE)
        self.disabled = _clone_rule(disabled if disabled is not None else BlueCompletionAssets.I_BLUE_DISABLED_COMPLETE)
        self.stamps = tuple(_clone_rule(rule) for rule in (
            stamps if stamps is not None else (
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_FIRST,
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_SECOND,
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_THIRD,
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_FOURTH)))

    def ready(self, image):
        if not self.page.match(image) or not self.daily.match(image) or self.inactive.match(image):
            return False
        if not self.disabled.match(image) or not all(rule.match(image) for rule in self.stamps):
            return False
        x, y, width, height = self.disabled.roi_front
        button = image[y:y + height, x:x + width]
        # A blank area of the second daily task card stays white in both
        # initial and completed pages; modal dimming must fail this check.
        card = image[285:330, 910:1010]
        if not button.size or not card.size:
            return False
        button_value = cv2.cvtColor(button, cv2.COLOR_RGB2HSV)[:, :, 2]
        card_value = cv2.cvtColor(card, cv2.COLOR_RGB2HSV)[:, :, 2]
        return float(np.mean(button_value)) <= 130 and float(np.median(card_value)) >= 190


class CompletionAwareCourtyardMixin(SkinAwareCourtyardAffairsMixin):
    def configure_courtyard_skin(self, courtyard_skin=CourtyardSkin.AUTO):
        skin = CourtyardSkin(courtyard_skin)
        if getattr(self, '_courtyard_skin', None) == skin:
            return
        # Keep pristine rules so reconfiguration never wraps an old selector
        # or mutates the shared Restart/GameUi assets used by other tasks.
        if not hasattr(self, '_courtyard_base_rules'):
            self._courtyard_base_rules = {name: _clone_rule(getattr(self, name)) for name in (
                'I_PAGE', 'I_NO_TASKS', 'I_COMPLETE_TASKS', 'I_DAILY',
                'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED', 'I_BLUE_DAILY_INACTIVE',
                'I_BLUE_COMPLETE_TASKS', 'I_BLUE_SUCCESS_CLAIMED')}
        base = self._courtyard_base_rules
        default_page, blue_page = base['I_PAGE'], base['I_BLUE_PAGE']
        other_pages = tuple(getattr(self, name) for name in (
            'I_CHECK_MAIN', 'I_CHECK_EXPLORATION', 'I_CHECK_RECORDS', 'I_CHECK_GUILD')
            if hasattr(self, name))
        self.I_NO_TASKS = CourtyardEmptyTasksRule(
            base['I_NO_TASKS'], default_page, blue_page, base['I_BLUE_DAILY_SELECTED'],
            base['I_BLUE_DAILY_INACTIVE'], other_pages, skin=skin)
        self.I_COMPLETE_TASKS = _skin_rule(
            default_page, blue_page, base['I_COMPLETE_TASKS'],
            base['I_BLUE_COMPLETE_TASKS'], base['I_BLUE_DAILY_SELECTED'],
            skin=skin, daily_inactive=base['I_BLUE_DAILY_INACTIVE'])
        self.I_DAILY = _skin_rule(
            default_page, blue_page, base['I_DAILY'], base['I_BLUE_DAILY_INACTIVE'], skin=skin)
        self.I_PAGE = _skin_rule(default_page, blue_page, skin=skin)
        self.I_BLUE_SUCCESS_CLAIMED = _clone_rule(base['I_BLUE_SUCCESS_CLAIMED'])
        self._courtyard_skin = skin
        navigator = getattr(self, 'navigator', None)
        if navigator is not None:
            previous = getattr(self, '_courtyard_closers', ())
            navigator.local_unknown_closers[:] = [closer for closer in navigator.local_unknown_closers
                                                  if not any(closer is old for old in previous)]
            closers = [conditional_action(self.I_PAGE, self.I_UI_BACK_YELLOW)]
            if skin != CourtyardSkin.DEFAULT:
                closers.insert(0, conditional_action(
                    self.I_BLUE_SUCCESS_CLAIMED, self.C_BLUE_REWARD_CLOSE))
            self._courtyard_closers = tuple(closers)
            navigator.add_unknown_closer(*closers)

    def harvest_courtyard_affairs(self, timeout_seconds=60, max_complete_clicks=3,
                                 courtyard_skin=CourtyardSkin.AUTO):
        self.configure_courtyard_skin(courtyard_skin)
        skin = self._courtyard_skin
        logger.info(f'Courtyard affairs skin: {skin.value}')
        detector = BlueCompletionDetector() if skin != CourtyardSkin.DEFAULT else None
        if not self.ui_click_multi_scale(self.I_NOTE, self.I_PAGE, timeout=3,
                                         scale_range=(.8, 1.2)):
            logger.warning('Courtyard affairs entry was not found')
            return False
        timer = Timer(timeout_seconds).start()
        complete_clicks = stable_done = 0
        while True:
            self.screenshot()
            if self.appear(self.I_NO_TASKS):
                logger.info('Courtyard affairs completed')
                return True
            if timer.reached():
                logger.warning('Courtyard affairs timed out; keep the task unfinished')
                return False
            # The blue claim result is a separate page, not the daily title.
            # Dismiss only a positively recognized result, then recheck daily
            # completion on fresh frames; a reward alone is not proof of done.
            if skin != CourtyardSkin.DEFAULT and self.appear_then_click(
                    self.I_BLUE_SUCCESS_CLAIMED, action=self.C_BLUE_REWARD_CLOSE, interval=1):
                stable_done = 0
                continue
            handled = (
                self.appear_then_click(self.I_HARVEST_SOUL_2, interval=1) or
                self.appear_then_click(self.I_HARVEST_SOUL_3, interval=1) or
                self.ui_reward_appear_click() or
                self.appear_then_click(self.I_UI_AWARD, interval=.2) or
                self.appear_then_click(self.I_CONFIRM, interval=1) or
                self.appear_then_click(self.I_DAILY, interval=1) or
                (skin != CourtyardSkin.BLUE and
                 self.appear_then_click(self.I_SUCCESS_CLAIMED, interval=1)) or
                self.appear_then_click(self.I_SKIP, interval=1) or
                self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=1))
            if handled:
                stable_done = 0
                continue
            stable_done = stable_done + 1 if detector is not None and detector.ready(self.device.image) else 0
            if stable_done >= 2:
                logger.info('Blue courtyard daily affairs completed; disabled button and completed rows confirmed')
                return True
            if stable_done:
                # The first done sample is already a disabled button; wait
                # for a second screenshot without attempting another claim.
                continue
            if complete_clicks >= max_complete_clicks:
                logger.warning('Courtyard affairs click limit reached without a completion screen')
                return False
            if self.appear_then_click(self.I_COMPLETE_TASKS, interval=2.3):
                complete_clicks += 1
