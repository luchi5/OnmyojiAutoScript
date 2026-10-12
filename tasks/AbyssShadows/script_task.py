# This Python file uses the following encoding: utf-8
# @brief    Ryou Dokan Toppa (阴阳竂道馆突破功能)
# @author   jackyhwei
# @note     draft version without full test
# github    https://github.com/roarhill/oas

from datetime import datetime, timedelta
import random
import numpy as np
import time
from enum import Enum
from cached_property import cached_property
from time import sleep

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.config_base import ConfigBase, Time
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_kekkai_toppa, page_shikigami_records, page_guild
from tasks.RealmRaid.assets import RealmRaidAssets

from module.logger import logger
from module.exception import TaskEnd
from module.atom.image_grid import ImageGrid
from module.base.utils import point2str
from module.base.timer import Timer
from module.exception import GamePageUnknownError
from pathlib import Path
from tasks.AbyssShadows.config import AbyssShadows
from tasks.AbyssShadows.assets import AbyssShadowsAssets
from tasks.AbyssShadows.selection_assets import AbyssSelectionAssets

class AreaType:
    """ 暗域类型 """
    DRAGON = AbyssShadowsAssets.I_ABYSS_DRAGON  # 神龙暗域
    PEACOCK = AbyssShadowsAssets.I_ABYSS_PEACOCK  # 孔雀暗域
    FOX = AbyssShadowsAssets.I_ABYSS_FOX  # 白藏主暗域
    LEOPARD = AbyssShadowsAssets.I_ABYSS_LEOPARD # 黑豹暗域

    @cached_property
    def name(self) -> str:
        """

        :return:
        """
        return Path(self.file).stem.upper()

    def __str__(self):
        return self.name

    __repr__ = __str__

class EmemyType(Enum):

    """ 敌人类型 """
    BOSS = 1  #  首领
    GENERAL = 2  #  副将
    ELITE = 3  #  精英


