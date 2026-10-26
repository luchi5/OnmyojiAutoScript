# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from copy import copy
from datetime import datetime, timedelta, timezone
import json
import os
import uuid
from pathlib import Path

import cv2
import numpy as np
import random
import re
from module.atom.click import RuleClick
from tasks.base_task import BaseTask
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_area_boss, page_shikigami_records, page_main
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.AreaBoss.assets import AreaBossAssets
from tasks.AreaBoss.config_boss import AreaBossFloor
from module.logger import logger
from module.exception import TaskEnd, GamePageUnknownError, GameStuckError, GameTooManyClickError
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from typing import List


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, AreaBossAssets):

    O_AB_PRESET_REJECTED = RuleOcr(
        name='area_preset_rejected', mode='Full', method='Default',
        roi=(300, 195, 700, 90), area=(300, 195, 700, 90), keyword='不满足',
    )
    C_AB_DISMISS_PRESET = RuleClick(
        roi_front=(960, 100, 60, 40), roi_back=(960, 100, 60, 40),
        name='area_dismiss_preset',
    )

    def _preset_selection_fallback(self, timed_out=False):
        """Keep the fielded team when a restricted encounter rejects a preset."""
        if not timed_out and not self.ocr_appear(self.O_AB_PRESET_REJECTED):
            return False
        logger.warning('Area Boss preset unavailable; keep current team and close preset panel')
        deadline = time.monotonic() + 8
        clicks = 0
        while time.monotonic() < deadline:
            self.screenshot()
            if not self.appear(self.I_PRESET_ENSURE):
                if self.is_in_prepare(False) or self._area_boss_battle_started():
                    logger.info('Area Boss preset panel closed; continue with current team')
                    return True
            elif clicks < 3 and self.click(self.C_AB_DISMISS_PRESET, interval=1):
                clicks += 1
            time.sleep(0.2)
        # A failed close must not be mistaken for successful preset switching.
        self._defer_battle_preparation()
        return True

    def _area_boss_identity(self):
        # Reuse the existing boss-name reader; list positions are not identities.
        try:
            text = self.O_AB_BOSS_NAME.detect_text(self.device.image)
            name = re.sub(r'\s+', '', text) if isinstance(text, str) else ''
            return name if 2 <= len(name) <= 80 else None
        except Exception as exc:
            logger.warning(f'Cannot identify Area Boss for daily evidence: {type(exc).__name__}')
            return None

    def _record_area_boss(self, name=None, basis=None, target=None, reputation=None):
        # Reporting is observational: a write/OCR failure cannot change battle
        # results, schedule, or the accepted existing single-boss completion rule.
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            account = self.config.config_name
            if (not isinstance(account, str) or not account or account in ('.', '..')
                    or any(c in account for c in '\\/:*?"<>|')
                    or any(ord(c) < 32 for c in account) or account[-1:] in (' ', '.')):
                raise ValueError('Invalid account name')
            root = Path(__file__).resolve().parents[2]
            directory = root / 'log' / 'area_boss_completion' / account
            path = directory / (now.date().isoformat() + '.json')
            for check in (root, root/'log', root/'log'/'area_boss_completion', directory, path):
                if check.exists() and (check.is_symlink() or getattr(check.lstat(), 'st_file_attributes', 0) & 0x400):
                    raise ValueError('Linked completion evidence path')
            if path.exists() and path.stat().st_nlink != 1:
                raise ValueError('Hardlinked completion evidence')
            record = {'schema_version': 1, 'source': 'area_boss_task_v1', 'task': 'AreaBoss',
                      'account': account, 'date': now.date().isoformat(), 'at': now.isoformat(timespec='seconds'),
                      'target': None, 'reputation': None, 'bosses': [], 'unidentified_successes': 0}
            if path.exists():
                previous = json.loads(path.read_text(encoding='utf-8'))
                if (previous.get('schema_version') != 1 or previous.get('source') != 'area_boss_task_v1'
                        or previous.get('account') != account or previous.get('date') != record['date']
                        or previous.get('task') != 'AreaBoss' or not isinstance(previous.get('bosses'), list)):
                    raise ValueError('Invalid existing daily evidence')
                record = previous
                record['at'] = now.isoformat(timespec='seconds')
            if type(target) is int and target in (1, 2, 3):
                record['target'], record['reputation'] = target, reputation
            if isinstance(name, str) and name and basis in ('existing_rank', 'battle_success'):
                if not any(entry.get('name') == name for entry in record['bosses']):
                    record['bosses'].append({'name': name, 'basis': basis, 'at': now.isoformat(timespec='seconds')})
            elif basis in ('existing_rank', 'battle_success'):
                record['unidentified_successes'] = record.get('unidentified_successes', 0) + 1
            count = len({entry['name'] for entry in record['bosses']})
            record['completed_count'] = count
            record['outcome'] = ('completed' if type(record['target']) is int and count >= record['target']
                                 else 'incomplete' if type(record['target']) is int and not record.get('unidentified_successes') else 'unverified')
            directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                fd = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(record, stream, ensure_ascii=False, indent=2)
                    stream.flush(); os.fsync(stream.fileno())
                if path.exists() and (path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400
                                      or path.stat().st_nlink != 1):
                    raise ValueError('Unsafe evidence target')
                os.replace(str(temporary), str(path))
            finally:
                if temporary.exists(): temporary.unlink()
        except Exception as exc:
            logger.warning(f'Cannot save Area Boss daily evidence: {type(exc).__name__}')

    def _get_dynamic_boss_count(self) -> int:
        """
        从地域鬼王主界面 OCR 声望值，动态决定可挑战次数
        :return: 可挑战鬼王数量 (1/2/3)
        """
        self.screenshot()
        rep = self.O_AB_REPUTATION.ocr_digit(self.device.image)
        logger.info(f"Area boss reputation: {rep}")
        target = 3 if rep >= 10000 else 2 if rep >= 2000 else 1
        self._record_area_boss(target=target, reputation=rep)
        if rep >= 10000:
            return 3
        elif rep >= 2000:
            return 2
        else:
            return 1

    def _select_challenge_button(self, battle: RuleImage) -> RuleImage | None:
        """Match a visible red/gold challenge button without mutating assets."""
        image = self.device.image
        if image is None or len(image.shape) != 3 or image.shape[2] != 3:
            return None
        height, width = image.shape[:2]
        x, y, w, h = battle.roi_back
        left, top = max(0, x - 10), max(0, y - 10)
        right, bottom = min(width, x + w + 10), min(height, y + h + 10)
        if right <= left or bottom <= top:
            return None
        target = copy(battle)
        target.roi_front = list(battle.roi_front)
        target.roi_back = (left, top, right - left, bottom - top)
        if not self.appear(target):
            return None
        if not target.match_saturation(image, roi=tuple(target.roi_front)):
            return None
        return target

    def _visible_challenge_buttons(self):
        """Find enabled buttons at their actual positions, including scrolled rows."""
        image = self.device.image
        if image is None or image.shape[:2] != (720, 1280):
            return []
        found = []
        for asset in (self.I_BATTLE_1, self.I_BATTLE_2, self.I_BATTLE_3):
            rule = copy(asset)
            rule.roi_front = list(asset.roi_front)
            rule.roi_back = (1070, 195, 100, 475)
            for score, x, y, w, h in rule.match_all_any(image):
                target = copy(rule)
                target.roi_front = [x, y, w, h]
                target.roi_back = (x, y, w, h)
                if target.match_saturation(image, roi=(x, y, w, h)):
                    found.append((score, target))
        selected = []
        for _, target in sorted(found, key=lambda item: item[0], reverse=True):
            center = target.roi_front[1] + target.roi_front[3] / 2
            if all(abs(center - (other.roi_front[1] + other.roi_front[3] / 2)) > 40
                   for other in selected):
                selected.append(target)
        return sorted(selected, key=lambda item: item.roi_front[1])[:4]

    def _boss_list_frame(self):
        image = self.device.image
        if image is None or image.shape[:2] != (720, 1280):
            return None
        # Restrict comparison to the list: the map and scrolling announcements
        # behind it animate even when the list has reached the bottom.
        return cv2.resize(cv2.cvtColor(image[195:670, 925:1170], cv2.COLOR_RGB2GRAY),
                          (98, 190), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _same_boss_list_frame(first, second):
        if first is None or second is None:
            return False
        difference = np.abs(first.astype(np.int16) - second.astype(np.int16))
        return float(difference.mean()) < 2 and float((difference > 15).mean()) < 0.005

    def _fight_filter_bosses(self, available, *, scroll):
        results, pages = [], []
        seen_names = getattr(self, '_area_boss_seen_names', set())
        # Daily quota counts unique attempted bosses as before; failures do not
        # create unlimited retries. Scrolling only expands which rows we inspect.
        # Returning from a fight may reset the list to its first page. Keep the
        # inspected positions per visual page, then walk past them without
        # reopening their details. Bound both distinct pages and total swipes.
        for step in range(37 if scroll else 13):
            if len(results) >= available:
                break
            self.screenshot()
            if not self.appear(self.I_AB_FILTER_OPENED):
                self.open_filter()
                self.screenshot()
            frame = self._boss_list_frame()
            inspected = sum(len(item['handled']) for item in pages)
            record = next((item for item in pages
                           if self._same_boss_list_frame(frame, item['frame'])), None)
            if record is None:
                if len(pages) >= 13:
                    logger.info('Area Boss list reached the bounded page limit')
                    break
                record = {'frame': frame, 'progress': set(), 'handled': set()}
                pages.append(record)
            elif inspected in record['progress']:
                logger.info('Area Boss list reached the bottom or repeated a page')
                break
            record['progress'].add(inspected)
            refresh = False
            for battle in self._visible_challenge_buttons():
                if len(results) >= available:
                    break
                position = round(battle.roi_front[1] / 10)
                if position in record['handled']:
                    continue
                self.screenshot()
                if not self.appear(self.I_AB_FILTER_OPENED):
                    self.open_filter()
                    self.screenshot()
                if not self._same_boss_list_frame(frame, self._boss_list_frame()):
                    # A return can reset the page or update this page's badges.
                    # Re-read its targets instead of reusing old coordinates or
                    # scrolling past the uninspected rows (also for favorites).
                    refresh = True
                    break
                # Never click coordinates from a stale frame without checking
                # the challenge button is still enabled at that location.
                target = self._select_challenge_button(battle)
                if target is None:
                    continue
                record['handled'].add(position)
                result = self.boss_fight(target, seen_names=seen_names)
                if result is not None:
                    results.append(result)
            if refresh:
                continue
            if not scroll or len(results) >= available or step == 36:
                break
            self.open_filter()
            self.swipe(self.S_AB_FILTER_UP, interval=1)
            time.sleep(0.6)
        return results

    def run(self) -> bool:
        """
        运行脚本
        :return:
        """
        # 直接手动关闭这个锁定阵容的设置
        self.config.area_boss.general_battle.lock_team_enable = False
        con = self.config.area_boss.boss

        if self.config.area_boss.switch_soul.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(self.config.area_boss.switch_soul.switch_group_team)

        if self.config.area_boss.switch_soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(self.config.area_boss.switch_soul.group_name,
                                         self.config.area_boss.switch_soul.team_name)

        self.goto_page(page_area_boss)

        # 已挑战鬼王数量
        boss_fought = 0
        self._area_boss_seen_names = set()
        if con.boss_reward:
            if self.fight_reward_boss():  # 挑战成功则加一
                boss_fought += 1

        self.open_filter()
        # 切换到对应集合(热门/收藏)
        if con.use_collect:
            self.switch_to_collect()
        else:
            self.switch_to_famous()

        # 动态获取可挑战次数
        dynamic_count = self._get_dynamic_boss_count()
        available = dynamic_count - boss_fought
        battle_results = self._fight_filter_bosses(available, scroll=not con.use_collect)
        # 退出
        self.go_back()
        if len(battle_results) < available or any(result is not True for result in battle_results):
            logger.warning('Area Boss challenge incomplete; retain actual completion evidence and use normal schedule')
        self.set_next_run(task='AreaBoss', success=True, finish=False)

        # 以抛出异常的形式结束
        raise TaskEnd

    def go_back(self) -> None:
        """
        返回, 要求这个时候是出现在地域鬼王的主界面
        :return:
        """
        # 点击返回
        logger.info("Script back home")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_UI_BACK_YELLOW, threshold=0.6, interval=2):
                continue
            if self.appear(self.I_CHECK_MAIN, threshold=0.6):
                break

    def boss(self, battle: RuleImage, collect: bool = False):

        # 点击右上角的鬼王选择
        logger.info("Script filter")
        while 1:
            self.screenshot()
            # 如果筛选界面已经打开 点击热门按钮
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_FAMOUS_BTN)
                break
            if self.appear_then_click(self.I_FILTER, interval=3):
                continue

        if collect:
            self.switch_to_collect()
        # 页面没有可挑战的BOSS
        if not (self.appear(self.I_BATTLE_1) or self.appear(self.I_BATTLE_2) or self.appear(self.I_BATTLE_3)):
            logger.error("There is no boss could be challenged")
            return
        # 点击第几个鬼王
        logger.info(f'Script area boss {battle}')
        self.ui_click(battle, self.I_AB_CLOSE_RED)
        # 点击挑战
        logger.info("Script fire ")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FIRE, interval=1):
                continue
            if not self.appear(self.I_AB_CLOSE_RED):  # 如果这个红色的关闭不见了才可以进行继续
                break
        if not self.run_general_battle(self.config.area_boss.general_battle):
            logger.info("地域鬼王第2只战斗失败")
        # 红色关闭
        logger.info("Script close red")
        self.wait_until_appear(self.I_AB_CLOSE_RED)
        self.ui_click(self.I_AB_CLOSE_RED, self.I_FILTER)

    def boss_fight(self, battle: RuleImage, fileter_open: bool = True, seen_names=None) -> bool | None:
        """
            完成挑战一个鬼王的全流程
            从打开筛选界面开始 到关闭鬼王详情界面结束
        @param battle: 挑战按钮,鬼王头像也可,只要点击能进入详情界面
        @type battle:
        @return:    True        挑战成功
                    False       挑战失败
        @rtype:
        """
        reward_floor = self.config.area_boss.boss.reward_floor
        if fileter_open and not self.appear(self.I_AB_FILTER_OPENED):
            self.open_filter(allow_detail=True)
        # A late detail page may appear after the final entry check. When
        # reopening the filter, accept that page and continue this same boss.
        if not self.open_boss_detail(battle, 3):
            if self.open_filter(allow_detail=True):
                return False

        boss_identity = self._area_boss_identity()
        if seen_names is not None:
            # The detail title identifies a boss across scrolling/overlapping
            # pages; a list position or character portrait does not.
            identity = boss_identity
            if identity is None:
                image = self.device.image
                if image is not None and image.shape[:2] == (720, 1280):
                    title = image[36:105, 114:362]
                    identity = ('title_pixels', (np.min(title, axis=2) > 170).tobytes())
            if identity is None or identity in seen_names:
                self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=1)
                return None
            seen_names.add(identity)
        elif boss_identity and hasattr(self, '_area_boss_seen_names'):
            self._area_boss_seen_names.add(boss_identity)

        # 如果已经打过该BOSS,直接跳过不打了
        if self.is_group_ranked():
            self._record_area_boss(boss_identity, 'existing_rank')
            logger.warning("There is no boss could be challenged")
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=3)
            return True

        # Prefer confirmed difficulty; ordinary level 1 has a bounded OCR fallback.
        try:
            ready = True
            if reward_floor in (AreaBossFloor.ONE, AreaBossFloor.TEN):
                ready = self.setup_ultra(reward_floor)
            elif reward_floor == AreaBossFloor.NORMAL_LV60:
                ready = self.setup_normal() and self.switch_to_level_60()
            elif reward_floor == AreaBossFloor.NORMAL_LV1:
                ready = self.setup_normal() and self.switch_to_level_1()
        except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as exc:
            logger.warning(f'Area Boss difficulty unconfirmed: {exc}')
            ready = False
        if not ready:
            self._defer_difficulty()

        result = True
        if not self.start_fight():
            result = False
            logger.warning("Area Boss Fight Failed ")
        if result is True:
            self._record_area_boss(boss_identity, 'battle_success')
        self.wait_until_appear(self.I_AB_CLOSE_RED)
        self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=1)
        return result

    def _defer_difficulty(self):
        """Leave an unconfirmed daily task pending without stopping the account."""
        self.set_next_run(
            task='AreaBoss', success=None, server=False,
            target=datetime.now() + timedelta(minutes=30),
        )
        logger.warning('Area Boss difficulty unconfirmed; defer 30 minutes and continue other tasks')
        try:
            self.goto_page(page_main)
        except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as exc:
            logger.warning(f'Area Boss return to courtyard deferred: {exc}')
        raise TaskEnd('Area Boss difficulty pending')

    def start_fight(self) -> bool:
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FIRE, interval=1):
                continue
            if not self.appear(self.I_AB_CLOSE_RED):  # 如果这个红色的关闭不见了才可以进行继续
                break

        return self.run_general_battle(self.config.area_boss.general_battle)

    def _area_boss_battle_started(self) -> bool:
        # Preparation can also match generic battle-page markers. Give the
        # visible preparation controls precedence over those markers.
        if self.is_in_prepare(False):
            return False
        return self.is_in_real_battle(False) or any(
            self.appear(rule) for rule in (
                self.I_WIN, self.I_FALSE, self.I_REWARD, self.I_REWARD_GOLD,
            )
        )

    def battle_before(self, buff, config, timeout: float = 30) -> bool:
        """Confirm entry after preset selection before waiting for results."""
        budget = max(30, timeout)
        deadline = time.monotonic() + budget
        configured = False
        clicks = confirmed = 0
        while time.monotonic() < deadline:
            self.screenshot()
            if self._area_boss_battle_started():
                confirmed += 1
                if confirmed >= 2:
                    logger.info('Area Boss battle entry confirmed')
                    return True
                time.sleep(0.2)
                continue
            confirmed = 0
            if self.appear_then_click(self.I_DISABLE_7DAYS_DIFF_SOUL, interval=0.6):
                continue
            if self.appear_then_click(self.I_CONFIRM_CLOSE_DIFF_SOUL, interval=0.6):
                continue
            if self.is_in_prepare(False) and not getattr(config, 'lock_team_enable', False):
                if self.current_count == 1 and not configured:
                    self.switch_preset_team(config.preset_enable, config.preset_group, config.preset_team)
                    self.check_and_open_buff(buff)
                    configured = True
                    # Preset selection may take longer than the original
                    # five-second budget. Wait on a fresh post-selection frame.
                    deadline = time.monotonic() + budget
                    continue
                if clicks < 6 and self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=0.8):
                    clicks += 1
                    continue
            time.sleep(0.2)
        return self._defer_battle_preparation()

    def _defer_battle_preparation(self) -> bool:
        """Leave a failed preparation pending without entering battle_wait."""
        logger.warning('Area Boss preparation unconfirmed; try leaving preparation')
        deadline = time.monotonic() + 12
        exiting = False
        confirmed = exit_clicks = confirm_clicks = close_clicks = false_clicks = 0
        while time.monotonic() < deadline:
            self.screenshot()
            preparing = self.is_in_prepare(False)
            # A delayed preparation click can still start the fight. Never
            # leave that fight; accept it only after two fresh confirmations.
            started = (not preparing and self.is_in_real_battle(False)) or (
                not exiting and self._area_boss_battle_started()
            )
            if started:
                confirmed += 1
                if confirmed >= 2:
                    logger.info('Area Boss delayed battle entry confirmed')
                    return True
                time.sleep(0.2)
                continue
            confirmed = 0
            if self.appear(self.I_AB_CLOSE_RED) and self.appear(self.I_FIRE):
                if close_clicks < 3 and self.appear_then_click(self.I_AB_CLOSE_RED, interval=0.8):
                    close_clicks += 1
                    continue
            elif self.appear(self.I_FILTER) and not preparing:
                break
            if exiting and confirm_clicks < 3 and self.appear_then_click(self.I_EXIT_ENSURE, interval=0.8):
                confirm_clicks += 1
                continue
            if exiting and false_clicks < 3 and self.appear_then_click(self.I_FALSE, interval=0.8):
                false_clicks += 1
                continue
            if preparing and exit_clicks < 3 and self.appear_then_click(self.I_EXIT, interval=0.8):
                exit_clicks += 1
                exiting = True
                continue
            time.sleep(0.2)
        if confirmed == 1:
            # Do not abandon a fight whose first confirmed frame arrived at
            # the deadline. One fresh frame completes the existing pair.
            self.screenshot()
            if (not self.is_in_prepare(False) and self.is_in_real_battle(False)) or (
                not exiting and self._area_boss_battle_started()
            ):
                logger.info('Area Boss delayed battle entry confirmed at deadline')
                return True
        self.set_next_run(
            task='AreaBoss', success=None, server=False,
            target=datetime.now() + timedelta(minutes=30),
        )
        logger.warning('Area Boss preparation pending; defer 30 minutes and continue other tasks')
        raise TaskEnd('Area Boss preparation pending')

    def setup_ultra(self, floor: AreaBossFloor = AreaBossFloor.ONE) -> bool:
        if not self.switch_difficulty(True):
            return False
        # 调整极地鬼层数
        match floor:
            case AreaBossFloor.ONE:
                return self.switch_to_floor_1()
            case AreaBossFloor.TEN:
                return self.switch_to_floor_10()
        return True

    def setup_normal(self) -> bool:
        """
        确保当前为普通地鬼模式
        :return: True 成功（已处于普通模式），False 失败
        """
        return self.switch_difficulty(False)

    def _normal_level(self):
        # Reuse the existing OCR session and read only the level diamond.
        reader = copy(self.O_AB_REPUTATION)
        reader.name = 'AB_NORMAL_LEVEL'
        reader.roi = (300, 160, 110, 100)
        reader.area = reader.roi
        # The shared Digit rule repairs '00' to 1. Difficulty must use the
        # complete original number instead of that permissive repair.
        reader.after_process = lambda text: text.strip()
        results = reader.detect_and_ocr(self.device.image)
        if not results:
            # A lone stylized '1' can be missed by the text detector. Read the
            # diamond's digits directly, excluding its border and nearby controls.
            reader.roi = (322, 183, 46, 48)
            reader.area = reader.roi
            text = reader.ocr_single_line(self.device.image)
            return int(text) if re.fullmatch(r'[1-9]|[1-5][0-9]|60', text) else None
        if len(results) != 1:
            return None
        text = results[0].ocr_text
        return int(text) if re.fullmatch(r'[1-9]|[1-5][0-9]|60', text) else None

    def _switch_to_normal_level(self, level: int) -> bool:
        if level not in (1, 60):
            raise ValueError('Unsupported Area Boss level')
        deadline = time.monotonic() + 15
        drags = confirmed = 0
        next_drag = 0
        while time.monotonic() < deadline:
            self.screenshot()
            if not self.appear(self.I_AB_CLOSE_RED) or not self._normal_mode_visible():
                return False
            if self._normal_level() == level:
                confirmed += 1
                if confirmed >= 2:
                    logger.info(f'Area Boss normal level {level} confirmed')
                    return True
            else:
                confirmed = 0
            if not confirmed and drags < 3 and time.monotonic() >= next_drag and self.appear(self.I_AB_LEVEL_HANDLE):
                x, y = self.I_AB_LEVEL_HANDLE.front_center()
                swipe = copy(self.S_AB_LEVEL_RIGHT)
                swipe.roi_front = (x - 2, y - 2, 4, 4)
                swipe.roi_back = (170 if level == 1 else 570, y - 2, 4, 4)
                swipe.name = 'ab_level_left' if level == 1 else 'ab_level_right'
                self.swipe(swipe, interval=3)
                drags += 1
                next_drag = time.monotonic() + 3
            time.sleep(0.2)
        logger.warning(f'Cannot confirm Area Boss normal level {level}')
        if (level == 1 and self.appear(self.I_AB_CLOSE_RED)
                and self._normal_mode_visible() and self._normal_level() is None):
            logger.warning('Area Boss normal mode confirmed; level OCR unavailable after retries; continue with user-authorized level-1 fallback')
            return True
        return False

    def switch_to_level_1(self) -> bool:
        return self._switch_to_normal_level(1)

    def switch_to_level_60(self) -> bool:
        return self._switch_to_normal_level(60)

    def get_difficulty(self) -> bool:
        """
        @return:    True           极地鬼
                    False           普通地鬼
        @rtype: bool
        """
        self.screenshot()
        return self.appear(self.I_AB_DIFFICULTY_JI)

    def _normal_mode_visible(self) -> bool:
        """Confirm ordinary layout even when the mode toggle is unavailable.

        The red 'extreme' button is a switch *from* ordinary mode, not the
        ordinary-mode label. Some valid ordinary pages have no toggle at all.
        A slider plus an exact full 1..60 digit supplies positive evidence;
        absence of the extreme button alone must never confirm a mode.
        """
        if self.appear(self.I_AB_DIFFICULTY_JI):
            return False
        if self.appear(self.I_AB_DIFFICULTY_NORMAL):
            return True
        return self.appear(self.I_AB_LEVEL_HANDLE) and self._normal_level() is not None

    def switch_difficulty(self, ultra: bool = True) -> bool:
        """
            切换普通地鬼/极地鬼
        @param ultra:  是否切换到极地鬼
                    True        切换到极地鬼
                    False       切换到普通地鬼
        @type ultra:
        """
        _from = self.I_AB_DIFFICULTY_NORMAL if ultra else self.I_AB_DIFFICULTY_JI
        deadline = time.monotonic() + 12
        clicks = confirmed = 0
        while time.monotonic() < deadline:
            self.screenshot()
            ultra_visible = self.appear(self.I_AB_DIFFICULTY_JI)
            ambiguous = ultra_visible and self.appear(self.I_AB_DIFFICULTY_NORMAL)
            normal_visible = self._normal_mode_visible()
            target = ultra_visible if ultra else normal_visible
            source = normal_visible if ultra else ultra_visible
            if self.appear(self.I_AB_CLOSE_RED) and target and not source and not ambiguous:
                confirmed += 1
                if confirmed >= 2:
                    return True
            else:
                confirmed = 0
                if (self.appear(self.I_AB_CLOSE_RED) and source and not target
                        and not ambiguous and clicks < 3 and self.appear(_from)):
                    if self.click(_from, interval=3):
                        clicks += 1
            time.sleep(0.2)
        logger.warning('Cannot confirm Area Boss difficulty mode')
        try:
            # Keep the actual failure frame; the deferred exit returns to the
            # courtyard, so a later screenshot cannot diagnose this control.
            directory = Path('log') / 'area_boss_difficulty'
            directory.mkdir(parents=True, exist_ok=True)
            account = getattr(self.config, 'config_name', 'account')
            account = re.sub(r'[^\w\-]', '_', str(account))
            path = directory / f'{account}-{datetime.now():%Y%m%d-%H%M%S}.png'
            ok, encoded = cv2.imencode('.png', cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR))
            if ok:
                encoded.tofile(str(path))
                logger.info(f'Area Boss difficulty diagnostic: {path}')
        except Exception as exc:
            logger.warning(f'Cannot save Area Boss difficulty diagnostic: {exc}')
        return False

    def switch_to_floor_1(self) -> bool:
        return self._switch_to_floor(self.I_AB_JI_FLOOR_ONE, first=True)

    def switch_to_floor_10(self) -> bool:
        return self._switch_to_floor(self.I_AB_JI_FLOOR_TEN, first=False)

    def _selected_star_visual_match(self, target):
        """Compare the title's lettering, independent of its red background.

        Selected text and menu text share the glyphs but use different backing
        panels. Comparing light strokes avoids relying on OCR of this font.
        The numeral also has its own threshold so the shared 'star' character
        cannot make a different number look like the requested difficulty.
        """
        image = self.device.image
        if image is None or image.shape[:2] != (720, 1280):
            return False

        def glyph_mask(pixels):
            mask = (np.min(pixels, axis=2) > 170).astype(np.uint8)
            ys, xs = np.nonzero(mask)
            if len(xs) < 80:
                return None
            left, top, right, bottom = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
            if not (30 <= right - left <= 58 and 18 <= bottom - top <= 32):
                return None
            return cv2.resize(mask[top:bottom, left:right], (94, 50),
                              interpolation=cv2.INTER_NEAREST).astype(bool)

        # The selector's light horizontal border is at y=116; including it
        # would overwhelm the lettering's bounding box.
        actual = glyph_mask(image[118:150, 380:444])
        reference = glyph_mask(target.image)
        if actual is None or reference is None:
            return False
        # Allow tiny rasterization shifts, never substitutions of a numeral.
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                shifted = np.zeros_like(actual)
                sx, sy = max(0, -dx), max(0, -dy)
                tx, ty = max(0, dx), max(0, dy)
                width, height = 94 - abs(dx), 50 - abs(dy)
                shifted[ty:ty + height, tx:tx + width] = actual[sy:sy + height, sx:sx + width]
                whole = 2 * np.logical_and(shifted, reference).sum() / (shifted.sum() + reference.sum())
                number, expected = shifted[:, :47], reference[:, :47]
                numeral = 2 * np.logical_and(number, expected).sum() / max(1, number.sum() + expected.sum())
                if whole >= 0.73 and numeral >= 0.73:
                    return True
        return False

    def _switch_to_floor(self, target: RuleImage, first: bool) -> bool:
        """Select a visible star, then independently verify the closed title."""
        from copy import copy

        def in_area(rule, area):
            result = copy(rule)
            result.roi_front = list(rule.roi_front)
            result.roi_back = area
            return result

        # The old list check is a screenshot of an enabled fifth star. On
        # accounts that have not unlocked it, the grey text is a different
        # template even though the list is already open.
        list_area = (365, 155, 130, 310)
        selected = in_area(target, (365, 108, 140, 45))
        choice = in_area(target, list_area)
        list_first = in_area(self.I_AB_JI_FLOOR_ONE, list_area)
        list_last = in_area(self.I_AB_JI_FLOOR_TEN, list_area)
        scroll = copy(self.S_AB_FLOOR_DOWN)
        scroll.roi_front = list(self.S_AB_FLOOR_DOWN.roi_front)
        scroll.roi_back = list(self.S_AB_FLOOR_DOWN.roi_back)
        if not first:
            scroll.roi_front, scroll.roi_back = scroll.roi_back, scroll.roi_front
            scroll.name = 'ab_floor_up'

        deadline = time.monotonic() + 40
        opens = swipes = selections = 0
        opened = False
        last_open = last_swipe = last_select = -float('inf')
        stable = 0
        while time.monotonic() < deadline:
            self.screenshot()
            visible_choice = self.appear(choice)
            list_visible = visible_choice or self.appear(list_first) or self.appear(list_last)
            # Closing the menu does not prove the click selected the target.
            # Require two fresh title matches; visual glyph comparison handles
            # the different header styling without reinstating an OCR gate.
            menu_closed = not list_visible and self.appear(self.I_AB_CLOSE_RED)
            confirmed = menu_closed and (self.appear(selected) or self._selected_star_visual_match(selected))
            if confirmed:
                stable += 1
                if stable >= 2:
                    logger.info(f'Switch to floor {1 if first else 10} confirmed')
                    return True
                time.sleep(0.2)
                continue
            stable = 0
            now = time.monotonic()
            if visible_choice:
                opened = True
                if selections >= 2:
                    if now - last_select >= 3:
                        logger.warning('Area boss star selection did not close the dropdown')
                        self._save_star_selection_failure()
                        return False
                    time.sleep(0.2)
                    continue
                if now - last_select >= 2 and self.click(choice, interval=1.5):
                    selections += 1
                    last_select = now
                time.sleep(0.2)
                continue
            if selections:
                # Do not reopen a selector after an unverified selection or
                # mistake a clicked menu item for the final difficulty.
                if now - last_select >= 5:
                    logger.warning('Area boss star dropdown closure was not confirmed')
                    self._save_star_selection_failure()
                    return False
                time.sleep(0.2)
                continue
            if list_visible:
                opened = True
            if opened:
                if swipes >= 6:
                    logger.warning('Area boss target star not found after bounded scrolling')
                    self._save_star_selection_failure()
                    return False
                if now - last_swipe >= 1.3:
                    self.swipe(scroll, interval=1)
                    swipes += 1
                    last_swipe = now
                time.sleep(0.2)
                continue
            if now - last_open < 3:
                time.sleep(0.2)
                continue
            if opens >= 2:
                logger.warning('Area boss star dropdown could not be recognized')
                self._save_star_selection_failure()
                return False
            if self.click(self.C_AB_JI_FLOOR_SELECTED, interval=3):
                opens += 1
                last_open = now
            time.sleep(0.2)
        logger.warning('Area boss star selection timed out')
        self._save_star_selection_failure()
        return False

    def _save_star_selection_failure(self):
        try:
            directory = Path('log') / 'area_boss_star_selection'
            directory.mkdir(parents=True, exist_ok=True)
            account = re.sub(r'[^\w\-]', '_', str(getattr(self.config, 'config_name', 'account')))
            path = directory / f'{account}-{datetime.now():%Y%m%d-%H%M%S-%f}.png'
            ok, encoded = cv2.imencode('.png', cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR))
            if ok:
                encoded.tofile(str(path))
                logger.info(f'Area Boss star selection diagnostic: {path}')
            else:
                logger.warning('Cannot encode Area Boss star selection diagnostic')
        except Exception as exc:
            logger.warning(f'Cannot save Area Boss star selection diagnostic: {exc}')

    def fight_reward_boss(self):
        BOSS_REWARD_PHOTO1 = [self.C_AB_BOSS_REWARD_PHOTO_1, self.C_AB_BOSS_REWARD_PHOTO_2, self.C_AB_BOSS_REWARD_PHOTO_3]
        BOSS_REWARD_PHOTO2 = [self.C_AB_BOSS_REWARD_PHOTO_MINUS_2, self.C_AB_BOSS_REWARD_PHOTO_MINUS_1]
        need_open_filter, boss_name, photo = self.get_hot_in_reward()  # 获取挑战人数最多的Boss的名字
        if photo is None or boss_name == '声望不够':
            return False
        # 不需要打开筛选界面说明直接找到了目标boss, 直接挑战
        if not need_open_filter:
            return self.boss_fight(photo, fileter_open=False)
        # 滑动到最顶层
        logger.info("Swipe to top")
        for i in range(random.randint(1, 3)):
            self.swipe(self.S_AB_FILTER_DOWN)
        # 遍历所有boss找到名称一致的即目前挑战人数最多的
        for PHOTO in BOSS_REWARD_PHOTO1:
            self.open_filter()
            name = self.get_bossName(PHOTO)
            if self.check_common_chars(str(name), boss_name):
                return self.boss_fight(PHOTO, fileter_open=False)
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED)
        # 倒数一和二
        for i in range(random.randint(1, 3)):
            self.swipe(self.S_AB_FILTER_UP)
        for PHOTO in BOSS_REWARD_PHOTO2:
            self.open_filter()
            name = self.get_bossName(PHOTO)
            if self.check_common_chars(str(name), boss_name):
                return self.boss_fight(PHOTO, fileter_open=False)
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED)
        self.ui_click_until_disappear(self.I_AB_CLOSE_RED)

    def get_hot_in_reward(self):
        """
            返回挑战人数最多的悬赏鬼王
        @return: 是否打开筛选界面, boss名称, boss图片的click
        @rtype:
        """
        self.switch_to_reward()
        boss_configs = [
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_1, "need_swipe": False},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_2, "need_swipe": False},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_3, "need_swipe": False},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_MINUS_2, "need_swipe": True},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_MINUS_1, "need_swipe": True},
        ]

        def check_boss(photo: RuleClick):
            """
            检查boss是否满足条件
            :param photo:
            :return: 是否满足条件, 挑战数量, boss名称
            """
            self.open_filter()
            num = self.get_num_challenge(photo) or 0
            if not num:
                name = '声望不够'
            else:
                name = self.get_bossName(photo)
                if num >= 20000 and not self.appear(self.I_AB_NUM_CHALLENGE_RAIL):
                    logger.info("The number of challenges is enough")
                    return True, num, name
            # 没找到满足的则关闭boss页面
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED)
            return False, num, name

        mx_challenge_boss_name = None
        mx_challenge_num = 0
        photo = None
        # 遍历所有boss配置, 找挑战人数最多的boss
        for cfg in boss_configs:
            if cfg["need_swipe"]:
                self.open_filter()
                for _ in range(random.randint(1, 3)):
                    self.swipe(self.S_AB_FILTER_UP)
                self.wait_until_appear(cfg["photo"], wait_time=1)
            ret, challenge_num, boss_name = check_boss(cfg["photo"])
            if ret:  # 直接找到满足条件的, 则不需要打开筛选界面直接挑战即可
                return False, boss_name, cfg["photo"]
            if challenge_num > mx_challenge_num:
                mx_challenge_num = challenge_num
                mx_challenge_boss_name = boss_name
                photo = cfg['photo']
                logger.attr(mx_challenge_num, f'Select:{boss_name},{photo.name}')
        return True, mx_challenge_boss_name if mx_challenge_boss_name else '声望不够', photo if mx_challenge_boss_name else None

    def get_num_challenge(self, click_area):
        """
            获取鬼王挑战人数
        @param click_area: 鬼王相应的挑战按钮
        @type click_area:
        @return:
        @rtype:
        """
        # 如果鬼王不可挑战(未解锁),限制3次尝试打开鬼王详情界面
        if not self.open_boss_detail(click_area, 3):
            logger.info("%s unavailable", str(click_area))
            return 0
        return self.O_AB_NUM_OF_CHALLENGE.ocr_digit(self.device.image)

    def get_bossName(self, click_area):
        """
            获取鬼王名字
        @param click_area: 鬼王相应的挑战按钮
        @type click_area:
        @return:
        @rtype:
        """
        # 如果鬼王不可挑战(未解锁),限制3次尝试打开鬼王详情界面
        if not self.open_boss_detail(click_area, 3):
            logger.info("%s unavailable", str(click_area))
            return 0
        ocrName = self.O_AB_BOSS_NAME.detect_and_ocr(self.device.image)
        bossName = re.sub(r"[\'\[\]]", "", str([result.ocr_text for result in ocrName]))
        return bossName

    def open_boss_detail(self, battle: RuleImage, try_num: int = 3) -> bool:
        """
            打开鬼王详情界面
        @param battle:
        @type battle:
        @param try_num: 重试次数
        @type try_num:
        @return:    True        打开成功
                    False       打开失败
        @rtype:
        """
        try_count = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_CLOSE_RED) and self.appear(self.I_FIRE):
                self.screenshot()
                if not self.appear(self.I_FIRE):
                    continue
                return True
            if try_count >= try_num:
                logger.warning(f"Cannot boss_detail, try {try_count} times")
                return False
            if self.click(battle, interval=3):
                try_count += 1
                continue
        return True

    def is_group_ranked(self):
        """
            判断该鬼王是否已经获取到小组排名
        """
        return not self.appear(self.I_AB_GROUP_RANK_NONE)
        pass

    def open_filter(self, allow_detail: bool = False) -> bool:
        """Return True for the filter, or False for a confirmed allowed detail page."""
        logger.info("openFilter")
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_OPENED):
                return True
            if self.appear(self.I_FILTER):
                self.appear_then_click(self.I_FILTER, interval=3)
                continue
            # Only infer detail from its positive controls, never merely from
            # the missing filter icon. Confirm again after a fresh screenshot.
            if self.appear(self.I_AB_CLOSE_RED) and self.appear(self.I_FIRE):
                self.screenshot()
                if self.appear(self.I_AB_CLOSE_RED) and self.appear(self.I_FIRE):
                    if allow_detail:
                        logger.info('Area Boss detail already open; continue current boss')
                        return False
                    # List/tab callers still require an actual open filter.
                    self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=1)
                    continue
            time.sleep(0.2)

    def switch_to_collect(self):
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_TITLE_COLLECTION):
                break
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_COLLECTION_BTN, 1.5)
                continue

    def switch_to_famous(self):
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_TITLE_FAMOUS):
                break
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_FAMOUS_BTN, 1.5)
                continue

    def switch_to_reward(self):
        self.open_filter()
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_TITLE_REWARD):
                break
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_REWARD_BTN, 1.5)
                continue

    def check_common_chars(self, bossName, name):
        # 将两个字符串转为集合，去除重复的字符
        set_boss = set(bossName)
        set_name = set(name)

        # 计算交集，判断交集的元素个数
        common_chars = set_boss & set_name  # & 是集合的交集运算符

        if len(common_chars) >= 2:
            return 1
        else:
            return 0  # 如果交集的字符少于2个，可以根据需要返回其他值
if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    # time.sleep(3)
    # t.switchFloor2One()
    # t.switch2Level60()
    t.run()
