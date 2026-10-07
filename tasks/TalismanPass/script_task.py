# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from datetime import datetime, timedelta

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_daily
from tasks.TalismanPass.assets import TalismanPassAssets
from tasks.TalismanPass.config import TalismanConfig, LevelReward

from module.logger import logger
from module.exception import TaskEnd
from module.base.timer import Timer
from tasks.Component.daily_closeout import (
    arm_daily_closeout, clear_manual_talisman_request, closeout_enabled,
    closeout_state, closeout_summary, hold_closeout_until,
    is_automatic_closeout, is_manual_talisman, mark_closeout_completed,
    next_closeout_check, schedule_closeout_retry,
)


class ScriptTask(GameUi, TalismanPassAssets):

    def run(self):
        started = datetime.now()
        self._daily_feedback_started_date = started.date()
        scheduled_at = self.config.talisman_pass.scheduler.next_run
        dynamic = closeout_enabled(self.config)
        automatic = is_automatic_closeout(self.config, scheduled_at=scheduled_at, now=started)
        manual = is_manual_talisman(self.config, scheduled_at=scheduled_at, now=started)
        capture_only = False
        if dynamic:
            try:
                from tasks.Component.daily_feedback import consume_recapture_request
                capture_only = consume_recapture_request(self.config, scheduled_at, now=started)
            except Exception as exc:
                logger.warning(f'只读补采请求暂不可用 ({type(exc).__name__})')
        if capture_only:
            manual = True
            automatic = False
        if dynamic and not automatic and not manual:
            held = hold_closeout_until(self.config, now=started)
            if not held and self.config.talisman_pass.scheduler.next_run <= started:
                target = (started + timedelta(minutes=3)).replace(microsecond=0)
                if target.date() != started.date():
                    target = next_closeout_check(self.config, now=started)
                self.config.talisman_pass.scheduler.next_run = target
                self.config.save()
            logger.info('花合战动态收尾：继续等待当天任务，暂不领取')
            raise TaskEnd('TalismanPass')
        if automatic:
            summary = closeout_summary(self.config, now=started)
            missing = '、'.join(item['label'] for item in summary['incomplete'])
            if missing:
                logger.info('花合战动态收尾：仍有未完成或未确认项目：' + missing)
            elif summary['completed']:
                logger.info('花合战动态收尾：已启用的寮收尾项目均已确认完成')
            else:
                logger.info('花合战动态收尾：当天没有启用寮收尾项目')
        if capture_only:
            try:
                detector = getattr(self, 'detect_random_main_costume', None)
                if callable(detector):
                    self.device.screenshot()
                    detector()
                from tasks.Component.daily_feedback_capture import goto_talisman_readonly
                goto_talisman_readonly(self)
            except Exception as exc:
                from tasks.Component.daily_feedback_capture import CONTROL_EXCEPTIONS
                if isinstance(exc, CONTROL_EXCEPTIONS):
                    raise
                logger.warning(f'只读花合战导航暂不可用 ({type(exc).__name__})')
        else:
            self.goto_page(page_daily)
        con: TalismanConfig = self.config.talisman_pass.talisman

        # 收取全部奖励
        try:
            from tasks.Component.daily_feedback_capture import ensure_talisman_today
            ensure_talisman_today(self)
        except Exception as exc:
            from tasks.Component.daily_feedback_capture import CONTROL_EXCEPTIONS
            if isinstance(exc, CONTROL_EXCEPTIONS):
                raise
            logger.warning(f'花合战今日页暂未确认 ({type(exc).__name__})')
        # Optional feedback OCR/navigation never vetoes the original positive
        # task-page check used to collect earned daily rewards.
        if capture_only:
            self._capture_daily_feedback(final=True)
            self._capture_collective_feedback(started.date())
            self._finish_dynamic_run(started, scheduled_at, automatic=False, manual=True)
            try:
                from tasks.Component.daily_feedback import finish_recapture_request
                finish_recapture_request(self.config, scheduled_at, now=datetime.now())
            except Exception as exc:
                logger.warning(f'只读补采请求暂未清除 ({type(exc).__name__})')
            if datetime.now().date() == started.date():
                try:
                    from tasks.Component.daily_feedback import finalize_feedback
                    finalize_feedback(self.config, now=datetime.now())
                except Exception as exc:
                    logger.warning(f'只读补采报告暂未归档 ({type(exc).__name__})')
            raise TaskEnd('TalismanPass')
        in_task = self.in_task()
        if automatic and not in_task:
            logger.warning('花合战动态收尾：未确认任务页面，延后3分钟重查')
            self._capture_daily_feedback(final=automatic)
            self._finish_dynamic_run(started, scheduled_at, automatic=False, manual=False, retry=True)
            raise TaskEnd('TalismanPass')
        if in_task:
            self.get_all()
        self._capture_daily_feedback(final=automatic)
        # 收取花合战等级奖励
        self.get_flower(con.level_reward)
        # 收取1500签御魂
        if con.harvest_soul:
            self.goto_page(page_main)
            self.harvest_soul()
        if automatic:
            self._capture_collective_feedback(started.date())
        if dynamic:
            self._finish_dynamic_run(started, scheduled_at, automatic=automatic, manual=manual)
        else:
            self.set_next_run(task='TalismanPass', success=True, finish=True)
        raise TaskEnd('TalismanPass')

    def _capture_daily_feedback(self, *, final=False):
        try:
            from tasks.Component.daily_feedback_capture import capture_talisman
            return capture_talisman(self, final=final)
        except Exception as exc:
            from tasks.Component.daily_feedback_capture import CONTROL_EXCEPTIONS
            if isinstance(exc, CONTROL_EXCEPTIONS):
                raise
            logger.warning(f'花合战反馈采集未获取，任务继续 ({type(exc).__name__})')
            return False

    def _capture_collective_feedback(self, expected_date):
        try:
            from tasks.Component.daily_feedback_capture import refresh_collective_feedback
            return refresh_collective_feedback(self, expected_date=expected_date)
        except Exception as exc:
            from tasks.Component.daily_feedback_capture import CONTROL_EXCEPTIONS
            if isinstance(exc, CONTROL_EXCEPTIONS):
                raise
            logger.warning(f'收尾寮任务反馈未获取，任务继续 ({type(exc).__name__})')
            return False

    def _finish_dynamic_run(self, started, scheduled_at, *, automatic=False, manual=False, retry=False):
        # Load edits made in OASX/the phone while rewards were being collected.
        self.config.reload()
        now = datetime.now()
        if not closeout_enabled(self.config):
            self.set_next_run(task='TalismanPass', success=True, finish=True)
            return
        if manual:
            clear_manual_talisman_request(self.config, now=now, scheduled_at=scheduled_at)
        if automatic and now.date() == started.date():
            mark_closeout_completed(self.config, now=now)
            try:
                from tasks.Component.daily_feedback import finalize_feedback
                finalize_feedback(self.config, now=now)
            except Exception as exc:
                logger.warning(f'每日验收通知暂未发送，任务继续 ({type(exc).__name__})')
        if is_manual_talisman(self.config, now=now):
            # A newer request belongs to the next run, not to this one.
            return
        state = closeout_state(self.config, now=now)
        pending = (state.get('queued_date') == now.date().isoformat()
                   and state.get('completed_date') != now.date().isoformat())
        if (retry or pending) and schedule_closeout_retry(self.config, now=now):
            return
        armed = arm_daily_closeout(self.config, now=now)
        if not armed and self.config.talisman_pass.scheduler.next_run <= now:
            target = (now + timedelta(minutes=3)).replace(microsecond=0)
            if target.date() != now.date():
                # Keep today's unconfirmed proof; do not repeatedly reopen
                # the game or pass yesterday's queue into the reset day.
                target = next_closeout_check(self.config, now=now)
            self.config.talisman_pass.scheduler.next_run = target
            self.config.save()

    def get_all(self):
        """
        一键收取所有的
        :return:
        """
        self.screenshot()
        if not self.appear(self.I_TP_GET_ALL):
            logger.info('No appear get all button')
        self.ui_get_reward(self.I_TP_GET_ALL)
        logger.info('Get all reward')
        time.sleep(0.5)

    def get_flower(self, level: LevelReward = LevelReward.TWO):
        """
        收取花合战等级奖励
        :return:
        """
        match_level = {
            LevelReward.ONE: self.I_TP_LEVEL_1,
            LevelReward.TWO: self.I_TP_LEVEL_2,
            LevelReward.THREE: self.I_TP_LEVEL_3,
        }
        self.screenshot()
        if not self.appear(self.I_RED_POINT_LEVEL):
            logger.info('No any level reward')
            return
        logger.info('Appear level reward')
        self.ui_click(self.I_RED_POINT_LEVEL, self.I_TP_GET_ALL)
        logger.info('Click level reward')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()
            if self.appear_then_click(match_level[level], interval=0.8):
                logger.info(f'Select {level} reward')
                if self.appear_then_click(self.I_OVERFLOW_CONFIRME):
                    pass
                check_timer.reset()
                continue

            if self.ui_reward_appear_click(False):
                logger.info('Get reward')
                check_timer.reset()
                continue
            if check_timer.reached():
                logger.warning('No reward and break')
                break
            if self.appear_then_click(self.I_TP_GET_ALL, interval=2.1):
                logger.info('Get all reward')
                check_timer.reset()
                continue

    def in_task(self) -> bool:
        """
        判断是否在任务的界面
        :return:
        """
        self.screenshot()
        if self.appear(self.I_TP_GOTO) or self.appear(self.I_TP_EXP):
            return True
        return False
    
    def harvest_soul(self):
        """
        获得1500签御魂奖励
        :return: 如果没有发现御魂奖励则退出
        """
        logger.hr('Harvest soul')
        timer_harvest = Timer(5)  # 如果连续5秒没有发现任何奖励，退出
        while 1:
            self.screenshot()
            # 自选御魂
            if self.appear_multi_scale(self.I_TP_SOUL_1,scale_range=(0.8,1.1)):
                logger.info('Select soul 2')
                self.ui_click_multi_scale(self.I_TP_SOUL_1, stop=self.I_TP_SOUL_2,scale_range=(0.8,1.1))
                self.ui_click(self.I_TP_SOUL_2, stop=self.I_TP_SOUL_3, interval=3)
                self.ui_click_until_disappear(click=self.I_TP_SOUL_3)
                timer_harvest.reset()
            # 五秒内没有发现任何奖励，退出
            if not timer_harvest.started():
                timer_harvest.start()
            else:
                if timer_harvest.reached():
                    logger.info('No more reward')
                    return



if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

