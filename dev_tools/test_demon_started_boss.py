"""Offline checks for missed preparation; no device or account imports."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from dev_tools.test_demon_preset_transition import DEMON, extract, Logger


class StartedBossTests(unittest.TestCase):
    def harness(self, elapsed=5, active=True, best=False):
        convert = Mock(return_value=SimpleNamespace(random_click_swipt_enable=False))
        method = extract(DEMON, 'ScriptTask', ('_take_over_started_boss',), {
            'time': SimpleNamespace(monotonic=lambda: elapsed),
            'logger': Logger(), 'convert_to_general_battle_config': convert,
            'GeneralBattleConfig': lambda: SimpleNamespace(random_click_swipt_enable=True),
        })['_take_over_started_boss']
        h = SimpleNamespace(
            is_in_real_battle=Mock(return_value=active), battle_wait=Mock(return_value=False),
            current_count=9, best_demon_enable=best, boss_type='namazu',
            conf=SimpleNamespace(best_demon_battle_config='best', demon_battle_config='normal'))
        return h, convert, lambda switched=False: method(h, 0, switched)

    def test_waits_five_seconds_before_probe(self):
        h, _, run = self.harness(elapsed=4.9)
        self.assertFalse(run())
        h.is_in_real_battle.assert_not_called()
        h.battle_wait.assert_not_called()
        self.assertEqual(h.current_count, 9)

    def test_unknown_screen_keeps_waiting(self):
        h, _, run = self.harness(active=False)
        self.assertFalse(run())
        h.battle_wait.assert_not_called()
        self.assertEqual(h.current_count, 9)

    def test_active_battle_hands_off_even_when_battle_result_is_failure(self):
        for best in (False, True):
            with self.subTest(best=best):
                h, convert, run = self.harness(best=best)
                self.assertTrue(run())
                h.is_in_real_battle.assert_called_once_with(False)
                h.battle_wait.assert_called_once_with(random_click_swipt_enable=False)
                self.assertEqual(h.current_count, 1)
                kwargs = {'best_demon_battle_conf': 'best'} if best else {'demon_battle_conf': 'normal'}
                convert.assert_called_once_with('namazu', **kwargs)

    def test_retry_preserves_existing_counter_and_default_policy(self):
        h, convert, run = self.harness()
        self.assertTrue(run(switched=True))
        self.assertEqual(h.current_count, 10)
        convert.assert_not_called()
        h.battle_wait.assert_called_once_with(random_click_swipt_enable=True)


if __name__ == '__main__':
    unittest.main()
