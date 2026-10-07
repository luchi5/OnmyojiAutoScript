"""Completion handling for the independently scheduled courtyard task."""

import cv2
import numpy as np
from copy import copy

from module.atom.image import RuleImage
from module.base.timer import Timer
from module.logger import logger
from tasks.CourtyardAffairs.skin_assets import (
    BlueCourtyardAssets, CourtyardSkinRule, SkinAwareCourtyardAffairsMixin)


def _clone_rule(rule):
    if rule is None:
        return None
    result = copy(rule)
    result.roi_front = list(rule.roi_front)
    return result


def _skin_rule(*rules):
    # The old module can remain cached in a running account. Pass isolated
    # inputs even if its older constructor only made shallow copies.
    return CourtyardSkinRule(*(_clone_rule(rule) for rule in rules))


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
                 daily_inactive, other_pages=()):
        super().__init__(tuple(empty_rule.roi_front), empty_rule.roi_back,
                         empty_rule.method, empty_rule.threshold, empty_rule.file)
        self.empty_rule = _clone_rule(empty_rule)
        self.default_page = _clone_rule(default_page)
        self.blue_page = _clone_rule(blue_page)
        self.daily_selected = _clone_rule(daily_selected)
        self.daily_inactive = _clone_rule(daily_inactive)
        self.other_pages = tuple(_clone_rule(rule) for rule in other_pages)
        self.daily_confirmed = False

    def match(self, image, threshold=None):
        if self.blue_page.match(image):
            # Explicit inactive evidence takes precedence if both templates
            # happen to match; an ambiguous page must not count as completed.
            if self.daily_inactive.match(image):
                self.daily_confirmed = False
            elif self.daily_selected.match(image):
                self.daily_confirmed = True
        elif self.default_page.match(image):
            self.daily_confirmed = True
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

    def __init__(self, page=None, daily=None, disabled=None, stamps=None):
        self.page = _clone_rule(page if page is not None else BlueCourtyardAssets.I_BLUE_PAGE)
        self.daily = _clone_rule(daily if daily is not None else BlueCourtyardAssets.I_BLUE_DAILY_SELECTED)
        self.disabled = _clone_rule(disabled if disabled is not None else BlueCompletionAssets.I_BLUE_DISABLED_COMPLETE)
        self.stamps = tuple(_clone_rule(rule) for rule in (
            stamps if stamps is not None else (
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_FIRST,
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_SECOND,
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_THIRD,
                BlueCompletionAssets.I_BLUE_COMPLETED_STAMP_FOURTH)))

    def ready(self, image):
        if not self.page.match(image) or not self.daily.match(image):
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
    def harvest_courtyard_affairs(self, timeout_seconds=60, max_complete_clicks=3):
        # A newly imported task-local module replaces the cached old harvest
        # method when ScriptTask is loaded for its next execution.
        default_page = self.I_PAGE
        blue_page = self.I_BLUE_PAGE
        other_pages = tuple(getattr(self, name) for name in (
            'I_CHECK_MAIN', 'I_CHECK_EXPLORATION', 'I_CHECK_RECORDS', 'I_CHECK_GUILD')
            if hasattr(self, name))
        self.I_NO_TASKS = CourtyardEmptyTasksRule(
            self.I_NO_TASKS, default_page, blue_page, self.I_BLUE_DAILY_SELECTED,
            self.I_BLUE_DAILY_INACTIVE, other_pages)
        self.I_COMPLETE_TASKS = _skin_rule(
            default_page, blue_page, self.I_COMPLETE_TASKS,
            self.I_BLUE_COMPLETE_TASKS, self.I_BLUE_DAILY_SELECTED)
        self.I_DAILY = _skin_rule(
            default_page, blue_page, self.I_DAILY, self.I_BLUE_DAILY_INACTIVE)
        self.I_PAGE = _skin_rule(default_page, blue_page)
        detector = BlueCompletionDetector()
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
            handled = (
                self.appear_then_click(self.I_HARVEST_SOUL_2, interval=1) or
                self.appear_then_click(self.I_HARVEST_SOUL_3, interval=1) or
                self.ui_reward_appear_click() or
                self.appear_then_click(self.I_UI_AWARD, interval=.2) or
                self.appear_then_click(self.I_CONFIRM, interval=1) or
                self.appear_then_click(self.I_DAILY, interval=1) or
                self.appear_then_click(self.I_SUCCESS_CLAIMED, interval=1) or
                self.appear_then_click(self.I_SKIP, interval=1) or
                self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=1))
            if handled:
                stable_done = 0
                continue
            stable_done = stable_done + 1 if detector.ready(self.device.image) else 0
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
