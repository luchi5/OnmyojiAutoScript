"""Offline settlement regressions; no emulator or backend is initialized."""
import random
import unittest
from types import SimpleNamespace

from dev_tools.test_bondling_battle_entry import (
    BATTLE_FILE, Harness, FakeTimer, extract_methods, frame,
)


def settlement(frames):
    h = Harness(frames)
    h.config.bondling_fairyland.bondling_config.bondling_mode = 'low'
    for name in ('C_CAP_SUCCESS', 'C_REWARD_1', 'C_REWARD_2', 'C_REWARD_3'):
        setattr(h, name, name)
    method = extract_methods(BATTLE_FILE, 'BondlingBattle', ('catch_battle_wait',), {
        'Timer': lambda limit, count=0: FakeTimer(h.clock, limit, count),
        'logger': h.logger, 'random': random,
        'BondlingMode': SimpleNamespace(MODE3='medium', MODE4='high'),
    })['catch_battle_wait']
    return h, lambda: method(h, False)


class SettlementReturnTests(unittest.TestCase):
    def test_win_then_direct_challenge_return_exits_without_more_clicks(self):
        h, run = settlement([frame('I_WIN'), frame('I_BALL_FIRE')])
        self.assertFalse(run())  # Battle victory alone does not prove capture.
        self.assertEqual([click[0] for click in h.clicks], ['I_WIN'])
        self.assertLess(h.screenshots, 10)

    def test_capture_success_is_preserved_on_direct_return(self):
        h, run = settlement([frame('I_CAP_SUCCESS'), frame('I_BALL_FIRE')])
        self.assertTrue(run())

    def test_normal_reward_path_still_completes(self):
        h, run = settlement([frame('I_WIN'), frame('I_REWARD'),
                             frame('I_REWARD'), frame('I_BALL_FIRE')])
        self.assertFalse(run())
        self.assertIn('I_REWARD', [click[0] for click in h.clicks])

    def test_transient_return_does_not_end_battle(self):
        h, run = settlement([frame('I_BALL_FIRE')] * 2 + [frame('I_EXIT')] * 4
                            + [frame('I_CAP_SUCCESS'), frame('I_BALL_FIRE')])
        self.assertTrue(run())
        self.assertGreater(h.screenshots, 7)

    def test_results_preparation_and_battle_override_background_challenge(self):
        for marker in ('I_WIN', 'I_BUFF', 'I_PREPARE_HIGHLIGHT', 'I_EXIT'):
            with self.subTest(marker=marker):
                h, run = settlement([frame(marker, 'I_BALL_FIRE')] * 8
                                    + [frame('I_CAP_SUCCESS'), frame('I_BALL_FIRE')])
                self.assertTrue(run())
                self.assertGreater(h.screenshots, 9)

    def test_unknown_screen_does_not_claim_completion(self):
        h, run = settlement([frame()])
        with self.assertRaises(AssertionError):
            run()


if __name__ == '__main__':
    unittest.main()
