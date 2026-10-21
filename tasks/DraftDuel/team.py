"""Bounded invitation-only adapter. No challenge, registration or payment IO.

RGB task screenshots are converted at the task boundary; vision here uses BGR.
Every action is planned again from a new screenshot. Scrolling invalidates all
old row positions. Only the configured exact nickname can be selected/accepted.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import time

import cv2
import numpy as np

from tasks.DraftDuel.config import TeamRole
from tasks.DraftDuel.recognition import observe_lobby

RES = Path(__file__).with_name('res') / 'team'


def clean(text):
    return re.sub(r'\s+', '', text)


@dataclass(frozen=True)
class TeamView:
    kind: str = 'unknown'
    target: tuple | None = None
    selected: bool = False
    tab: str = ''
    signature: str = ''


@dataclass(frozen=True)
class TeamAction:
    kind: str
    point: tuple | None = None


class TeamVision:
    def __init__(self, ocr):
        self.ocr = ocr
        self.templates = {p.stem: cv2.imread(str(p)) for p in RES.glob('*.png')}

    def match(self, image, name, rect, threshold=.9):
        template = self.templates.get(name)
        if template is None:
            return None
        x, y, w, h = rect
        region = image[y:y+h, x:x+w]
        th, tw = template.shape[:2]
        if region.shape[0] < th or region.shape[1] < tw:
            return None
        scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (dx, dy) = cv2.minMaxLoc(scores)
        if score < threshold:
            return None
        actual = region[dy:dy+th, dx:dx+tw]
        if np.abs(actual.astype(float) - template.astype(float)).mean() > 20:
            return None
        return x+dx+tw//2, y+dy+th//2

    def text(self, image, rect):
        x, y, w, h = rect
        text, score = self.ocr(image[y:y+h, x:x+w])
        return clean(text) if score >= .85 else ''

    def read(self, image, teammate, own_name=''):
        if image is None or image.shape != (720, 1280, 3):
            return TeamView()
        # The accept/reject pair moves vertically between courtyard and lobby.
        accept = self.match(image, 'accept', (80, 130, 90, 420))
        reject = self.match(image, 'reject', (5, 130, 80, 420))
        if accept and reject and abs(accept[1]-reject[1]) < 8:
            cy = accept[1]
            sender = self.text(image, (226, cy-39, 217, 36))
            mode = self.match(image, 'invite_mode', (225, cy-6, 135, 39))
            return TeamView('invitation' if sender == teammate and mode
                            else 'other_invitation', accept)
        invite = self.match(image, 'invite', (700, 535, 150, 80))
        cancel = self.match(image, 'cancel', (425, 535, 160, 80))
        if invite and cancel:
            # Read only names inside fully visible friend cards, excluding the
            # top/bottom clipped rows and the realm line below each nickname.
            tab = ''
            for i, name in enumerate(('好友', '最近', '寮友', '跨区')):
                x = 320+i*113
                patch = image[90:142, x:x+104]
                if np.mean(patch[:, :, 0]) > 115:
                    tab = name
            hits = []
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray[158:529, 350:930], 60, 150)
            # Card borders form horizontal lines; pair consecutive lines at
            # least 70px apart to locate rows after arbitrary scrolling.
            counts = np.count_nonzero(edges, axis=1)
            ys = [y+158 for y in range(len(counts)) if counts[y] > 300]
            tops = []
            for y in ys:
                if not tops or y-tops[-1] > 8:
                    tops.append(y)
            for y in tops:
                if y+80 > 530:
                    continue
                for x in (351, 646):
                    # Verify bottom border belongs to the same full card.
                    if not any(76 <= bottom-y <= 94 for bottom in tops):
                        continue
                    name = self.text(image, (x+90, y+9, 180, 33))
                    if name == teammate:
                        selected = self.match(image, 'selected', (x+238, y-10, 55, 58), .86) is not None
                        hits.append(((x+150, y+38), selected))
            signature = str(hash(cv2.resize(image[165:525, 355:925], (57, 36)).tobytes()))
            if len(hits) == 1:
                return TeamView('friend_list', hits[0][0], hits[0][1], tab, signature)
            return TeamView('friend_list', tab=tab, signature=signature)
        # Team membership requires positive nameplates at both player slots;
        # disappearance of the plus alone is never treated as success.
        title = self.match(image, 'title', (140, 8, 140, 45))
        if title:
            left = self.text(image, (640, 174, 126, 30))
            right = self.text(image, (977, 168, 122, 33))
            if own_name and {left, right} == {own_name, teammate}:
                # The configured member must occupy the formerly empty slot.
                # Plus disappearance is necessary, together with both names.
                plus = self.match(image, 'plus', (960, 305, 110, 105), .86)
                if not plus:
                    return TeamView('team_ready')
        lobby = observe_lobby(image, color_order='BGR')
        if lobby.is_lobby and lobby.is_add_teammate_visible:
            return TeamView('lobby', (1013, 356))
        # Collapsed invite flag: click only its sampled red 邀 banner to expand.
        flag = self.match(image, 'invite_flag', (70, 90, 530, 240), .93)
        if flag:
            return TeamView('invite_flag', flag)
        return TeamView()


class TeamWorker:
    def __init__(self, role, tab='跨区', retry_seconds=20, clock=time.monotonic):
        self.role = TeamRole(role)
        if self.role == TeamRole.UNASSIGNED:
            raise ValueError('Choose leader/member before starting')
        self.tab = tab
        self.retry_seconds = retry_seconds
        self.clock = clock
        self.next_action = 0
        self.next_invite = 0
        self.scrolls = 0
        self.tab_requested = False
        self.sent = False
        self.selection_at = None

    def plan(self, view):
        now = self.clock()
        if view.kind == 'team_ready':
            return TeamAction('done')
        if now < self.next_action:
            return TeamAction('wait')
        action = TeamAction('wait')
        if self.role == TeamRole.MEMBER:
            if view.kind in ('invite_flag', 'invitation') and not self.sent:
                action = TeamAction('accept' if view.kind == 'invitation' else 'expand', view.target)
                if view.kind == 'invitation':
                    self.sent = True  # Accept once, then confirm the actual team.
        elif view.kind == 'lobby' and now >= self.next_invite:
            self.tab_requested = False
            self.scrolls = 0
            action = TeamAction('open_list', view.target)
        elif view.kind == 'friend_list':
            if not self.tab_requested:
                self.tab_requested = True
                action = TeamAction('tab', (370+113*('好友', '最近', '寮友', '跨区').index(self.tab), 118))
            elif view.tab != self.tab:
                return TeamAction('wait')
            elif self.selection_at is not None and not (view.target and view.selected):
                return TeamAction('selection_unconfirmed' if now-self.selection_at > 6 else 'wait')
            elif view.target:
                action = TeamAction('invite' if view.selected else 'select',
                                    (775, 575) if view.selected else view.target)
                if view.selected:
                    self.next_invite = now+self.retry_seconds
                    self.sent = True
                    self.selection_at = None
                else:
                    self.selection_at = now
            elif self.scrolls < 12:
                self.scrolls += 1
                action = TeamAction('scroll')
            else:
                return TeamAction('friend_not_found')
        if action.kind != 'wait':
            self.next_action = now+1.5
        return action


def execute_action(io, action):
    if action.kind == 'scroll':
        io.swipe(850, 468, 850, 235, 450)
    elif action.point is not None:
        io.tap_point(*action.point)


class TaskTeamIO:
    """Runner boundary; raw device screenshot avoids generic invite handlers."""
    def __init__(self, task):
        self.task = task

    def capture(self):
        self.task.device.screenshot()
        return cv2.cvtColor(self.task.device.image, cv2.COLOR_RGB2BGR), time.monotonic()

    def tap_point(self, x, y, control_name='DRAFT_DUEL_TEAM'):
        self.task.device.click(x, y, control_name=control_name)

    def swipe(self, x1, y1, x2, y2, ms):
        self.task.device.swipe((x1, y1), (x2, y2), duration=ms/1000)


def run_team(io, vision, settings, own_name='', *, execute=False, seconds=None, event=None, on_tick=None):
    worker = TeamWorker(settings.user_status, settings.friend_tab.value)
    end = time.monotonic()+(seconds or settings.partner_wait_seconds)
    last = None
    while time.monotonic() < end:
        image, _ = io.capture()
        view = vision.read(image, settings.teammate_name, own_name)
        if on_tick:
            on_tick(view, image)
        action = worker.plan(view)
        record = {'view': view.kind, 'action': action.kind, 'point': action.point}
        if record != last and event:
            event(record, image)
        last = record
        if action.kind == 'done':
            return True
        if action.kind in ('friend_not_found', 'selection_unconfirmed'):
            return False
        if execute:
            execute_action(io, action)
        time.sleep(.3)
    return False
