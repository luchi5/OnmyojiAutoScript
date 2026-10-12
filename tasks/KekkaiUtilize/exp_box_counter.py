"""Experience-box daily quota includes the level bonus after the plus sign."""
import re
import unicodedata

from module.atom.ocr import RuleOcr


# Include the current amount and separator; the old ROI began inside the total.
O_BOX_EXP_DAILY = RuleOcr(
    roi=(492, 538, 346, 40), area=(492, 538, 346, 40),
    mode='Single', method='Default', keyword='', name='box_exp_daily_quota',
)


def parse_exp_box_counter(text):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))
    match = re.search(r'(?<!\d)(\d+)/(\d+)(?:\+(\d+))?$', text)
    if not match:
        return None
    current, base = int(match[1]), int(match[2])
    bonus = int(match[3] or 0)
    total = base + bonus
    if base <= 0 or current > total:
        return None
    return current, total - current, total


def read_exp_box_counter(image):
    return parse_exp_box_counter(O_BOX_EXP_DAILY.ocr(image))
