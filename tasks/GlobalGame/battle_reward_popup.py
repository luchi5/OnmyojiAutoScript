"""Dismiss verified soul announcements and pet treasure reward overlays."""
from module.atom.image import RuleImage
from module.atom.click import RuleClick
from module.logger import logger


def _rule(name, roi):
    x, y, w, h = roi
    return RuleImage(
        roi_front=roi, roi_back=(x - 8, y - 8, w + 16, h + 16),
        threshold=0.9, method='Template matching',
        file='./tasks/GlobalGame/ui/' + name + '.png',
    )


TITLE = _rule('soul_bonus_title', (479, 105, 518, 96))
SUBTITLE = _rule('soul_bonus_subtitle', (789, 228, 199, 27))
CLOSE = _rule('soul_bonus_close', (1114, 128, 38, 38))
PET_TITLE = _rule('pet_treasure_title', (478, 134, 326, 92))
PET_ORNAMENT = RuleImage(
    roi_front=(873, 184, 62, 147), roi_back=(873, 184, 62, 147),
    threshold=0.9, method='Template matching',
    file='./tasks/Orochi/o/o_pet_present.png',
)
PET_CONTINUE = RuleClick(
    roi_front=(600, 580, 150, 40), roi_back=(600, 580, 150, 40),
    name='PET_TREASURE_CONTINUE',
)


def close_soul_bonus_popup(task):
    if not all(task.appear(marker) for marker in (TITLE, SUBTITLE, CLOSE)):
        return False
    if task.click(CLOSE, interval=1.5):
        logger.info('Close soul bonus announcement overlay')
    # Keep blocking background actions even while the close interval is pending.
    return True


def close_pet_treasure_popup(task):
    if not task.appear(PET_TITLE) or not task.appear(PET_ORNAMENT):
        return False
    if task.click(PET_CONTINUE, interval=1.5):
        logger.info('Collect pet treasure reward overlay')
    return True
