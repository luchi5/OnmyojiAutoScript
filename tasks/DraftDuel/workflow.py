"""Normal DraftDuel task: the leader's configured count of complete series.

The configured leader alone owns draft inputs for both accounts. The member
task accepts its invitation, then stays alive as a passive lease/phase peer.
The leader owns both accounts' inputs until final settlement is acknowledged.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import time
import json
import subprocess
import cv2

from tasks.DraftDuel.local_draft import AdbIO, LocalDraft, BUTTONS
from tasks.DraftDuel.preflight import AdmissionOffer
from tasks.DraftDuel.batch import SeriesBatch
from tasks.DraftDuel.rules import GAME_TIMEZONE, opening_window
from tasks.DraftDuel.session import DraftDuelSession, SessionError, StaleState
from tasks.DraftDuel.team import run_team
from datetime import datetime


@dataclass(frozen=True)
class StartOffer:
    cost: int | None = None
    confidence: float = 0
    start: tuple | None = None


def read_admission_page(vision, image, teammate, own_name):
    """Only verify the paired unopened lobby; price OCR is not an entry gate."""
    ready = (vision.read(image, teammate, own_name).kind == 'team_ready'
             and bool(vision.match(image, 'not_started', (130, 90, 180, 90))))
    start = vision.match(image, 'start', (1140, 555, 120, 120)) if ready else None
    return ready, start


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
    readings = {}
    invalid_numeric = False
    # Retain the complete actual-price crop for a discounted offer. A crop
    # containing the struck-through old price plus the new price is not parsed.
    # Agreement of two views is a rejection guard, not independent OCR evidence.
    crops = [image[683:716, 1140:1275]] + [image[684:715, x:x+width]
             for x, width in ((1148, 84), (1148, 100), (1176, 94))]
    for crop in crops:
        for pixels in (crop, cv2.resize(crop, None, fx=2, fy=2)):
            text, confidence = vision.ocr(pixels)
            text = re.sub(r'\s+', '', text)
            if confidence < .90:
                continue
            if text in ('本次免费', '免费'):
                readings.setdefault(0, []).append(float(confidence))
                continue
            match = re.fullmatch(r'(\d+(?:\.\d+)?)万', text)
            if match:
                value = float(match[1])*10000
                # Only price tiers actually sampled in this adapter are
                # supported. A dropped zero (30万 -> 3万) must not lower a budget.
                if value not in (100000, 300000):
                    invalid_numeric = True
                else:
                    readings.setdefault(int(value), []).append(float(confidence))
    if not invalid_numeric and len(readings) == 1:
        cost, scores = next(iter(readings.items()))
        if len(scores) >= 2:
            return StartOffer(cost, min(scores), start)
    return StartOffer(start=start)


class PartnerDraftIO(AdbIO):
    def __init__(self, adb, serial, guard):
        super().__init__(adb, serial)
        self.guard = guard

    def tap(self, slot):
        self.guard()
        super().tap(slot)

    def tap_point(self, x, y, control_name=None):
        self.guard()
        subprocess.run(self.command + ['shell', 'input', 'tap', str(x), str(y)],
                       capture_output=True, check=True, timeout=2,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def drag(self, source, target):
        self.guard()
        subprocess.run(self.command + ['shell', 'input', 'swipe',
                       *map(str, source), *map(str, target), '400'],
                       capture_output=True, check=True, timeout=3,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def confirm(self):
        self.tap_point(1170, 590)


class OwnDraftIO:
    def __init__(self, io, guard):
        self.io, self.guard = io, guard

    def capture(self):
        return self.io.capture()

    def tap(self, slot):
        self.guard()
        self.io.tap_point(*BUTTONS[slot], control_name='DRAFT_DUEL_PICK_'+str(slot))

    def tap_point(self, x, y, control_name='DRAFT_DUEL_SERIES'):
        self.guard()
        self.io.tap_point(x, y, control_name=control_name)

    def drag(self, source, target):
        self.guard()
        self.io.swipe(*source, *target, 400)

    def confirm(self):
        self.tap_point(1170, 590, control_name='DRAFT_DUEL_DEPLOY_CONFIRM')


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
        self.checkpoint_path = self.root/'log/draft-duel-series'/self.session.pair_id/'checkpoint.json'
        self.batch = SeriesBatch(self.checkpoint_path.with_name('batch.json'),
            self.pair.for_account(self.pair.leader).settings.challenge_limit, self.checkpoint_path)
        self.checkpoint_path = self.batch.checkpoint

    def has_pending_series(self):
        if self.batch.pending:
            return True
        return self._checkpoint_pending()

    def _checkpoint_pending(self):
        if not self.checkpoint_path.is_file():
            return False
        try:
            state = json.loads(self.checkpoint_path.read_text(encoding='utf-8'))
            return (state.get('schema_version') == 1 and state.get('leader') == self.pair.leader
                    and set(state.get('owners', ())) == {a.name for a in self.pair.accounts}
                    and not state.get('controller', {}).get('complete', False))
        except (OSError, ValueError, TypeError):
            return False

    def publish(self, stage):
        self.session.heartbeat()
        self.session.observe(0, 0, 'workflow', {'stage': stage})

    def join(self):
        self.session.acquire_lease()
        if self.session.is_leader:
            prior = self.session.open_session()
            pending = self.has_pending_series()
            if pending and prior['status'] == 'running':
                # Keep the live member's session_id valid when only the leader
                # restarted. The saved stage is reconciled by lead(resume=True).
                self.session.set_workflow('series')
                self.publish('series')
                self.event({'phase': 'pair_session_resumed',
                            'session_id': self.session.session_id})
                return
            if prior['status'] != 'stopped':
                self.session.request_stop('new normal task invocation')
                self.session.finish_session()
            self.session.open_session(new=True)
            stage = 'series' if pending else 'team'
            self.session.set_workflow(stage)
            self.publish(stage)
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
                        and self.session.workflow()['phase'] in ('team', 'starting', 'draft', 'series', 'between_series')):
                    return
            except SessionError:
                pass
            time.sleep(.3)
        raise SessionError('队长任务未就绪；请在双方账号启动协同对弈任务')

    def peer_guard(self):
        from tasks.DraftDuel.session import LeaseLost, SessionUnavailable
        end = time.monotonic()+self.settings.partner_wait_seconds
        recovering = False
        while True:
            try:
                try:
                    self.session.heartbeat()
                except LeaseLost:
                    # acquire_lease still rejects a different live owner. A
                    # suspended worker must not take another worker's device.
                    self.session.acquire_lease()
                    recovering = True
                if self.session.status()['status'] != 'running':
                    raise SessionError('搭档任务停止，停止后续点击')
                try:
                    self.session.snapshots()
                    if recovering:
                        self.last_peer_recovery = time.monotonic()
                        self.event({'phase': 'pair_connection_recovered'})
                    return
                except StaleState:
                    if not recovering:
                        self.event({'phase': 'pair_connection_rechecking'})
                    recovering = True
                    # Both peers may enter this wait together. Continue our own
                    # workflow heartbeat so neither waits on the other's stale
                    # metadata indefinitely. This is not new screen evidence.
                    old = self.session.snapshots(require_fresh=False).get(self.name, {})
                    stage = old.get('payload', {}).get('stage')
                    self.publish(stage or ('series_control' if self.session.is_leader else 'member_waiting'))
            except SessionUnavailable:
                if self.session.is_leader:
                    raise
                # A member can follow a recovered leader session without
                # requiring a manual stop/start of the member task.
                status = self.session.open_session()
                if status['status'] != 'running':
                    raise SessionError('队长任务已停止')
                recovering = True
                self.publish('member_waiting')
            if time.monotonic() >= end:
                raise StaleState('搭档连接在重试窗口内未恢复，当前整轮进度已保留')
            time.sleep(.3)

    def run(self):
        try:
            self.join()
            if self.session.workflow()['phase'] == 'series':
                return self.run_batch() if self.session.is_leader else self.follow()
            ready = run_team(self.io, self.vision, self.settings, self.own_name,
                             execute=True, event=self.event,
                             on_tick=lambda view, image: self.publish(view.kind))
            if not ready:
                raise SessionError('组队超时或指定昵称未确认')
            return self.run_batch() if self.session.is_leader else self.follow()
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
        from tasks.DraftDuel.session import LeaseLost, SessionUnavailable
        # The leader owns the series deadline. The passive member must not
        # expire an hour after joining while the leader is still completing it.
        while True:
            try:
                try:
                    self.publish('member_waiting')
                except LeaseLost:
                    self.session.acquire_lease()
                    self.publish('member_waiting')
                phase = self.session.workflow()
                if phase['phase'] == 'failed' or self.session.status()['status'] != 'running':
                    raise SessionError(phase['payload'].get('reason', '队长任务已停止'))
                if phase['phase'] == 'complete':
                    self.publish('complete_seen')
                    return True
                self.peer_guard()
            except SessionUnavailable:
                status = self.session.open_session()
                if status['status'] != 'running':
                    raise SessionError('队长任务已停止')
                self.event({'phase': 'member_session_rejoined',
                            'session_id': self.session.session_id})
            time.sleep(.5)

    def recover_admission(self, devices, attempts=3):
        """Recheck both paired unopened pages without requiring price digits."""
        reason = '队伍或未开启大厅暂未识别'
        for attempt in range(1, attempts+1):
            self.peer_guard()
            offers = {}
            captured = {}
            start = None
            for account in self.pair.accounts:
                image, captured[account.name] = devices[account.name].capture()
                own = self.pair.for_account(account.settings.partner_config).settings.teammate_name
                ready, point = read_admission_page(self.vision, image, account.settings.teammate_name, own)
                offers[account.name] = AdmissionOffer(None, 1 if ready else 0, ready)
                if account.name == self.name:
                    start = point
                self.event({'phase': 'admission', 'attempt': attempt,
                            'account': account.name, 'ready': ready}, image)
            # Admission is a static lobby, unlike the short draft countdown.
            # Check after OCR too, so slow reads cannot authorize from old data.
            now = time.monotonic()
            if not all(0 <= now-at < 10 for at in captured.values()):
                reason = '大厅截图已过期，重新读取双方'
                self.event({'phase': 'admission_recovering', 'attempt': attempt, 'reason': reason})
                if attempt < attempts:
                    time.sleep(.5)
                continue
            allowed = all(offer.ready for offer in offers.values())
            if allowed and start is not None:
                image, fresh_at = devices[self.name].capture()
                ready, point = read_admission_page(self.vision, image, self.settings.teammate_name, self.own_name)
                now = time.monotonic()
                recent_pair = all(0 <= now-at < 10 for name, at in captured.items()
                                  if name != self.name)
                if (ready and point is not None
                        and captured[self.name] < fresh_at <= now
                        and now-fresh_at < 10 and recent_pair):
                    return offers, point
                reason = '点击前画面变化，重新核对双方'
            else:
                reason = '双方未同时就绪' if not allowed else '开始按钮暂未识别'
            self.event({'phase': 'admission_recovering', 'attempt': attempt, 'reason': reason})
            if attempt < attempts:
                time.sleep(.5)
        raise SessionError('自动重读后仍无法核实未开启大厅或队伍：'+reason)

    def _save_draft_checkpoint(self, costs=None, start_attempts=0):
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        state = {'schema_version': 1, 'owners': [a.name for a in self.pair.accounts],
                 'leader': self.pair.leader, 'stage': 'draft',
                 'controller': {'complete': False}, 'saved_at': time.time(),
                 'admission_pending': costs is not None, 'admission_costs': costs,
                 'start_attempts': start_attempts}
        temporary = self.checkpoint_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.checkpoint_path)

    def _send_admission(self, start, costs, attempt):
        self._save_draft_checkpoint(costs, attempt)
        before_guard = time.monotonic()
        self.peer_guard()
        if getattr(self, 'last_peer_recovery', -1) >= before_guard:
            self.event({'phase': 'admission_reobserve_after_peer_recovery', 'attempt': attempt})
            return False
        self.session.set_workflow('starting', {'costs': costs, 'attempt': attempt})
        try:
            self.io.tap_point(*start, control_name='DRAFT_DUEL_START')
        except (OSError, subprocess.SubprocessError) as error:
            self.event({'phase': 'start_input_uncertain', 'attempt': attempt, 'error': str(error)})
            return False
        self.event({'phase': 'start_sent', 'point': start, 'attempt': attempt})
        return True

    def _retry_unopened_admission(self, devices, phases):
        if not all(p == 'lobby_unopened' for p in phases):
            return False
        state = json.loads(self.checkpoint_path.read_text(encoding='utf-8'))
        if not state.get('admission_pending') or state.get('start_attempts', 0) >= 3:
            return False
        offers, start = self.recover_admission(devices)
        costs = {name: offer.cost for name, offer in offers.items()}
        return self._send_admission(start, costs, state.get('start_attempts', 0)+1)

    def _mark_draft_observed(self):
        if not self.checkpoint_path.exists():
            return
        state = json.loads(self.checkpoint_path.read_text(encoding='utf-8'))
        if state.get('admission_pending'):
            state['admission_pending'] = False
            temporary = self.checkpoint_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.checkpoint_path)

    def input_guard(self):
        before = time.monotonic()
        self.peer_guard()
        if getattr(self, 'last_peer_recovery', -1) >= before:
            raise StaleState('搭档恢复后重新读取画面，再发送输入')

    def run_series(self, devices, resume=False):
        from tasks.DraftDuel.round_runtime import SeriesRuntime
        self.session.set_workflow('series')
        runtime = SeriesRuntime(devices, self.name, self.settings, event=self.event,
            guard=self.peer_guard, tick=lambda: self.publish('series_control'),
            checkpoint_path=self.checkpoint_path)
        try:
            if resume:
                runtime.restore()
            seconds = (self.settings.limit_time.hour*3600+self.settings.limit_time.minute*60
                       +self.settings.limit_time.second)
            if not runtime.run(seconds):
                return False
            result = {'wins': runtime.controller.wins, 'losses': runtime.controller.losses,
                      'matches': runtime.controller.match_id, 'settled_accounts': list(devices)}
        finally:
            runtime.close()
        return result

    def run_batch(self):
        adopt = self._checkpoint_pending()
        result = {}
        while self.batch.begin(adopt=adopt):
            self.checkpoint_path = self.batch.checkpoint
            self.event({'phase': 'series_batch', 'completed_series': self.batch.completed,
                        'target_series': self.batch.target})
            # A complete checkpoint not yet journalled must be counted once,
            # without replaying admission after a crash between these writes.
            resume = self.checkpoint_path.exists()
            result = self.lead(resume=resume)
            if not result:
                return False
            self.batch.settle(result)
            self.session.set_workflow('between_series', {'completed_series': self.batch.completed,
                                      'target_series': self.batch.target})
            self.event(dict(result, phase='series_settled', completed_series=self.batch.completed,
                            target_series=self.batch.target))
            adopt = False
        result = dict(self.batch.state['results'][-1], completed_series=self.batch.completed,
                      target_series=self.batch.target)
        self.session.set_workflow('complete', result)
        self.event(dict(result, phase='complete'))
        end = time.monotonic()+self.settings.partner_wait_seconds
        while time.monotonic() < end:
            self.session.heartbeat()
            peer = self.session.snapshots(require_fresh=False).get(self.partner.name, {})
            if peer.get('payload', {}).get('stage') == 'complete_seen':
                self.batch.finish()
                return True
            time.sleep(.3)
        raise SessionError('整轮已结算，等待搭档任务确认收尾超时，结果已保存')

    def lead(self, resume=False):
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
        devices = {self.name: OwnDraftIO(self.io, self.input_guard),
                   self.partner.name: PartnerDraftIO(adb, self.partner.serial, self.input_guard)}
        draft_resume = False
        if resume:
            data = json.loads(self.checkpoint_path.read_text(encoding='utf-8'))
            draft_resume = data.get('stage') == 'draft'
            if not draft_resume:
                return self.run_series(devices, resume=True)
        runner = LocalDraft(devices, event=self.event)
        try:
            runner.observe_pair()  # Warm both dedicated OCR models before starting.
            if not draft_resume:
                if not opening_window(datetime.now(GAME_TIMEZONE)).is_scheduled_open:
                    raise SessionError('当前不在已核验的开放时间内')
                offers, start = self.recover_admission(devices)
                self._send_admission(start, {n: o.cost for n, o in offers.items()}, 1)
            self.session.set_workflow('draft')
            end = time.monotonic()+min(1800, self.settings.limit_time.hour*3600+self.settings.limit_time.minute*60+self.settings.limit_time.second)
            last_progress = time.monotonic()
            progress = None
            last_reconcile = 0
            while time.monotonic() < end:
                self.publish('draft_control')
                self.peer_guard()
                record = runner.step(execute=True)
                if runner.attempted or record.get('state') in ('ranked', 'auto_rereading'):
                    self._mark_draft_observed()
                current = (len(runner.attempted), len(runner.confirmed))
                if current != progress:
                    progress, last_progress = current, time.monotonic()
                    self.event(dict(record, phase='draft', attempted=current[0], confirmed=current[1]))
                if runner.complete:
                    roster = {a: [c.name for c in runner.confirmed if c.owner == a] for a in devices}
                    self.event({'phase': 'draft_complete', 'rosters': roster})
                    runner.close()
                    return self.run_series(devices)
                # A timer/default pick may advance without our own click.
                # Reconcile actual phase and later reread all five portraits.
                if (record.get('state') in ('waiting_or_unrecognized', 'auto_rereading')
                        and time.monotonic()-last_reconcile > 4):
                    from tasks.DraftDuel.round_vision import RoundVision
                    last_reconcile = time.monotonic()
                    phases = []
                    recovery_vision = RoundVision(self.vision.ocr)
                    for device in devices.values():
                        image, captured = device.capture()
                        phases.append(recovery_vision.read(image, captured).phase)
                    if self._retry_unopened_admission(devices, phases):
                        self.session.set_workflow('draft')
                    if all(p in ('lobby_ready', 'roster_detail', 'matching', 'deploying',
                                 'battle', 'result', 'series_result') for p in phases):
                        self.event({'phase': 'draft_reconciled_after_auto_selection', 'screens': phases})
                        runner.close()
                        return self.run_series(devices)
                if time.monotonic()-last_progress > self.settings.phase_timeout_seconds:
                    self.event({'phase': 'draft_auto_recovery', 'reason': 'reread_actual_stage'})
                    last_progress = time.monotonic()
                time.sleep(.2)
            raise SessionError('选将时间到限，已保存进度，下次自动重新识别当前阶段')
        finally:
            runner.close()
