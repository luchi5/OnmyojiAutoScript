# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import timedelta, datetime, time
from cached_property import cached_property

from module.exception import TaskEnd
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_hunt, page_shikigami_records, page_guild
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.DemonRetreat.assets import DemonRetreatAssets
from tasks.AbyssShadows.assets import AbyssShadowsAssets
from tasks.DemonRetreat.config import DemonRetreat
from tasks.Component.guild_retry_schedule import plan_opening_check

class ScriptTask(GameUi, GeneralBattle, SwitchSoul, DemonRetreatAssets, AbyssShadowsAssets):

    def run(self):
        """
        首领退治主函数
        """
        from tasks.Component.daily_closeout import report_closeout_outcome
        self._retreat_closeout_date = datetime.now().date()

        cfg: DemonRetreat = self.config.demon_retreat

        opening = self._opening_plan()
        if not opening.in_window:
            self._schedule_opening_check(plan=opening)
            raise TaskEnd

        if cfg.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(cfg.switch_soul_config.switch_group_team)
        if cfg.switch_soul_config.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(cfg.switch_soul_config.group_name, cfg.switch_soul_config.team_name)

        # 进入妖怪退治
        if not self.goto_demon_retreat(opening_deadline=opening.deadline):
            logger.warning("Failed to enter demon retreat")
            if self.appear_then_click(self.I_DEMON_BACK_CHECK, interval=1):
                pass
            self.goto_main()
            self._schedule_opening_check()
            raise TaskEnd

        # 首领退治战斗
        success = self.demon_retreat()

        # 战斗结束 回到寮信息界面 准备领取奖励
        # 先返回
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_PRAY, interval=1):
                logger.warning("Claim rewards")
            if self.appear_then_click(self.I_HUNT, interval=1):
                continue
            if self.appear_then_click(self.I_REWARD_ALL, interval=1.5):
                self.ui_reward_appear_click(True)
                logger.info('Claim rewards finished')
                break
            if self.appear(self.I_RANK_LSIT):
                logger.info("No rewards to claim")
                if self.appear_then_click(self.I_DEMON_BACK_CHECK, interval=1):
                    break

        # 保持好习惯，一个任务结束了就返回到庭院，方便下一任务的开始
        self.goto_main()

        self._schedule_opening_check(completed=success)
        if success and self._retreat_closeout_date == datetime.now().date():
            report_closeout_outcome(self.config, 'DemonRetreat', 'completed',
                                    detail='battle_win_confirmed')

        raise TaskEnd



    def goto_demon_retreat(self, opening_deadline=None) -> bool:
        """
        进入首领退治
        """
        cfg: DemonRetreat = self.config.demon_retreat
        logger.info("Entering demon_retreat")
        if opening_deadline is None:
            opening = self._opening_plan()
            # A direct caller outside the window may only resume an already
            # entered activity, not use the next event's deadline to navigate.
            opening_deadline = opening.deadline if opening.in_window else datetime.now()
        self.goto_page(page_guild)

        goto_demon_retreat_num = 0
        while 1:
            self.screenshot()
            # An entered gathering/preparation may finish after the opening window.
            if (self.appear(self.I_HUNT_CHECK)
                    or self.appear(self.I_DEMON_GATHER)
                    or self.is_in_prepare(False)):
                if self.appear_then_click(self.I_QUIT_BACK, interval=1):
                    pass
                logger.info("Enter demon_retreat success")
                return True
            if datetime.now() >= opening_deadline:
                logger.warning('Demon retreat did not open before the check deadline')
                return False
            # 进入神社
            if self.appear_then_click(self.I_SHRINE, interval=1):
                logger.info("Enter I_SHRINE")
                continue
            # 进入首领退治
            if self.appear_then_click(self.I_HUNT, interval=1.5):
                goto_demon_retreat_num += 1

            # 确保不离开退治
            if self.appear_then_click(self.I_QUIT_BACK, interval=1):
                pass
            # 周六打完了，但是迟到了只能领取奖励
            if self.appear_then_click(self.I_REWARD_ALL, interval=1):
                logger.info("Already challenged demon_retreat")
                sleep(1)
                if self.appear_then_click(self.I_DEMON_BACK_CHECK, interval=1):
                    pass
                self._schedule_opening_check(completed=True)
                from tasks.Component.daily_closeout import report_closeout_outcome
                now = datetime.now()
                if getattr(self, '_retreat_closeout_date', now.date()) == now.date():
                    report_closeout_outcome(self.config, 'DemonRetreat', 'skipped', now=now,
                                            detail='existing_reward_claimed_without_new_battle')
                raise TaskEnd

            if self.appear(self.I_RANK_LSIT):
                logger.info("Enter demon_retreat false")
                sleep(3)
                if self.appear_then_click(self.I_DEMON_BACK_CHECK, interval=1):
                    pass
                sleep(20)
            # 超过五次没有进入进入认为失败
            if goto_demon_retreat_num >= 5:
                break
        return False

    def _opening_plan(self, now=None, completed=False):
        cfg = self.config.demon_retreat
        return plan_opening_check(
            now or datetime.now(), [(5, cfg.demon_retreat_time.custom_run_time)],
            getattr(cfg.demon_retreat_time, 'opening_retry_interval', cfg.scheduler.failure_interval),
            completed=completed,
            window=timedelta(minutes=getattr(cfg.demon_retreat_time, 'opening_wait_minutes', 60)),
        )

    def _schedule_opening_check(self, completed=False, now=None, plan=None):
        from tasks.Component.daily_closeout import report_closeout_outcome

        plan = plan or self._opening_plan(now=now, completed=completed)
        if plan.status == 'retry':
            logger.info(f'Demon retreat not open or not completed; retry at {plan.target}, deadline {plan.deadline}')
        elif plan.status == 'window_expired':
            logger.warning('Demon retreat opening window expired without confirmed completion; wait for the next configured opening')
        elif plan.status == 'completed':
            logger.info('Demon retreat completed; wait for the next configured opening')
        else:
            logger.info(f'Demon retreat opening has not started; next check at {plan.target}')
        self.set_next_run(task='DemonRetreat', server=False, target=plan.target)
        event_now = now or datetime.now()
        if (plan.status == 'window_expired'
                and getattr(self, '_retreat_closeout_date', event_now.date()) == event_now.date()):
            report_closeout_outcome(self.config, 'DemonRetreat', 'expired', now=now,
                                    detail='opening_window_expired')
        return plan

    def demon_retreat(self):
        cfg: DemonRetreat = self.config.demon_retreat
        logger.hr('demon retreat', 2)

        # 来晚了直接进入战斗
        if not set(self.O_LATER_ENTER_CHECK.ocr(image=self.device.image)).intersection(set("集结")):
            logger.info("arrive later")
            self.ui_click_until_disappear(self.I_ENTER_FIRE, interval=1)
            self.device.stuck_record_add('BATTLE_STATUS_S')
            success = self.run_demon_battle(cfg.general_battle)
        else:
            # 等待进入战斗
            sleep(5)
            self.device.stuck_record_add('BATTLE_STATUS_S')
            self.wait_until_disappear(self.I_DEMON_GATHER)
            self.device.stuck_record_clear()
            self.device.stuck_record_add('BATTLE_STATUS_S')
            success = self.run_demon_battle(cfg.general_battle)

        return success




    def run_demon_battle(self, config: GeneralBattleConfig = None) -> bool:
        """
        重写通用战斗
        三轮战斗 战斗过程中检测挑战
        """
        # TODO 战斗过程中切换预设
        logger.hr("General battle start", 2)
        self.current_count += 1
        logger.info(f"Current count: {self.current_count}")
        if config is None:
            config = GeneralBattleConfig()

        # 如果没有锁定队伍。那么可以根据配置设定队伍
        if not config.lock_team_enable:
            logger.info("Lock team is not enable")
            # 如果更换队伍
            if self.current_count == 1:
                self.switch_preset_team(config.preset_enable, config.preset_group, config.preset_team)

            # 点击准备按钮
            self.wait_until_appear(self.I_PREPARE_HIGHLIGHT)
            self.wait_until_appear(self.I_BUFF)
            while 1:
                self.screenshot()
                if not self.appear(self.I_BUFF):
                    break
                if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1.5):
                    continue

            logger.info("Click prepare ensure button")

            # 照顾一下某些模拟器慢的
            sleep(0.1)

        # 绿标
        self.wait_until_disappear(self.I_BUFF)
        if self.is_in_battle(False):
            self.green_mark(config.green_enable, config.green_mark)

        win = self.battle_wait(config.random_click_swipt_enable)
        if win:
            return True
        else:
            return False




    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        """
        重写 三轮战斗 战斗过程中点击准备 返回到寮信息界面
        :param random_click_swipt_enable:
        :return:
        """
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        # 战斗过程 随机点击和滑动 防封 并点击 准备
        logger.info("Start battle process")
        stuck_timer = Timer(180)
        stuck_timer.start()
        while 1:
            self.screenshot()
            if self.appear(self.I_WIN):
                logger.info('Battle win')
                self.ui_click_until_disappear(self.I_WIN)
                return True
            # 战斗过程中出现准备
            if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1.5):
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
            # 如果出现失败 就点击，返回False
            if self.appear(self.I_FALSE, threshold=0.8):
                logger.info("Battle result is false")
                self.ui_click_until_disappear(self.I_FALSE)
                return False
            # 如果三分钟还没打完，再延长五分钟
            if stuck_timer and stuck_timer.reached():
                stuck_timer = None
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')


    def goto_main(self):
        ''' 保持好习惯，一个任务结束了就返回庭院，方便下一任务的开始或者是出错重启
        '''
        logger.info("Exiting DemonRetreat")
        self.goto_page(page_main)






if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('日常1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

