"""Solo Orochi must return to the challenge page before starting another run.

Exercises production methods with synthetic frames; never opens a device.
"""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dev_tools.test_demon_preset_transition import extract, frame, Logger, Timer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/Orochi/script_task.py'


class GameStuckError(Exception):
    pass


class SettlementHarness:
    def __init__(self, frames):
        self.frames = frames
        self.frame = frames[0]
        self.screenshots = 0
        self.clicks = []
        self.clock = SimpleNamespace(now=0)
        self.current_count = 0
        self.limit_count = 1
        self.limit_time = timedelta(hours=1)
        self.start_time = datetime.now()
        self.config = SimpleNamespace(orochi=SimpleNamespace(
            orochi_config=SimpleNamespace(layer='伍层'),
            general_battle_config=SimpleNamespace(lock_team_enable=False),
        ))
        self.goto_page = Mock()
        self.check_layer = Mock()
        self.check_lock = Mock()
        self.run_general_battle = Mock(side_effect=self.finish_battle_early)
        for name in ('I_OROCHI_FIRE', 'I_FORM_TEAM', 'I_REWARD', 'I_REWARD_GOLD',
                     'I_GREED_GHOST', 'I_PET_PRESENT', 'I_WIN', 'I_FALSE',
                     'I_UI_BACK_YELLOW', 'C_WIN_3', 'C_REWARD_1',
                     'C_REWARD_2', 'C_REWARD_3'):
            setattr(self, name, name)
        namespace = dict(
            logger=Logger(), Timer=lambda limit: Timer(self.clock, limit),
            sleep=self.sleep, random=SimpleNamespace(choice=lambda items: items[0]),
            GameStuckError=GameStuckError, datetime=datetime,
            page_orochi='orochi', page_main='main',
        )
        for name, method in extract(SOURCE, 'ScriptTask',
                                    ('run_alone', '_finish_solo_settlement'),
                                    namespace).items():
            setattr(self, name, method.__get__(self))

    def finish_battle_early(self, **kwargs):
        self.current_count += 1
        return True

    def sleep(self, seconds):
        self.clock.now += seconds

    def screenshot(self):
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        self.clock.now += 1
        if self.screenshots > 100:
            raise AssertionError('Settlement never finished or timed out')

    def appear(self, marker, **kwargs):
        if 'pixels' in self.frame:
            return self.image_rules[marker].match(self.frame['pixels'])
        return marker in self.frame['visible']

    def click(self, marker, **kwargs):
        self.clicks.append((marker, self.screenshots))
        return True

    def appear_then_click(self, marker, action=None, **kwargs):
        return self.appear(marker) and self.click(action or marker, **kwargs)


class OrochiSettlementTests(unittest.TestCase):
    def test_missing_reward_during_transition_is_not_completion(self):
        h = SettlementHarness([
            frame(), frame('I_REWARD'), frame(), frame('I_REWARD_GOLD'),
            frame('I_OROCHI_FIRE'), frame(),
            frame('I_OROCHI_FIRE'), frame('I_OROCHI_FIRE'),
        ])
        self.assertTrue(h._finish_solo_settlement())
        self.assertEqual(h.screenshots, 8)
        self.assertEqual(h.clicks, [('C_REWARD_1', 2), ('C_REWARD_1', 4)])

    def test_victory_failure_and_pet_overlays_are_consumed(self):
        h = SettlementHarness([
            frame('I_WIN'), frame('I_FALSE'), frame('I_PET_PRESENT'),
            frame('I_GREED_GHOST'), frame('I_REWARD'),
            frame('I_OROCHI_FIRE'), frame('I_OROCHI_FIRE'),
        ])
        self.assertTrue(h._finish_solo_settlement())
        self.assertEqual(h.clicks, [
            ('C_WIN_3', 1), ('I_FALSE', 2), ('C_WIN_3', 3),
            ('C_REWARD_1', 4), ('C_REWARD_1', 5),
        ])

    def test_challenge_page_is_confirmed_without_clicking_next_battle(self):
        h = SettlementHarness([frame('I_OROCHI_FIRE')] * 2)
        self.assertTrue(h._finish_solo_settlement())
        self.assertEqual(h.screenshots, 2)
        self.assertEqual(h.clicks, [])

    def test_unknown_page_or_single_challenge_frame_times_out(self):
        for frames in ([frame()], [frame('I_OROCHI_FIRE'), frame()]):
            with self.subTest(frames=frames):
                h = SettlementHarness(frames)
                with self.assertRaisesRegex(GameStuckError, 'challenge page not confirmed'):
                    h._finish_solo_settlement()
                self.assertEqual(h.clicks, [])

    def test_persistent_reward_page_does_not_claim_completion(self):
        h = SettlementHarness([frame('I_REWARD')])
        with self.assertRaisesRegex(GameStuckError, 'challenge page not confirmed'):
            h._finish_solo_settlement()
        self.assertTrue(h.clicks)

    def test_solo_runner_confirms_page_after_battle_handler_returns_early(self):
        for result in (True, False):
            with self.subTest(result=result):
                h = SettlementHarness([
                    frame('I_OROCHI_FIRE'), frame(), frame(), frame('I_REWARD'),
                    frame('I_OROCHI_FIRE'), frame('I_OROCHI_FIRE'),
                    frame('I_OROCHI_FIRE'), frame(),
                ])
                def finish(**kwargs):
                    h.current_count += 1
                    return result
                h.run_general_battle.side_effect = finish
                h.run_alone()
                h.run_general_battle.assert_called_once()
                self.assertIn(('C_REWARD_1', 4), h.clicks)
                self.assertEqual(h.current_count, 1)
                self.assertEqual(h.screenshots, 8)
                self.assertEqual(h.goto_page.call_args.args, ('main',))

    def test_solo_runner_recovers_leftover_reward_even_at_count_limit(self):
        h = SettlementHarness([
            frame('I_REWARD'), frame('I_REWARD'), frame(),
            frame('I_OROCHI_FIRE'), frame('I_OROCHI_FIRE'),
            frame('I_OROCHI_FIRE'), frame(),
        ])
        h.current_count = 1
        h.run_alone()
        h.run_general_battle.assert_not_called()
        self.assertEqual(h.clicks, [('C_REWARD_1', 2)])
        self.assertEqual(h.screenshots, 7)

    def test_account08_actual_reward_frame_is_clicked_before_page_confirmation(self):
        files = list((ROOT / 'log/error/1792669094134').glob('*.png'))
        if not files:
            self.skipTest('Private incident screenshot is not distributed')
        import cv2
        import numpy as np
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
        from tasks.Orochi.assets import OrochiAssets

        pixels = cv2.cvtColor(cv2.imdecode(np.frombuffer(files[0].read_bytes(), np.uint8),
                                         cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        h = SettlementHarness([
            dict(pixels=pixels), frame(),
            frame('I_OROCHI_FIRE'), frame('I_OROCHI_FIRE'),
        ])
        h.image_rules = {
            name: getattr(OrochiAssets, name, getattr(GeneralBattleAssets, name, None))
            for name in ('I_OROCHI_FIRE', 'I_REWARD', 'I_REWARD_GOLD',
                         'I_GREED_GHOST', 'I_PET_PRESENT', 'I_WIN', 'I_FALSE')
        }
        self.assertTrue(h._finish_solo_settlement())
        self.assertEqual(h.clicks, [('C_REWARD_1', 1)])
        self.assertEqual(h.screenshots, 4)


if __name__ == '__main__':
    unittest.main()
