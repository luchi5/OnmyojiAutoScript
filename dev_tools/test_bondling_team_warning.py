import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest

from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer


class TeamWarningTests(unittest.TestCase):
    def run_team(self, stuck=False):
        source = Path(__file__).resolve().parents[1] / 'tasks/BondlingFairyland/script_task.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == 'create_bond_team')
        method.args.args = [ast.arg(arg='self')]
        clock = FakeClock()
        state = ['unchecked']
        clicks = []
        def appear(marker, **kwargs):
            if marker == 'warning':
                return state[0] != 'room'
            if marker == 'unchecked':
                return state[0] == 'unchecked'
            if marker == 'room':
                return True  # Background room must not outrank the warning.
            return False
        def click(marker, **kwargs):
            clicks.append(marker)
            if not stuck:
                state[0] = 'checked' if marker == 'unchecked' else 'room'
            return True
        task = NS(screenshot=clock.advance, appear=appear, appear_then_click=click,
                  I_LOW_PLATE_CONTINUE='warning', I_LOW_PLATE_UNCHECKED='unchecked',
                  I_GI_IN_ROOM='room', check_and_invite=Mock())
        ns = dict(Timer=lambda t: FakeTimer(clock, t), GameStuckError=RuntimeError)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(source), 'exec'), ns)
        self.clicks = clicks
        result = ns['create_bond_team'](task)
        task.check_and_invite.assert_not_called()
        return result

    def test_warning_then_continue_then_room(self):
        self.assertTrue(self.run_team())
        self.assertEqual(self.clicks, ['unchecked', 'warning'])

    def test_stuck_warning_times_out_without_help_or_back(self):
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.run_team(stuck=True)
        self.assertEqual(set(self.clicks), {'unchecked'})


if __name__ == '__main__':
    unittest.main()
