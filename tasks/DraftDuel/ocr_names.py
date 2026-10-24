"""Bounded, exact-dictionary name OCR for DraftDuel only.

Agreement between crops of one model is a rejection guard, not independent
evidence or calibrated accuracy. Never repair a character by fuzzy matching.
"""
from dataclasses import asdict, dataclass
import re

import cv2


def normalize_name(text):
    # Only remove the same separators already observed beside draft names.
    return re.sub(r"[\s、，,。·]+", "", str(text))


@dataclass(frozen=True)
class Reading:
    view: str
    raw: str
    normalized: str
    confidence: float


@dataclass(frozen=True)
class NameResult:
    name: str | None
    confidence: float
    reason: str
    readings: tuple

    def to_dict(self):
        return asdict(self)


def decide_name(readings, names, threshold):
    """Two exact qualified reads; conflicting known identities always abstain."""
    qualified = [r for r in readings if r.confidence >= threshold and r.normalized in names]
    identities = {r.normalized for r in qualified}
    if len(identities) > 1:
        return NameResult(None, 0., "conflicting_exact_names", tuple(readings))
    if len(qualified) >= 2:
        name = qualified[0].normalized
        # Preserve the lower model score; it is explicitly not an accuracy rate.
        return NameResult(name, min(r.confidence for r in qualified),
                          "two_exact_readings", tuple(readings))
    return NameResult(None, 0., "insufficient_exact_readings", tuple(readings))


def read_name(ocr, crop, names, threshold=.85, expanded=None):
    """Read a complete name ROI using bounded views, stopping on agreement.

    expanded is the historical roster fallback ROI (45 x 170); its white-text
    view excludes the table divider and retains the full horizontal name span.
    No global OCR settings, character whitelist, or model weights are changed.
    """
    readings = []
    views = [("raw", crop), ("raw_2x", cv2.resize(crop, None, fx=2, fy=2))]
    if expanded is not None:
        views += [("expanded", expanded),
                  ("expanded_2x", cv2.resize(expanded, None, fx=2, fy=2)),
                  ("raw_1_5x", cv2.resize(crop, None, fx=1.5, fy=1.5))]
        # Names are cream lettering with dark outlines on a beige table. Keep
        # the lettering only, without shortening an SP name into its base name.
        ink = expanded[6:40]
        gray = cv2.cvtColor(ink, cv2.COLOR_BGR2GRAY)
        white = cv2.cvtColor(cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)[1],
                            cv2.COLOR_GRAY2BGR)
        views += [("white_text", white),
                  ("white_text_2x", cv2.resize(white, None, fx=2, fy=2))]
    for view, pixels in views:
        text, confidence = ocr(pixels)
        readings.append(Reading(view, str(text), normalize_name(text), float(confidence)))
        result = decide_name(readings, names, threshold)
        if result.name or result.reason == "conflicting_exact_names":
            return result
    if hasattr(ocr, "name_readings"):
        # One detector call per unreadable name; repeated whole-image detection
        # exceeded the coordinator's 3-second frame freshness on archived frames.
        for index, (text, confidence) in enumerate(ocr.name_readings(crop)):
            readings.append(Reading("detected_text_%d" % index, str(text),
                                    normalize_name(text), float(confidence)))
        return decide_name(readings, names, threshold)
    if hasattr(ocr, "name"):
        for view, pixels in (("detected", crop),
                             ("detected_2x", cv2.resize(crop, None, fx=2, fy=2))):
            text, confidence = ocr.name(pixels)
            readings.append(Reading(view, str(text), normalize_name(text), float(confidence)))
            result = decide_name(readings, names, threshold)
            if result.name or result.reason == "conflicting_exact_names":
                return result
    return decide_name(readings, names, threshold)