class CilckArea:
    """ 点击区域 """
    GENERAL_1 = AbyssShadowsAssets.C_GENERAL_1_CLICK_AREA
    GENERAL_2 = AbyssShadowsAssets.C_GENERAL_2_CLICK_AREA
    ELITE_1 = AbyssShadowsAssets.C_ELITE_1_CLICK_AREA
    ELITE_2 = AbyssShadowsAssets.C_ELITE_2_CLICK_AREA
    ELITE_3 = AbyssShadowsAssets.C_ELITE_3_CLICK_AREA
    BOSS= AbyssShadowsAssets.C_BOSS_CLICK_AREA

    @cached_property
    def name(self) -> str:
        """

        :return:
        """
        return Path(self.file).stem.upper()

    def __str__(self):
        return self.name

    __repr__ = __str__


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, AbyssShadowsAssets, AbyssSelectionAssets):
    

    boss_fight_count = 0  # 首领战斗次数
    general_fight_count = 0  # 副将战斗次数
    elite_fight_count = 0  # 精英战斗次数
    
    def run(self):
        """ 狭间暗域主函数

        :return:
        """
        from tasks.Component.daily_closeout import report_closeout_outcome

        cfg: AbyssShadows = self.config.abyss_shadows
        self._abyss_closeout_date = datetime.now().date()
        # 每次调用重新取得战斗证明，结束前单独核对实际完成的次数。
        self._closeout_fight_counts = {'BOSS': 0, 'GENERAL': 0, 'ELITE': 0}
        self._abyss_preset_selected = False
        self.boss_fight_count = 0
        self.general_fight_count = 0
        self.elite_fight_count = 0

        if cfg.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(cfg.switch_soul_config.switch_group_team)
        if cfg.switch_soul_config.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(cfg.switch_soul_config.group_name, cfg.switch_soul_config.team_name)
        today = datetime.now().weekday()
        if today not in [4, 5, 6]:
            logger.info(f"Today is not abyss shadows day, exit")
            # 设置下次运行时间为本周五
            self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_friday, time_delta=4-today)
            raise TaskEnd
        success = True
        # 进入狭间
        self.goto_abyss_shadows()
        # 已封印的区域不能进入，从第一个仍可挑战的区域开始。
        areas = (AreaType.DRAGON, AreaType.PEACOCK, AreaType.FOX, AreaType.LEOPARD)
        if not any(self.select_boss(area) for area in areas):
            logger.warning("Failed to enter abyss shadows")
            self.goto_main()
            self.set_next_run(task='AbyssShadows', finish=False, server=True, success=False)
            raise TaskEnd
        
        # 等待可进攻时间  
        self.device.stuck_record_add('BATTLE_STATUS_S')
        # 集结中图片
        self.wait_until_disappear(self.I_WAIT_TO_START)
        self.device.stuck_record_clear()

        # 两种模式均按真实战斗补足 2/4/6；智能模式保持同类连续攻打。
        if cfg.abyss_shadows_combat_time.CombatTime_enable:
            for enemy_type, required_count in (
                    (EmemyType.BOSS, 2), (EmemyType.GENERAL, 4), (EmemyType.ELITE, 6)):
                self.fight_and_switch(enemy_type, required_count)
        else:
            for area in areas:
                if not self.change_area(area):
                    continue
                # 首领已击破不代表副将和精英也已击破。
                for enemy_type in (EmemyType.BOSS, EmemyType.GENERAL, EmemyType.ELITE):
                    self.find_enemy(enemy_type)
                if self.quota_complete():
                    break
        logger.info(f"Actual fight times: boss {self.boss_fight_count}/2, "
                    f"general {self.general_fight_count}/4, elite {self.elite_fight_count}/6")
        if not self.quota_complete():
            logger.warning("Available areas exhausted before reaching battle quotas")

        # 保持好习惯，一个任务结束了就返回到庭院，方便下一任务的开始
        self.goto_main()

        # 设置下次运行时间
        if success:
            print("我要重新设置时间了")
            if today == 4:
                # 周五推迟到周六
                logger.info(f"The next abyss shadows day is Saturday")
                self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_saturday, time_delta=1)
            elif today == 5:
                # 周六推迟到周日
                logger.info(f"The next abyss shadows day is Sunday")
                self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_sunday, time_delta=1)
            elif today == 6:
                # 周日推迟到下周五
                logger.info(f"The next abyss shadows day is Friday")
                self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_friday, time_delta=5)
        else:
            self.set_next_run(task='AbyssShadows', finish=True, server=True, success=False)
        if success and self._abyss_closeout_date == datetime.now().date():
            actual = self._closeout_fight_counts
            quota_confirmed = (
                self.boss_fight_count >= 2 and self.general_fight_count >= 4
                and self.elite_fight_count >= 6 and actual['BOSS'] >= 2
                and actual['GENERAL'] >= 4 and actual['ELITE'] >= 6
            )
            report_closeout_outcome(
                self.config, 'AbyssShadows', 'completed' if quota_confirmed else 'skipped',
                detail=('actual_battle_quota_confirmed' if quota_confirmed else
                        'targets_exhausted_before_quota: '
                        f"boss={actual['BOSS']}/2, general={actual['GENERAL']}/4, "
                        f"elite={actual['ELITE']}/6"),
            )
        raise TaskEnd



    def fight_count(self, enemy_type):
        return getattr(self, {
            EmemyType.BOSS: 'boss_fight_count',
            EmemyType.GENERAL: 'general_fight_count',
            EmemyType.ELITE: 'elite_fight_count',
        }[enemy_type])

    def quota_complete(self):
        return (self.boss_fight_count >= 2 and self.general_fight_count >= 4
                and self.elite_fight_count >= 6)

    def fight_and_switch(self, enemy_type, required_count):
        # 每种敌人重新扫描四个区域，避免首领流程向后切图后漏掉前面的副将/精英。
        # find_enemy 内部完成真实战斗后更新计数，此处不能再次假定 +1/+2/+3。
        for area in (AreaType.DRAGON, AreaType.PEACOCK, AreaType.FOX, AreaType.LEOPARD):
            if self.fight_count(enemy_type) >= required_count:
                break
            if not self.change_area(area):
                continue
            self.find_enemy(enemy_type)
        return self.fight_count(enemy_type)

    def is_area_selection(self):
        # 顶层标题和切换弹窗提示与难度、神龙是否已封印无关。
        return self.appear(self.I_ABYSS_SELECT_TITLE) or self.appear(self.I_ABYSS_SELECT_MODAL)

    def is_area_sealed(self, area):
        marker = {
            AreaType.DRAGON: self.I_ABYSS_SELECT_DRAGON_SEALED,
            AreaType.PEACOCK: self.I_ABYSS_SELECT_PEACOCK_SEALED,
            AreaType.FOX: self.I_ABYSS_SELECT_FOX_SEALED,
            AreaType.LEOPARD: self.I_ABYSS_SELECT_LEOPARD_SEALED,
        }[area]
        return self.appear(marker)

    def check_current_area(self) -> AreaType:
        timer = Timer(20).start()
        while True:
            self.screenshot()
            if timer.reached():
                raise GamePageUnknownError('Cannot identify current Abyss Shadows area')
            # 弹窗后面仍可看到原区域标题，必须先识别弹窗。
            if self.is_area_selection():
                return None
            if self.appear_then_click(self.I_ABYSS_MAP_EXIT, interval=1):
                continue
            for area, marker in ((AreaType.PEACOCK, self.I_PEACOCK_AREA),
                                 (AreaType.DRAGON, self.I_DRAGON_AREA),
                                 (AreaType.FOX, self.I_FOX_AREA),
                                 (AreaType.LEOPARD, self.I_LEOPARD_AREA)):
                if self.appear(marker):
                    return area

    def change_area(self, area_name: AreaType) -> bool:
        timer = Timer(20).start()
        while True:
            self.screenshot()
            if timer.reached():
                raise GamePageUnknownError('Cannot switch Abyss Shadows area')
            if self.is_area_selection():
                return self.select_boss(area_name)
            if self.appear_then_click(self.I_ABYSS_MAP_EXIT, interval=1):
                continue
            if self.check_current_area() == area_name:
                return True
            self.appear_then_click(self.I_CHANGE_AREA, interval=4)

    def goto_main(self):
        ''' 保持好习惯，一个任务结束了就返回庭院，方便下一任务的开始或者是出错重启
        '''
        logger.info("Exiting abyss_shadows")
        self.goto_page(page_main)

    def goto_abyss_shadows(self) -> bool:
        ''' 进入狭间
        :return bool
        '''
        logger.info("Entering abyss_shadows")
        self.goto_page(page_guild)
        
        while 1:
            self.screenshot()
            # Entering Shenshe can resume an active Abyss Shadows map directly.
            if self.appear(self.I_ABYSS_NAVIGATION):
                logger.info("Already in abyss_shadows")
                break
            # 进入神社
            if self.appear_then_click(self.I_RYOU_SHENSHE,interval=1):
                logger.info("Enter Shenshe")
                continue
            # 查找狭间
            if not self.appear(self.I_ABYSS_SHADOWS, threshold=0.8):
                self.swipe(self.S_TO_ABBSY_SHADOWS,interval=3)
                continue
            # 进入狭间
            if self.appear_then_click(self.I_ABYSS_SHADOWS):
                logger.info("Enter abyss_shadows")
                break
        return True
 
    def select_boss(self, area_name: AreaType) -> bool:
        click_times = 0
        timer = Timer(20).start()
        click = {
            AreaType.DRAGON: self.C_ABYSS_DRAGON,
            AreaType.PEACOCK: self.C_ABYSS_PEACOCK,
            AreaType.FOX: self.C_ABYSS_FOX,
            AreaType.LEOPARD: self.C_ABYSS_LEOPARD,
        }[area_name]
        while True:
            self.screenshot()
            if timer.reached():
                raise GamePageUnknownError('Cannot identify Abyss Shadows selection page')
            if self.is_area_selection():
                if self.is_area_sealed(area_name):
                    logger.info(f"Skip sealed area {area_name.name}")
                    return False
                # 旧神龙图只作为兼容入口，封印后仍能识别区域选择页。
                if click_times >= 3:
                    logger.warning(f"Cannot enter area {area_name.name}")
                    return False
                if self.click(click, interval=2):
                    click_times += 1
                continue
            if self.appear(self.I_ABYSS_NAVIGATION):
                return True
            if self.appear(self.I_ABYSS_DRAGON):
                if click_times >= 3:
                    return False
                if self.click(click, interval=2):
                    click_times += 1

    def find_enemy(self, enemy_type: EmemyType) -> bool:
        ''' 寻找敌人,并开始寻路进入战斗
        :return 是否找到敌人，若目标已死亡则返回False，否则返回True
        True 找到敌人，并已经战斗完成
        '''
        print(f"Find enemy: {enemy_type}")
        while 1:
            self.screenshot()
            # 点击战报按钮
            if self.appear(self.I_ABYSS_MAP):
                break
            if self.appear_then_click(self.I_ABYSS_NAVIGATION,interval=1):
                continue
 
        match enemy_type:
            case EmemyType.BOSS: success = self.run_boss_fight()
            case EmemyType.GENERAL: success = self.run_general_fight()
            case EmemyType.ELITE: success = self.run_elite_fight()
            
        return success    

    def run_boss_fight(self) -> bool:
        ''' 首领战斗
        只要进入了战斗都返回成功
        :return 
        '''
        if self.boss_fight_count >= 2:
            logger.info(f"boss fight count {self.boss_fight_count} times, skip")
            return True
        success = True
        logger.info(f"Run boss fight") 
        if self.click_emeny_area(CilckArea.BOSS):
            logger.info(f"Click {CilckArea.BOSS.name}")
            if not self.run_general_battle_back(Monster_type="BOSS"):
                return False
            self.boss_fight_count += 1
            logger.info(f'Fight, boss_fight_count {self.boss_fight_count} times')
        else:
            success = False
        return success

    def run_general_fight(self) -> bool:
        ''' 副将战斗
        :return 
        '''
        general_list = [CilckArea.GENERAL_1, CilckArea.GENERAL_2]
        logger.info(f"Run general fight") 
        for general in general_list:
            # 副将战斗次数达到4个时，退出循环
            if self.general_fight_count >= 4:
                logger.info(f"general fight count {self.general_fight_count} times, skip")
                break
            if self.click_emeny_area(general):
                logger.info(f"Click {general.name}")
                if not self.run_general_battle_back(Monster_type="GENERAL"):
                    continue
                self.general_fight_count += 1
                logger.info(f'Fight, general_fight_count {self.general_fight_count} times')
        return True


    def run_elite_fight(self) -> bool:
        ''' 精英战斗
        :return 
        '''
        elite_list = [CilckArea.ELITE_1, CilckArea.ELITE_2, CilckArea.ELITE_3]
        logger.info(f"Run elite fight")  
        for elite in elite_list:
            # 精英战斗次数达到6个时，退出循环
            if self.elite_fight_count >= 6:
                logger.info(f"Elite fight count {self.elite_fight_count} times, skip")
                break
            if self.click_emeny_area(elite):
                logger.info(f"Click {elite.name}")
                if not self.run_general_battle_back(Monster_type="ELITE"):
                    continue
                self.elite_fight_count += 1
                logger.info(f'Fight, elite_fight_count {self.elite_fight_count} times')
        return True

    def click_emeny_area(self, click_area: CilckArea) -> bool:
        suceess = True
        ''' 点击敌人区域
        
        :return 
        '''
        logger.info(f"Click emeny area: {click_area.name}")
        # 点击战报
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_ABYSS_NAVIGATION, interval=1.5):
                logger.info(f"Click {self.I_ABYSS_NAVIGATION.name}")
                continue
            if self.appear(self.I_ABYSS_MAP):
                logger.info("Find abyss map, exit")
            
            click_times = 0
            # 点击攻打区域
            while 1:
                self.screenshot()
                # 如果点3次还没进去就表示目标已死亡,跳过
                if click_times >= 3:
                    logger.warning(f"Failed to click {click_area}")
                    return
                # 出现前往按钮就退出
                if self.appear(self.I_ABYSS_GOTO_ENEMY):
                    break
                if self.click(click_area,interval=1.5):
                    click_times += 1
                    continue
                if self.appear_then_click(self.I_ENSURE_BUTTON,interval=1):
                    continue
        
            # 点击前往按钮
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_ABYSS_GOTO_ENEMY,interval=1):
                    logger.info(f"Click {self.I_ABYSS_GOTO_ENEMY.name}")
                    # 点击敌人后，如果是不同区域会确认框，点击确认                
                    if self.appear_then_click(self.I_ENSURE_BUTTON, interval=1):
                        logger.info(f"Click {self.I_ENSURE_BUTTON.name}")
                    # 跑动画比较花时间
                    sleep(3)
                    continue
                else:
                    break
            
            # 如果遇到点击前往按钮后不动的 bug，则再次尝试进入
            if self.wait_until_appear(self.I_ABYSS_FIRE, wait_time=20):
                break
            logger.warning("Failed to enter fire")
        
        # 点击战斗按钮
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_ABYSS_FIRE,interval=1):
                  
                logger.info(f"Click {self.I_ABYSS_FIRE.name}")        
                # 挑战敌人后，如果是奖励次数上限，会出现确认框   
                if self.appear_then_click(self.I_ENSURE_BUTTON, interval=1):
                    logger.info(f"Click {self.I_ENSURE_BUTTON.name}")
                continue
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                break
            
        return suceess

    def _preset_selection_interrupted(self):
        # Preset selection can race a transition into battle or back to the map.
        return (self.is_in_real_battle(False) or self.appear(self.I_WIN)
                or self.appear(self.I_ABYSS_NAVIGATION))

    def run_general_battle_back(self, Monster_type: str) -> bool:
        """
        重写父类方法，因为狭间暗域的准备和战斗流程不一样
        进入挑战然后直接返回
        :param config:
        :return:
        """
        cfg: AbyssShadows = self.config.abyss_shadows
        while 1:
            #确保进入战斗
            self.screenshot()
            if self.wait_until_appear(self.I_EQUIPPING, wait_time=4):
                battle = cfg.general_battle_config
                if (battle.preset_enable and not battle.lock_team_enable
                        and not getattr(self, '_abyss_preset_selected', False)):
                    self.switch_preset_team(True, battle.preset_group, battle.preset_team)
                    self.screenshot()
                    if self._preset_selection_interrupted():
                        continue
                    self._abyss_preset_selected = True
                    # Selection changes the frame; recheck before clicking ready.
                    if not self.appear(self.I_EQUIPPING):
                        continue
                self.click(self.I_EQUIPPING, interval=1.5)
            if not self.appear(self.I_EQUIPPING):
                break
        logger.info(f"Click {self.I_EQUIPPING.name}")
        logger.info(f"点击准备了")

        # 进入战斗后，开始计时
        start_time = time.time()
        if cfg.abyss_shadows_combat_time.CombatTime_enable:
            self.device.stuck_record_add('BATTLE_STATUS_S')
            if Monster_type == "BOSS":  # BOSS战斗
                combat_time = cfg.abyss_shadows_combat_time.boss_combat_time
            elif Monster_type == "GENERAL":  # 是副将战斗
                combat_time = cfg.abyss_shadows_combat_time.general_combat_time
            elif Monster_type == "ELITE":  #  精英战斗
                combat_time = cfg.abyss_shadows_combat_time.elite_combat_time
            else:
                combat_time = 60  # 默认为 60 秒
            # 等待设定的战斗时间
            while time.time() - start_time < combat_time:
                self.screenshot()
                if self.appear_then_click(self.I_WIN, interval=1.5):
                    break
            logger.info("Combat time ended, proceeding to exit.")
            self.device.stuck_record_clear()
        # 战斗提前结束这时没有返回按钮
        if self.appear_then_click(self.I_WIN, interval=1.5):
            counts = getattr(self, '_closeout_fight_counts', None)
            if isinstance(counts, dict) and Monster_type in counts:
                counts[Monster_type] += 1
            return True

        # 点击返回
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT, interval=2):
                continue
            if self.appear_then_click(self.I_EXIT_ENSURE, interval=2):
                continue
            if self.appear_then_click(self.I_WIN, interval=2):
                continue
            if self.appear(self.I_ABYSS_NAVIGATION):
                break
        logger.info(f"Click {self.I_EXIT_ENSURE.name}")

        counts = getattr(self, '_closeout_fight_counts', None)
        if isinstance(counts, dict) and Monster_type in counts:
            counts[Monster_type] += 1
        return True



if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('zhu')
    device = Device(config)
    t = ScriptTask(config, device)
    t.run()
