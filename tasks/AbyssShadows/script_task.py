# This Python file uses the following encoding: utf-8
# @brief    Ryou Dokan Toppa (阴阳竂道馆突破功能)
# @author   jackyhwei
# @note     draft version without full test
# github    https://github.com/roarhill/oas

from datetime import datetime, timedelta
import time
from cached_property import cached_property
from time import sleep

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_shikigami_records, page_guild

from module.logger import logger
from module.atom.image import RuleImage
from module.exception import TaskEnd
from module.base.timer import Timer
from module.exception import GamePageUnknownError
from pathlib import Path
from tasks.AbyssShadows.xy_preset import XYPresetMixin
from tasks.AbyssShadows.config import AbyssShadows
from tasks.AbyssShadows.assets import AbyssShadowsAssets
from tasks.AbyssShadows.selection_assets import AbyssSelectionAssets
from tasks.AbyssShadows.workflow import (
    ProgressStore, TargetPlan, QUOTAS, enemy_type, legacy_order, next_run,
    opening_slots, parse_order,
)

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


class ScriptTask(XYPresetMixin, GeneralBattle, GameUi, SwitchSoul, AbyssShadowsAssets, AbyssSelectionAssets):
    # XY's tighter glyph templates tolerate the current scene/background.
    I_ABYSS_ATTACK_XY = RuleImage(
        roi_front=(576,54,91,45), roi_back=(576,54,91,45), threshold=0.8,
        method='Template matching', file='./tasks/AbyssShadows/selection/res_is_attack.png')
    I_ABYSS_FINISHED_XY = RuleImage(
        roi_front=(570,50,120,60), roi_back=(570,50,120,60), threshold=0.8,
        method='Template matching', file='./tasks/AbyssShadows/selection/res_check_finish.png')
    I_REWARD_EXHAUSTED = RuleImage(
        roi_front=(461,267,368,32), roi_back=(420,235,440,110), threshold=0.85,
        method='Template matching', file='./tasks/AbyssShadows/res/reward_exhausted.png')
    I_REWARD_EXHAUSTED_CANCEL = RuleImage(
        roi_front=(442,413,168,53), roi_back=(410,380,230,110), threshold=0.85,
        method='Template matching', file='./tasks/AbyssShadows/res/reward_exhausted_cancel.png')

    boss_fight_count = 0  # 首领战斗次数
    general_fight_count = 0  # 副将战斗次数
    elite_fight_count = 0  # 精英战斗次数
    
    def run(self):
        """Use XY's target lists and retry calendar with our existing combat setup."""
        cfg: AbyssShadows = self.config.abyss_shadows
        self._abyss_closeout_date = datetime.now().date()
        self._abyss_preset_selected = False
        self.init_target_plan()
        now = datetime.now()
        start_at = opening_slots(cfg.abyss_shadows_time).get(now.weekday())
        expired = (start_at is not None and
                   now >= datetime.combine(now.date(), start_at) + timedelta(hours=1)
                   and not self.target_plan.done)
        # The one-hour window controls opening checks, not a resumed active run.
        if (start_at is None or now < datetime.combine(now.date(), start_at)
                or expired):
            self.schedule_abyss()
            if expired:
                from tasks.Component.daily_closeout import report_closeout_outcome
                report_closeout_outcome(self.config, 'AbyssShadows', 'expired',
                                        detail='opening_wait_window_expired')
            raise TaskEnd
        if self.target_plan.next_target() is None:
            self.finish_abyss()

        if cfg.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(cfg.switch_soul_config.switch_group_team)
        if cfg.switch_soul_config.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(cfg.switch_soul_config.group_name, cfg.switch_soul_config.team_name)
        try:
            self.goto_abyss_shadows()
            while (code := self.target_plan.next_target()) is not None:
                area = self.target_area(code)
                if self.select_boss(area):
                    break
                if self.is_area_selection() and self.is_area_sealed(area):
                    self.target_plan.seal_area(code[0])
                    self.save_target_plan()
                    continue
                self.defer_abyss('Not opened yet')
            else:
                self.finish_abyss()
            self.device.stuck_record_add('BATTLE_STATUS_S')
            try:
                ready = self.wait_abyss_attack()
            finally:
                self.device.stuck_record_clear()
            if ready == 'finished':
                self.finish_abyss()
            if ready != 'attack':
                self.defer_abyss('Assembly did not become attackable yet')
        except GamePageUnknownError as exc:
            self.defer_abyss(f'Opening page not ready: {exc}')

        while (code := self.target_plan.next_target()) is not None:
            self.target_plan.current_type = enemy_type(code)
            self.execute_target(code)
        self.finish_abyss()

    def wait_abyss_attack(self):
        timer = Timer(180).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_ABYSS_FINISHED_XY):
                return 'finished'
            if self.appear(self.I_ABYSS_ATTACK_XY) or self.appear(self.I_IS_ATTACK):
                return 'attack'
        return 'waiting'

    def init_target_plan(self):
        cfg = self.config.abyss_shadows
        options = getattr(cfg, 'process_manage', None)
        order = getattr(options, 'attack_order', '')
        order = parse_order(order) if order.strip() else legacy_order(
            cfg.abyss_shadows_combat_time.CombatTime_enable)
        root = Path(__file__).resolve().parents[2] / 'config' / 'abyss_progress'
        self.progress_store = ProgressStore(root, self.config.config_name, self._abyss_closeout_date)
        done, unavailable = self.progress_store.load()
        self.target_plan = TargetPlan(order, done, unavailable,
                                      complete=getattr(options, 'try_complete_enemy_count', True))
        self.target_plan.reward_exhausted = getattr(self.progress_store, 'reward_exhausted', set())
        self.refresh_target_counts()
        logger.info(f'Abyss checkpoint loaded: done={done}, unavailable={unavailable}')

    def refresh_target_counts(self):
        counts = self.target_plan.counts
        self.boss_fight_count = counts['BOSS']
        self.general_fight_count = counts['GENERAL']
        self.elite_fight_count = counts['ELITE']
        self._closeout_fight_counts = counts

    def save_target_plan(self):
        self.progress_store.save(self.target_plan)
        self.refresh_target_counts()

    @staticmethod
    def target_area(code):
        return dict(zip('ABCD', (AreaType.DRAGON, AreaType.PEACOCK,
                                AreaType.FOX, AreaType.LEOPARD)))[code[0]]

    @staticmethod
    def target_click(code):
        return (CilckArea.BOSS, CilckArea.GENERAL_1, CilckArea.GENERAL_2,
                CilckArea.ELITE_1, CilckArea.ELITE_2, CilckArea.ELITE_3)[int(code[-1]) - 1]

    def execute_target(self, code):
        self.screenshot()
        if self.appear(self.I_ABYSS_FINISHED_XY):
            self.finish_abyss()
        area = self.target_area(code)
        if not self.change_area(area):
            if self.is_area_selection() and self.is_area_sealed(area):
                self.target_plan.seal_area(code[0])
                self.save_target_plan()
                return
            self.defer_abyss(f'Cannot enter target area {code[0]}')
        # XY retries one target at most twice, then advances its done list.
        for attempt in range(2):
            entered = self.click_emeny_area(self.target_click(code))
            if entered == 'reward_exhausted':
                self.target_plan.mark_reward_exhausted(enemy_type(code))
                self.save_target_plan()
                self.recover_target_map()
                return
            if entered is None:
                # Preparation timed out: reopen the map on the next attempt.
                # This is not evidence of a kill or a completed battle.
                if attempt == 0:
                    continue
                self.defer_abyss(f'Target {code} preparation timed out twice')
                return
            if not entered:
                if attempt == 0:
                    self.target_plan.mark_unavailable(code)
                    self.save_target_plan()
                    return
                break
            if self.run_general_battle_back(enemy_type(code)):
                break
        self.target_plan.mark_done(code)
        self.save_target_plan()
        logger.info(f'Abyss target {code} completed by battle strategy; counts={self.target_plan.counts}')

    def schedule_abyss(self, success=False):
        cfg = self.config.abyss_shadows
        target = next_run(datetime.now(), cfg.abyss_shadows_time,
                          getattr(cfg.abyss_shadows_time, 'opening_retry_interval', timedelta(minutes=3)),
                          success=success)
        self.set_next_run(task='AbyssShadows', server=False, target=target)
        return target

    def defer_abyss(self, reason):
        # Save the retry before navigating back, so recovery cannot lose it.
        target = self.schedule_abyss()
        logger.info(f'Abyss pending: {reason}; next opening check {target}')
        self.goto_main()
        raise TaskEnd

    def finish_abyss(self):
        from tasks.Component.daily_closeout import report_closeout_outcome
        self.goto_main()
        self.schedule_abyss(success=True)
        counts = self.target_plan.counts
        completed = all(counts[kind] >= amount or kind in self.target_plan.reward_exhausted
                        for kind, amount in QUOTAS.items())
        if self._abyss_closeout_date == datetime.now().date():
            report_closeout_outcome(
                self.config, 'AbyssShadows', 'completed' if completed else 'skipped',
                detail=f'xy_strategy_targets: boss={counts["BOSS"]}/2, '
                       f'general={counts["GENERAL"]}/4, elite={counts["ELITE"]}/6; '
                       f'reward_exhausted={sorted(self.target_plan.reward_exhausted)}',
            )
        # Same lifecycle as XY: retain on interruption, clear on normal finish.
        self.progress_store.clear()
        raise TaskEnd

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
            if self.appear(self.I_ABYSS_FINISHED_XY):
                self.finish_abyss()
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
            if self.appear(self.I_ABYSS_FINISHED_XY):
                self.finish_abyss()
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
        timer = Timer(45).start()
        while 1:
            self.screenshot()
            if timer.reached():
                raise GamePageUnknownError('Cannot reach Abyss Shadows opening page')
            if self.is_area_selection():
                break
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






    def click_emeny_area(self, click_area: CilckArea) -> bool:
        suceess = True
        ''' 点击敌人区域
        
        :return 
        '''
        logger.info(f"Click emeny area: {click_area.name}")
        # 点击战报
        while 1:
            self.screenshot()
            if self.appear(self.I_ABYSS_FINISHED_XY):
                self.finish_abyss()
            if self.appear_then_click(self.I_ABYSS_NAVIGATION, interval=1.5):
                logger.info(f"Click {self.I_ABYSS_NAVIGATION.name}")
                continue
            if self.appear(self.I_ABYSS_MAP):
                logger.info("Find abyss map, exit")
            
            click_times = 0
            # 点击攻打区域
            while 1:
                self.screenshot()
                if self.appear(self.I_ABYSS_FINISHED_XY):
                    self.finish_abyss()
                # 如果点3次还没进去就表示目标已死亡,跳过
                if click_times >= 3:
                    logger.warning(f"Failed to click {click_area}")
                    return False
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
                if self.appear(self.I_ABYSS_FINISHED_XY):
                    self.finish_abyss()
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
        
        return self.wait_target_prepare()

    I_TARGET_DETAIL_CLOSE = RuleImage(
        roi_front=(1194,24,38,37), roi_back=(970,70,90,90), threshold=0.8,
        method='Template matching', file='./tasks/AreaBoss/res/res_ab_close_red.png')

    def recover_target_map(self):
        deadline = time.monotonic() + 15
        cancel_clicks = 0
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(self.I_REWARD_EXHAUSTED):
                if cancel_clicks >= 3:
                    break
                if self.appear_then_click(self.I_REWARD_EXHAUSTED_CANCEL, interval=1):
                    cancel_clicks += 1
                continue
            if self.appear_then_click(self.I_UI_CANCEL, interval=1):
                continue
            if self.appear_then_click(self.I_UI_CANCEL_SAMLL, interval=1):
                continue
            if self.appear_then_click(self.I_TARGET_DETAIL_CLOSE, interval=1):
                continue
            if self.appear(self.I_ABYSS_MAP):
                return
            self.appear_then_click(self.I_ABYSS_NAVIGATION, interval=1)
        self.defer_abyss('Cannot restore target map')

    def wait_target_prepare(self):
        deadline = time.monotonic() + 30
        challenge_clicks = 0
        while True:
            self.screenshot()
            if self.appear(self.I_ABYSS_FINISHED_XY):
                self.finish_abyss()
            # Match the message, not a generic confirmation button. This dialog
            # has a checkbox and moves the buttons; it can also survive a restart.
            if self.appear(self.I_REWARD_EXHAUSTED):
                logger.info('Current target type reward quota exhausted; cancel challenge')
                return 'reward_exhausted'
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                return True
            if time.monotonic() >= deadline:
                logger.warning('Preparation not found within 30 seconds; reopen map and locate target again')
                self.recover_target_map()
                return None
            if challenge_clicks < 3 and self.appear_then_click(self.I_ABYSS_FIRE, interval=2):
                challenge_clicks += 1
                logger.info('Clicked abyss challenge; waiting for preparation')

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
        won = False
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
                    won = True
                    break
            logger.info("Combat time ended, proceeding to exit.")
            self.device.stuck_record_clear()
        # A consumed victory marker is still a win. Drain settlement before
        # saving the target; an interrupted exit keeps it pending for resumption.
        timer = Timer(30).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_ABYSS_NAVIGATION) or self.appear(self.I_ABYSS_FINISHED_XY):
                return True
            if timer.reached():
                raise GamePageUnknownError('Cannot return from Abyss Shadows battle')
            if self.appear_then_click(self.I_WIN, interval=2):
                won = True
                continue
            if self.ui_reward_appear_click():
                continue
            if not won:
                if self.appear_then_click(self.I_EXIT, interval=2):
                    continue
                if self.appear_then_click(self.I_EXIT_ENSURE, interval=2):
                    continue



if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('zhu')
    device = Device(config)
    t = ScriptTask(config, device)
    t.run()
