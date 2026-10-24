"""Prepare this round's portraits from observed lobby + named detail screenshots."""
from dataclasses import asdict
from itertools import permutations
from pathlib import Path
import json
import re
import time
import cv2

from tasks.DraftDuel.strategy import Candidate, Stats, rank_lineups
from tasks.DraftDuel.ratings import Ratings, rating_policy
from tasks.DraftDuel.deployment import DeploymentVision, Deployment, TARGETS
from tasks.DraftDuel.ocr_names import read_name
from tasks.DraftDuel.ocr_numbers import read_number


def read_roster(detail, lobby, owner, ocr, *, allow_unknown=False, read_stats=True):
    if detail.shape[:2] != (720, 1280) or lobby.shape[:2] != (720, 1280):
        raise ValueError("Roster screenshots must be 1280x720")
    ratings = Ratings()
    candidates, portraits = [], {}
    stat_rows = (("attack", 224), ("health", 270), ("defense", 315), ("speed", 359),
                 ("crit_chance", 405), ("effect_hit", 495), ("effect_resist", 539))
    for index in range(5):
        x = 223+index*194
        reading = read_name(ocr, detail[164:198, x:x+190], ratings.profiles,
                            threshold=.8, expanded=detail[160:205, x+10:x+180])
        name = reading.name
        if name is None:
            if not allow_unknown:
                raise ValueError("Unverified roster name %s slot %d: %s %r" %
                                 (owner, index, reading.reason, reading.to_dict()))
            # This is an observed portrait identity, not an invented character.
            # It can be matched and deployed without claiming any role/strength.
            name = "未识别头像:%s:%d" % (owner, index)
        if name in portraits and allow_unknown:
            name = "未识别头像:%s:%d" % (owner, index)
        stats = {}
        for key, y in stat_rows if read_stats else ():
            limit = 100 if key == "crit_chance" else (500 if key == "speed" else 100000)
            reading_number = read_number(ocr, detail[y-15:y+17, x+30:x+160],
                minimum=50 if key == "speed" else 0, maximum=limit,
                percent=key in ("crit_chance", "effect_hit", "effect_resist"))
            if reading_number.value is not None:
                stats[key] = reading_number.value
        candidates.append(ratings.annotate(Candidate(
            owner+":"+str(index), owner, name, stats=Stats(**stats),
            confidence=reading.confidence)))
        lx = 470+86*index
        portraits[name] = lobby[39:81, lx+5:lx+56].copy()
    if len(portraits) != 5:
        raise ValueError("Duplicate identities in roster")
    return candidates, portraits


def compute_lineup(candidates):
    owners = tuple(sorted({c.owner for c in candidates}))
    plans = rank_lineups(candidates, owners, policy=rating_policy())
    if len(plans) != 100:
        raise ValueError("Expected five identified characters per account")
    best = plans[0]
    # Stable roster order, not an invented recommendation of reveal order.
    lineups = {owner: [c.name for c in best.choices if c.owner == owner] for owner in owners}
    return lineups, dict(combinations=len(plans), score=best.evaluation.total,
                         reasons=[t.reason for t in best.evaluation.terms])


class RoundDeploymentVision(DeploymentVision):
    """This-round portraits replace fixed character templates; match each card."""
    def __init__(self, ocr, portraits):
        super().__init__(ocr)
        self.top = portraits
        self.cards = {}
        self.last_side = None
        self.completed_stage = 0
        self.highest_observed_stage = 0

    @staticmethod
    def multiscale(roi, template, scales):
        best = (-1, None)
        for scale in scales:
            t = cv2.resize(template, None, fx=scale, fy=scale)
            if t.shape[0] > roi.shape[0] or t.shape[1] > roi.shape[1]:
                continue
            result = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            if score > best[0]:
                best = (score, (loc[0]+t.shape[1]//2, loc[1]+t.shape[0]//2))
        return best

    def read(self, image, captured=None):
        captured = time.monotonic() if captured is None else captured
        if image is None or not (1270 <= image.shape[1] <= 1280 and 690 <= image.shape[0] <= 720):
            return None
        button, confidence = self.ocr(image[540:627, 1100:1233])
        if "确定" not in button or confidence < .8:
            return None
        seconds = self.timer(image)
        if seconds is None:
            return None
        hits = [s for s in range(6) if self.score(
            image[24:112, 78+s*72:158+s*72], self.marker) >= .9]
        if len(hits) == 1:
            side = "right" if hits[0] < 3 else "left"
            stage = hits[0] % 3 + 1
        else:
            return None
        # A missing teammate marker is not evidence of the current stage.
        # Manual intervention/timeouts can advance without our confirmation.
        if stage < self.highest_observed_stage:
            return None
        self.highest_observed_stage = stage
        if self.last_side is not None and side != self.last_side:
            return None
        own = stage-1+(3 if side == "right" else 0)
        roi = image[24:112, 78+own*72:158+own*72]
        scores = sorted(((self.score(roi, t), n) for n, t in self.top.items()), reverse=True)
        if scores[0][0] < .8 or scores[0][0]-scores[1][0] < .15:
            return None
        # All five cards must be uniquely identified before any drag.
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        names = list(self.top)
        matrix, centers = [], {}
        for name in names:
            row = []
            template = cv2.cvtColor(self.top[name], cv2.COLOR_BGR2GRAY)
            # Portrait frames differ between the lobby and deployment cards.
            # Match the interior artwork, retaining the five-way assignment
            # and its ambiguity margin rather than lowering confidence.
            template = template[3:-3, 3:-3]
            for slot in range(5):
                x = 268+slot*114
                s, center = self.multiscale(gray[510:min(690, image.shape[0]), x:x+112],
                                          template, tuple(1.3+i*.02 for i in range(21)))
                row.append(s)
                if center:
                    centers[name, slot] = (x+center[0], 510+center[1])
            matrix.append(row)
        assignments = sorted(((sum(matrix[i][s] for i, s in enumerate(p)), p)
                               for p in permutations(range(5))), reverse=True)
        assignment = assignments[0][1]
        if assignments[0][0]-assignments[1][0] < .2 or any(
                matrix[i][s] < .78 for i, s in enumerate(assignment)):
            return None
        cards = {name: centers[name, assignment[i]] for i, name in enumerate(names)}
        self.last_side = side
        return Deployment(captured, side, stage, seconds, scores[0][1],
                          TARGETS[own], cards)
