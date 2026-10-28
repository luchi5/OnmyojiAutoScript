import time
import re
import json
from pathlib import Path

from cached_property import cached_property

from module.logger import logger
from module.base.timer import Timer
from module.atom.image import RuleImage
from module.exception import GameStuckError
from tasks.base_task import BaseTask
from tasks.SixRealms.assets import SixRealmsAssets


class MoonSeaSkills(BaseTask, SixRealmsAssets):

    I_REFRESH_DIALOG = RuleImage(
        roi_front=(424,280,438,60), roi_back=(420,276,446,68), threshold=0.9,
        method='Template matching', file='./tasks/SixRealms/moon_sea/refresh/dialog.png')
    I_REFRESH_UNCHECKED = RuleImage(
        roi_front=(544,344,36,36), roi_back=(542,342,40,40), threshold=0.95,
        method='Template matching', file='./tasks/SixRealms/moon_sea/refresh/unchecked.png')
    I_REFRESH_CHECKED = RuleImage(
        roi_front=(544,344,36,36), roi_back=(542,342,40,40), threshold=0.95,
        method='Template matching', file='./tasks/SixRealms/moon_sea/refresh/checked.png')

    def _handle_skill_refresh_confirm(self) -> bool:
        if not self.appear(self.I_REFRESH_DIALOG):
            return False
        if not self.appear(self.I_REFRESH_CHECKED):
            if not self.appear(self.I_REFRESH_UNCHECKED):
                raise GameStuckError('Skill refresh reminder checkbox is unknown')
            self.click(self.I_REFRESH_UNCHECKED)
            # Click once only: a repeated click would clear the checkbox again.
            for _ in range(3):
                time.sleep(0.3)
                self.screenshot()
                if not self.appear(self.I_REFRESH_DIALOG):
                    raise GameStuckError('Skill refresh dialog changed before confirmation')
                if self.appear(self.I_REFRESH_CHECKED):
                    break
            else:
                raise GameStuckError('Skill refresh reminder checkbox was not checked')
        if not self.appear_then_click(self.I_UI_CONFIRM):
            raise GameStuckError('Skill refresh confirmation button is missing')
        logger.info('Skill refresh confirmed with do-not-remind checked')
        return True

    @property
    def cnt_skill101(self):
        return getattr(self, '_skill101_level', 0)

    @cnt_skill101.setter
    def cnt_skill101(self, value):
        self._skill101_level = min(5, max(0, int(value)))
        path = Path('log/six-realms-state') / (self.config.config_name + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'skill101': self._skill101_level}), encoding='utf-8')

    def restore_skill101(self):
        path = Path('log/six-realms-state') / (self.config.config_name + '.json')
        try:
            value = json.loads(path.read_text(encoding='utf-8'))['skill101']
            if type(value) is not int or not 1 <= value <= 5:
                raise ValueError('invalid level')
            self._skill101_level = value
        except (OSError, ValueError, KeyError, TypeError):
            self._skill101_level = 0
            logger.warning('Resumed skill level unknown; skip paid refresh and summon')

    def needs_skill101(self):
        return 0 < self.cnt_skill101 < 5


    def in_main(self, screenshot: bool = False):
        if screenshot:
            self.screenshot()
        if self.appear(self.I_M_STORE):
            return True
        if self.appear(self.I_M_STORE_ACTIVITY):
            return True
        if self.appear(self.I_BOSS_FIRE):
            return True
        return False

    def battle_lock_team(self):
        self.ui_click(self.I_BATTLE_TEAM_UNLOCK, self.I_BATTLE_TEAM_LOCK)
        return

    def island_battle(self):
        # 小怪战斗
        self.screenshot()
        while 1:
            self.screenshot()
            if self.appear(self.I_SKILL_REFRESH):
                break
            if self.appear(self.I_COIN):
                break
            if self.appear(self.I_SELECT_SHIKIGAMI_AND_CONFIRM):
                self.select_shikigami_and_confirm()
                continue
            if self.appear_then_click(self.I_NPC_FIRE, interval=1):
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
                continue
        self.device.stuck_record_clear()

    @cached_property
    def selects_button(self):
        return [
            self.I_SELECT_0,
            self.I_SELECT_1,
            self.I_SELECT_2,
            self.I_SELECT_3,
        ]

    def _select_skill(self) -> int:
        self.screenshot()
        self.wait_until_stable(self.I_SELECT_3)
        select = 3  # 从0开始计数
        button = None
        # 只选柔风
        if button is None and self.cnt_skill101 < 5 and self.appear(self.I_SKILL101):
            if self.cnt_skill101 > 0:
                self.cnt_skill101 += 1
            logger.info(f'Skill 101 level: {self.cnt_skill101}')
            button = self.I_SKILL101
        # elif button is None and self.appear(self.I_SKILL102):
        #     button = self.I_SKILL102
        # elif button is None and self.appear(self.I_SKILL103):
        #     button = self.I_SKILL103
        # elif button is None and self.appear(self.I_SKILL104):
        #     button = self.I_SKILL104
        elif button is None and self.appear(self.I_SKILL105):
            logger.info(f'Skill 105 level: {self.cnt_skill101}')
            button = self.I_SKILL105
        if button is not None:
            x, y = button.front_center()
            if x < 360:
                select = 0
            elif 360 <= x < 640:
                select = 1
            elif 640 <= x < 960:
                select = 2
            else:
                select = 3
        logger.info(f'Select {select}')
        return select

    def select_skill(self, refresh: bool = False):
        def check_coin_skill() -> bool:
            coin = self.O_COIN_NUM.ocr(self.device.image)
            return False if coin < 50 else True

        def check_refresh() -> bool:
            # 检测是否有钱刷新技能
            text = self.O_SKILL_REFRESH.ocr(self.device.image)
            matches = re.search(f"剩\d+次", text)
            if matches:
                refresh_time = int(matches.group()[1])
                logger.info(f'Refresh time: {refresh_time}')
                if refresh_time <= 0:
                    logger.warning('Refresh time is 0')
                    return False
                else:
                    return True
            return False
        # 战斗结束后选技能
        logger.info('Start select skill')

        while 1:
            self.screenshot()
            if self.in_main():
                break

            if self._handle_skill_refresh_confirm():
                continue
            if self.appear(self.I_UI_CONFIRM):
                self.ui_click_until_disappear(self.I_UI_CONFIRM)

            if self.appear(self.I_SKILL_REFRESH) and self.appear(self.I_SELECT_3) and not self.appear(self.I_COIN2):
                select = self._select_skill()
                # 如果没有柔风并且钱够并且还有刷新次数
                if refresh and self.needs_skill101() and select == 3 and check_coin_skill() and check_refresh():
                    logger.info('Refresh skill')
                    self.appear_then_click(self.I_SKILL_REFRESH)
                    self.wait_until_stable(self.I_UI_CONFIRM, timeout=Timer(2))
                    self._handle_skill_refresh_confirm()
                    self.wait_animate_stable(self.C_MAIN_ANIMATE_KEEP, timeout=1)
                    continue
                if self.appear_then_click(self.selects_button[select]):
                    self.wait_animate_stable(self.C_MAIN_ANIMATE_KEEP, timeout=2)
                    continue
            if self.appear_then_click(self.I_COIN, action=self.C_UI_REWARD, interval=1.5):
                continue


