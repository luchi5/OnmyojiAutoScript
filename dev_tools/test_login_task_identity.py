"""Offline login navigation regression; no account or device operations."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from module.exception import ScriptError
from tasks.Component.Costume.costume_base import CostumeBase
from tasks.GameUi.default_pages import handle_login_page
from tasks.Restart.login import LoginHandler
from tasks.Restart.script_task import ScriptTask


class ReadOnlyModel:
    def __init__(self, running_task):
        object.__setattr__(self, 'running_task', running_task)

    def __setattr__(self, name, value):
        raise AssertionError(f'Unexpected configuration write: {name}')


class NoDeviceAccess:
    def __getattr__(self, name):
        raise AssertionError(f'Unexpected device access: {name}')


def offline_config(running_task):
    costume = SimpleNamespace(costume_main_type=None, costume_carpbanner_type=None,
                              costume_battle_type=None, costume_battle_scene_type=None,
                              costume_shikigami_type=None)
    return SimpleNamespace(model=ReadOnlyModel(running_task),
                           global_game=SimpleNamespace(costume_config=costume),
                           restart=SimpleNamespace(login_character_config=
                                                   SimpleNamespace(character='offline')))


class LoginIdentityTests(unittest.TestCase):
    def setUp(self):
        # Keep the real constructor and check_costume -> get_task_name chain.
        for method in ('check_costume_main', 'check_costume_carpbanner',
                       'check_costume_battle', 'check_costume_battle_scene',
                       'check_costume_shikigami'):
            replacement = patch.object(CostumeBase, method)
            replacement.start()
            self.addCleanup(replacement.stop)
        original = LoginHandler.O_LOGIN_SPECIFIC_SERVE.keyword
        self.addCleanup(setattr, LoginHandler.O_LOGIN_SPECIFIC_SERVE, 'keyword', original)

    def test_navigation_login_accepts_outer_task_without_writing_scheduler_state(self):
        for name in ('WantedQuests', 'GoldYoukai', 'FrogBoss', 'Chess', ''):
            with self.subTest(task=name):
                shared = offline_config(name)
                outer = SimpleNamespace(config=shared, device=NoDeviceAccess())
                with patch.object(LoginHandler, 'app_handle_login', autospec=True,
                                  return_value=True) as login:
                    self.assertTrue(handle_login_page(outer))
                    helper = login.call_args.args[0]
                    self.assertIs(helper.config, shared)
                    self.assertEqual(helper.get_task_name(), 'Restart')
                self.assertEqual(shared.model.running_task, name)

    def test_scheduled_restart_still_rejects_wrong_task(self):
        with self.assertRaisesRegex(ScriptError,
                '^Task name mismatch: model=WantedQuests, path=Restart$'):
            ScriptTask(offline_config('WantedQuests'), NoDeviceAccess())

    def test_scheduled_restart_accepts_own_name_or_manual_start(self):
        for name in ('Restart', 'restart', ''):
            with self.subTest(task=name):
                self.assertEqual(ScriptTask(offline_config(name), NoDeviceAccess()).get_task_name(),
                                 'Restart')

    def test_login_helper_remains_valid_after_model_reload(self):
        shared = offline_config('WantedQuests')
        helper = LoginHandler(shared, NoDeviceAccess())
        shared.model = ReadOnlyModel('Chess')
        self.assertEqual(helper.get_task_name(), 'Restart')
        self.assertEqual(shared.model.running_task, 'Chess')


if __name__ == '__main__':
    unittest.main()
