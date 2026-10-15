"""Dokan must not open records unless a soul switch was requested."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
import unittest
from dev_tools.test_demon_preset_transition import extract, Logger

ROOT = Path(__file__).resolve().parents[1]


def run(enable=False, by_name=False, done=False):
    soul = SimpleNamespace(enable=enable, enable_switch_by_name=by_name,
                           switch_group_team='1,2', group_name='group', team_name='team')
    h = SimpleNamespace(switch_soul_done=done,
                        config=SimpleNamespace(dokan=SimpleNamespace(switch_soul_config=soul)),
                        I_RYOU_DOKAN_SHIKIGAMI='records',
                        _enter_dokan_soul_records=Mock(),
                        ui_click_until_disappear=Mock(),run_switch_soul=Mock(),
                        run_switch_soul_by_name=Mock())
    method = extract(ROOT/'tasks/Dokan/script_task.py','ScriptTask',
                     ('switch_soul_in_dokan',),{'logger':Logger(),
                     'DokanSoulSwitchInterrupted': RuntimeError})['switch_soul_in_dokan']
    fake = SimpleNamespace(GameUiAssets=SimpleNamespace(I_BACK_Y='back'))
    with patch.dict(sys.modules,{'tasks.GameUi.assets':fake}):
        method(h)
    return h


class SoulGateTests(unittest.TestCase):
    def test_disabled_does_not_open_records(self):
        h = run()
        h.ui_click_until_disappear.assert_not_called()
        h._enter_dokan_soul_records.assert_not_called()
        h.run_switch_soul.assert_not_called()
        h.run_switch_soul_by_name.assert_not_called()
        self.assertTrue(h.switch_soul_done)

    def test_already_done_does_not_open_records_again(self):
        h = run(enable=True,done=True)
        h.ui_click_until_disappear.assert_not_called()
        h._enter_dokan_soul_records.assert_not_called()

    def test_numbered_switch_is_preserved(self):
        h = run(enable=True)
        h.run_switch_soul.assert_called_once_with('1,2')
        h.run_switch_soul_by_name.assert_not_called()
        h._enter_dokan_soul_records.assert_called_once_with()
        h.ui_click_until_disappear.assert_called_once_with('back', interval=2)

    def test_named_switch_is_preserved(self):
        h = run(by_name=True)
        h.run_switch_soul_by_name.assert_called_once_with('group','team')
        h.run_switch_soul.assert_not_called()
        h._enter_dokan_soul_records.assert_called_once_with()
        h.ui_click_until_disappear.assert_called_once_with('back', interval=2)


if __name__ == '__main__':
    unittest.main()
