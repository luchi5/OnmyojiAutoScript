"""Real runtime/controller loop with injected screen transport, never devices."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tasks.DraftDuel.config import DraftDuelConfig
from tasks.DraftDuel.round_model import RoundView
from tasks.DraftDuel.round_runtime import SeriesRuntime


class FakeOCR:
    def __call__(self, image): return '', 0
    def close(self): pass


class RuntimeTests(unittest.TestCase):
    def make_runtime(self, root=None):
        self.now = 100.0
        devices = {o: Mock() for o in ('one', 'two')}
        runtime = SeriesRuntime(devices, 'one', DraftDuelConfig(),
            clock=lambda: self.now, sleep=lambda seconds: None,
            checkpoint_path=Path(root)/'state.json' if root else None,
            ocr_factory=FakeOCR, vision_factory=lambda ocr: Mock())
        self.addCleanup(runtime.close)
        return runtime, devices

    def test_unreadable_roster_names_and_numbers_still_make_a_portrait_plan(self):
        runtime, devices = self.make_runtime()
        image = np.full((720, 1280, 3), 120, np.uint8)
        runtime.images = {o: image for o in devices}
        runtime.lobbies = {o: image for o in devices}
        views = {o: RoundView('roster_detail', self.now, {'close_roster': (1210, 120)})
                 for o in devices}
        runtime.prepare(views)
        self.assertEqual(set(runtime.lineups), set(devices))
        self.assertTrue(all(len(v) == 3 for v in runtime.lineups.values()))
        self.assertTrue(all(c.strength is None and c.stats.speed is None
                            for roster in runtime.rosters.values() for c in roster))
        self.assertTrue(all(c.name.startswith('未识别头像:')
                            for roster in runtime.rosters.values() for c in roster))
        for io in devices.values(): io.tap_point.assert_called_once()

    def test_failed_detail_open_automatically_uses_observed_portraits(self):
        runtime, devices = self.make_runtime()
        runtime.images = {o: np.ones((720, 1280, 3), np.uint8) for o in devices}
        for _ in range(4):
            self.now += 3
            views = {o: RoundView('lobby_ready', self.now, {'open_roster': (500, 60)}) for o in devices}
            runtime.prepare(views)
        self.assertEqual(len(runtime.lineups), 2)
        self.assertEqual(devices['one'].tap_point.call_count, 3)

    def test_runtime_drives_result_roster_rematch_and_both_settlements(self):
        runtime, devices = self.make_runtime()
        phases = {'one': 'battle', 'two': 'battle'}
        losses = [0]
        names = ('battle', 'result', 'lobby_ready', 'roster_detail', 'matching',
                 'series_result', 'lobby_unopened')
        actions = {'result': 'result_continue', 'lobby_ready': 'open_roster',
                   'roster_detail': 'close_roster', 'series_result': 'collect'}
        clicks = []

        def capture(owner):
            return np.full((720, 1280, 3), names.index(phases[owner]), np.uint8), self.now

        def read(image, at):
            phase = names[int(image[0, 0, 0])]
            points = {actions[phase]: (10, 10)} if phase in actions else {}
            if phase == 'lobby_ready': points['match'] = (1200, 630)
            return RoundView(phase, at, points, 0, losses[0],
                             'loss' if phase == 'result' else None)

        def tap(owner, x, y, control_name):
            clicks.append((owner, control_name))
            if control_name.endswith('_RESULT_CONTINUE'):
                phases[owner] = 'series_result' if losses[0] == 2 else 'lobby_ready'
            elif control_name.endswith('_OPEN_ROSTER'): phases[owner] = 'roster_detail'
            elif control_name.endswith('_CLOSE_ROSTER'): phases[owner] = 'lobby_ready'
            elif control_name.endswith('_MATCH'):
                phases.update({o: 'matching' for o in phases})
            elif control_name.endswith('_COLLECT'): phases[owner] = 'lobby_unopened'

        def sleep(seconds):
            self.now += max(seconds, .1)
            if set(phases.values()) == {'battle'}:
                losses[0] += 1
                phases.update({o: 'result' for o in phases})
            elif set(phases.values()) == {'matching'}:
                phases.update({o: 'battle' for o in phases})

        for owner, io in devices.items():
            io.capture.side_effect = lambda owner=owner: capture(owner)
            io.tap_point.side_effect = lambda x, y, control_name, owner=owner: tap(owner, x, y, control_name)
            runtime.visions[owner].read.side_effect = read
        runtime.sleep = sleep
        self.assertTrue(runtime.run(30))
        self.assertEqual(runtime.controller.losses, 2)
        self.assertEqual(set(phases.values()), {'lobby_unopened'})
        self.assertEqual(sum(name.endswith('_MATCH') for _, name in clicks), 1)
        self.assertEqual(sum(name.endswith('_COLLECT') for _, name in clicks), 2)
        self.assertFalse(any('START' in name for _, name in clicks))

    def test_checkpoint_restores_roster_and_result_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, devices = self.make_runtime(folder)
            image = np.full((720, 1280, 3), 50, np.uint8)
            for owner in devices: runtime._portrait_fallback(owner, image)
            runtime.prepare({})
            runtime.controller.step({o: RoundView('battle', self.now) for o in devices})
            self.now += 1
            result = {o: RoundView('result', self.now, {'result_continue': (10, 10)},
                                  outcome='loss') for o in devices}
            runtime.controller.step(result)
            runtime.save()
            restored, _ = self.make_runtime(folder)
            self.assertTrue(restored.restore())
            self.now += 1
            restored.controller.step({o: replace(v, captured=self.now) for o, v in result.items()})
            self.assertEqual(restored.controller.losses, 1)
            self.assertEqual(len(restored.lineups), 2)

    def test_failed_capture_is_reread_and_does_not_end_runtime(self):
        runtime, devices = self.make_runtime()
        for io in devices.values(): io.capture.side_effect = OSError('temporary screenshot timeout')
        self.assertTrue(all(v.phase == 'unknown' for v in runtime.observe().values()))
        for owner, io in devices.items():
            io.capture.side_effect = None
            io.capture.return_value = (np.zeros((720, 1280, 3), np.uint8), self.now)
            runtime.visions[owner].read.return_value = RoundView('battle', self.now)
        self.assertTrue(all(v.phase == 'battle' for v in runtime.observe().values()))

    def test_deployment_controller_resets_for_each_new_match(self):
        runtime, devices = self.make_runtime()
        runtime.lineups['one'] = ['a', 'b', 'c']
        runtime.portraits['one'] = {}
        with patch('tasks.DraftDuel.round_runtime.RoundDeploymentVision'), \
             patch('tasks.DraftDuel.round_runtime.DeploymentController') as controller:
            runtime.controller.match_id = 1
            runtime.deploy('one'); runtime.deploy('one')
            self.assertEqual(controller.call_count, 1)
            runtime.controller.match_id = 2
            runtime.deploy('one')
            self.assertEqual(controller.call_count, 2)


if __name__ == '__main__': unittest.main()
