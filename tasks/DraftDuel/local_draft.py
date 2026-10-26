"""Local timed draft adapter. Never starts a round, game, emulator or battle.

Only the observed 1280x720 three-choice draft screen is actionable. Unknown
screens/identities are abstentions. Each account owns its own OCR connection.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import time
import threading

import cv2
import numpy as np

from tasks.DraftDuel.ratings import Ratings, rating_policy
from tasks.DraftDuel.strategy import Candidate, rank_draft_choices
from tasks.DraftDuel.ocr_names import read_name

BUTTONS = ((398, 660), (773, 660), (1149, 660))
NAMES = ((215, 672, 125, 37), (585, 672, 140, 37), (945, 672, 165, 37))


class TimedOCR:
    """Dedicated local model per worker, no shared OCR server queue."""
    def __init__(self):
        from module.ocr.backend import configured_text_system
        from module.server.setting import State
        # This dedicated worker decodes ADB PNGs with OpenCV (BGR).
        self.model = configured_text_system(State.deploy_config, input_color="BGR")

    def __call__(self, image):
        return self.model.ocr_single_line(image)

    def name(self, image):
        padded = cv2.copyMakeBorder(image, 15, 15, 15, 15, cv2.BORDER_CONSTANT)
        items = self.model.detect_and_ocr(padded)
        if len(items) != 1:
            return "", 0
        return items[0].ocr_text, float(items[0].score)

    def name_readings(self, image):
        """Detect once, then validate on the complete detected text at 2x."""
        padded = cv2.copyMakeBorder(image, 15, 15, 15, 15, cv2.BORDER_CONSTANT)
        items = self.model.detect_and_ocr(padded)
        if len(items) != 1:
            return (("", 0.),)
        item = items[0]
        enlarged = cv2.resize(item.text_img, None, fx=2, fy=2)
        return ((item.ocr_text, float(item.score)), self.model.ocr_single_line(enlarged))

    def close(self):
        close = getattr(self.model, "close", None)
        if close is not None:
            close()


@dataclass(frozen=True)
class Frame:
    owner: str
    captured: float
    round_no: int
    seconds: int
    candidates: tuple
    signature: str
    roster: np.ndarray
    image: np.ndarray | None = None
    complete_candidates: bool = True
    buttons_verified: bool = False

    def remaining(self, now):
        return self.seconds - (now - self.captured)


class DraftVision:
    def __init__(self, ocr, ratings=None):
        self.ocr = ocr
        self.ratings = ratings or Ratings()
        self.last_ocr = {}
        self.button = cv2.imread(str(Path(__file__).with_name("res") / "draft_select.png"))
        self.lobby_title = cv2.imread(str(Path(__file__).with_name('res') / 'team/title.png'))
        self.roster_heading = cv2.imread(str(Path(__file__).with_name('res') / 'my_roster_heading.png'))

    def finished_lobby(self, image):
        """Positive own-roster header and mode title after the fifth selection."""
        if image is None or image.shape[:2] != (720, 1280):
            return False
        for template, region in ((self.lobby_title, image[8:53, 140:280]),
                                 (self.roster_heading, image[10:106, 405:460])):
            if template is None or cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED).max() < .9:
                return False
        return True

    def read(self, image, owner, captured=None, *, allow_partial=False):
        captured = time.monotonic() if captured is None else captured
        self.last_ocr = {"owner": owner, "names": [], "reason": "unrecognized_page"}
        if image is None or image.shape[:2] != (720, 1280):
            return None
        header, confidence = self.ocr(image[123:160, 480:795])
        text = re.sub(r"\s+", "", header)
        match = re.fullmatch(r"请选择第([1-5])名式神[^\d]*?(\d{1,2})[sS秒]?", text)
        if confidence < .85 or not match:
            return None
        round_no, seconds = map(int, match.groups())
        if not 1 <= seconds <= 30:
            return None
        candidates = []
        names = []
        for slot, (x, y, w, h) in enumerate(NAMES):
            # Require each actual gold selection button, not merely a header.
            bx, by = BUTTONS[slot]
            roi = image[by-52:by+50, bx-58:bx+62]
            score = cv2.matchTemplate(roi, self.button, cv2.TM_CCOEFF_NORMED).max()
            if score < .8:
                return None
            reading = read_name(self.ocr, image[y:y+h, x:x+w], self.ratings.profiles)
            self.last_ocr["names"].append(reading.to_dict())
            name, conf = reading.name, reading.confidence
            names.append(name or "")
            if name is not None:
                candidates.append(self.ratings.annotate(Candidate(
                    "%s:r%d:s%d" % (owner, round_no, slot), owner, name,
                    confidence=float(conf))))
        known = [name for name in names if name]
        # Conflicting duplicate identities are not usable fallback candidates.
        if len(known) != len(set(known)):
            self.last_ocr["reason"] = "incomplete_or_duplicate_candidates"
            return None
        complete = len(candidates) == 3
        if not complete and not allow_partial:
            self.last_ocr["reason"] = "incomplete_or_duplicate_candidates"
            return None
        self.last_ocr["reason"] = ("all_candidates_identified" if complete else
                                   "partial_candidates_auto_reread")
        signature = "%d:%s" % (round_no, "|".join(names))
        roster = cv2.resize(image[20:90, 438:858], (210, 35))
        return Frame(owner, captured, round_no, seconds, tuple(candidates), signature,
                     roster, image, complete, True)


class AdbIO:
    """Only screenshot and tap: no recovery/restart methods by design."""
    def __init__(self, executable, serial):
        self.command = [str(executable), "-s", serial]

    def capture(self):
        at = time.monotonic()
        result = subprocess.run(self.command + ["exec-out", "screencap", "-p"],
                                capture_output=True, check=True, timeout=3,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR), at

    def tap(self, slot):
        x, y = BUTTONS[slot]
        subprocess.run(self.command + ["shell", "input", "tap", str(x), str(y)],
                       capture_output=True, check=True, timeout=1,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def same_selection(before, after, now):
    return (after is not None and before.owner == after.owner
            and before.signature == after.signature
            and before.remaining(now) > 5 and after.remaining(now) > 5
            and now - after.captured < 3
            and float(np.abs(before.roster.astype(float)-after.roster).mean()) < 4)


class LocalDraft:
    """Coordinator with fresh-screen pre-click validation and no repeated taps.

    One instance covers one five-pick draft. A new draft requires a new instance.
    Confirmation requires both the drafted slot changing and a phase advance.
    Unconfirmed actions never contribute to the next pick's scoring context.
    """
    def __init__(self, devices, vision_factory=DraftVision, event=None):
        if len(devices) != 2:
            raise ValueError("Exactly two accounts required")
        self.devices = devices
        self.vision_factory = vision_factory
        self.attempted = set()
        self.confirmed = ()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.local = threading.local()
        self.pending = {}
        self.awaiting = {}
        self.recovery_reads = {}
        self.event = event or (lambda record, image=None: None)

    def observe(self, owner):
        if not hasattr(self.local, "vision"):
            self.local.vision = self.vision_factory(TimedOCR())
        image, at = self.devices[owner].capture()
        pending = self.awaiting.get(owner)
        if pending is not None:
            before, candidate = pending
            header, confidence = self.local.vision.ocr(image[123:160, 480:795])
            text = re.sub(r"\s+", "", header)
            advanced = ("等待队友选择" in text or
                        "请选择第%d名式神" % (before.round_no+1) in text)
            final_lobby = before.round_no == 5 and self.local.vision.finished_lobby(image)
            after_roster = cv2.resize(image[20:90, 438:858], (210, 35))
            x = (before.round_no-1)*42
            delta = float(np.abs(after_roster[:, x:x+42].astype(float)-
                                 before.roster[:, x:x+42]).mean())
            if ((confidence >= .85 and advanced) or final_lobby) and delta > 8:
                self.event({'phase': 'pick_confirmed', 'owner': owner, 'pick': before.round_no,
                            'name': candidate.name, 'portrait_delta': delta}, image)
                return ("confirmed", candidate)
        return self.local.vision.read(image, owner, at, allow_partial=True)

    def observe_pair(self):
        for owner in self.devices:
            if owner not in self.pending:
                self.pending[owner] = self.pool.submit(self.observe, owner)
        deadline = time.monotonic() + 5
        frames = []
        for owner, future in list(self.pending.items()):
            try:
                frame = future.result(timeout=max(0, deadline-time.monotonic()))
            except TimeoutError:
                # Keep the pending read instead of accumulating stale OCR jobs.
                continue
            except Exception:
                del self.pending[owner]
                raise
            else:
                del self.pending[owner]
                if isinstance(frame, tuple) and frame[0] == "confirmed":
                    candidate = frame[1]
                    self.confirmed += (candidate,)
                    self.awaiting.pop(owner, None)
                    continue
                if frame is not None and time.monotonic()-frame.captured < 3:
                    frames.append(frame)
        return frames

    def commit(self, frame, fresh, candidate):
        key = (frame.owner, frame.round_no)
        if key in self.attempted:
            return "already_attempted"
        if not same_selection(frame, fresh, time.monotonic()):
            return "changed_or_expired"
        identity = (candidate.candidate_id, candidate.name)
        if any(identity not in {(c.candidate_id, c.name) for c in f.candidates}
               for f in (frame, fresh)):
            return "candidate_changed"
        if not frame.complete_candidates:
            if not (frame.buttons_verified and fresh.buttons_verified
                    and fresh.captured > frame.captured):
                return "fallback_needs_fresh_confirmation"
            self.event({'phase': 'pick_recovery_fallback', 'owner': frame.owner,
                        'pick': frame.round_no, 'name': candidate.name,
                        'known_candidates': [c.name for c in frame.candidates],
                        'reason': 'partial_candidates_stable_on_two_captures'}, fresh.image)
        # Mark before sending: an ADB timeout must never cause duplicate clicks.
        self.attempted.add(key)
        slot = int(candidate.candidate_id.rsplit(":s", 1)[1])
        self.event({'phase': 'pick_before_tap', 'owner': frame.owner,
                    'pick': frame.round_no, 'name': candidate.name, 'point': BUTTONS[slot]}, fresh.image)
        self.devices[frame.owner].tap(slot)
        self.awaiting[frame.owner] = (fresh, candidate)
        return "tap_sent_unconfirmed"

    def step(self, execute=False):
        owners = tuple(self.devices)
        frames = self.observe_pair()
        active, recovering = [], []
        for frame in frames:
            key = (frame.owner, frame.round_no)
            if key in self.attempted or frame.owner in self.awaiting:
                continue
            now = time.monotonic()
            if now-frame.captured >= 3 or frame.remaining(now) <= 6:
                continue
            if not frame.complete_candidates:
                last_at, count = self.recovery_reads.get(key, (None, 0))
                if last_at is None or frame.captured > last_at:
                    count += 1
                    self.recovery_reads[key] = (frame.captured, count)
                recovery = {'phase': 'pick_auto_reread', 'owner': frame.owner,
                            'pick': frame.round_no, 'reads': count,
                            'known_candidates': [c.name for c in frame.candidates]}
                recovering.append(recovery)
                self.event(recovery, frame.image)
                if (not frame.buttons_verified or not frame.candidates
                        or (count < 3 and frame.remaining(now) > 10)):
                    continue
            active.append(frame)
        if not active:
            return {"state": "auto_rereading" if recovering else "waiting_or_unrecognized",
                    "actions": [], "recovery": recovering}
        plans = rank_draft_choices(
            tuple(c for f in active for c in f.candidates), self.confirmed, owners,
            active_owners=tuple(f.owner for f in active), policy=rating_policy())
        if not plans:
            return {"state": "no_plan", "actions": []}
        plan = plans[0]
        by_owner = {f.owner: f for f in active}
        fresh = {f.owner: f for f in self.observe_pair()} if execute else {}
        def act(candidate):
            status = self.commit(by_owner[candidate.owner], fresh.get(candidate.owner),
                                 candidate) if execute else "observe_only"
            return {"owner": candidate.owner, "name": candidate.name, "status": status}
        actions = [act(c) for c in plan.choices]
        return {"state": "ranked", "score": plan.evaluation.total, "actions": actions,
                "reasons": [t.reason for t in plan.evaluation.terms],
                "warnings": list(plan.evaluation.warnings), "recovery": recovering}

    def close(self):
        self.pool.shutdown(wait=True)

    @property
    def complete(self):
        expected = {(owner, pick) for owner in self.devices for pick in range(1, 6)}
        actual = {(c.owner, int(c.candidate_id.rsplit(':r', 1)[1].split(':', 1)[0])) for c in self.confirmed}
        return actual == expected and not self.awaiting
