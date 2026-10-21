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
        return now-self.captured < 2 and self.seconds-(now-self.captured) > 5


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
        readings = []
        for left, right in ((616, 668), (629, 655)):
            for threshold in (110, 130):
                gray = cv2.cvtColor(image[65:94, left:right], cv2.COLOR_BGR2GRAY)
                binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)[1]
                binary = cv2.copyMakeBorder(binary, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
                text, score = self.ocr(cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR))
                if text.strip().isdigit() and score >= .8:
                    value = int(text.strip())
                    if 0 <= value <= 30:
                        readings.append(value)
        # Cropping a two-digit timer can yield its last digit: conservative min.
        return min(readings) if readings else None

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


class DeploymentController:
    def __init__(self, io, vision, lineup, clock=time.monotonic):
        if len(lineup) != 3 or len(set(lineup)) != 3:
            raise ValueError("Three distinct characters required")
        self.io, self.vision, self.lineup, self.clock = io, vision, lineup, clock
        self.confirmed = set()
        self.dragged = set()
        self.last_observation = None

    def observe(self):
        image, captured = self.io.capture()
        self.last_observation = self.vision.read(image, captured)
        return self.last_observation

    def step(self, execute=False):
        first = self.observe()
        if first is None:
            return "unknown_or_waiting"
        if not first.usable(self.clock()):
            return "handoff_last_five_seconds"
        key = (first.side, first.stage)
        if key in self.confirmed:
            return "confirmation_already_sent"
        wanted = self.lineup[first.stage-1]
        if not execute:
            return "would_confirm" if first.current == wanted else "would_drag:"+wanted
        fresh = self.observe()
        if fresh is None or (fresh.side, fresh.stage, fresh.current) != (
                first.side, first.stage, first.current) or not fresh.usable(self.clock()):
            return "changed_or_expired"
        if fresh.current != wanted:
            if key in self.dragged or wanted not in fresh.cards:
                return "needs_manual_selection"
            self.dragged.add(key)  # A timeout never causes an unverified repeat drag.
            self.io.drag(fresh.cards[wanted], fresh.target)
            fresh = self.observe()
            if (fresh is None or (fresh.side, fresh.stage) != key or
                    fresh.current != wanted or not fresh.usable(self.clock())):
                return "drag_not_verified_no_confirmation"
        self.confirmed.add(key)
        self.io.confirm()
        return "confirmation_sent"
