"""Replay real Chess result polling at deadline boundaries without a device."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'tasks/Chess/script_task.py'


class ResultTimeout(Exception):
    pass


class ResultDeadlineTests(unittest.TestCase):
    def replay(self, *, result_at=None, recovered_at=None, capture_delay=0.,
               rank=4, initial_in_game=False):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == 'ChessLegacyScriptTask')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == '_finish_chess_game_after_markers_missing')
        clock = SimpleNamespace(now=0.)
        def sleep(seconds):
            clock.now += seconds
        namespace = {'time': SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep),
                     'logger': Mock(), 'GameStuckError': ResultTimeout}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(SOURCE), 'exec'), namespace)
        h = SimpleNamespace(GAME_END_CONFIRM_FRAMES=3, SLOW_POLL_INTERVAL=1.,
                            GAME_OVER_WAIT_TIMEOUT=60., frame='in_game' if initial_in_game else 'unknown',
                            device=SimpleNamespace(stuck_record_clear=Mock()),
                            I_GAME_OVER_1='end', I_GAME_OVER_2='result',
                            _read_game_rank=Mock(return_value=(rank, '第四名' if rank else '')))
        captures = []
        def screenshot():
            clock.now += capture_delay
            h.frame = ('result' if result_at is not None and clock.now >= result_at else
                       'in_game' if recovered_at is not None and clock.now >= recovered_at else 'unknown')
            captures.append((clock.now, h.frame))
            if len(captures) > 100:
                raise AssertionError('Polling did not stay bounded')
        h.screenshot = Mock(side_effect=screenshot)
        h._is_in_chess_game = lambda: h.frame == 'in_game'
        h.appear = Mock(side_effect=lambda rule: h.frame == rule)
        return h, clock, captures, lambda: namespace[method.name](h, 'round_24')

    def test_result_in_last_frame_at_deadline_is_not_discarded(self):
        h, clock, captures, run = self.replay(result_at=64.)
        self.assertTrue(run())
        self.assertEqual(clock.now, 64.)
        self.assertEqual(captures[-1], (64., 'result'))
        self.assertEqual(h._last_game_rank, 4)

    def test_capture_finishing_after_deadline_still_gets_checked(self):
        h, clock, _, run = self.replay(result_at=64.5, capture_delay=.025)
        self.assertTrue(run())
        self.assertGreater(clock.now, 64.05)
        self.assertEqual(h._last_game_rank, 4)

    def test_result_before_deadline_remains_supported(self):
        h, clock, _, run = self.replay(result_at=10.)
        self.assertTrue(run())
        self.assertEqual(clock.now, 10.)
        h._read_game_rank.assert_called_once()

    def test_absent_result_still_times_out(self):
        h, clock, _, run = self.replay()
        with self.assertRaises(ResultTimeout):
            run()
        self.assertEqual(clock.now, 64.)
        h._read_game_rank.assert_not_called()

    def test_in_game_recovery_at_deadline_is_not_a_timeout(self):
        h, _, _, run = self.replay(recovered_at=64.)
        self.assertFalse(run())
        h._read_game_rank.assert_not_called()

    def test_initial_in_game_frame_skips_result_wait(self):
        h, clock, _, run = self.replay(initial_in_game=True)
        self.assertFalse(run())
        self.assertEqual(clock.now, 0.)
        h.screenshot.assert_not_called()

    def test_unreadable_rank_remains_unknown(self):
        h, _, _, run = self.replay(result_at=64., rank=None)
        self.assertTrue(run())
        self.assertIsNone(h._last_game_rank)


if __name__ == '__main__':
    unittest.main()
