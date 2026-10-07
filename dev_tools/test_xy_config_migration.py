"""Synthetic migration checks; no live configs, services, emulators or writes.

Run: toolkit/python.exe -B dev_tools/test_xy_config_migration.py
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xy_config_migration import Converter, ConfigModel, convert_xy_config


def scheduled(**fields):
    return {"scheduler": {"enable": True, "next_run": "2026-10-06 08:21:00"}, **fields}


class ConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.converter = Converter()

    def convert(self, source):
        before = copy.deepcopy(source)
        with patch.object(ConfigModel, "read_json", side_effect=AssertionError("must not read private configs")), \
             patch.object(ConfigModel, "write_json", side_effect=AssertionError("must not write configs")):
            result = self.converter.convert(source, "xy-synthetic")
        self.assertEqual(source, before)
        return result

    def test_preserves_identity_scheduling_chess_and_scalar_courtyard(self):
        source = {"config_name": "synthetic-secret", "running_task": "Chess",
                  "script": {"device": {"serial": "synthetic-device", "package_name": "com.netease.onmyoji", "emulatorinfo_path": "C:/synthetic/emulator.exe"}},
                  "global_game": {"costume_config": {"costume_main_type": "costume_main_13"}},
                  "chess": scheduled(chess_config={"run_count": 12, "lineup_bond": "随机"})}
        # Supply only shared fields; line-up options are validated separately.
        source["chess"]["chess_config"].pop("lineup_bond")
        candidate, report = self.convert(source)
        self.assertEqual(candidate["script"]["device"]["serial"], "synthetic-device")
        self.assertEqual(candidate["script"]["device"]["emulatorinfo_path"], "C:/synthetic/emulator.exe")
        self.assertEqual(candidate["chess"]["scheduler"], {**candidate["chess"]["scheduler"], **source["chess"]["scheduler"]})
        self.assertEqual(candidate["global_game"]["costume_config"]["costume_main_type"], ["costume_main_13"])
        self.assertEqual(report["enabled_tasks"], ["chess"])
        self.assertEqual(candidate["running_task"], "")
        self.assertNotIn("synthetic-secret", json.dumps(report))
        self.assertNotIn("synthetic-device", json.dumps(report))

    def test_combined_idle_semantics_and_thresholds(self):
        for old_mode, new_mode in (("close_emulator_or_goto_main", "goto_main"), ("close_emulator_or_close_game", "close_game")):
            source = {"script": {"optimization": {"when_task_queue_empty": old_mode,
                       "close_game_limit_time": "00:12:00", "close_emulator_limit_time": "00:28:00"}}}
            candidate, report = self.convert(source)
            actual = candidate["script"]["optimization"]
            self.assertEqual(actual["when_task_queue_empty"], new_mode)
            self.assertEqual(actual["close_game_wait_duration"], "00:12:00")
            self.assertEqual(actual["close_emulator_wait_duration"], "00:28:00")
            self.assertFalse(report["blocked_config"])

    def test_plain_idle_does_not_gain_emulator_close_and_zero_is_disabled(self):
        for mode in ("goto_main", "close_game", "close_emulator_or_close_game"):
            candidate, _ = self.convert({"script": {"optimization": {"when_task_queue_empty": mode,
                                              "close_emulator_limit_time": "00:00:00" if mode.startswith("close_emulator") else "00:30:00"}}})
            self.assertEqual(candidate["script"]["optimization"]["close_emulator_wait_duration"], "00:00:00")

    def test_banquet_alias_preserves_time_and_switch(self):
        candidate, report = self.convert({"guild_banquet": scheduled(guild_banquet_config={"auto_switch_shikigami": True, "run_time_1": "19:35:00"})})
        self.assertTrue(candidate["guild_banquet"]["guild_banquet_time"]["auto_switch_shikigami"])
        self.assertEqual(candidate["guild_banquet"]["guild_banquet_time"]["run_time_1"], "19:35:00")
        self.assertIn("guild_banquet", report["enabled_tasks"])

    def test_unsupported_selectors_disable_only_affected_tasks(self):
        candidate, report = self.convert({"chess": scheduled(), "six_realms": scheduled(six_realms_gate={"six_realms_type": "PeacockKingdom"}),
                      "dokan": scheduled(dokan_owner_battle_conf={"preset_enable": True}),
                      "abyss_shadows": scheduled(process_manage={}), "activity_shikigami": scheduled(general_config={})})
        self.assertEqual(report["enabled_tasks"], ["chess"])
        self.assertEqual(set(report["disabled_tasks"]), {"six_realms", "dokan", "abyss_shadows", "activity_shikigami"})
        self.assertTrue(candidate["chess"]["scheduler"]["enable"])

    def test_moon_sea_remains_enabled(self):
        candidate, report = self.convert({"six_realms": scheduled(six_realms_gate={"six_realms_type": "MoonSea"}, pk_switch_soul_conf={})})
        self.assertTrue(candidate["six_realms"]["scheduler"]["enable"])
        self.assertEqual(report["disabled_tasks"], [])

    def test_compatible_weekly_renames_but_extra_shop_does_not(self):
        candidate, report = self.convert({"weekly_purchase": scheduled()})
        self.assertTrue(candidate["rich_man"]["scheduler"]["enable"])
        self.assertNotIn("weekly_purchase", report["unsupported_tasks"])
        candidate, report = self.convert({"weekly_purchase": scheduled(itachi_coin_shop={"itachi_coin_buy_jade": True})})
        self.assertFalse(candidate["rich_man"]["scheduler"]["enable"])
        self.assertIn("weekly_purchase", report["unsupported_tasks"])
        self.assertIn("weekly_purchase", report["disabled_tasks"])

    def test_one_and_two_friend_lists_map_without_leaking_values(self):
        for text, number in (("synthetic-first", "one"), ("synthetic-first\nsynthetic-second", "two")):
            candidate, report = self.convert({"orochi": scheduled(invite_config={"friend_list": text})})
            self.assertEqual(candidate["orochi"]["invite_config"]["invite_number"], number)
            self.assertEqual(candidate["orochi"]["invite_config"]["friend_1"], "synthetic-first")
            self.assertIn("orochi", report["enabled_tasks"])
            self.assertNotIn("synthetic-first", json.dumps(report))

    def test_overlarge_friend_list_and_name_green_are_not_fallbacks(self):
        _, report = self.convert({"orochi": scheduled(invite_config={"friend_list": "a\nb\nc"}),
                                 "secret": scheduled(general_battle={"green_enable": True, "green_mark_type": "name", "green_mark_name": "synthetic-secret"})})
        self.assertIn("orochi", report["disabled_tasks"])
        self.assertIn("secret", report["disabled_tasks"])
        self.assertNotIn("synthetic-secret", json.dumps(report))

    def test_noncritical_unknown_field_keeps_schedule(self):
        candidate, report = self.convert({"chess": scheduled(chess_config={"xy_cosmetic": "synthetic-secret"})})
        self.assertTrue(candidate["chess"]["scheduler"]["enable"])
        self.assertEqual(report["disabled_tasks"], [])
        self.assertNotIn("synthetic-secret", json.dumps(report))

    def test_invalid_range_is_not_silent_ConfigBase_fallback(self):
        candidate, report = self.convert({"chess": scheduled(chess_config={"matchmaking_timeout_seconds": 1}), "orochi": scheduled()})
        self.assertFalse(candidate["chess"]["scheduler"]["enable"])
        self.assertTrue(candidate["orochi"]["scheduler"]["enable"])
        self.assertIn("chess", report["disabled_tasks"])

    def test_invalid_identity_blocks_config_instead_of_using_auto_device(self):
        candidate, report = self.convert({"script": {"device": {"package_name": "synthetic-invalid-private"}}, "chess": scheduled()})
        self.assertTrue(report["blocked_config"])
        self.assertEqual(report["enabled_tasks"], [])
        self.assertFalse(candidate["chess"]["scheduler"]["enable"])
        self.assertNotIn("synthetic-invalid-private", json.dumps(report))

    def test_legacy_courtyard_does_not_auto_enable(self):
        candidate, report = self.convert({"restart": scheduled(harvest_config={"enable": True, "enable_courtyard_affairs": True})})
        self.assertTrue(report["courtyard_legacy_eligible"])
        self.assertFalse(candidate["courtyard_affairs"]["scheduler"]["enable"])
        self.assertEqual(candidate["courtyard_affairs"]["scheduler"]["next_run"], "2026-10-06 08:21:00")
        self.assertIn("restart", report["enabled_tasks"])
        _, report = self.convert({"restart": scheduled()})
        self.assertFalse(report["courtyard_legacy_eligible"])

    def test_wrapper_contract(self):
        candidate, report = convert_xy_config({"chess": scheduled()}, "xy-contract")
        self.assertEqual(candidate["config_name"], "xy-contract")
        self.assertTrue(report["validated"])

    def test_malformed_task_and_global_groups_do_not_raise_raw_errors(self):
        candidate, report = self.convert({"script": "synthetic-invalid-private", "chess": scheduled(), "courtyard_affairs": None})
        self.assertTrue(report["blocked_config"])
        self.assertEqual(report["enabled_tasks"], [])
        self.assertNotIn("synthetic-invalid-private", json.dumps(report))
        self.assertFalse(candidate["courtyard_affairs"]["scheduler"]["enable"])

    def test_invalid_interval_and_numbered_lists_reset_only_affected_tasks(self):
        candidate, report = self.convert({"chess": {"scheduler": {"enable": True, "success_interval": "synthetic-invalid"}},
                      "find_jade": scheduled(invite_info_list_2={"name": "synthetic-private"}), "orochi": scheduled()})
        self.assertFalse(candidate["chess"]["scheduler"]["enable"])
        self.assertFalse(candidate["find_jade"]["scheduler"]["enable"])
        self.assertTrue(candidate["orochi"]["scheduler"]["enable"])
        self.assertNotIn("synthetic-private", json.dumps(report))

    def test_text_enable_never_grants_permission(self):
        candidate, report = self.convert({"chess": {"scheduler": {"enable": "true"}}})
        self.assertFalse(candidate["chess"]["scheduler"]["enable"])
        self.assertEqual(report["enabled_tasks"], [])

    def test_hunt_forced_continuous_default_has_native_equivalent(self):
        candidate, report = self.convert({"hunt": scheduled(netherworld_battle_config={"continuous_battle": True, "max_continuous": 0, "quick_exit": False})})
        self.assertTrue(candidate["hunt"]["scheduler"]["enable"])
        self.assertIn("hunt", report["enabled_tasks"])
        candidate, report = self.convert({"hunt": scheduled(netherworld_battle_config={"continuous_battle": True, "max_continuous": 3})})
        self.assertFalse(candidate["hunt"]["scheduler"]["enable"])
        self.assertIn("hunt", report["disabled_tasks"])


if __name__ == "__main__":
    unittest.main()
