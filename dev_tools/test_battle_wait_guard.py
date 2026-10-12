"""Slow automatic battles keep the long-wait guard after input clears it."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dev_tools.test_bondling_battle_entry import extract_methods


ROOT = Path(__file__).resolve().parents[1]


class GameStuckError(Exception):
    pass


class BattleWaitGuardTests(unittest.TestCase):
    def task(self, real_battle=True, long_timeout=False):
        device = SimpleNamespace(
            detect_record=set(),
            stuck_timer=SimpleNamespace(reached=lambda: True),
            stuck_timer_long=SimpleNamespace(reached=lambda: long_timeout),
            stuck_long_wait_list=['BATTLE_STATUS_S'],
            stuck_record_clear=Mock(), app_is_running=lambda: True,
        )
        device.stuck_record_add = Mock(side_effect=device.detect_record.add)
        task = SimpleNamespace(device=device, is_in_real_battle=Mock(return_value=real_battle))
        namespace = {
            'PublicContext': object, 'PrivateContext': object,
            'HookSignal': SimpleNamespace(CONTINUE='continue'),
        }
        idle = extract_methods(ROOT / 'tasks/Component/GeneralBattle/battle_wait.py',
                               'BattleWait', ('_bw_idle_default',), namespace)['_bw_idle_default']
        check = extract_methods(
            ROOT / 'module/device/device.py', 'Device', ('stuck_record_check',),
            {'logger': SimpleNamespace(warning=lambda *args: None),
             'GameStuckError': GameStuckError, 'GameNotRunningError': RuntimeError},
        )['stuck_record_check']
        return task, lambda: idle(task, None, None), lambda: check(device)

    def test_old_missing_marker_causes_sixty_second_false_alarm(self):
        _, _, check = self.task()
        with self.assertRaises(GameStuckError):
            check()

    def test_real_battle_survives_sixty_seconds_after_random_input(self):
        task, idle, check = self.task()
        idle()
        self.assertFalse(check())
        task.device.stuck_record_clear.assert_not_called()
        task.device.detect_record.clear()  # Preparation/random input clears it.
        idle()
        self.assertFalse(check())
        self.assertEqual(task.device.stuck_record_add.call_count, 2)

    def test_existing_marker_is_not_readded_each_frame(self):
        task, idle, _ = self.task()
        idle()
        idle()
        task.device.stuck_record_add.assert_called_once_with('BATTLE_STATUS_S')

    def test_nonbattle_page_still_uses_normal_timeout(self):
        task, idle, check = self.task(real_battle=False)
        idle()
        task.device.stuck_record_add.assert_not_called()
        with self.assertRaises(GameStuckError):
            check()

    def test_original_long_timeout_is_not_reset_or_bypassed(self):
        _, idle, check = self.task(long_timeout=True)
        idle()
        with self.assertRaises(GameStuckError):
            check()


if __name__ == '__main__':
    unittest.main()
