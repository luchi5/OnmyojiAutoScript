"""Normal DraftDuel task: team -> one admission -> joint five-pick draft.

The configured leader alone owns draft inputs for both accounts. The member
task accepts its invitation, then stays alive as a passive lease/phase peer.
No subprocess tool is launched and no matchmaking or deployment is performed.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import time
import cv2

from tasks.DraftDuel.local_draft import AdbIO, LocalDraft, BUTTONS
from tasks.DraftDuel.preflight import AdmissionOffer, check_pair_admission
from tasks.DraftDuel.rules import GAME_TIMEZONE, opening_window
from tasks.DraftDuel.session import DraftDuelSession, SessionError, StaleState
from tasks.DraftDuel.team import run_team
from datetime import datetime


@dataclass(frozen=True)
class StartOffer:
    cost: int | None = None
    confidence: float = 0
    start: tuple | None = None


def save_frame(path, image):
    """OpenCV filenames are not Unicode-safe on all bundled Windows builds."""
    ok, png = cv2.imencode('.png', image)
    if not ok:
        raise ValueError('Cannot encode DraftDuel evidence screenshot')
    Path(path).write_bytes(png.tobytes())


def read_offer(vision, image, teammate, own_name):
    if vision.read(image, teammate, own_name).kind != 'team_ready':
        return StartOffer()
    if not vision.match(image, 'not_started', (130, 90, 180, 90)):
        return StartOffer()  # Existing drafted series must not be re-opened.
    start = vision.match(image, 'start', (1140, 555, 120, 120))
    text, confidence = vision.ocr(image[683:716, 1140:1275])
    if re.sub(r'\s+', '', text) in ('本次免费', '免费') and confidence >= .90:
        return StartOffer(0, float(confidence), start)
    readings = {}
    # Reject a crop containing both struck-through and actual prices.
    for x, width in ((1148, 84), (1148, 100), (1176, 94)):
        text, confidence = vision.ocr(image[684:715, x:x+width])
        text = re.sub(r'\s+', '', text)
        if text in ('本次免费', '免费') and confidence >= .90:
            readings[0] = max(float(confidence), readings.get(0, 0))
        match = re.fullmatch(r'(\d+(?:\.\d+)?)万', text)
        if confidence >= .90 and match:
            value = float(match[1])*10000
            if value.is_integer() and 0 < value <= 300000:
                readings[int(value)] = max(float(confidence), readings.get(int(value), 0))
    if len(readings) == 1:
        cost, confidence = next(iter(readings.items()))
        return StartOffer(cost, confidence, start)
    return StartOffer(start=start)


class PartnerDraftIO(AdbIO):
    def __init__(self, adb, serial, guard):
        super().__init__(adb, serial)
        self.guard = guard

    def tap(self, slot):
        self.guard()
        super().tap(slot)


class OwnDraftIO:
    def __init__(self, io, guard):
        self.io, self.guard = io, guard

    def capture(self):
        return self.io.capture()

    def tap(self, slot):
        self.guard()
        self.io.tap_point(*BUTTONS[slot], control_name='DRAFT_DUEL_PICK_'+str(slot))


class DraftWorkflow:
    def __init__(self, pair, account_name, io, vision, root, event=None):
        self.pair, self.name, self.io, self.vision = pair, account_name, io, vision
        self.account = pair.for_account(account_name)
        self.partner = pair.for_account(self.account.settings.partner_config)
        self.settings = self.account.settings
        self.own_name = self.partner.settings.teammate_name
        self.root = Path(root)
        self.event = event or (lambda record, image=None: None)
        self.session = DraftDuelSession(
            self.root/'log/draft_duel_runtime.sqlite3',
            DraftDuelSession.default_pair_id(pair.leader, pair.member),
            self.name, self.partner.name, leader_id=pair.leader)

    def publish(self, stage):
        self.session.heartbeat()
        self.session.observe(0, 0, 'workflow', {'stage': stage})

    def join(self):
        self.session.acquire_lease()
        if self.session.is_leader:
            prior = self.session.open_session()
            if prior['status'] != 'stopped':
                self.session.request_stop('new normal task invocation')
                self.session.finish_session()
            self.session.open_session(new=True)
            self.session.set_workflow('team')
            self.publish('team')
            return
        end = time.monotonic()+self.settings.partner_wait_seconds
        while time.monotonic() < end:
            self.session.heartbeat()
            try:
                status = self.session.open_session()
                snapshots = self.session.snapshots(require_fresh=False)
                leader = snapshots.get(self.pair.leader)
                if (status['status'] == 'running' and leader
                        and leader['observed_at'] > time.time()-15
                        and self.session.workflow()['phase'] in ('team', 'starting', 'draft')):
                    return
            except SessionError:
                pass
            time.sleep(.3)
        raise SessionError('队长任务未就绪；请在双方账号启动协同对弈任务')

    def peer_guard(self):
        self.session.heartbeat()
        if self.session.status()['status'] != 'running':
            raise SessionError('搭档任务停止，停止后续点击')
        self.session.snapshots()  # Both processes must still own fresh leases.

    def run(self):
        try:
            self.join()
            ready = run_team(self.io, self.vision, self.settings, self.own_name,
                             execute=True, event=self.event,
                             on_tick=lambda view, image: self.publish(view.kind))
            if not ready:
                raise SessionError('组队超时或指定昵称未确认')
            return self.lead() if self.session.is_leader else self.follow()
        except Exception as error:
            self.event({'phase': 'failed', 'error': str(error)})
            if self.session.session_id:
                try:
                    if self.session.is_leader:
                        self.session.set_workflow('failed', {'reason': str(error)})
                    self.session.request_stop(str(error))
                except SessionError:
                    pass
            raise
        finally:
            self.session.release_lease()

    def follow(self):
        end = time.monotonic()+3600
        while time.monotonic() < end:
            image, _ = self.io.capture()
            self.publish('member_waiting')
            phase = self.session.workflow()
            if phase['phase'] == 'complete':
                self.publish('complete_seen')
                return True
            if phase['phase'] == 'failed' or self.session.status()['status'] != 'running':
                raise SessionError(phase['payload'].get('reason', '队长任务已停止'))
            self.peer_guard()
            time.sleep(.5)
        raise SessionError('等待队长选将结束超时')

    def lead(self):
        end = time.monotonic()+self.settings.partner_wait_seconds
        while True:
            self.publish('team_ready')
            try:
                snapshots = self.session.snapshots()
                if snapshots[self.partner.name]['payload']['stage'] in ('team_ready', 'member_waiting'):
                    break
            except StaleState:
                pass
            if time.monotonic() >= end:
                raise SessionError('队员脚本未确认就绪，未点击开始')
            time.sleep(.3)
        adb = self.root/'toolkit/Lib/site-packages/adbutils/binaries/adb.exe'
        devices = {self.name: OwnDraftIO(self.io, self.peer_guard),
                   self.partner.name: PartnerDraftIO(adb, self.partner.serial, self.peer_guard)}
        runner = LocalDraft(devices, event=self.event)
        try:
            runner.observe_pair()  # Warm both dedicated OCR models before starting.
            if not opening_window(datetime.now(GAME_TIMEZONE)).is_scheduled_open:
                raise SessionError('当前不在已核验的开放时间内')
            offers = {}
            start = None
            for account in self.pair.accounts:
                image, _ = devices[account.name].capture()
                own = self.pair.for_account(account.settings.partner_config).settings.teammate_name
                offer = read_offer(self.vision, image, account.settings.teammate_name, own)
                offers[account.name] = AdmissionOffer(offer.cost, offer.confidence, offer.cost is not None)
                if account.name == self.name:
                    start = offer.start
                self.event({'phase': 'admission', 'account': account.name, 'cost': offer.cost}, image)
            allowed = check_pair_admission(self.pair, offers, {a.name: 0 for a in self.pair.accounts})
            if not allowed or start is None:
                raise SessionError('未开始：'+(allowed.reason if not allowed else '战按钮未确认'))
            image, _ = self.io.capture()
            fresh = read_offer(self.vision, image, self.settings.teammate_name, self.own_name)
            if fresh.start is None or fresh.cost != offers[self.name].cost:
                raise SessionError('点击前队伍或费用已变化，停止开始')
            start = fresh.start
            self.peer_guard()
            self.session.set_workflow('starting', {'costs': {n: o.cost for n, o in offers.items()}})
            self.io.tap_point(*start, control_name='DRAFT_DUEL_START')  # Single attempt, persisted before input.
            self.event({'phase': 'start_sent', 'point': start})
            self.session.set_workflow('draft')
            end = time.monotonic()+min(1800, self.settings.limit_time.hour*3600+self.settings.limit_time.minute*60+self.settings.limit_time.second)
            last_progress = time.monotonic()
            progress = None
            while time.monotonic() < end:
                self.publish('draft_control')
                self.peer_guard()
                record = runner.step(execute=True)
                current = (len(runner.attempted), len(runner.confirmed))
                if current != progress:
                    progress, last_progress = current, time.monotonic()
                    self.event(dict(record, phase='draft', attempted=current[0], confirmed=current[1]))
                if runner.complete:
                    roster = {a: [c.name for c in runner.confirmed if c.owner == a] for a in devices}
                    self.session.set_workflow('complete', {'rosters': roster})
                    self.event({'phase': 'complete', 'rosters': roster})
                    for _ in range(15):
                        self.session.heartbeat()
                        peer = self.session.snapshots(require_fresh=False).get(self.partner.name, {})
                        if peer.get('payload', {}).get('stage') == 'complete_seen':
                            return True
                        time.sleep(.3)
                    raise SessionError('选将已完成，但队员脚本未确认结束')
                if time.monotonic()-last_progress > self.settings.phase_timeout_seconds:
                    raise SessionError('选将阶段无进展；请检查报名确认弹窗或未识别画面')
                time.sleep(.2)
            raise SessionError('选将任务达到时间上限')
        finally:
            runner.close()
