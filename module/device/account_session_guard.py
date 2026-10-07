"""Detect the specific other-device-login message without invoking OCR."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


_TEMPLATE = Path(__file__).resolve().parent / "assets" / "account_logged_in_elsewhere.png"
# Only search the popup's message line, never its generic confirmation button.
_SEARCH = (442, 285, 400, 90)
_THRESHOLD = 0.94


@lru_cache(maxsize=1)
def _message_template():
    encoded = np.fromfile(str(_TEMPLATE), dtype=np.uint8)
    template = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    if template is None or template.size == 0:
        raise RuntimeError("Other-device login message template is unavailable")
    return template


def other_device_login_visible(image) -> bool:
    """Input uses the same RGB 1280x720 screenshots as the device interface."""
    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
        return False
    if image.shape[:2] != (720, 1280):
        return False
    x, y, width, height = _SEARCH
    crop = image[y:y + height, x:x + width]
    if crop.ndim == 3:
        if crop.shape[2] != 3:
            return False
        crop = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    template = _message_template()
    # The exact words, unlike a common button/box, distinguish an account
    # takeover from ordinary network failures and unrelated game dialogs.
    score = cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1]
    return score >= _THRESHOLD
