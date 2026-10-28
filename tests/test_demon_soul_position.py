"""Position mode must bypass name lookup and preserve boss/enable selection."""
import unittest
from unittest.mock import Mock

from dev_tools.test_demon_soul_selection import DemonHarness, TaskEnd
from tasks.DemonEncounter.config import DemonConfig, BestDemonConfig


class PositionTests(unittest.TestCase):
    def test_all_bosses_use_numeric_position_without_name_search(self):
        for day in range(7):
            for extreme in (False, True):
                task = DemonHarness(day=day, extreme=extreme, extreme_enabled=True)
                for conf in (task.conf.demon_soul_config, task.conf.best_demon_soul_config):
                    conf.selection_mode = 'position'
                    conf.position = '4,1' if extreme else '1,3'
                task.run_switch_soul = Mock()
                task.run_switch_soul_by_name = Mock()
                task.checkout_soul()
                task.run_switch_soul.assert_called_once_with((4, 1) if extreme else (1, 3))
                task.run_switch_soul_by_name.assert_not_called()

    def test_extreme_disabled_falls_back_to_normal_position(self):
        task = DemonHarness(extreme=True, extreme_enabled=False)
        task.conf.demon_soul_config.selection_mode = 'position'
        task.conf.demon_soul_config.position = '1，2'
        self.assertEqual(task.resolve_soul_target(), (1, 2))

    def test_invalid_position_stays_pending_without_switching(self):
        for value in ('', None, '-1,-1', '0,2', '8,1', '1,5', '1,长线通用', '1,2,3'):
            task = DemonHarness()
            task.conf.demon_soul_config.selection_mode = 'position'
            task.conf.demon_soul_config.position = value
            task.run_switch_soul = Mock()
            with self.assertRaises(TaskEnd):
                task.checkout_soul()
            task.run_switch_soul.assert_not_called()
            self.assertTrue(task.delays)

    def test_disabled_switches_stay_disabled(self):
        task = DemonHarness(normal_enabled=False, extreme_enabled=False)
        task.conf.demon_soul_config.selection_mode = 'position'
        task.conf.demon_soul_config.position = '1,2'
        self.assertIsNone(task.resolve_soul_target())

    def test_old_configs_keep_name_mode_and_schema_exposes_choice(self):
        for cls in (DemonConfig, BestDemonConfig):
            self.assertEqual(cls().selection_mode, 'name')
            schema = cls.model_json_schema()
            self.assertIn('position', schema['properties'])
            self.assertEqual(schema['$defs']['SoulSelectionMode']['enum'], ['name', 'position'])


if __name__ == '__main__':
    unittest.main()
