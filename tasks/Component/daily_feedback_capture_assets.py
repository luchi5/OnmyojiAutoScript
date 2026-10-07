"""Explicit navigation landmarks for the daily feedback collector."""
from module.atom.image import RuleImage
from module.atom.click import RuleClick


class SelectedTodayRule(RuleImage):
    def match(self, image, threshold=None):
        return (super().match(image, threshold=threshold)
                and self.match_brightness(image, threshold=.93, roi=self.roi_front))


class DailyFeedbackCaptureAssets:
    I_TALISMAN_HEADER = RuleImage(roi_front=(104, 21, 99, 37), roi_back=(80, 8, 160, 65),
                                threshold=.9, method='Template matching',
                                file='./tasks/Component/daily_feedback_assets/talisman_header.png')
    # Right-side task tab. This small region excludes the purchase/skin controls.
    C_TALISMAN_TASK_TAB = RuleClick((1195, 340, 29, 58), (1195, 340, 29, 58),
                                    name='feedback_talisman_tasks')
    I_TALISMAN_TODAY_SELECTED = SelectedTodayRule(
        roi_front=(503, 175, 145, 36), roi_back=(494, 165, 165, 54),
        threshold=.85, method='Template matching',
        file='./tasks/Component/daily_feedback_assets/talisman_today_selected.png')
    C_TALISMAN_TODAY_TAB = RuleClick((520, 177, 100, 26), (520, 177, 100, 26),
                                    name='feedback_talisman_today')
