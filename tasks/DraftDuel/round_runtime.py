"""Full-series device adapter: observe, recover, deploy and settle both peers.

The state machine is also used by the offline rehearsal. No worker is started,
stopped or restarted here. Only the owning workflow supplies live IO.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from pathlib import Path
import json
import time

import cv2
import numpy as np

from tasks.DraftDuel.deployment import DeploymentController
from tasks.DraftDuel.lineup_runtime import read_roster, compute_lineup, RoundDeploymentVision
from tasks.DraftDuel.local_draft import TimedOCR
from tasks.DraftDuel.ratings import Ratings
from tasks.DraftDuel.round_controller import SeriesController
from tasks.DraftDuel.round_model import RoundView
from tasks.DraftDuel.round_vision import RoundVision
from tasks.DraftDuel.session import SessionError
from tasks.DraftDuel.strategy import Candidate


class SeriesRuntime:
    def __init__(self, devices, leader, settings, *, event=None, guard=None,
                 tick=None, checkpoint_path=None, clock=time.monotonic,
                 sleep=time.sleep, vision_factory=RoundVision, ocr_factory=TimedOCR):
        self.devices, self.leader, self.settings = devices, leader, settings
        self.event = event or (lambda record, image=None: None)
        self.guard = guard or (lambda: None)
        self.tick = tick or (lambda: None)
        self.clock, self.sleep = clock, sleep
        self.path = Path(checkpoint_path) if checkpoint_path else None
        self.ocr = {owner: ocr_factory() for owner in devices}
        self.visions = {owner: vision_factory(self.ocr[owner]) for owner in devices}
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.pending, self.images, self.lobbies = {}, {}, {}
        self.views = {}
        self.rosters, self.portraits, self.lineups = {}, {}, {}
        self.preparation_reads, self.preparation_actions = {}, {}
        self.deployments, self.deployment_match = {}, {}
        self.saved_portraits = set()
        self.controller = SeriesController(tuple(devices), leader, self.act, self.deploy,
            event=lambda record: self.event(record), clock=clock,
            matchmaking_timeout=settings.matchmaking_timeout_seconds,
            phase_timeout=settings.phase_timeout_seconds)

    def _read(self, owner):
        image, captured = self.devices[owner].capture()
        view = self.visions[owner].read(image, captured)
        return image, view

    def observe(self):
        for owner in self.devices:
            if owner not in self.pending:
                self.pending[owner] = self.pool.submit(self._read, owner)
        views = {}
        deadline = self.clock()+5
        for owner, future in list(self.pending.items()):
            try:
                image, view = future.result(timeout=max(0, deadline-self.clock()))
            except TimeoutError:
                views[owner] = RoundView('unknown', self.clock(), evidence=('capture_pending',))
                continue
            except Exception as error:
                del self.pending[owner]
                self.event({'phase': 'screen_retry', 'owner': owner, 'error': str(error)})
                views[owner] = RoundView('unknown', self.clock(), evidence=('capture_retry',))
            else:
                del self.pending[owner]
                self.images[owner] = image
                views[owner] = view
        self.views.update(views)
        return views

    def act(self, owner, action, point):
        self.guard()
        view = self.views.get(owner)
        if view is not None and not view.fresh(self.clock()):
            raise OSError('Reobserve after peer recovery before using an old button')
        self.save()
        self.event({'phase': 'series_input', 'owner': owner, 'action': action,
                    'point': point, 'match_id': self.controller.match_id}, self.images.get(owner))
        self.devices[owner].tap_point(*point, control_name='DRAFT_DUEL_%d_%s' %
                                     (self.controller.match_id, action.upper()))

    def _prepare_action(self, owner, action, view):
        point = view.actions.get(action)
        if point is None or not view.fresh(self.clock()):
            return False
        previous = self.preparation_actions.get((owner, action),
                                                 (-float('inf'), -float('inf'), 0))
        last_at, last_capture = previous[:2]
        attempts = previous[2] if len(previous) > 2 else 1
        delay = min(15, 2 * 2**min(4, max(0, attempts-2)))
        if self.clock()-last_at < delay or view.captured <= last_capture:
            return False
        self.preparation_actions[owner, action] = (self.clock(), view.captured, attempts+1)
        try:
            accepted = self.act(owner, action, point)
            if accepted is False:
                raise OSError('preparation input was not sent')
        except Exception as error:
            self.event({'phase': 'roster_input_retry', 'owner': owner,
                        'action': action, 'attempt': attempts+1, 'error': str(error)})
            return False
        return True

    def _portrait_fallback(self, owner, lobby):
        """Keep playing with actual portrait identities when names stay unreadable."""
        self.rosters[owner], self.portraits[owner] = [], {}
        for index in range(5):
            name = '未识别头像:%s:%d' % (owner, index)
            self.rosters[owner].append(Candidate(owner+':'+str(index), owner, name, confidence=0))
            x = 470+86*index
            self.portraits[owner][name] = lobby[39:81, x+5:x+56].copy()
        self.event({'phase': 'roster_portrait_fallback', 'owner': owner,
                    'reason': 'use_observed_portraits_without_inventing_names_or_stats'}, lobby)

    def prepare(self, views):
        prepared = dict(views)
        for owner, view in views.items():
            if not view.fresh(self.clock()):
                continue
            image = self.images.get(owner)
            image_valid = isinstance(image, np.ndarray) and image.shape == (720, 1280, 3)
            lobby = self.lobbies.get(owner)
            lobby_valid = isinstance(lobby, np.ndarray) and lobby.shape == (720, 1280, 3)
            capture_key = (owner, 'captured')
            new_frame = view.captured > self.preparation_reads.get(capture_key, -float('inf'))
            if owner not in self.rosters and view.phase in ('lobby_ready', 'roster_detail') and new_frame:
                self.preparation_reads[capture_key] = view.captured
                self.preparation_reads[owner] = self.preparation_reads.get(owner, 0)+1
            reads = self.preparation_reads.get(owner, 0)
            if view.phase == 'lobby_ready':
                if not image_valid:
                    prepared[owner] = replace(view, phase='unknown', actions={})
                    self.event({'phase': 'roster_image_retry', 'owner': owner,
                                'reason': 'missing_actual_lobby_frame'})
                    continue
                self.lobbies[owner] = image.copy()
                self.preparation_actions.pop((owner, 'close_roster'), None)
                if owner not in self.rosters:
                    if reads >= 4:
                        self._portrait_fallback(owner, self.lobbies[owner])
                    else:
                        self._prepare_action(owner, 'open_roster', view)
                        prepared[owner] = replace(view, phase='roster_detail', actions={})
            elif view.phase == 'roster_detail':
                self.preparation_actions.pop((owner, 'open_roster'), None)
                if owner not in self.rosters and lobby_valid and new_frame:
                    try:
                        if not image_valid:
                            raise ValueError('missing actual roster-detail frame')
                        roster, portraits = read_roster(image, lobby, owner,
                            self.ocr[owner], allow_unknown=True, read_stats=False)
                        if len(roster) != 5 or len(portraits) != 5:
                            raise ValueError('incomplete observed roster')
                    except Exception as error:
                        self.event({'phase': 'roster_read_retry', 'owner': owner,
                                    'attempt': reads, 'error': str(error)})
                        if reads >= 4:
                            self._portrait_fallback(owner, lobby)
                    else:
                        self.rosters[owner], self.portraits[owner] = roster, portraits
                        self.event({'phase': 'roster_prepared', 'owner': owner,
                            'names': [c.name for c in roster],
                            'mode': 'observed_names_and_base_ratings_no_numeric_dependency'}, image)
                # A detail page encountered after reconnect has no trustworthy
                # portrait strip yet: close it, capture the real lobby and retry.
                # Failed close clicks keep retrying with fresh frames/backoff.
                if owner in self.rosters or not lobby_valid:
                    self._prepare_action(owner, 'close_roster', view)
        if len(self.rosters) == len(self.devices) and not self.lineups:
            self.lineups, evaluation = compute_lineup([c for roster in self.rosters.values() for c in roster])
            self.event({'phase': 'lineups_ready', 'lineups': self.lineups, 'evaluation': evaluation})
            self.save()
        return prepared

    def deploy(self, owner):
        if owner not in self.lineups:
            # The game advances timed deployment itself. Keep observing and
            # reconcile the actual round on returning to the drafted lobby.
            return 'awaiting_automatic_deployment_then_reconcile'
        match_id = self.controller.match_id
        if self.deployment_match.get(owner) != match_id:
            vision = RoundDeploymentVision(self.ocr[owner], self.portraits[owner])
            self.deployments[owner] = DeploymentController(
                self.devices[owner], vision, self.lineups[owner], clock=self.clock)
            self.deployment_match[owner] = match_id
        result = self.deployments[owner].step(execute=True)
        if result == 'handoff_last_five_seconds':
            result = 'awaiting_automatic_deployment_then_reconcile'
        return result

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        folder = self.path.with_suffix('')
        folder.mkdir(exist_ok=True)
        roster_data = {}
        for owner, roster in self.rosters.items():
            rows = []
            for index, candidate in enumerate(roster):
                filename = '%d-%d.png' % (tuple(self.devices).index(owner), index)
                destination = folder/filename
                if (owner, index) not in self.saved_portraits:
                    ok, data = cv2.imencode('.png', self.portraits[owner][candidate.name])
                    if not ok:
                        raise OSError('Could not save round portrait')
                    destination.write_bytes(data.tobytes())
                    self.saved_portraits.add((owner, index))
                rows.append({'name': candidate.name, 'confidence': candidate.confidence, 'portrait': filename})
            roster_data[owner] = rows
        state = {'schema_version': 1, 'owners': list(self.devices), 'leader': self.leader,
                 'controller': self.controller.checkpoint(), 'rosters': roster_data,
                 'lineups': self.lineups, 'saved_at': time.time()}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def restore(self):
        if self.path is None or not self.path.exists():
            return False
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if (data.get('schema_version') != 1 or data.get('owners') != list(self.devices)
                or data.get('leader') != self.leader):
            return False
        self.controller.restore(data['controller'])
        for owner, rows in data.get('rosters', {}).items():
            if owner not in self.devices or len(rows) != 5:
                continue
            portraits, roster = {}, []
            for index, row in enumerate(rows):
                source = (self.path.with_suffix('')/row['portrait']).resolve()
                if source.parent != self.path.with_suffix('').resolve():
                    raise ValueError('Invalid checkpoint portrait path')
                pixels = cv2.imdecode(np.frombuffer(source.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                if pixels is None:
                    raise ValueError('Missing checkpoint portrait')
                portraits[row['name']] = pixels
                roster.append(Ratings().annotate(Candidate(owner+':'+str(index), owner,
                    row['name'], confidence=row['confidence'])))
            self.rosters[owner], self.portraits[owner] = roster, portraits
        if len(self.rosters) == len(self.devices):
            self.lineups, _ = compute_lineup([c for values in self.rosters.values() for c in values])
        self.event({'phase': 'series_checkpoint_restored', 'match_id': self.controller.match_id})
        return True

    def run(self, seconds):
        deadline = self.clock()+seconds
        previous = None
        while self.clock() < deadline:
            self.tick()
            self.guard()
            views = self.observe()
            views = self.prepare(views)
            result = self.controller.step(views, execute=True)
            marker = (result.get('state'), self.controller.match_id,
                      self.controller.wins, self.controller.losses)
            if marker != previous:
                self.event(dict(result, phase='series_progress'))
                previous = marker
            self.save()
            if self.controller.complete:
                return True
            self.sleep(result.get('next_poll', .5))
        self.save()
        raise SessionError('本次运行时间到限，已保存整轮进度，下次运行将自动对照画面继续')

    def close(self):
        self.pool.shutdown(wait=True)
        for ocr in self.ocr.values():
            ocr.close()
