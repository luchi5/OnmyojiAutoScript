import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
import cv2
from dev_tools.test_compact_realm_harvest import RuleImage, ROOT


class SignTests(unittest.TestCase):
    def run_handler(self, enabled=True, stuck=False):
        source = ROOT/'tasks/Restart/sign_popup.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        now = [0]
        def sleep(s): now[0] += s
        env = dict(time=NS(monotonic=lambda:now[0], sleep=sleep), RuleImage=RuleImage,
                   GameStuckError=RuntimeError, logger=Mock())
        exec(compile(tree,str(source),'exec'),env)
        frame = cv2.cvtColor(cv2.imread(str(ROOT/'dev_tools/fixtures/daily_sign_account14.png')),cv2.COLOR_BGR2RGB)
        task = NS(config=NS(restart=NS(harvest_config=NS(enable=True,enable_sign=enabled))),device=Mock())
        task.appear = lambda rule: rule.match(frame)
        if not stuck:
            task.device.screenshot.side_effect = lambda:frame.fill(0)
        self.task = task
        return env['handle_sign_popup'](task)

    def test_real_popup_sign_and_verify_disappearance(self):
        self.assertTrue(self.run_handler())
        self.task.device.click.assert_called_once_with(x=640,y=285,control_name='daily_sign_popup')

    def test_disabled_only_dismisses(self):
        self.assertTrue(self.run_handler(False))
        self.task.device.click.assert_called_once_with(x=1040,y=550,control_name='daily_sign_popup')

    def test_stuck_popup_never_falls_through(self):
        with self.assertRaises(RuntimeError): self.run_handler(stuck=True)
        self.assertEqual(self.task.device.click.call_count,3)

    def test_all_sign_clicks_are_gated_by_their_switch(self):
        tree = ast.parse((ROOT/'tasks/Restart/login.py').read_text(encoding='utf-8'))
        checked = 0
        for node in ast.walk(tree):
            if isinstance(node,ast.If) and 'self.appear_then_click(self.I_HARVEST_SIGN' in ast.unparse(node.test):
                text = ast.unparse(node.test)
                key = 'enable_sign_999' if 'I_HARVEST_SIGN_999' in text else 'enable_sign'
                self.assertTrue(text.startswith('self.config.restart.harvest_config.'+key+' and '),text)
                checked += 1
        self.assertEqual(checked,5)

if __name__ == '__main__': unittest.main()
