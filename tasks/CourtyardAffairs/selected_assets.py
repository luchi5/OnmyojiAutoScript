"""Selected courtyard templates, independent of legacy worker module caches."""

from copy import copy

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from tasks.Component.CourtyardAffairs.courtyard_affairs import CourtyardAffairsMixin
from tasks.CourtyardAffairs.themes import CourtyardSkin


def _clone_rule(rule):
    if rule is None:
        return None
    result = copy(rule)
    # RuleImage.match updates the front list in place after localization.
    # A shallow object copy alone would leak those coordinates to the asset.
    result.roi_front = list(rule.roi_front)
    return result


class BlueCourtyardAssets:
    I_BLUE_PAGE = RuleImage(
        roi_front=(735, 75, 245, 64), roi_back=(690, 48, 360, 112),
        threshold=0.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_page.png')
    I_BLUE_DAILY_SELECTED = RuleImage(
        roi_front=(1160, 170, 43, 70), roi_back=(1138, 125, 88, 210),
        threshold=0.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_daily_selected.png')
    I_BLUE_DAILY_INACTIVE = RuleImage(
        roi_front=(1160, 153, 48, 90), roi_back=(1138, 123, 92, 253),
        threshold=0.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_daily_inactive.png')
    I_BLUE_COMPLETE_TASKS = RuleImage(
        roi_front=(1124, 599, 77, 75), roi_back=(1100, 570, 142, 128),
        threshold=0.85, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_complete_tasks.png')
    I_BLUE_SUCCESS_CLAIMED = RuleImage(
        roi_front=(772, 81, 267, 62), roi_back=(730, 50, 350, 112),
        threshold=.9, method='Template matching',
        file='./tasks/CourtyardAffairs/blue_skin/blue_success_claimed.png')
    C_BLUE_REWARD_CLOSE = RuleClick(
        roi_front=(610, 674, 90, 26), roi_back=(610, 674, 90, 26),
        name='COURTYARD_BLUE_REWARD_CLOSE')


class CourtyardSkinRule(RuleImage):
    """Select matched coordinates only within a positively recognized page.

    The blue one-click button is shared by daily and special affairs. It
    must never become a click target unless the daily tab is selected.
    """

    def __init__(self, default_page, blue_page, default_action=None,
                 blue_action=None, daily_selected=None,
                 skin=CourtyardSkin.AUTO, daily_inactive=None):
        reference = default_action if default_action is not None else default_page
        super().__init__(tuple(reference.roi_front), reference.roi_back,
                         reference.method, reference.threshold, reference.file)
        self.default_page = _clone_rule(default_page)
        self.blue_page = _clone_rule(blue_page)
        self.default_action = _clone_rule(default_action)
        self.blue_action = _clone_rule(blue_action)
        self.daily_selected = _clone_rule(daily_selected)
        self.daily_inactive = _clone_rule(daily_inactive)
        self.skin = CourtyardSkin(skin)

    def match(self, image, threshold=None):
        # Check the skin before the default branch. The pages have distinct
        # titles, while the words on their buttons can be similar.
        if self.blue_page.match(image):
            if self.skin == CourtyardSkin.DEFAULT:
                return False
            if self.daily_selected is not None and not self.daily_selected.match(image):
                return False
            if self.daily_inactive is not None and self.daily_inactive.match(image):
                return False
            target = self.blue_action if self.blue_action is not None else self.blue_page
        elif self.skin != CourtyardSkin.BLUE and self.default_page.match(image):
            target = self.default_action if self.default_action is not None else self.default_page
        else:
            return False
        if not target.match(image, threshold=threshold):
            return False
        self.roi_front = list(target.roi_front)
        return True


class CourtyardCompletionRule(RuleImage):
    """Keep the common empty-tasks popup scoped to the daily view.

    Its text is shared with special affairs. A blue special page must not
    be reported as a completed daily task just because it has no actions.
    Remember a positively observed daily page through its reward popup.
    """

    def __init__(self, empty_rule, default_page, blue_page, daily_selected):
        super().__init__(tuple(empty_rule.roi_front), empty_rule.roi_back,
                         empty_rule.method, empty_rule.threshold, empty_rule.file)
        self.empty_rule = _clone_rule(empty_rule)
        self.default_page = _clone_rule(default_page)
        self.blue_page = _clone_rule(blue_page)
        self.daily_selected = _clone_rule(daily_selected)
        self.daily_confirmed = False

    def match(self, image, threshold=None):
        if self.blue_page.match(image):
            self.daily_confirmed = self.daily_selected.match(image)
        elif self.default_page.match(image):
            # Preserve the existing mainline page/tab workflow.
            self.daily_confirmed = True
        if not self.daily_confirmed or not self.empty_rule.match(image, threshold=threshold):
            return False
        self.roi_front = list(self.empty_rule.roi_front)
        return True


class SkinAwareCourtyardAffairsMixin(CourtyardAffairsMixin, BlueCourtyardAssets):
    def harvest_courtyard_affairs(self, timeout_seconds=60, max_complete_clicks=3):
        # These rules are local to the new task instance. Keep the default
        # assets and the shared GameUi/Restart classes unchanged.
        default_page = self.I_PAGE
        blue_page = self.I_BLUE_PAGE
        self.I_NO_TASKS = CourtyardCompletionRule(
            self.I_NO_TASKS, default_page, blue_page, self.I_BLUE_DAILY_SELECTED)
        self.I_COMPLETE_TASKS = CourtyardSkinRule(
            default_page, blue_page, self.I_COMPLETE_TASKS,
            self.I_BLUE_COMPLETE_TASKS, self.I_BLUE_DAILY_SELECTED)
        self.I_DAILY = CourtyardSkinRule(
            default_page, blue_page, self.I_DAILY, self.I_BLUE_DAILY_INACTIVE)
        self.I_PAGE = CourtyardSkinRule(default_page, blue_page)
        return super().harvest_courtyard_affairs(timeout_seconds, max_complete_clicks)
