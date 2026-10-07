"""Task-local N-card selection controls, calibrated against the live panel."""

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr


class ExpandedNCardRule(RuleImage):
    def match(self, image, threshold=None):
        # The inactive label has the same shape at lower brightness, so plain
        # normalized template matching also accepts stacked mode.
        return (super().match(image, threshold=threshold)
                and self.match_brightness(image, threshold=.96, roi=self.roi_front))


class FeedSelectionAssets:
    I_FEED_N_PAGE = RuleImage(
        roi_front=(895, 452, 337, 28), roi_back=(885, 442, 357, 48),
        threshold=.85, method='Template matching',
        file='./tasks/CollectiveMissions/feed/feed_n_page.png')
    I_FEED_EXPANDED = ExpandedNCardRule(
        roi_front=(12, 613, 125, 68), roi_back=(5, 606, 139, 82),
        threshold=.9, method='Template matching',
        file='./tasks/CollectiveMissions/feed/feed_expanded.png')
    C_FEED_EXPAND = RuleClick((20, 628, 43, 35), (20, 628, 43, 35), name='feed_expand')
    C_FEED_CANCEL = RuleClick((452, 350, 96, 37), (452, 350, 96, 37), name='feed_cancel')
    C_FEED_BACK = RuleClick((32, 43, 30, 31), (32, 43, 30, 31), name='feed_back')
    O_FEED_SUBMIT_COUNT = RuleOcr(
        roi=(514, 20, 262, 48), area=(514, 20, 262, 48),
        mode='Single', method='Default', keyword='', name='feed_submit_count')
