# This Python file uses the following encoding: utf-8
# @author ohspecial
# github https://github.com/ohspecial
from datetime import datetime ,timedelta
from enum import Enum
import time

from module.exception import TaskEnd
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_guild, page_main
from tasks.GuildBanquet.assets import GuildBanquetAssets
from tasks.Component.guild_retry_schedule import plan_opening_check

WEEKDAYDICT = {
    0: '星期一',
    1: '星期二',
    2: '星期三',
    3: '星期四',
    4: '星期五',
    5: '星期六',
    6: '星期日'
}

class Weekday(str,Enum):
    Monday: str = "星期一"
    Tuesday: str = "星期二" 
    Wednesday: str = "星期三"
    Thursday: str = "星期四"
    Friday: str = "星期五"
    Saturday: str = "星期六"
    Sunday: str = "星期日"
    
class ScriptTask(GameUi, GuildBanquetAssets):

    def run(self):
        from tasks.Component.daily_closeout import report_closeout_outcome

        self._banquet_closeout_date = datetime.now().date()
        self.run_time = self.config.guild_banquet.guild_banquet_time
        self._banquet_full_samples = 0
        self._banquet_auto_switch_failed = False
        self._banquet_last_switch = float('-inf')
        # 第一天宴会日期及时间
        self.banquet_day_1 = self.get_key_from_value(WEEKDAYDICT, self.run_time.day_1.value)
        self.banquet_day_1_start_time = self.run_time.run_time_1
        
        # 第二天宴会日期及时间
        self.banquet_day_2 = self.get_key_from_value(WEEKDAYDICT, self.run_time.day_2.value)
        self.banquet_day_2_start_time = self.run_time.run_time_2

        opening = self._opening_plan()
        if not opening.in_window:
            self._schedule_opening_check(plan=opening)
            raise TaskEnd

        self.goto_page(page_guild)
        
        if self.appear(self.I_FLAG):
            wait_count = 0
            wait_timer = Timer(230)
            wait_timer.start()
            logger.info("Start guild banquet!")
            self.device.stuck_record_add('BATTLE_STATUS_S')
        else:
            self._schedule_opening_check()
            self.goto_page(page_main)
            raise TaskEnd

        last_check_time = 0  # 记录上次实际检测时间
        last_log_time = 0  # 记录上次日志输出时间
        last_flag_status = False  # 记录上次真实检测结果
        banquet_completed = False

        while True:
            self.screenshot()
            # 条件1: 强制检测间隔管理
            current_time = time.time()
            if current_time - last_check_time >= 10:
                # 达到间隔要求时执行真实检测
                actual_status = self.appear(self.I_FLAG)
                last_flag_status = actual_status
                last_check_time = current_time
                logger.debug(f"Actual detection at {current_time}, status: {actual_status}")
                
                # 重置日志计时器
                last_log_time = current_time
                if actual_status:
                    self.check_full_experience()
            else:
                # 未达间隔时沿用上次结果
                logger.debug(f"Using cached status: {last_flag_status}")
                
                
            # 条件2: 状态判断逻辑
            if last_flag_status:
                if current_time - last_log_time >= 10:
                    logger.info("Banquet ongoing, waiting...")
                    last_log_time = current_time
            else:
                logger.info("Guild banquet end")
                banquet_completed = True
                break  # 退出循环

            # 条件3: 超时保护
            if wait_timer.reached():
                wait_timer.reset()
                if wait_count >= 3:
                    # 宴会最长15分钟
                    logger.info('Guild banquet timeout')
                    break
                wait_count += 1
                logger.info(f'Banquet ongoing, waiting... (Count: {wait_count})')
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.stuck_record_clear()
        self.goto_page(page_main)
        self._schedule_opening_check(completed=banquet_completed)
        if banquet_completed and self._banquet_closeout_date == datetime.now().date():
            report_closeout_outcome(self.config, 'GuildBanquet', 'completed',
                                    detail='entered_banquet_ended')
        raise TaskEnd

    def check_full_experience(self) -> bool:
        """Require two actual checks and a cooldown before changing the banquet lineup."""
        if (not getattr(self.run_time, 'auto_switch_shikigami', False)
                or self._banquet_auto_switch_failed):
            return False
        if not self.appear(self.I_BANQUET_EXP_FULL):
            self._banquet_full_samples = 0
            return False
        self._banquet_full_samples += 1
        if self._banquet_full_samples < 2 or time.monotonic() - self._banquet_last_switch < 30:
            return False
        self._banquet_full_samples = 0
        self._banquet_last_switch = time.monotonic()
        success = self.switch_shikigami()
        if not success:
            # An optional change must not repeatedly clear the lineup after a failed UI/OCR check.
            self._banquet_auto_switch_failed = True
            logger.warning('Banquet automatic replacement failed; skip replacement for the rest of this banquet')
        self.device.stuck_record_clear()
        self.device.stuck_record_add('BATTLE_STATUS_S')
        return success

    def switch_shikigami(self) -> bool:
        """Use the game's clear/fill controls, with a bounded wait and a nonempty confirmation."""
        logger.info('Banquet experience full; replace shikigami')
        try:
            if not self.ui_click(self.I_BANQUET_EXP_FULL, self.I_BANQUET_SWITCH,
                                 interval=1.5, timeout=20):
                return False
            if not self.ui_click(self.I_BANQUET_SWITCH, self.I_BANQUET_CLEAR_ALL,
                                 interval=1.5, timeout=20):
                return False
            if not self.appear_then_click(self.I_BANQUET_CLEAR_ALL, interval=1.5):
                return False
            # Verify the clear has completed; 0/0 is an OCR failure, not an empty team.
            clear_timer = Timer(8).start()
            empty_samples = 0
            while not clear_timer.reached():
                self.device.sleep(0.6)
                self.screenshot()
                count, _, total = self.O_BANQUET_SHIKIGAMI_NUM.ocr_digit_counter(self.device.image)
                empty_samples = empty_samples + 1 if count == 0 and total > 0 else 0
                if empty_samples >= 2:
                    break
            else:
                logger.warning('Banquet clear was not confirmed; do not fill or confirm the old full lineup')
                return False
            self.screenshot()
            if not self.appear_then_click(self.I_BANQUET_ALL_PUT, interval=1.5):
                return False
            timer = Timer(12).start()
            last_count = None
            while not timer.reached():
                self.device.sleep(0.6)
                self.screenshot()
                count, _, total = self.O_BANQUET_SHIKIGAMI_NUM.ocr_digit_counter(self.device.image)
                # Empty/invalid OCR must never trigger confirmation of an empty team.
                valid = 0 < count <= total
                if valid and (count, total) == last_count:
                    if count < total:
                        logger.warning(f'Only {count}/{total} eligible banquet shikigami; keep the nonempty lineup')
                    if not self.ui_click(self.I_BANQUET_CONFIRM, self.I_BANQUET_SWITCH,
                                         interval=1.5, timeout=15):
                        return False
                    return self.ui_click(self.I_UI_BACK_YELLOW, self.I_FLAG,
                                         interval=1.5, timeout=10)
                last_count = (count, total) if valid else None
            logger.warning('Cannot confirm a nonempty banquet lineup; cancel replacement')
            return False
        finally:
            # Return through the existing mainline controls, without importing XY's navigator.
            self.screenshot()
            if self.appear(self.I_BANQUET_CONFIRM) or self.appear(self.I_BANQUET_CLEAR_ALL):
                self.ui_click(self.I_UI_BACK_RED, self.I_BANQUET_SWITCH, interval=1.5, timeout=10)
                self.screenshot()
            if self.appear(self.I_BANQUET_SWITCH):
                self.ui_click(self.I_UI_BACK_YELLOW, self.I_FLAG, interval=1.5, timeout=10)
            elif not self.appear(self.I_FLAG):
                self.goto_page(page_guild)
    def _opening_plan(self, now=None, completed=False):
        configured = self.config.guild_banquet.guild_banquet_time
        slots = [
            (self.get_key_from_value(WEEKDAYDICT, configured.day_1.value), configured.run_time_1),
            (self.get_key_from_value(WEEKDAYDICT, configured.day_2.value), configured.run_time_2),
        ]
        return plan_opening_check(
            now or datetime.now(), slots,
            getattr(configured, 'opening_retry_interval', self.config.guild_banquet.scheduler.failure_interval),
            completed=completed,
            window=timedelta(minutes=getattr(configured, 'opening_wait_minutes', 60)),
        )

    def _schedule_opening_check(self, completed=False, now=None, plan=None):
        from tasks.Component.daily_closeout import report_closeout_outcome

        plan = plan or self._opening_plan(now=now, completed=completed)
        if plan.status == 'retry':
            logger.info(f'Guild banquet not open or not completed; retry at {plan.target}, deadline {plan.deadline}')
        elif plan.status == 'window_expired':
            logger.warning('Guild banquet opening window expired without confirmed completion; wait for the next configured opening')
        elif plan.status == 'completed':
            logger.info('Guild banquet completed; wait for the next configured opening')
        else:
            logger.info(f'Guild banquet opening has not started; next check at {plan.target}')
        self.set_next_run(task='GuildBanquet', server=False, target=plan.target)
        event_now = now or datetime.now()
        if (plan.status == 'window_expired'
                and getattr(self, '_banquet_closeout_date', event_now.date()) == event_now.date()):
            report_closeout_outcome(self.config, 'GuildBanquet', 'expired', now=now,
                                    detail='opening_window_expired')
        return plan

    def check_runtime(self) -> bool:
        return self._opening_plan().in_window

    def plan_next_run(self):
        return self._schedule_opening_check(completed=True)
    
    def get_key_from_value(self, dict, value):
        return [k for k, v in dict.items() if v == value][0]
    
    def get_weekday_enum(self, value: str) -> Weekday:
        for day in Weekday:
            if day.value == value:
                return day
        
    def set_config(self):
        """
        修改周几配置时会出现警告
        UserWarning: Pydantic serializer warnings:
  Expected `enum` but got `Weekday` with value `<Weekday.Thursday: '星期四'>` - serialized value may not be as expected
        """
        
        try:
            # 当结束宴会时，设置宴会时间的日期及时间，宴会时间设置为运行结束时间提前15分钟(因识图问题，宴会可能被认为提前关闭几秒钟)
            next_time = datetime.now() - timedelta(minutes=14, seconds=55)
            next_time = next_time.replace(second=0, microsecond=0)
            # 计算下次运行时间
            next_time = datetime.time(next_time)
            
            today = datetime.now().weekday()          
            
            # 修改配置文件
            if today == self.banquet_day_1:
                self.run_time.run_time_1 = next_time
            elif today == self.banquet_day_2:
                self.run_time.run_time_2 = next_time
            elif today < self.banquet_day_1:
                self.run_time.day_1 = self.get_weekday_enum(WEEKDAYDICT.get(today))
                self.run_time.run_time_1 = next_time
            elif today > self.banquet_day_2:
                self.run_time.day_2 = self.get_weekday_enum(WEEKDAYDICT.get(today))    
                self.run_time.run_time_2 = next_time
            else:
                # 如果当前时间在两个配置时间之间，则默认把工作日设置第一天，周末设为第二天
                if today <= 4:  # 工作日
                    self.run_time.day_1 = self.get_weekday_enum(WEEKDAYDICT.get(today))
                    self.run_time.run_time_1 = next_time
                else:  # 周末
                    self.run_time.day_2 = self.get_weekday_enum(WEEKDAYDICT.get(today))       
                    self.run_time.run_time_2 = next_time
            logger.info(f"Set next run time: {self.run_time}")
            
            self.config.save()
        except Exception as e:
            logger.error(f"Error setting banquet config: {e}")
            raise TaskEnd


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()

