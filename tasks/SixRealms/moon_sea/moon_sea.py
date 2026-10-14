import time

from module.logger import logger
from module.atom.image import RuleImage
from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.exception import GameStuckError

from cached_property import cached_property
from datetime import datetime, timedelta

from tasks.SixRealms.moon_sea.map import MoonSeaMap
from tasks.SixRealms.moon_sea.l101 import MoonSeaL101
from tasks.SixRealms.moon_sea.l102 import MoonSeaL102
from tasks.SixRealms.moon_sea.l103 import MoonSeaL103
from tasks.SixRealms.moon_sea.l104 import MoonSeaL104
from tasks.SixRealms.moon_sea.l105 import MoonSeaL105
from tasks.SixRealms.common import MoonSeaType


class MoonSea(MoonSeaMap, MoonSeaL101, MoonSeaL102, MoonSeaL103, MoonSeaL104, MoonSeaL105):

    @cached_property
    def island_func(self) -> dict:
        return {
            MoonSeaType.island101: self.run_l101(),
            MoonSeaType.island102: self.run_l102(),
            MoonSeaType.island103: self.run_103(),
            MoonSeaType.island104: self.run_l104(),
            MoonSeaType.island105: self.run_l105(),
        }

    @property
    def _conf(self):
        return self.config.model.six_realms.six_realms_gate

    def _run_moon_sea(self):
        # 在六道界面
        limit_time = self._conf.limit_time
        max_cont = self._conf.limit_count
        max_time: timedelta = timedelta(
            hours=limit_time.hour,
            minutes=limit_time.minute,
            seconds=limit_time.second
        )
        cnt = 0
        while 1:
            if not getattr(self._conf, 'double_rewards_only', False) and cnt >= max_cont:
                logger.info('Run out of count, exit')
                break
            if datetime.now() - self.start_time >= max_time:
                logger.info('Run out of time, exit')
                break
            if self.one() is False:
                break
            cnt += 1
        logger.info('Exit Moon Sea')


    def one(self):
        if self._start() is False:
            return False
        while 1:
            self.screenshot()
            if not self.in_main():
                continue
            isl_type, isl_num, isl_roi = self.decide()
            if isl_num == 1 and isl_type != MoonSeaType.island106:
                # 如果前一个，召唤一次宁息
                if not self.needs_skill101():
                    # 如果柔风满级就不召唤
                    pass
                elif self.appear(self.I_M_STORE) and not self.appear(self.I_M_STORE_ACTIVITY):
                    # 如果没有三百块就不能召唤
                    logger.info('There have no money to active store at the last island')
                    pass
                else:
                    self.activate_store()
                    self.wait_animate_stable(self.C_MAIN_ANIMATE_KEEP, timeout=3)
                    isl_type, isl_num, isl_roi = self.decide()
                    # enter_island below handles an unrecognized map once;
                    # do not enter here and then enter a second time.

            # 如果是boss
            if isl_type == MoonSeaType.island106:
                self.boss_team_lock()
                if self.boss_battle():
                    break
                else:
                    continue

            self.enter_island(isl_type=isl_type, isl_roi=isl_roi)
            isl_type = self.island_name()
            match isl_type:
                case MoonSeaType.island101: self.run_l101()
                case MoonSeaType.island102: self.run_l102()
                case MoonSeaType.island103: self.run_103()
                case MoonSeaType.island104: self.run_l104()
                case MoonSeaType.island105: self.run_l105()
            self.wait_animate_stable(self.C_MAIN_ANIMATE_KEEP, timeout=3)
            continue

    def _continue(self):
        logger.warning('Moon Sea Continue')
        while 1:
            self.screenshot()
            if self.in_main():
                break
            if self.appear(self.I_SELECT_SHIKIGAMI_AND_CONFIRM):
                self.select_shikigami_and_confirm()
                continue
            if self.appear_then_click(self.I_MCONINUE, interval=1):
                continue

    def read_double_reward_count(self):
        # Single preserves OCR failure as empty text; Digit would turn it into 0.
        counter = RuleOcr(roi=(1160,25,52,28), area=(1160,25,52,28),
                          mode='Single', method='Default', keyword='',
                          name='moon_sea_double_reward_count')
        previous = None
        for _ in range(4):
            self.screenshot()
            if not (self.appear(self.I_MSTART) or self.appear(self.I_MCONINUE)):
                previous = None
                continue
            text = str(counter.ocr(self.device.image)).strip()
            if not text or not text.isascii() or not text.isdigit():
                previous = None
                continue
            count = int(text)
            if count == previous:
                logger.info('Moon sea double rewards remaining: {}'.format(count))
                return count
            previous = count
            time.sleep(0.3)
        raise GameStuckError('Cannot confirm moon sea double reward count on entry page')

    def _start(self):
        logger.hr('Moon Sea', 1)
        while 1:
            self.screenshot()
            start = self.appear(self.I_MSTART)
            resume = self.appear(self.I_MCONINUE)
            if start or resume:
                if getattr(self._conf, 'double_rewards_only', False):
                    if self.read_double_reward_count() == 0:
                        logger.info('No double rewards; preserve unfinished progress and skip')
                        return False
                if resume:
                    self.restore_skill101()
                    self._continue()
                    return True
                break
            if self.appear_then_click(self.I_MENTER, interval=1):
                continue
        self.cnt_skill101 = 1
        logger.info("Ensure select ShouZu")
        while 1:
            self.screenshot()
            if self.appear(self.I_MSHOUZU):
                break
            if self.appear_then_click(self.I_MSHUTEN, interval=3):
                continue
            if self.appear_then_click(self.I_MSHOUZU_SELECT, interval=1):
                continue
        logger.info("Ensure selected ShouZu")
        while 1:
            self.screenshot()
            if self.appear(self.I_PREPARE_BATTLE):
                break
            if self.appear_then_click(self.I_MSTART_UNCHECK, interval=0.6):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            if self.appear_then_click(self.I_MSKIP, interval=1.5):
                continue
            if self.appear_then_click(self.I_MSTART, interval=3):
                continue
            if self.appear_then_click(self.I_MSTART_CONFIRM, interval=3):
                continue
            if self.appear_then_click(self.I_MSTART_CONFIRM2, interval=3):
                continue
            if self.appear_then_click(self.I_MCONINUE, interval=3):
                continue
        # Hook: select one shikigami and confirm before starting
        if self.appear(self.I_SELECT_SHIKIGAMI_AND_CONFIRM):
            self.select_shikigami_and_confirm()

        logger.info("Start Roguelike")
        while 1:
            self.screenshot()
            if self.appear(self.I_M_STORE):
                break
            if self.appear_then_click(self.I_MFIRST_SKILL, interval=1):
                continue
        # 选中第一个柔风
        logger.info("Select first skill")

    def island_name(self) -> MoonSeaType:
        while 1:
            self.screenshot()
            text = self.O_ISLAND_NAME.ocr(self.device.image)
            if '星' in text:
                return MoonSeaType.island105
            if '战' in text:
                return MoonSeaType.island104
            if '混' in text:
                return MoonSeaType.island103
            if '神秘' in text:
                return MoonSeaType.island102
            if '宁息' in text:
                return MoonSeaType.island101

    def boss_team_lock(self):
        while 1:
            self.screenshot()
            if self.appear(self.I_BOSS_TEAM_LOCK):
                break
            if self.appear_then_click(self.I_BOSS_TEAM_UNLOCK, interval=2):
                logger.info('Click lock Boss Team')
                continue

    def handle_boss_reward(self):
        # This large reward panel has a different title from the global popup.
        # Advance only a recognized panel, using blank space below its contents.
        title = RuleImage(roi_front=(520,119,241,64), roi_back=(470,80,350,140),
                          threshold=0.85, method='Template matching',
                          file='./tasks/SixRealms/moon_sea/markers/boss_reward_title.png')
        dismiss = RuleClick(roi_front=(580,610,100,40), roi_back=(580,610,100,40),
                            name='moon_sea_reward_dismiss')
        if self.appear_then_click(title, action=dismiss, interval=1.2):
            return True
        return self.ui_reward_appear_click()

    def boss_battle(self) -> bool:
        logger.hr('Boss Battle')
        self.ui_click_until_disappear(self.I_BOSS_FIRE, interval=1)
        self.device.stuck_record_clear()
        self.device.stuck_record_add('BATTLE_STATUS_S')
        double_requested = False
        while 1:
            self.screenshot()
            if self.handle_boss_reward():
                continue
            if self.appear(self.I_BOSS_SHARE):
                if double_requested:
                    logger.info('Weekly realm chest consumed: 1')
                break
            if self.appear(self.I_BOSS_BATTLE_GIVEUP):
                # 打boss失败了
                logger.warning('Boss battle give up')
                self.ui_click_until_disappear(self.I_BOSS_BATTLE_GIVEUP, interval=1)
                continue

            if self.appear_then_click(self.I_BOSS_USE_DOUBLE, interval=1):
                # 双倍奖励
                double_requested = True
                logger.info('Double reward')
                continue
            if self.appear_then_click(self.I_BOSS_GET_EXP, interval=1):
                logger.info('Get EXP')
                continue
            if self.appear_then_click(self.I_UI_CANCEL, interval=1):
                # 取消购买 万相赐福
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            if self.appear_then_click(self.I_BOSS_SKIP, interval=1):
                # 第二个boss
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
                continue
        logger.info('Boss battle end')
        if self.wait_until_appear(self.I_BOSS_SHUTU, wait_time=20):
            self.ui_click(self.I_BOSS_SHUTU, stop=self.I_MSTART)
        return True

    def select_shikigami_and_confirm(self):
        """Select shikigami and confirm to start battle."""
        while True:
            self.screenshot()
            if not self.appear(self.I_SELECT_SHIKIGAMI_AND_CONFIRM):
                break
            self.swipe(self.S_TEAM_SWIPE_UP)
            self.appear_then_click(self.I_MSTART_CONFIRM, interval=2)
            self.appear_then_click(self.I_COIN2, interval=2)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = MoonSea(c, d)
    t.screenshot()

    t.one()
