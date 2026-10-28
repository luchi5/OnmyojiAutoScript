import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import cv2

from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.GlobalGame.config_emergency import FriendInvitation
from tasks.GlobalGame.wanted_invitation import is_new_invitation, choose_action, handle_new_invitation


class WantedInvitationTests(unittest.TestCase):
    def test_accept_icon_alone_identifies_popup_without_other_features(self):
        image = self.frame('incoming_stamina_second')
        image[35:110, 500:780] = 0  # Title.
        image[65:130, 770:835] = 0  # Close.
        image[550:630, 825:1020] = 0  # Reject.
        image[470:530, 920:1010] = 0  # Accept text, leaving only its green icon.
        self.assertTrue(is_new_invitation(image))
        image[455:550, 825:925] = 0
        self.assertFalse(is_new_invitation(image))

    def test_both_real_stamina_popups_are_never_classified_as_jade(self):
        from tasks.GlobalGame.wanted_invitation import rules
        for name in ('incoming_stamina', 'incoming_stamina_second'):
            image = self.frame(name)
            self.assertTrue(is_new_invitation(image))
            self.assertTrue(rules()['stamina'].match(image))
            self.assertFalse(rules()['jade'].match(image))
            self.assertEqual(choose_action(self.task(FriendInvitation.JADE_AND_FOOD), image), 'close')

    def test_verified_legacy_jade_icon_in_new_reward_row_is_accepted(self):
        # Synthetic composition tests placement only; a real new jade popup is still required.
        image = self.frame('incoming_stamina')
        image[490:590, 530:760] = 0
        icon = GlobalGameAssets.I_G_JADE.image
        h, w = icon.shape[:2]
        image[517:517+h, 677:677+w] = icon
        for policy in (FriendInvitation.ONLY_JADE, FriendInvitation.JADE_AND_FOOD):
            self.assertEqual(choose_action(self.task(policy), image), 'accept')

    def test_stamina_dismissal_never_schedules_wanted_combat(self):
        task = self.task(FriendInvitation.JADE_AND_FOOD)
        task.device = SimpleNamespace(image=self.frame('incoming_stamina_second'), detect_record='original')
        task.set_next_run = Mock()
        task.click = Mock()
        task.device.screenshot = lambda: setattr(task.device, 'image', self.frame('explore'))
        self.assertTrue(handle_new_invitation(task))
        self.assertEqual(task.click.call_args.args[0].name, 'CLOSE')
        task.set_next_run.assert_not_called()

    def frame(self, name):
        return cv2.cvtColor(cv2.imread(f'tests/fixtures/wanted_202610/{name}.png'), cv2.COLOR_BGR2RGB)

    def task(self, policy):
        class Task(GlobalGameAssets):
            pass
        task = Task()
        task.config = SimpleNamespace(global_game=SimpleNamespace(emergency=SimpleNamespace(friend_invitation=policy)))
        return task

    def test_popup_signature_does_not_match_home_or_route_sheet(self):
        self.assertTrue(is_new_invitation(self.frame('incoming_stamina')))
        for name in ('home', 'traced', 'detail', 'explore'):
            self.assertFalse(is_new_invitation(self.frame(name)))

    def test_policies_and_unknown_reward(self):
        image = self.frame('incoming_stamina')
        for policy, action in ((FriendInvitation.ACCEPT, 'accept'), (FriendInvitation.REJECT, 'reject'),
                               (FriendInvitation.IGNORE, 'close'), (FriendInvitation.ONLY_JADE, 'close'),
                               (FriendInvitation.JADE_AND_FOOD, 'close')):
            self.assertEqual(choose_action(self.task(policy), image), action)
        image[490:590, 530:760] = 0
        for policy in (FriendInvitation.ONLY_JADE, FriendInvitation.JADE_AND_FOOD):
            self.assertEqual(choose_action(self.task(policy), image), 'close')

    def test_accept_schedules_only_after_new_frame_confirms_dismissal(self):
        task = self.task(FriendInvitation.ACCEPT)
        task.device = SimpleNamespace(image=self.frame('incoming_stamina'), detect_record='original')
        task.set_next_run = Mock()
        task.click = Mock(side_effect=lambda *a, **k: task.set_next_run.assert_not_called())
        task.device.screenshot = lambda: setattr(task.device, 'image', self.frame('explore'))
        self.assertTrue(handle_new_invitation(task))
        task.click.assert_called_once()
        task.set_next_run.assert_called_once()
        self.assertEqual(task.device.detect_record, 'original')

    def test_unknown_screen_leaves_legacy_handler_in_control(self):
        task = self.task(FriendInvitation.ACCEPT)
        task.device = SimpleNamespace(image=self.frame('explore'))
        task.click = Mock()
        self.assertIsNone(handle_new_invitation(task))
        task.click.assert_not_called()
