# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from time import sleep

from enum import Enum
from cached_property import cached_property
from datetime import datetime, timedelta

from module.logger import logger
from module.exception import GameStuckError, TaskEnd
from module.base.timer import Timer
from module.atom.ocr import RuleOcr
from module.atom.image import RuleImage
from module.atom.click import RuleClick

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.DemonEncounter.config import BossType, DemonEncounter, convert_to_general_battle_config
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_demon_encounter, page_demon_encounter_realworld, page_shikigami_records, page_town
from tasks.DemonEncounter.assets import DemonEncounterAssets
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.DemonEncounter.data.answer import Answer
from tasks.DemonEncounter.letter_ocr import read_letter_option


class LanternClass(Enum):
    BATTLE = 0  # 打怪  --> 无法判断因为怪的图片不一样，用排除法
    BOX = 1  # 开宝箱
    MAIL = 2  # 邮件答题
    REALM = 3  # 打结界
    EMPTY = 4  # 空
    MYSTERY = 5  # 神秘任务
    BOSS = 6  # 大鬼王


class ScriptTask(GameUi, GeneralBattle, DemonEncounterAssets, SwitchSoul):
    conf: DemonEncounter = None

    # Match only the coin at the rating reward position; amounts vary.
    I_DE_SETTLEMENT_COIN = RuleImage(
        roi_front=(873, 578, 36, 40), roi_back=(865, 570, 52, 56),
        threshold=0.8, method='Template matching',
        file='./tasks/DemonEncounter/demon/demon_settlement_coin.png',
    )

    # Keep the distinctive face, excluding map/background pixels at the rim.
    I_DE_BOSS_BEST_CORE = RuleImage(
        roi_front=(907, 650, 30, 35), roi_back=(903, 646, 38, 43),
        threshold=0.8, method='Template matching',
        file='./tasks/DemonEncounter/demon/demon_de_boss_best_core.png',
    )

    # Keep these settlement rules local to DemonEncounter; other battles use
    # different result pages. OCR is only needed after a confirmation appears.
    O_DE_BOSS_ESCAPED = RuleOcr(
        name='de_boss_escaped', mode='Full', method='Default',
        roi=(438, 270, 405, 92), area=(438, 270, 405, 92), keyword='逃走',
    )
    O_DE_SETTLEMENT_CONTINUE = RuleOcr(
        name='de_settlement_continue', mode='Full', method='Default',
        roi=(463, 661, 351, 58), area=(463, 661, 351, 58), keyword='点击屏幕',
    )

    def screenshot(self):
        """Use XY's long-battle recovery only while a real battle is visible."""
        try:
            return super().screenshot()
        except GameStuckError:
            if not self.exist_image() or not self.is_in_real_battle(False):
                raise
            logger.warning(
                'Demon encounter wait timeout, but battle is still active; '
                'reset stuck timer'
            )
            self.device.stuck_record_clear()
            self.device.stuck_record_add('BATTLE_STATUS_S')
            return super().screenshot()

    def run(self):
        self.conf = self.config.demon_encounter
        if not self.check_time():
            logger.warning('Time is not right')
            raise TaskEnd('DemonEncounter')
        # 切换御魂
        soul_target = self.resolve_soul_target()
        if soul_target is not None:
            self.goto_page(page_shikigami_records)
            self.checkout_soul(soul_target)
        self.goto_page(page_demon_encounter_realworld)
        # Exploration rewards are independent of the boss challenge quota.
        self.execute_lantern()
        self.finish_if_challenge_done()
        self.execute_boss()

        self.set_next_run(task='DemonEncounter', success=True, finish=False)
        raise TaskEnd('DemonEncounter')

    def check_challenge_done(self) -> bool:
        """
        OCR识别现世逢魔顶部"今日挑战次数:剩余/总", 支持老号X/1与新号X/2
        DigitCounter 返回 (current, remain, total), current 即剩余次数
        :return: True表示剩余=0, 今日已打过
        """
        for attempt in range(3):
            self.screenshot()
            current, _remain, total = self.O_DE_CHALLENGE_COUNT.ocr(self.device.image)
            if total > 0 and 0 <= current <= total:
                logger.info(f'Demon encounter challenge count: {current}/{total}')
                return current == 0
            if attempt < 2:
                time.sleep(0.3)
        logger.warning('Challenge count not recognized, assume attempts remain')
        return False

    def finish_if_challenge_done(self):
        """Player and script completions share the same successful daily schedule."""
        if self.check_challenge_done():
            logger.info('Demon encounter completed: no daily challenge attempts remain')
            self.set_next_run(task='DemonEncounter', success=True, finish=False)
            raise TaskEnd('DemonEncounter')

    def resolve_soul_target(self):
        """Respect separate boss selection and soul-switching enable flags."""
        boss_key = self.boss_type
        normal_key = boss_key.removeprefix('best_')
        normal = self.conf.demon_soul_config
        best = self.conf.best_demon_soul_config
        if boss_key.startswith('best_') and best.enable:
            selected, key = best, boss_key
        elif normal.enable:
            selected, key = normal, normal_key
            if boss_key.startswith('best_'):
                logger.info('Extreme demon soul switching disabled; use enabled normal soul config')
        else:
            logger.info('Soul switching is disabled for this demon encounter; keep current souls')
            return None
        preset = getattr(selected, key, None)
        # Preserve the upstream historical trailing-u field in old backups.
        if preset is None and key == 'best_demon_nightly_aramitama':
            preset = getattr(selected, 'best_demon_nightly_aramitamau', None)
        position_mode = getattr(selected, 'selection_mode', 'name') == 'position'
        if position_mode:
            preset = getattr(selected, 'position', None)
        parts = [part.strip() for part in preset.replace('，', ',').split(',')] if isinstance(preset, str) else []
        valid_position = (
            len(parts) == 2 and parts[0] in tuple(map(str, range(1, 8)))
            and parts[1] in ('1', '2', '3', '4')
        )
        if (len(parts) != 2 or not all(parts)
                or tuple(part.casefold() for part in parts) == ('group', 'team')
                or (position_mode and not valid_position)):
            logger.warning(f'Invalid demon soul preset for {key}; keep task unfinished and recheck in 3 minutes')
            self.set_next_run(task='DemonEncounter', finish=False, success=None, server=False,
                              target=datetime.now() + timedelta(minutes=3))
            raise TaskEnd('DemonEncounter soul preset is not configured')
        logger.info(f'Demon soul preset: {parts[0]} / {parts[1]} ({key}, '
                    f'mode={"position" if position_mode else "name"})')
        if position_mode:
            return tuple(map(int, parts))
        return tuple(parts)

    def checkout_soul(self, target=None):
        """Switch only to a validated, enabled preset for today's boss."""
        target = self.resolve_soul_target() if target is None else target
        if target is not None:
            if all(isinstance(value, int) for value in target):
                self.run_switch_soul(target)
            else:
                self.run_switch_soul_by_name(*target)

    def _preset_selection_interrupted(self) -> bool:
        """Timed preparation can end while the preset menu is still opening."""
        if (self.is_in_prepare(False) or self.appear(self.I_PRESET_ENSURE)
                or self.appear(self.I_PRESENT_LESS_THAN_5)):
            return False
        if self.is_in_real_battle(False) or any(self.appear(rule) for rule in (
                self.I_WIN, self.I_DE_WIN, self.I_FALSE, self.I_REWARD)):
            logger.info('Demon encounter battle started during preset selection; continue battle')
            return True
        return False

    def _click_boss_search(self, search_button) -> bool:
        """Retry fresh map frames; accept the cropped extreme face only on the map."""
        for attempt in range(3):
            self.screenshot()
            if self.appear_then_click(search_button, interval=2):
                return True
            if (self.best_demon_enable and self.appear(self.I_DE_BOSS)
                    and self.appear_then_click(self.I_DE_BOSS_BEST_CORE, interval=2)):
                return True
            if attempt < 2:
                time.sleep(0.5)
        return False

    def execute_boss(self):
        """
        打boss
        :return:
        """
        logger.hr('Start boss battle', 1)

        def find_boss():
            search_button = self.I_DE_BOSS_BEST if self.best_demon_enable else self.I_DE_BOSS
            boss_name = 'best boss' if self.best_demon_enable else 'normal boss'

            # 每轮搜索前若中央有未购买的宝箱展示, 先点左下角定位(小指针)把它挪开;
            # 一整轮(2次搜索)都没找到则重进逢魔之时清状态, 最多3轮。
            for reenter_round in range(1, 4):
                self.screenshot()
                if self.appear(self.I_DE_BOX_CENTER):
                    logger.info(
                        f'Box display at map center, click location to reset view '
                        f'(round {reenter_round}/3)'
                    )
                    self.appear_then_click(self.I_DE_LOCATION, interval=2)
                    time.sleep(1)

                # 最多重新执行两轮“逢魔/极逢魔 -> 地图中央首领”的完整流程。
                for search_attempt in range(1, 3):
                    self.device.click_record_clear()
                    self.screenshot()
                    if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                        return True
                    self.finish_if_challenge_done()
                    # 没找到boss但地图中央出现宝箱, 导致点击宝箱出现50勾玉购买界面(事后补救)
                    if self.appear(self.I_JADE_50):
                        self.ui_click_until_smt_disappear(self.I_DE_FIND, self.I_JADE_50, interval=1)
                        continue
                    if not self._click_boss_search(search_button):
                        raise GameStuckError(f'Cannot find {boss_name} search button')
                    logger.info(
                        f'Finding {boss_name}, attempt {search_attempt}/2 '
                        f'(re-enter round {reenter_round}/3)...'
                    )
                    time.sleep(1)

                    # 每轮点击地图中央框选的红色“集结”区域至多两次，
                    # 每次等待集结挑战标志5秒。
                    for center_attempt in range(1, 3):
                        self.click(self.C_DM_BOSS_CLICK, interval=2)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            self.screenshot()
                            if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                                logger.info(
                                    f'{boss_name} gather appeared after center click '
                                    f'{center_attempt}/2'
                                )
                                return True
                            time.sleep(0.2)
                        logger.warning(
                            f'{boss_name} gather did not appear after center click '
                            f'{center_attempt}/2'
                        )

                    # 本轮失败，返回逢魔地图，重新点击逢魔/极逢魔进行下一轮搜寻。
                    self.screenshot()
                    if self.appear(self.I_UI_BACK_RED):
                        self.appear_then_click(self.I_UI_BACK_RED, interval=2)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            self.screenshot()
                            if self.appear(search_button):
                                break
                            time.sleep(0.2)

                # 2次搜索都没找到: 重进地图清状态, 最多3轮
                logger.info(f'Boss not found, re-enter demon encounter map (round {reenter_round}/3)')
                self.goto_page(page_town)
                self.goto_page(page_demon_encounter_realworld)

            raise GameStuckError(
                f'Cannot enter {boss_name} after 3 re-enter rounds'
            )

        def enter_boss():
            logger.info('trying to enter boss...')
            # 点击集结挑战
            boss_fire_count = 0  # 五次没点到就意味着今天已经挑战过了
            ocr_people_item = self.O_DE_BEST_BOSS_PEOPLE if self.best_demon_enable else self.O_DE_BOSS_PEOPLE
            while 1:
                self.screenshot()

                if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                    current, remain, total = ocr_people_item.ocr(self.device.image)
                    if total == 300 and current >= 290:
                        logger.info('Boss battle people is full')
                        if not self.appear(self.I_UI_BACK_RED):
                            logger.warning('Boss battle people is full but no red back')
                            continue
                        self.ui_click_until_disappear(self.I_UI_BACK_RED)
                        # 退出重新选一个没人慢的boss
                        logger.info('Exit and reselect')
                        return False

                logger.info('Boss battle people is not full')

                if self.appear(self.I_BOSS_CONFIRM):
                    self.ui_click(self.I_BOSS_NO_SELECT, self.I_BOSS_SELECTED)
                    self.ui_click(self.I_BOSS_CONFIRM, self.I_BOSS_GATHER)
                    break
                if self.appear(self.I_BOSS_GATHER):
                    break
                if boss_fire_count >= 5:
                    logger.warning('Boss battle already done')
                    self.set_next_run(task='DemonEncounter', success=False, finish=True, server=True)
                    self.ui_click_until_disappear(self.I_UI_BACK_RED)
                    raise TaskEnd('DemonEncounter')

                if (self.appear_then_click(self.I_BOSS_FIRE, interval=3)
                        or self.appear_then_click(self.I_BEST_BOSS_FIRE, interval=3)):
                    boss_fire_count += 1
                    continue
            return True

        fail_count = 0
        while True:
            if fail_count >= 5:
                return
            if not find_boss():
                continue
            if enter_boss():
                break
            fail_count += 1

        logger.info('Boss battle confirm and enter')
        # 等待挑战, 5秒也是等
        time.sleep(5)
        # 延长时间并在战斗结束后改回来
        self.device.stuck_timer_long = Timer(480, count=480).start()
        preset_switched = False
        prepare_wait_started = time.monotonic()
        while True:
            self.screenshot()
            if self.appear(self.I_BOSS_DONE_CHECK):
                break
            if self.appear(self.I_BOSS_GATHER):
                prepare_wait_started = time.monotonic()
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
                logger.info('Boss Gathering...')
                sleep(2)
                continue
            if self.appear(self.I_BOSS_WAIT):
                prepare_wait_started = time.monotonic()
                logger.info('Boss battle failed, waiting for 2 seconds...')
                sleep(2)
                continue
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                prepare_wait_started = time.monotonic()
                if preset_switched:
                    self.run_general_battle()
                    continue
                preset_switched = True
                # 逢魔其他战斗会影响current_count导致大于0
                self.current_count = 0
                if self.best_demon_enable:
                    general_battle_config = convert_to_general_battle_config(self.boss_type,
                                                                             best_demon_battle_conf=self.conf.best_demon_battle_config)
                else:
                    general_battle_config = convert_to_general_battle_config(self.boss_type,
                                                                             demon_battle_conf=self.conf.demon_battle_config)
                self.run_general_battle(config=general_battle_config)
                continue
            if self._take_over_started_boss(prepare_wait_started, preset_switched):
                preset_switched = True
                prepare_wait_started = time.monotonic()
                continue
            logger.info('Unknown scene Or Boss fight failed.waiting for Prepare_Button appear...')
            self.wait_until_appear(self.I_PREPARE_HIGHLIGHT, wait_time=2)

        self.device.stuck_timer_long = Timer(300, count=300).start()

        # 等待回到挑战boss主界面
        self.wait_until_appear(self.I_BOSS_GATHER)
        while 1:
            self.screenshot()
            if self.appear(self.I_DE_LOCATION):
                break
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            if self.appear_then_click(self.I_BOSS_BACK_WHITE, interval=1):
                continue
        # 返回到封魔主界面

    def _take_over_started_boss(self, wait_started, preset_switched):
        """Recover a missed preparation screen without changing an active team."""
        if time.monotonic() - wait_started < 5:
            return False
        if not self.is_in_real_battle(False):
            return False
        logger.warning('Prepare button missed, but battle icon detected; take over battle settlement')
        if preset_switched:
            config = GeneralBattleConfig()
            self.current_count += 1
        else:
            kwargs = ({'best_demon_battle_conf': self.conf.best_demon_battle_config}
                      if self.best_demon_enable else
                      {'demon_battle_conf': self.conf.demon_battle_config})
            config = convert_to_general_battle_config(self.boss_type, **kwargs)
            self.current_count = 1
        # Do not re-enter preparation or switch presets after combat started.
        self.battle_wait(random_click_swipt_enable=config.random_click_swipt_enable)
        return True

    def wait_lantern_discovery(self):
        """Wait for lagging discovery results instead of repeatedly clicking."""
        deadline = time.monotonic() + 240
        previous = None
        stable = attempts = 0
        next_click = 0
        self.device.stuck_record_add('PAUSE')  # Existing 300-second wait guard.
        try:
            while time.monotonic() < deadline:
                self.screenshot()
                current, remain, total = self.O_DE_COUNTER.ocr(self.device.image)
                if total != 4 or not 0 <= current <= 4 or remain != 4 - current:
                    stable = 0
                    time.sleep(1)
                    continue
                if current == 0:
                    stable += 1
                    if stable >= 2:
                        return True
                    time.sleep(1)
                    continue
                stable = 0
                if previous is not None and current > previous:
                    time.sleep(1)
                    continue
                if previous is None or current < previous:
                    previous = current
                    attempts = 0
                    next_click = time.monotonic() + (2 if next_click else 0)
                if time.monotonic() >= next_click:
                    if attempts >= 3:
                        break
                    if self.appear_then_click(self.I_DE_FIND, interval=1):
                        attempts += 1
                        next_click = time.monotonic() + 60
                        logger.info(f'逢魔点灯剩余{current}/4，等待加载，最多60秒；本状态第{attempts}次点击')
                        self.device.stuck_record_add('PAUSE')
                time.sleep(1)
            return False
        finally:
            self.device.detect_record.discard('PAUSE')

    def execute_lantern(self):
        """
        点灯笼 四次
        :return:
        """
        if not self.wait_lantern_discovery():
            logger.warning('逢魔点灯长时间无进展，保留未完成状态，10分钟后重试')
            self.set_next_run(task='DemonEncounter', target=datetime.now() + timedelta(minutes=10), server=False)
            raise TaskEnd('DemonEncounter lantern discovery pending')
        logger.info('Lantern count success')
        # 然后领取红色达摩
        self.screenshot()
        if not self.appear(self.I_DE_AWARD):
            self.ui_get_reward(self.I_DE_RED_DHARMA)
        self.wait_until_appear(self.I_DE_AWARD)
        # 然后到四个灯笼
        match_click = {
            1: self.C_DE_1,
            2: self.C_DE_2,
            3: self.C_DE_3,
            4: self.C_DE_4,
        }
        for i in range(1, 5):
            logger.hr(f'Check lantern {i}', 3)
            lantern_type = self.check_lantern(i)
            match lantern_type:
                case LanternClass.BOX:
                    self._box(match_click[i])
                case LanternClass.MAIL:
                    self._mail(match_click[i])
                case LanternClass.REALM:
                    self._realm(match_click[i])
                case LanternClass.EMPTY:
                    logger.warning(f'Lantern {i} is empty')
                case LanternClass.BATTLE:
                    self._battle(match_click[i])
                case LanternClass.MYSTERY:
                    self._mystery(match_click[i])
                case LanternClass.BOSS:
                    self._boss(match_click[i])
            time.sleep(1)

    def check_lantern(self, index: int = 1):
        """
        检查灯笼的类型
        :param index: 四个灯笼，从1开始
        :return:
        """
        # 分类模板的搜索区(须容纳完整灯笼图案), 与点击区 C_DE_* 分离:
        # 点击区只包住内部图形, 模板放不进搜索区会全部误判成 battle
        match_roi = {
            1: self.C_DE_MATCH_1.roi_front,
            2: self.C_DE_MATCH_2.roi_front,
            3: self.C_DE_MATCH_3.roi_front,
            4: self.C_DE_MATCH_4.roi_front,
        }
        match_empty = {
            1: self.I_DE_DEFEAT_1,
            2: self.I_DE_DEFEAT_2,
            3: self.I_DE_DEFEAT_3,
            4: self.I_DE_DEFEAT_4,
        }
        self.I_DE_BOX.roi_back = match_roi[index]
        self.I_DE_LETTER.roi_back = match_roi[index]
        self.I_DE_MYSTERY.roi_back = match_roi[index]
        self.I_DE_REALM.roi_back = match_roi[index]
        self.I_DE_FIND_BOSS.roi_back = match_roi[index]
        target_box = self.I_DE_BOX
        target_letter = self.I_DE_LETTER
        target_mystery = self.I_DE_MYSTERY
        target_realm = self.I_DE_REALM
        target_find_boss = self.I_DE_FIND_BOSS
        target_empty = match_empty[index]

        # 开始判断
        self.screenshot()
        if self.appear(target_box):
            logger.info(f'Lantern {index} is box')
            return LanternClass.BOX
        elif self.appear(target_letter):
            logger.info(f'Lantern {index} is letter')
            return LanternClass.MAIL
        elif self.appear(target_mystery):
            logger.info(f'Lantern {index} is mystery task')
            return LanternClass.MYSTERY
        elif self.appear(target_realm):
            logger.info(f'Lantern {index} is realm')
            return LanternClass.REALM
        elif self.appear(target_empty):
            logger.info(f'Lantern {index} is empty')
            return LanternClass.EMPTY
        elif self.appear(target_find_boss):
            logger.info(f'Lantern {index} is boss')
            return LanternClass.BOSS
        else:
            # 无法判断是否是战斗的还是结界的
            logger.info(f'Lantern {index} is battle')
            return LanternClass.BATTLE

    def _box(self, target_click):
        box_buy_config = self.config.demon_encounter.box_buy_config
        while 1:
            self.screenshot()
            if self.appear(self.I_JADE_50):
                break
            if self.click(target_click, interval=1):
                continue
        while 1:
            self.screenshot()
            if not self.appear(self.I_MYSTERY_AMULET) and not (box_buy_config.box_buy_sushi and self.appear(self.I_SUSHI)):
                if self.appear_then_click(self.I_DE_FIND, interval=2.5):
                    break
            # 默认购买蓝票
            if self.appear(self.I_MYSTERY_AMULET):
                logger.info('Buy a mystery amulet for 50 jade')
                self.click(self.I_JADE_50)
                continue
            # 可选购买体力
            if box_buy_config.box_buy_sushi and self.appear(self.I_SUSHI):
                logger.info('Buy one hundred sushi for 50 jade')
                self.click(self.I_JADE_50)
                continue

    def _mail(self, target_click):
        # 答题
        def answer():
            click_match = {
                1: self.C_ANSWER_1,
                2: self.C_ANSWER_2,
                3: self.C_ANSWER_3,
            }
            index = None
            question = self.O_LETTER_QUESTION.detect_text(self.device.image)
            question = question.replace('?', '').replace('？', '')
            answer_1 = read_letter_option(self.device.image, self.O_LETTER_ANSWER_1)
            answer_2 = read_letter_option(self.device.image, self.O_LETTER_ANSWER_2)
            answer_3 = read_letter_option(self.device.image, self.O_LETTER_ANSWER_3)
            if any('获得奖励' in text for text in (question, answer_1, answer_2, answer_3)):
                logger.warning('Letter text contains a reward overlay; wait for it to clear')
                return None
            if not all(text.strip() for text in (question, answer_1, answer_2, answer_3)):
                logger.warning('Incomplete letter OCR; use available options or the default answer')
            if answer_1 == '其余选项皆对':
                index = 1
            elif answer_2 == '其余选项皆对':
                index = 2
            elif answer_3 == '其余选项皆对':
                index = 3
            if not index:
                index = Answer().answer_one(question=question, options=[answer_1, answer_2, answer_3])
            if index is None:
                index = 1
            logger.info(f'Question: {question}, Answer: {index}')
            return click_match[index]

        while 1:
            self.screenshot()
            if self.appear(self.I_LETTER_CLOSE):
                break
            if self.click(target_click, interval=1):
                continue
        logger.info('Question answering Start')
        result_mark = RuleImage(
            roi_front=(784, 436, 71, 72), roi_back=(780, 258, 85, 255),
            threshold=0.85, method='Template matching',
            file='./tasks/DemonEncounter/demon/letter_answer_result.png')
        timeout = Timer(90).start()
        answered = set()
        attempts = {}
        pending_count = None
        retry_wait = Timer(3)
        last_count = None
        stable_frames = 0
        while not timeout.reached():
            self.screenshot()
            if self.ui_reward_appear_click():
                last_count, stable_frames = None, 0
                time.sleep(0.5)
                continue
            if not self.appear(self.I_LETTER_CLOSE):
                time.sleep(0.5)
                self.screenshot()
                if not self.appear(self.I_LETTER_CLOSE):
                    logger.info('Question answering finished')
                    return
                continue
            current, _, total = self.O_LETTER_COUNT.ocr(self.device.image)
            if total != 3 or current not in (1, 2, 3):
                last_count, stable_frames = None, 0
                continue
            count = int(current)
            stable_frames = stable_frames + 1 if count == last_count else 1
            last_count = count
            # Results may advance directly without a reward overlay.
            if self.appear(result_mark):
                answered.add(count)
                if count == 3:
                    self.click(self.I_LETTER_CLOSE, interval=1.5)
                else:
                    self.click(RuleClick(roi_front=(560, 90, 140, 30),
                        roi_back=(560, 90, 140, 30), name='letter_result_continue'), interval=1.5)
                continue
            if stable_frames < 2 or count in answered:
                continue
            if pending_count is not None and count > pending_count:
                answered.add(pending_count)
                pending_count = None
            if pending_count == count and not retry_wait.reached():
                continue
            if attempts.get(count, 0) >= 3:
                raise GameStuckError(f'Demon letter question {count}: no result after 3 answer attempts')
            logger.hr(f'Answer {count}', 3)
            answer_click = answer()
            if answer_click is None:
                last_count, stable_frames = None, 0
                continue
            # OCR takes time: a delayed reward can cover this question meanwhile.
            self.screenshot()
            if self.ui_reward_appear_click():
                last_count, stable_frames = None, 0
                time.sleep(0.5)
                continue
            if not self.appear(self.I_LETTER_CLOSE):
                last_count, stable_frames = None, 0
                continue
            now, _, maximum = self.O_LETTER_COUNT.ocr(self.device.image)
            if now != count or maximum != 3 or self.appear(result_mark):
                last_count, stable_frames = None, 0
                continue
            # BaseTask.click returns False without an interval even when input
            # was sent. Only a later screenshot can acknowledge this attempt.
            self.click(answer_click)
            attempts[count] = attempts.get(count, 0) + 1
            pending_count = count
            retry_wait.reset()
            # Keep the existing per-question click-history boundary, but a
            # retry must not erase the device's repeated-click protection.
            if attempts[count] == 1:
                self.device.click_record_clear()
        raise GameStuckError('Demon letter did not advance after answering; stop repeated clicks')

    def _battle(self, target_click):
        while 1:
            self.screenshot()
            if not self.appear(self.I_DE_LOCATION):
                logger.info('Battle Start')
                break
            if self.appear(self.I_DE_SMALL_FIRE):
                # 小鬼王
                logger.info('Small Boss')
                while 1:
                    self.screenshot()
                    if not self.appear(self.I_DE_SMALL_FIRE):
                        break
                    if self.appear_then_click(self.I_DE_SMALL_FIRE, interval=1):
                        continue
                break

            if self.click(target_click, interval=1):
                continue
        if self.run_general_battle():
            logger.info('Battle End')

    def _realm(self, target_click):
        # 结界
        while 1:
            self.screenshot()
            if not self.appear(self.I_DE_LOCATION):
                logger.info('Battle Start')
                break
            if self.appear_then_click(self.I_DE_REALM_FIRE, interval=0.7):
                continue

            if self.click(target_click, interval=1):
                continue
        if self.run_general_battle():
            logger.info('Battle End')

    def _mystery(self, target_click):
        # 神秘任务， 不做
        pass

    def _boss(self, target_click):
        # 运气爆表，点灯笼出现大鬼王
        while 1:
            self.screenshot()
            if self.appear(self.I_BOSS_KILLED):
                # 这个大鬼王已经击败
                logger.warning('Boss already killed')
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                break
            if self.appear(self.I_BOSS_FIRE):
                self.execute_boss()
                break
            if self.click(target_click, interval=2.3):
                continue

    def check_time(self):
        """
        检查时间是否正确，
        如果正确就继续
        如果不在17:00到22:00之间,就推迟到下一个 17:30
        :return:
        """
        now = datetime.now()
        if now.hour < 17:
            # 17点之前，推迟到当天的17点半
            logger.info('Before 17:00, wait to 17:30')
            target_time = datetime(now.year, now.month, now.day, 17, 30, 0)
            self.set_next_run(task='DemonEncounter', success=False, finish=False, target=target_time)
            return False
        elif now.hour >= 23:
            # 23点之后，推迟到第二天的17:30
            logger.info('After 23:00, wait to 17:30')
            target_time = datetime(now.year, now.month, now.day, 17, 30, 0) + timedelta(days=1)
            self.set_next_run(task='DemonEncounter', success=False, finish=False, target=target_time)
            return False
        else:
            return True

    def _finish_failed_settlement(self):
        """Wait through delayed loss rewards before handing control to the map."""
        timeout = Timer(20).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(self.I_DE_LOCATION):
                return
            if self.is_in_real_battle(False):
                continue
            if self.appear_then_click(self.I_FALSE, interval=1):
                continue
            if self.appear_then_click(self.I_REWARD, interval=1):
                continue
            if self.appear_then_click(self.O_DE_SETTLEMENT_CONTINUE, interval=1):
                continue
        raise GameStuckError('Demon failed settlement did not return to map')

    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        # 重写
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        # 战斗过程 随机点击和滑动 防封
        logger.info("Start battle process")
        check_timer = None
        while 1:
            self.screenshot()
            # The escaped-boss dialog obscures the normal loss marker. Close
            # both the notice and its settlement before the next lantern.
            if (not self.is_in_real_battle(False)
                    and self.appear(self.I_UI_CONFIRM_SAMLL)
                    and self.appear(self.O_DE_BOSS_ESCAPED)):
                logger.warning('Demon encounter boss escaped; close failed settlement')
                self.ui_click_until_disappear(self.I_UI_CONFIRM_SAMLL)
                self._finish_failed_settlement()
                return False
            if self.appear(self.I_DE_WIN):
                logger.info('Appear [demon encounter] win button')
                self.ui_click_until_disappear(self.I_DE_WIN)
                check_timer = Timer(3)
                check_timer.start()
                continue
            if self.appear_then_click(self.I_WIN, interval=1):
                logger.info('Appear win button')
                check_timer = Timer(3)
                check_timer.start()
                continue
            if self.appear(self.I_REWARD):
                logger.info('Win battle')
                self.ui_click_until_disappear(self.I_REWARD)
                return True

            # 失败的
            if self.appear(self.I_FALSE):
                logger.warning('False battle')
                self.ui_click_until_disappear(self.I_FALSE)
                self._finish_failed_settlement()
                return False
            if (not self.is_in_real_battle(False)
                    and self.appear(self.I_DE_SETTLEMENT_COIN)):
                logger.info('Demon encounter coin settlement; continue')
                self.ui_click_until_disappear(self.I_DE_SETTLEMENT_COIN)
                check_timer = Timer(3).start()
                continue
            # 时间到
            if check_timer and check_timer.reached():
                logger.warning('Obtain battle timeout')
                return True

    @property
    def boss_type(self) -> str:
        boss_name = BossType(datetime.now().weekday()).name
        if self.best_demon_enable:
            return f'best_demon_{boss_name}'
        return f'demon_{boss_name}'

    @property
    def best_demon_enable(self) -> bool:
        boss_name = BossType(datetime.now().weekday()).name
        return getattr(self.conf.best_demon_boss_config, f'best_demon_{boss_name}_select', False)

    def wait_and_click(self, target) -> bool:
        """
        等待目标出现并点击，然后返回True
        :param target: 图片规则
        :return: 点击成功返回True
        """
        from module.base.timer import Timer
        from module.exception import TaskEnd
        timeout = 60
        timer = Timer(timeout)
        timer.start()
        while not timer.reached():
            self.screenshot()
            if self.appear(target):
                if self.appear_then_click(target, interval=1):
                    return True
        logger.warning(f'wait_and_click {target.name} timeout')
        return False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('du')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
    # t.battle_wait(True)
