# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from copy import copy
from datetime import datetime, timedelta
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
from typing import List


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, AreaBossAssets):

    def _get_dynamic_boss_count(self) -> int:
        """
        从地域鬼王主界面 OCR 声望值，动态决定可挑战次数
        :return: 可挑战鬼王数量 (1/2/3)
        """
        self.screenshot()
        rep = self.O_AB_REPUTATION.ocr_digit(self.device.image)
        logger.info(f"Area boss reputation: {rep}")
        if rep >= 10000:
            return 3
        elif rep >= 2000:
            return 2
        else:
            return 1

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
        if available >= 3:
            self.boss_fight(self.I_BATTLE_1)
            self.boss_fight(self.I_BATTLE_2)
            self.boss_fight(self.I_BATTLE_3)
        elif available == 2:
            self.boss_fight(self.I_BATTLE_1)
            self.boss_fight(self.I_BATTLE_2)
        elif available == 1:
            self.boss_fight(self.I_BATTLE_1)
        # 退出
        self.go_back()
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

    def boss_fight(self, battle: RuleImage, fileter_open: bool = True) -> bool:
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
            self.open_filter()
        # 如果打不开鬼王详情界面,直接退出
        if not self.open_boss_detail(battle, 3):
            return False

        # 如果已经打过该BOSS,直接跳过不打了
        if self.is_group_ranked():
            logger.warning("There is no boss could be challenged")
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=3)
            return True

        # Only start a battle after the requested difficulty is confirmed.
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

    def _switch_to_floor(self, target: RuleImage, first: bool) -> bool:
        """Select a visible star and verify the closed selector's title."""
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
        selected_text = copy(self.O_AB_BOSS_NAME)
        # Exclude the difficulty icon on the left and the selector arrow.
        selected_text.roi = [378, 112, 78, 38]
        selected_text.name = 'AB_SELECTED_STAR'
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
            menu_closed = not list_visible and (not opened or selections > 0)
            title_matches = self.appear(selected)
            if menu_closed and not title_matches:
                # Header lettering has a different background from the menu.
                # Reuse the account's OCR session and require an exact star,
                # rather than relaxing a template enough to confuse levels.
                text = re.sub(r'\s+', '', selected_text.detect_text(self.device.image))
                title_matches = text in (
                    ('壹星', '一星', '1星') if first else ('拾星', '十星', '10星')
                )
            if title_matches and menu_closed:
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
                    logger.warning('Area boss selected star was not confirmed in the title')
                    return False
                time.sleep(0.2)
                continue
            if list_visible:
                opened = True
            if opened:
                if swipes >= 6:
                    logger.warning('Area boss target star not found after bounded scrolling')
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
                return False
            if self.click(self.C_AB_JI_FLOOR_SELECTED, interval=3):
                opens += 1
                last_open = now
            time.sleep(0.2)
        logger.warning('Area boss star selection timed out')
        return False

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

    def open_filter(self):
        """打开筛选界面"""
        logger.info("openFilter")
        self.ui_click(self.I_FILTER, self.I_AB_FILTER_OPENED, interval=3)

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
