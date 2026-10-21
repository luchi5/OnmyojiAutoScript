"""Read remaining weekly double rewards only from the OWT challenge page."""
import re
import unicodedata
from module.atom.ocr import RuleOcr

COUNTER = RuleOcr(roi=(210, 605, 70, 30), area=(210, 605, 70, 30),
                  mode='Single', method='Default', keyword='', name='owt_double_remaining')

def parse_remaining(text):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))
    match = re.search(r'(?<![\d-])(\d{1,2})/60(?!\d)', text)
    if not match or int(match[1]) > 60:
        return None
    return int(match[1])

def read_remaining(image):
    return parse_remaining(COUNTER.ocr(image))
