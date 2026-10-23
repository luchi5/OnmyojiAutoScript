"""Read eligible cards and selected ticks on the visible borrowing roster."""
from functools import lru_cache
from pathlib import Path

import cv2


@lru_cache(maxsize=1)
def templates():
    folder = Path(__file__).with_name('tt')
    return (cv2.imread(str(folder / 'tt_tt_shikigami.png')),
            cv2.imread(str(folder / 'selected_tick.png')))


def borrow_cards(image):
    if image is None or image.shape != (720, 1280, 3):
        return []
    full, tick = templates()
    if full is None or tick is None:
        return []
    cards = []
    # Restrict recognition to the roster, excluding the chosen cards at the bottom.
    for y in (250, 367, 484):
        for x in (300, 425, 550, 675, 800, 925):
            badge = image[y:y + 35, x:x + 35]
            if cv2.minMaxLoc(cv2.matchTemplate(badge, full, cv2.TM_CCOEFF_NORMED))[1] < 0.8:
                continue
            corner = image[y - 14:y + 35, x + 75:x + 119]
            selected = cv2.minMaxLoc(cv2.matchTemplate(corner, tick, cv2.TM_CCOEFF_NORMED))[1] >= 0.8
            cards.append((x + 50, y + 55, selected))
    return cards


@lru_cache(maxsize=4)
def extra_template(name):
    return cv2.imread(str(Path(__file__).with_name('tt') / name))


def empty_borrow_slots(image):
    template = extra_template('empty_slot.png')
    return sum(cv2.minMaxLoc(cv2.matchTemplate(image[610:695, x:x + 85], template,
                                               cv2.TM_CCOEFF_NORMED))[1] >= 0.85
               for x in (735, 860, 985))


def beginner_travel(image):
    """Only explicit newcomer banner identifies beginners; unknown uses normal flow."""
    if image is None or image.shape != (720, 1280, 3):
        return False
    template = extra_template('newcomer_banner.png')
    if template is None:
        return False
    # Grayscale avoids mixing the RGB screenshot with BGR-loaded templates.
    template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    region = cv2.cvtColor(image[15:65, 470:765], cv2.COLOR_RGB2GRAY)
    return cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))[1] >= 0.85


def borrow_only_page(image):
    template = extra_template('borrow_only.png')
    return cv2.minMaxLoc(cv2.matchTemplate(image[60:145, 630:965], template,
                                          cv2.TM_CCOEFF_NORMED))[1] >= 0.85
