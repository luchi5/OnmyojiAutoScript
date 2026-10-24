"""Observed deployment screen: plan -> fresh validation -> drag -> verify -> confirm.

No matchmaking or game lifecycle operations. Templates are this test roster,
not a universal character recognizer. Unknown pages/rosters abstain.
"""
from dataclasses import dataclass
from pathlib import Path
import time
import subprocess
import cv2
import numpy as np
from tasks.DraftDuel.local_draft import AdbIO

RES = Path(__file__).with_name("res") / "deployment"
NAMES = ("ichikami", "onigiri", "momiji", "masakado", "tamamo",
         "kaguya", "inaba", "jiaotu", "magatsuhi", "ameonna")
# Positions on the board, not the six small portrait slots at the top.
TARGETS = ((180, 330), (330, 340), (520, 340),
           (750, 330), (940, 340), (1100, 340))


class DeploymentAdbIO(AdbIO):
    def capture(self):
        image, captured = super().capture()
        self.last_image = image
        return image, captured

    def drag(self, source, target):
        subprocess.run(self.command + ["shell", "input", "swipe",
                       *map(str, source), *map(str, target), "400"],
                       capture_output=True, check=True, timeout=2,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def confirm(self):
        subprocess.run(self.command + ["shell", "input", "tap", "1170", "590"],
                       capture_output=True, check=True, timeout=1,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@dataclass(frozen=True)
class Deployment:
    captured: float
    side: str
    stage: int
    seconds: int
    current: str
    target: tuple
    cards: dict

    def usable(self, now):
        return 0 <= now-self.captured < 2 and self.seconds-(now-self.captured) > 5


@dataclass(frozen=True)
class PendingConfirmation:
    side: str
    stage: int
    wanted: str
    attempted_at: float
    source_captured: float


@dataclass(frozen=True)
class ConfirmationEvidence:
    captured: float
    side: str
    stage: int
    wanted: str
    next_stage: int
    kind: str = "next_stage_with_locked_portrait"


class DeploymentVision:
    def __init__(self, ocr):
        self.ocr = ocr
        self.marker = cv2.imread(str(RES / "teammate.png"))
        self.top = {n: cv2.imread(str(RES / (n+"_top.png"))) for n in NAMES}
        self.cards = {n: cv2.imread(str(RES / (n+"_card.png")), 0)
                      for n in NAMES if (RES / (n+"_card.png")).exists()}

    @staticmethod
    def score(image, template):
        return float(cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED).max())

    def timer(self, image):
        # Keep the complete number box at every threshold. A narrow central
        # crop can discard the tens digit and turn 20 seconds into 0 seconds.
        diagnostic = dict(value=None, reason="unreadable", roi=(612, 60, 672, 101), readings=[])
        self.last_timer_diagnostic = diagnostic
        if image is None or image.ndim != 3 or image.shape[0] < 101 or image.shape[1] < 672:
            diagnostic["reason"] = "missing_number_region"
            return None
        gray = cv2.cvtColor(image[60:101, 612:672], cv2.COLOR_BGR2GRAY)
        values, eligible = [], []
        incomplete_digits = False
        out_of_range = False
        for threshold in (100, 110, 125, 140):
            mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)[1]
            _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            clean = np.zeros_like(mask)
            components = []
            for label, (x, y, width, height, area) in enumerate(stats[1:], 1):
                # The bright circle edges touch the outer crop; complete timer
                # glyphs are inside it. Preserve every complete glyph together.
                if (x > 0 and y > 0 and x+width < mask.shape[1]
                        and y+height < mask.shape[0] and 2 <= width <= 22
                        and 12 <= height <= 29 and area >= 8):
                    clean[labels == label] = 255
                    components.append(tuple(map(int, (x, y, width, height))))
            reading = dict(threshold=threshold, components=components, raw=None,
                           confidence=None, value=None, reason="ambiguous_digit_geometry")
            diagnostic["readings"].append(reading)
            if len(components) not in (1, 2):
                continue
            ys, xs = np.nonzero(clean)
            left, right, top, bottom = int(xs.min()), int(xs.max()+1), int(ys.min()), int(ys.max()+1)
            if (right-left > 38 or abs((left+right)/2-30) > 12
                    or max(c[1] for c in components)-min(c[1] for c in components) > 4):
                continue
            number = 255-clean[top:bottom, left:right]
            number = cv2.copyMakeBorder(number, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
            number = cv2.resize(number, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
            raw, score = self.ocr(cv2.cvtColor(number, cv2.COLOR_GRAY2BGR))
            text = raw.strip()
            reading.update(raw=raw, confidence=float(score), reason="low_confidence_or_non_number")
            if score < .8 or not text.isascii() or not text.isdigit() or not 1 <= len(text) <= 2:
                continue
            value = int(text)
            values.append(value)
            reading["value"] = value
            if len(text) != len(components):
                incomplete_digits = True
                reading["reason"] = "digit_count_disagreement"
            elif not 0 <= value <= 40:
                out_of_range = True
                reading["reason"] = "out_of_range"
            else:
                eligible.append(value)
                reading["reason"] = "eligible"
        if len(set(values)) > 1:
            diagnostic["reason"] = "conflicting_numeric_readings"
        elif incomplete_digits:
            diagnostic["reason"] = "digit_count_disagreement"
        elif out_of_range:
            diagnostic["reason"] = "out_of_range"
        elif len(eligible) < 2:
            diagnostic["reason"] = "insufficient_agreement"
        else:
            diagnostic.update(value=eligible[0], reason="consistent_complete_number")
        return diagnostic["value"]

    def read(self, image, captured=None):
        captured = time.monotonic() if captured is None else captured
        if image is None or not (1270 <= image.shape[1] <= 1280 and 690 <= image.shape[0] <= 720):
            return None
        hits = []
        for slot in range(6):
            roi = image[24:112, 78+slot*72:158+slot*72]
            if self.score(roi, self.marker) >= .9:
                hits.append(slot)
        if len(hits) != 1:
            return None
        teammate = hits[0]
        side = "right" if teammate < 3 else "left"
        stage = teammate % 3
        own_slot = stage + (3 if side == "right" else 0)
        roi = image[24:112, 78+own_slot*72:158+own_slot*72]
        scores = sorted(((self.score(roi, t), n) for n, t in self.top.items()), reverse=True)
        if scores[0][0] < .8 or scores[0][0]-scores[1][0] < .15:
            return None
        seconds = self.timer(image)
        button, confidence = self.ocr(image[540:627, 1100:1233])
        if seconds is None or "确定" not in button or confidence < .8:
            return None
        card_roi = cv2.cvtColor(image[500:min(690, image.shape[0]), 260:835], cv2.COLOR_BGR2GRAY)
        cards = {}
        for name, template in self.cards.items():
            result = cv2.matchTemplate(card_roi, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            if score >= .9:
                cards[name] = (260+loc[0]+template.shape[1]//2,
                               500+loc[1]+template.shape[0]//2)
        return Deployment(captured, side, stage+1, seconds, scores[0][1],
                          TARGETS[own_slot], cards)

    def read_confirmation(self, image, pending, captured=None, observation=None):
        """Verify an earlier slot from a positively recognized next stage.

        The final stage deliberately has no acknowledgement recognizer yet.
        A missing button/marker, a sent input, or a skipped stage is not evidence.
        Subclasses with this round's portraits inherit the same identity check.
        """
        captured = time.monotonic() if captured is None else captured
        if (captured <= pending.attempted_at or captured <= pending.source_captured
                or pending.stage not in (1, 2)):
            return None
        frame = observation if observation is not None else self.read(image, captured)
        if (frame is None or frame.captured != captured or frame.side != pending.side
                or frame.stage != pending.stage+1):
            return None
        slot = pending.stage-1+(3 if pending.side == "right" else 0)
        roi = image[24:112, 78+slot*72:158+slot*72]
        scores = sorted(((self.score(roi, template), name)
                         for name, template in self.top.items()), reverse=True)
        if (len(scores) < 2 or scores[0][1] != pending.wanted
                or scores[0][0] < .8 or scores[0][0]-scores[1][0] < .15):
            return None
        return ConfirmationEvidence(captured, pending.side, pending.stage,
                                    pending.wanted, frame.stage)


class DeploymentController:
    def __init__(self, io, vision, lineup, clock=time.monotonic):
        if len(lineup) != 3 or len(set(lineup)) != 3:
            raise ValueError("Three distinct characters required")
        self.io, self.vision, self.lineup, self.clock = io, vision, lineup, clock
        self.attempted = set()
        self.pending = {}
        self.verified = set()
        # Compatibility: confirmed now means observed acknowledgement, never send.
        self.confirmed = self.verified
        self.dragged = set()
        self.drag_attempts = {}
        self.last_drag_at = {}
        self.last_observation = None
        self.last_image = None
        self.last_captured = None
        self.last_confirmation = None

    def observe(self):
        image, captured = self.io.capture()
        self.last_image, self.last_captured = image, captured
        self.last_observation = self.vision.read(image, captured)
        return self.last_observation

    def _verify_pending(self, observation):
        key, pending = next(iter(self.pending.items()))
        captured = self.last_captured
        if (self.last_image is None or captured is None
                or not 0 <= self.clock()-captured < 2
                or captured <= pending.attempted_at
                or captured <= pending.source_captured):
            return "confirmation_pending"
        evidence = self.vision.read_confirmation(
            self.last_image, pending, captured, observation=observation)
        # Keep this boundary defensive even for alternate recognizers.
        if (not isinstance(evidence, ConfirmationEvidence)
                or evidence.captured != captured
                or (evidence.side, evidence.stage, evidence.wanted) !=
                   (pending.side, pending.stage, pending.wanted)
                or pending.stage not in (1, 2)
                or evidence.next_stage != pending.stage+1
                or evidence.kind != "next_stage_with_locked_portrait"):
            return "confirmation_pending"
        self.last_confirmation = evidence
        self.verified.add(key)
        del self.pending[key]
        return "confirmation_verified"

    def step(self, execute=False):
        first = self.observe()
        if self.pending:
            return self._verify_pending(first)
        if first is None:
            return "unknown_or_waiting"
        if not first.usable(self.clock()):
            return "handoff_last_five_seconds"
        key = (first.side, first.stage)
        if key in self.verified:
            return "confirmation_already_verified"
        if key in self.attempted:
            return "confirmation_already_attempted"
        wanted = self.lineup[first.stage-1]
        if not execute:
            return "would_confirm" if first.current == wanted else "would_drag:"+wanted
        fresh = self.observe()
        if fresh is None or (fresh.side, fresh.stage, fresh.current) != (
                first.side, first.stage, first.current) or not fresh.usable(self.clock()):
            return "changed_or_expired"
        if fresh.current != wanted:
            if wanted not in fresh.cards:
                return "rereading_target_card"
            attempts = self.drag_attempts.get(key, 0)
            if attempts >= 2:
                return "waiting_for_stage_recovery"
            if attempts and (self.clock()-self.last_drag_at[key] < 1
                             or fresh.captured <= self.last_drag_at[key]):
                return "waiting_for_fresh_drag_retry"
            self.dragged.add(key)
            self.drag_attempts[key] = attempts+1
            self.last_drag_at[key] = self.clock()
            self.io.drag(fresh.cards[wanted], fresh.target)
            fresh = self.observe()
            if (fresh is None or (fresh.side, fresh.stage) != key or
                    fresh.current != wanted or not fresh.usable(self.clock())):
                return "drag_not_verified_no_confirmation"
        # A cached pre-drag portrait is not evidence that the drag succeeded.
        # Also enforce this on later steps that already show the wanted card.
        if key in self.last_drag_at and fresh.captured <= self.last_drag_at[key]:
            return "drag_not_verified_no_confirmation"
        # Preserve the uncertain outcome even if the input transport raises.
        # Only later screenshots may move this request into verified.
        self.attempted.add(key)
        self.pending[key] = PendingConfirmation(
            fresh.side, fresh.stage, wanted, self.clock(), fresh.captured)
        self.io.confirm()
        return "confirmation_sent"
