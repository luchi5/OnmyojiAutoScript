"""Optional realm entry layouts, used only after legacy entry matching fails."""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class EntryProfile:
    key: str
    scene_file: str
    scene_roi: tuple
    anchor_file: str
    anchor_roi: tuple
    card_offset: tuple
    scene_threshold: float = 0.90
    anchor_threshold: float = 0.85


@dataclass(frozen=True)
class EntryMatch:
    skin: str
    score: float
    scene_score: float
    anchor_score: float
    rect: tuple


PROFILES = (
    EntryProfile(
        key='costume_realm_shen_an',
        scene_file='shen_an/entry_scene.png', scene_roi=(520, 170, 120, 100),
        anchor_file='shen_an/i_shi_grown.png', anchor_roi=(610, 470, 65, 90),
        card_offset=(133, 0),
    ),
)


def _score(image, filename, roi):
    path = Path(__file__).parent / filename
    template = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if template is None:
        return None
    template = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)
    x, y, width, height = roi
    source = image[y:y + height, x:x + width]
    h, w = template.shape[:2]
    if source.shape[0] < h or source.shape[1] < w:
        return None
    _, score, _, point = cv2.minMaxLoc(
        cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED))
    return score, (x + point[0], y + point[1], w, h)


def detect_realm_entry(image, candidates, *, profiles=None):
    """Select the highest qualified scene+anchor match; never choose a weak best.

    The scene fingerprint is independent of the common growth-label artwork.
    Narrow layout regions prevent a label on another part of a page from
    becoming an anchor. Nothing here changes the configured realm/banner skin.
    """
    if image is None or image.shape != (720, 1280, 3):
        return None
    allowed = set(candidates)
    best = None
    for profile in PROFILES if profiles is None else profiles:
        if profile.key not in allowed:
            continue
        scene = _score(image, profile.scene_file, profile.scene_roi)
        if scene is None or scene[0] < profile.scene_threshold:
            continue
        anchor = _score(image, profile.anchor_file, profile.anchor_roi)
        if anchor is None or anchor[0] < profile.anchor_threshold:
            continue
        x, y, w, h = anchor[1]
        dx, dy = profile.card_offset
        rect = (x + dx + 2, y + dy + 5, w - 4, h - 10)
        if not (rect[0] >= 0 and rect[1] >= 0
                and rect[0] + rect[2] <= 1280 and rect[1] + rect[3] <= 720):
            continue
        score = (scene[0] + anchor[0]) / 2
        match = EntryMatch(profile.key, score, scene[0], anchor[0], rect)
        if best is None or match.score > best.score:
            best = match
    return best
