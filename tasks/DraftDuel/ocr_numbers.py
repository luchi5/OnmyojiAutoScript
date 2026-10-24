"""Conservative numeric OCR for the observed roster table, not global OCR."""
from dataclasses import asdict, dataclass
import re

import cv2
import numpy as np


@dataclass(frozen=True)
class NumberResult:
    value: float | None
    confidence: float
    reason: str
    readings: tuple

    def to_dict(self):
        return asdict(self)


def decide_number(readings, minimum, maximum, percent=False, threshold=.8):
    grammar = r"\d{1,6}(?:\.\d{1,2})?%" if percent else r"\d{1,6}"
    qualified = []
    for reading in readings:
        text = reading["raw"].strip()
        if reading["confidence"] >= threshold and re.fullmatch(grammar, text):
            qualified.append((float(text.rstrip("%")), reading["confidence"]))
    values = {v for v, _ in qualified}
    # Include out-of-range numbers in conflict detection: 111/11 is evidence of
    # dropped digits even though 11 cannot be a valid observed speed.
    if len(values) > 1:
        return NumberResult(None, 0., "conflicting_digits", tuple(readings))
    if len(qualified) < 2:
        return NumberResult(None, 0., "insufficient_numeric_readings", tuple(readings))
    value = qualified[0][0]
    if not minimum <= value <= maximum:
        return NumberResult(None, 0., "out_of_range", tuple(readings))
    return NumberResult(value, min(c for _, c in qualified), "consistent_digits", tuple(readings))


def read_number(ocr, crop, minimum, maximum, percent=False, threshold=.8):
    # Locate *all* dark ink in the complete cell. A narrow fixed speed crop can
    # remove a leading digit; a wide blank ROI can make 111 read as 11.
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)[1]
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    ink = [s for s in stats[1:] if s[4] >= 2 and s[3] >= 2]
    if not ink:
        return NumberResult(None, 0., "no_numeric_ink", ())
    x0, y0 = min(s[0] for s in ink), min(s[1] for s in ink)
    x1, y1 = max(s[0]+s[2] for s in ink), max(s[1]+s[3] for s in ink)
    if x0 <= 0 or y0 <= 0 or x1 >= crop.shape[1] or y1 >= crop.shape[0]:
        return NumberResult(None, 0., "ink_touches_crop_boundary", ())
    background = tuple(int(v) for v in np.median(crop.reshape(-1, 3), axis=0))
    tight = cv2.copyMakeBorder(crop[y0:y1, x0:x1], 3,3,3,3,
                              cv2.BORDER_CONSTANT, value=background)
    views = [("complete_ink", tight),
             ("complete_ink_2x", cv2.resize(tight, None, fx=2, fy=2))]
    for cutoff in (125, 140):
        binary = cv2.threshold(cv2.cvtColor(tight, cv2.COLOR_BGR2GRAY), cutoff,
                               255, cv2.THRESH_BINARY)[1]
        views.append(("complete_ink_threshold_%d" % cutoff,
                      cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)))
    readings = []
    for view, pixels in views:
        raw, confidence = ocr(pixels)
        readings.append(dict(view=view, raw=str(raw), confidence=float(confidence)))
    return decide_number(readings, minimum, maximum, percent, threshold)
