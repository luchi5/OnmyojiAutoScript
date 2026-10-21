"""Battle entry must accept results when preparation/combat frames were missed."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dev_tools.test_demon_preset_transition import (
    Harness, GENERAL, Logger, extract, frame, prepare,
)


class GameStuckError(Exception):
    pass


def harness(frames):
    h = Harness(frames, demon=False)
    h.conf.lock_team_enable = True
    h.conf.preset_enable = False
    h.run_general_battle.__func__.__globals__['GameStuckError'] = GameStuckError
    h.battle_wait = Mock(return_value=True)
    return h


class FastBattleSettlementTests(unittest.TestCase):
    def test_direct_results_skip_prepare_preset_buff_and_green_mark(self):
        for marker in ('I_WIN', 'I_DE_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD'):
            with self.subTest(marker=marker):
                h = harness([frame(marker)])
                h.battle_wait.return_value = marker != 'I_FALSE'
                self.assertEqual(h.run_general_battle(h.conf), marker != 'I_FALSE')
                self.assertEqual(h.current_count, 1)
                self.assertEqual(h.screenshots, 1)
                self.assertEqual(h.clicks + h.buff_calls + h.ocr_calls + h.green_calls, [])
                h.battle_wait.assert_called_once_with(random_click_swipt_enable=False)

    def test_active_combat_keeps_normal_green_mark_and_result_handling(self):
        h = harness([frame('I_BATTLE_INFO')])
        self.assertTrue(h.run_general_battle(h.conf))
        self.assertEqual(h.green_calls, [(True, 'left1')])
        self.assertEqual(h.current_count, 1)
        h.battle_wait.assert_called_once()

    def test_prepare_can_jump_directly_to_reward_after_prepare_click(self):
        h = harness([prepare(), prepare(), frame('I_REWARD')])
        h.conf.lock_team_enable = False
        self.assertTrue(h.run_general_battle(h.conf))
        self.assertIn('I_PREPARE_HIGHLIGHT', h.clicks)
        self.assertEqual(h.green_calls, [])
        self.assertEqual(h.current_count, 1)
        h.battle_wait.assert_called_once()

    def test_reward_in_retry_window_is_accepted_without_recounting(self):
        h = harness([frame()] * 8 + [frame('I_REWARD')])
        original = h.screenshot
        def capture():
            h.clock.now += 1
            original()
        h.screenshot = capture
        self.assertTrue(h.run_general_battle(h.conf))
        self.assertEqual(h.current_count, 1)
        self.assertEqual(h.green_calls, [])
        h.battle_wait.assert_called_once()

    def test_unknown_page_still_times_out_without_claiming_success(self):
        h = harness([frame()])
        original = h.screenshot
        def capture():
            h.clock.now += 1
            original()
        h.screenshot = capture
        with self.assertRaisesRegex(GameStuckError, 'preparation'):
            h.run_general_battle(h.conf)
        h.battle_wait.assert_not_called()

    def test_existing_result_handler_consumes_rewards_and_preserves_failure(self):
        from tasks.Component.GeneralBattle import battle_wait as bw

        for marker in ('I_REWARD', 'I_REWARD_GOLD', 'I_FALSE', 'I_WIN'):
            with self.subTest(marker=marker):
                # RealmRaid uses the default strategy hooks, including their
                # real success/failure result bookkeeping and reward clicks.
                h = harness([frame(marker)])
                for name in ('I_GREED_GHOST', 'I_REWARD_GOLD_SNAKE_SKIN',
                             'I_END_FIX_1', 'I_END_FIX_2'):
                    setattr(h, name, name)
                h.screenshot = lambda: None
                def click(rule, **kwargs):
                    h.clicks.append(str(rule))
                    h.frame['visible'].discard(rule)
                    if rule == 'I_WIN':
                        h.frame['visible'].add('I_REWARD')
                    if rule == 'reward_continue':
                        h.frame['visible'].clear()
                    return True
                h.click = click
                h.ui_click_until_disappear = lambda rule: click(rule)
                pub = SimpleNamespace(per_battle=SimpleNamespace(success=None))
                pri = SimpleNamespace(
                    per_battle=bw.PerBattleSuccess('win_continue', 'reward_continue'),
                    options=bw.OptionSuccessDefault(),
                )
                namespace = dict(vars(bw), logger=Logger(),
                                 random=SimpleNamespace(random=lambda: 1),
                                 runtime=SimpleNamespace(hook_enabled_update=Mock()))
                methods = extract(Path(bw.__file__), 'BattleWait',
                                  ('_bw_success_default', '_bw_failure_default'), namespace)
                def wait(**kwargs):
                    for _ in range(4):
                        methods['_bw_failure_default'](h, pub, pri)
                        if pub.per_battle.success is None:
                            methods['_bw_success_default'](h, pub, pri)
                        if pub.per_battle.success is not None:
                            return pub.per_battle.success == bw.BattleResult.SUCCESS
                    self.fail('Existing settlement handler did not finish')
                h.battle_wait = wait
                self.assertEqual(h.run_general_battle(h.conf), marker != 'I_FALSE')
                self.assertEqual(h.frame['visible'], set())
                self.assertIn('I_FALSE' if marker == 'I_FALSE' else 'reward_continue', h.clicks)
                self.assertEqual(h.green_calls, [])
                self.assertEqual(h.current_count, 1)

    def test_saved_account12_reward_hands_off_without_preparation_retry(self):
        import cv2
        import numpy as np
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets

        root = Path(__file__).resolve().parents[1]
        files = list((root / 'log/error/1792585611726').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the repository')
        pixels = cv2.cvtColor(cv2.imdecode(np.fromfile(files[0], np.uint8),
                                         cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        h = harness([frame()])
        for name in dir(GeneralBattleAssets):
            if name.startswith('I_'):
                setattr(h, name, getattr(GeneralBattleAssets, name))
        h.screenshot = Mock()
        h.appear = lambda rule, **kwargs: rule.match(pixels, threshold=kwargs.get('threshold'))
        self.assertTrue(h.run_general_battle(h.conf))
        h.screenshot.assert_called_once()
        h.battle_wait.assert_called_once()
        self.assertEqual(h.green_calls, [])
        self.assertEqual(h.current_count, 1)


if __name__ == '__main__':
    unittest.main()
