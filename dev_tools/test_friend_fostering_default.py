"""Offline regression: fostering must not force a max-level-only rarity list."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from enum import Enum
import sys
import unittest

from dev_tools.test_bondling_battle_entry import extract_methods


class FosteringDefaultTests(unittest.TestCase):
    def run_case(self, markers, entered=True, rarity='默认/全部', order=7):
        root = Path(__file__).resolve().parents[1]
        ns = {'SelectFriendList': SimpleNamespace(SAME_SERVER='same'),
              'ShikigamiClass': Enum('Rarity', {'N':'N','R':'R'}, type=str), 'logger': Mock()}
        method = extract_methods(root / 'tasks/KekkaiUtilize/script_task.py',
                                 'ScriptTask', ('run_utilize',), ns)['run_utilize']
        h = SimpleNamespace(first_utilize=False, utilize_add_count=0,
                            I_U_ENTER_REALM='enter', I_U_ADD_1='slot1', I_U_ADD_2='slot2',
                            switch_friend_list=Mock(), screenshot=Mock(),
                            in_utilize_list=lambda: True,
                            _select_with_friend_fallback=lambda friend: True,
                            _enter_friend_growth=lambda: entered,
                            in_shikigami_growth=lambda: True,
                            appear=lambda marker: marker in markers,
                            save_image=Mock(), set_shikigami=Mock())
        h.switch_rarity = Mock()
        module = SimpleNamespace(switch_friend_growth_class=h.switch_rarity)
        with patch.dict(sys.modules, {'tasks.Component.ReplaceShikigami.friend_growth_selection': module}):
            result = method(h, friend='same', shikigami_class=rarity, shikigami_order=order)
        return h, result

    def test_default_first_for_either_available_slot_without_rarity_assets(self):
        for slot in ('slot1', 'slot2'):
            with self.subTest(slot=slot):
                h, result = self.run_case({'enter', slot})
                self.assertTrue(result)
                h.set_shikigami.assert_called_once_with(1, slot)
                h.switch_rarity.assert_not_called()

    def test_explicit_rarity_keeps_configured_position(self):
        for rarity in ('N', 'R'):
            h, result = self.run_case({'enter', 'slot1'}, rarity=rarity, order=4)
            self.assertTrue(result)
            self.assertEqual(h.switch_rarity.call_args.args[1].value, rarity)
            h.set_shikigami.assert_called_once_with(4, 'slot1')

    def test_failed_entry_never_selects_shikigami(self):
        h, result = self.run_case({'enter', 'slot1'}, entered=False)
        self.assertFalse(result)
        h.set_shikigami.assert_not_called()

    def test_slot_taken_by_someone_else_never_selects_shikigami(self):
        h, result = self.run_case({'enter'})
        h.set_shikigami.assert_not_called()
        h.save_image.assert_called_once()


if __name__ == '__main__':
    unittest.main()
