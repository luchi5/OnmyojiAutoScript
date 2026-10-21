"""Offline tests: only temporary account files and injected status observations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tasks.DraftDuel.preflight import (
    AccountActivity, AdmissionOffer, PreflightError, check_pair_activity,
    check_pair_admission, load_pair_config,
)


class DraftDuelPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.configs = {
            'alpha': self.make_account('beta', '127.0.0.1:16384', 'MuMu-alpha'),
            'beta': self.make_account('alpha', '127.0.0.1:16416', 'MuMu-beta'),
        }
        self.write_accounts()

    @staticmethod
    def make_account(partner, serial, emulator):
        return {
            'script': {'device': {'serial': serial, 'emulatorinfo_name': emulator}},
            'draft_duel': {'draft_duel_config': {
                'partner_config': partner,
                'user_status': 'leader' if partner == 'beta' else 'member',
                'teammate_name': partner,
            }},
        }

    def write_accounts(self):
        for name, contents in self.configs.items():
            (self.root / f'{name}.json').write_text(json.dumps(contents), encoding='utf-8')

    def assert_error(self, code, account='alpha'):
        with self.assertRaises(PreflightError) as caught:
            load_pair_config(self.root, account)
        self.assertEqual(caught.exception.code, code)

    def pair_with_budgets(self, alpha=100, beta=80):
        for name, budget in (('alpha', alpha), ('beta', beta)):
            self.configs[name]['draft_duel']['draft_duel_config'].update(
                coin_budget=budget, free_only=False)
        self.write_accounts()
        return load_pair_config(self.root, 'alpha')

    def test_read_only_loading_is_symmetric_and_uses_safe_defaults(self):
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()}
        pair = load_pair_config(self.root, 'beta')
        self.assertEqual((pair.leader, pair.member), ('alpha', 'beta'))
        self.assertEqual(pair, load_pair_config(self.root, 'alpha'))
        self.assertEqual(pair.for_account('alpha').settings.coin_budget, 0)
        self.assertTrue(pair.for_account('beta').settings.free_only)
        self.assertEqual(pair.for_account('alpha').settings.challenge_limit, 1)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()})

    def test_path_traversal_and_windows_special_paths_are_rejected(self):
        for name in ('../alpha', '..\\alpha', '/alpha', 'C:\\alpha', 'alpha:stream', '..', '.', 'nul', 'con.json', 'alpha\x00'):
            with self.subTest(name=name):
                self.assert_error('invalid_account_name', name)
        self.configs['alpha']['draft_duel']['draft_duel_config']['partner_config'] = '../beta'
        self.write_accounts()
        self.assert_error('invalid_account_name')

    def test_missing_and_self_accounts_are_rejected(self):
        self.assert_error('account_missing', 'missing')
        self.configs['alpha']['draft_duel']['draft_duel_config']['partner_config'] = 'ALPHA'
        self.write_accounts()
        self.assert_error('same_account')

    def test_one_sided_partner_is_rejected(self):
        self.configs['beta']['draft_duel']['draft_duel_config']['partner_config'] = 'someone_else'
        self.write_accounts()
        self.assert_error('nonreciprocal_pair')

    def test_roles_can_be_reversed_and_must_be_complementary(self):
        self.configs['alpha']['draft_duel']['draft_duel_config']['user_status'] = 'member'
        self.configs['beta']['draft_duel']['draft_duel_config']['user_status'] = 'leader'
        self.write_accounts()
        pair = load_pair_config(self.root, 'alpha')
        self.assertEqual((pair.leader, pair.member), ('beta', 'alpha'))
        self.configs['alpha']['draft_duel']['draft_duel_config']['user_status'] = 'leader'
        self.write_accounts()
        self.assert_error('invalid_team_roles')

    def test_missing_invitation_identity_is_rejected(self):
        self.configs['beta']['draft_duel']['draft_duel_config']['teammate_name'] = ''
        self.write_accounts()
        self.assert_error('missing_teammate_name')

    def test_same_device_including_loopback_aliases_is_rejected(self):
        for serial in ('127.0.0.1:16384', 'localhost:16384', '[::1]:16384'):
            with self.subTest(serial=serial):
                self.configs['beta']['script']['device']['serial'] = serial
                self.write_accounts()
                self.assert_error('same_device')

    def test_same_emulator_and_automatic_device_are_rejected(self):
        original = deepcopy(self.configs)
        self.configs['beta']['script']['device']['emulatorinfo_name'] = 'mumu-ALPHA'
        self.write_accounts()
        self.assert_error('same_emulator')
        for field in ('serial', 'emulatorinfo_name'):
            for value in ('', 'auto', ' UNKNOWN ', None):
                with self.subTest(field=field, value=value):
                    self.configs = deepcopy(original)
                    self.configs['beta']['script']['device'][field] = value
                    self.write_accounts()
                    self.assert_error('unknown_device')

    def test_invalid_stored_budget_does_not_silently_fall_back(self):
        self.configs['beta']['draft_duel']['draft_duel_config']['coin_budget'] = -1
        self.write_accounts()
        self.assert_error('invalid_account_config')

    def test_busy_unknown_or_unreadable_activity_denies_without_actions(self):
        pair = load_pair_config(self.root, 'alpha')
        self.assertTrue(check_pair_activity(pair, lambda name: AccountActivity(name == 'alpha', 'DraftDuel')))
        status = {'alpha': AccountActivity(False), 'beta': AccountActivity(True, 'Orochi')}
        reader = Mock(side_effect=status.get)
        self.assertEqual(check_pair_activity(pair, reader).code, 'account_busy')
        self.assertEqual([call.args[0] for call in reader.call_args_list], ['alpha', 'beta'])
        self.assertEqual(check_pair_activity(pair, lambda name: None).code, 'activity_unknown')
        self.assertEqual(check_pair_activity(pair, Mock(side_effect=RuntimeError('offline'))).code, 'activity_unknown')

    def test_unknown_low_confidence_or_unready_fee_never_admits(self):
        pair = self.pair_with_budgets()
        for offer, code in (
                (AdmissionOffer(None, 1, True), 'cost_unknown'),
                (AdmissionOffer(0, 0.4, True), 'cost_low_confidence'),
                (AdmissionOffer(0, float('nan'), True), 'cost_low_confidence'),
                (AdmissionOffer(0, 1, False), 'account_not_ready'),
                (AdmissionOffer(True, 1, True), 'cost_unknown')):
            with self.subTest(offer=offer):
                result = check_pair_admission(pair, {'alpha': AdmissionOffer(0, 1, True), 'beta': offer}, {'alpha': 0, 'beta': 0})
                self.assertFalse(result)
                self.assertEqual(result.code, code)

    def test_both_independent_budgets_include_previous_spending(self):
        pair = self.pair_with_budgets(alpha=100, beta=80)
        offers = {'alpha': AdmissionOffer(50, 1, True), 'beta': AdmissionOffer(40, 1, True)}
        self.assertTrue(check_pair_admission(pair, offers, {'alpha': 50, 'beta': 40}))
        self.assertEqual(check_pair_admission(pair, offers, {'alpha': 50, 'beta': 41}).code, 'budget_exceeded')
        self.assertEqual(check_pair_admission(pair, offers, {'alpha': 51, 'beta': 0}).code, 'budget_exceeded')
        self.assertEqual(check_pair_admission(pair, offers, {'alpha': 0}).code, 'budget_unknown')

    def test_free_only_and_zero_budget_are_separate_guards(self):
        pair = load_pair_config(self.root, 'alpha')
        offers = {'alpha': AdmissionOffer(0, 1, True), 'beta': AdmissionOffer(1, 1, True)}
        self.assertEqual(check_pair_admission(pair, offers, {'alpha': 0, 'beta': 0}).code, 'free_only')
        offers['beta'] = AdmissionOffer(0, 1, True)
        self.assertTrue(check_pair_admission(pair, offers, {'alpha': 0, 'beta': 0}))
        pair = self.pair_with_budgets(alpha=0, beta=0)
        offers['beta'] = AdmissionOffer(1, 1, True)
        self.assertEqual(check_pair_admission(pair, offers, {'alpha': 0, 'beta': 0}).code, 'budget_exceeded')


if __name__ == '__main__':
    unittest.main()
