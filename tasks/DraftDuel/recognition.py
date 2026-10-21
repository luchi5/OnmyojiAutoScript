"""Read-only recognition for the actually sampled Draft Duel lobby.

The legacy closed_label asset reads 未开启: it means this series has not started,
not that the activity is closed. Admission still requires pair and fee validation.
No click, emulator, OCR, account, or scheduler operation occurs in this module.
"""
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

import cv2
import numpy as np

from tasks.DraftDuel.assets import (
    ADD_TEAMMATE_ICON, CLOSED_LABEL, FRAME_SIZE, FREE_LABEL, HELP_ICON,
    LOBBY_ANCHORS, TemplateAsset,
)


class LobbyState(str, Enum):
    UNKNOWN = "unknown"
    LOBBY_UNKNOWN = "lobby_unknown"
    LOBBY_CLOSED = "lobby_closed"
    LOBBY_NOT_STARTED = "lobby_not_started"


@dataclass(frozen=True)
class LobbyObservation:
    state: LobbyState = LobbyState.UNKNOWN
    is_lobby: bool = False
    is_closed: bool = False
    round_not_started: bool = False
    is_free: bool = False
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    scores: tuple[tuple[str, float], ...] = ()
    is_help_visible: bool = False
    is_add_teammate_visible: bool = False

    @property
    def can_start(self) -> bool:
        """No positive open-lobby evidence has been collected in this version."""
        return False


@lru_cache(maxsize=16)
def _template(path: str):
    # imdecode supports Windows paths containing non-ASCII characters.
    try:
        data = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    except (OSError, ValueError, cv2.error):
        return None
    return image


def _match(gray: np.ndarray, asset: TemplateAsset) -> tuple[bool, float]:
    template = _template(str(asset.path))
    x, y, width, height = asset.roi
    if template is None or template.shape != (height, width) or float(template.std()) < 1:
        return False, 0.0
    margin = asset.search_margin
    left, top = max(0, x - margin), max(0, y - margin)
    right, bottom = min(FRAME_SIZE[0], x + width + margin), min(FRAME_SIZE[1], y + height + margin)
    region = gray[top:bottom, left:right]
    if region.shape[0] < height or region.shape[1] < width or float(region.std()) < 1:
        return False, 0.0
    correlations = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, correlation, _, position = cv2.minMaxLoc(correlations)
    dx, dy = position
    matched = region[dy:dy + height, dx:dx + width]
    # Normalized correlation alone accepts a darkened popup background. Require
    # similar actual brightness too; light JPEG compression remains tolerated.
    error = float(np.mean(cv2.absdiff(matched, template)))
    if not np.isfinite(correlation) or not np.isfinite(error):
        return False, 0.0
    confidence = max(0.0, min(float(correlation), 1.0 - error / 255.0))
    return correlation >= asset.threshold and error <= asset.max_mean_error, confidence


def observe_lobby(image, *, color_order: str = "RGB") -> LobbyObservation:
    """Inspect one RGB uint8 screenshot (or explicit BGR) at exactly 1280x720.

    Three independent fixed-region anchors must match: the mode title, side
    rules, and bottom navigation. Overlays, other sizes, bad inputs, and absent
    assets fail closed. LOBBY_UNKNOWN requires new positive evidence, not a click.
    """
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.shape != (FRAME_SIZE[1], FRAME_SIZE[0], 3)
            or color_order not in ("RGB", "BGR")):
        return LobbyObservation(evidence=("invalid_frame",))
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY if color_order == "RGB" else cv2.COLOR_BGR2GRAY)
    scores = []
    evidence = []
    anchor_results = []
    for anchor in LOBBY_ANCHORS:
        matched, confidence = _match(gray, anchor)
        scores.append((anchor.name, confidence))
        anchor_results.append(matched)
        if matched:
            evidence.append(anchor.name)
    if not all(anchor_results):
        return LobbyObservation(evidence=tuple(evidence + ["lobby_anchors_incomplete"]),
                                scores=tuple(scores))
    confirmed = {}
    for asset in (CLOSED_LABEL, FREE_LABEL, HELP_ICON, ADD_TEAMMATE_ICON):
        matched, confidence = _match(gray, asset)
        confirmed[asset.name] = matched
        scores.append((asset.name, confidence))
        if matched:
            evidence.append(asset.name)
    not_started = confirmed[CLOSED_LABEL.name]
    state = LobbyState.LOBBY_NOT_STARTED if not_started else LobbyState.LOBBY_UNKNOWN
    required = {asset.name for asset in LOBBY_ANCHORS}
    # The single confidence value covers every positively asserted flag,
    # including the fee label and buttons, rather than only the page identity.
    required.update(name for name, matched in confirmed.items() if matched)
    confidence = min(value for name, value in scores if name in required)
    if not not_started:
        evidence.append("round_state_not_observed")
    return LobbyObservation(
        state=state, is_lobby=True, is_closed=False, round_not_started=not_started, is_free=confirmed[FREE_LABEL.name],
        confidence=confidence, evidence=tuple(evidence), scores=tuple(scores),
        is_help_visible=confirmed[HELP_ICON.name],
        is_add_teammate_visible=confirmed[ADD_TEAMMATE_ICON.name],
    )
