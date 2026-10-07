# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import random
import re
from cached_property import cached_property
from enum import Enum
from datetime import datetime, timedelta

from module.exception import TaskEnd, RequestHumanTakeover
from module.logger import logger
from module.base.timer import Timer
from module.atom.ocr import RuleOcr
from module.atom.click import RuleClick

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_guild
from tasks.CollectiveMissions.assets import CollectiveMissionsAssets
from tasks.CollectiveMissions.feed_selection_assets import FeedSelectionAssets
from tasks.Component.collective_missions_link import (
    begin_collective_attempt, collective_continuation_target, collective_gate,
    complete_collective, linked_mode, next_collective_target, pending_collective_check,
)


class MC(str, Enum):
    BL = '契灵'
    AW1 = '觉醒一'
    AW2 = '觉醒二'
    AW3 = '觉醒三'
    GR1 = '御灵一'
    GR2 = '御灵二'
    GR3 = '御灵三'
    SO1 = '御魂一'
    SO2 = '御魂二'
    SO3 = '御魂三'  # Recognize for card refresh only; never submit six-star souls.
    FRIEND = '结伴同行'
    UNKNOWN = '未知'
    FEED = '远远不够'  # 喂N卡

class ScriptTask(GameUi, CollectiveMissionsAssets, FeedSelectionAssets):
    missions: list = []  # 用于记录三个的任务的种类
    # Task source is reloaded between runs; an older worker may retain the
    # first imported asset class, so keep this new exit control task-local.
    C_FEED_BACK = RuleClick((32, 43, 30, 31), (32, 43, 30, 31), name='feed_back')

    @cached_property
    def rule(self) -> list:
        rule = self.config.collective_missions.missions_config.missions_rule
        rule = rule.replace(' ', '').replace('\n', '')
        # 正则表达式 分离 ">"
        rule = re.split(r'>', rule)
        mc_values_list = [member.value for member in MC]
        rule = [item for item in rule if item in mc_values_list]
        return rule

    def run(self):
        from tasks.Component.daily_closeout import report_closeout_outcome

        closeout_date = datetime.now().date()
        self._daily_feedback_started_date = closeout_date

        def report_collective(outcome, detail):
            # A task entered yesterday cannot prove today's reset counter.
            now = datetime.now()
            if now.date() == closeout_date:
                report_closeout_outcome(self.config, 'CollectiveMissions', outcome,
                                        now=now, detail=detail)

        def complete_confirmed_collective():
            if datetime.now().date() == closeout_date:
                complete_collective(self.config)
                report_collective('completed', 'counter_30_of_30_confirmed_twice')

        gate = collective_gate(self.config)
        if not gate.allowed:
            logger.info(f'Collective missions skipped: {gate.reason}')
            if gate.reason == 'already_completed':
                report_collective('completed', 'previous_confirmed_30_of_30')
            self._schedule_collective(waiting=True)
            raise TaskEnd('CollectiveMissions')
        pending_target = pending_collective_check(self.config)
        if pending_target is not None:
            logger.info(f'Keep the already scheduled collective reward check at {pending_target}')
            self._schedule_collective(target=pending_target)
            raise TaskEnd('CollectiveMissions')
        self._collective_pending_until = None
        self.goto_page(page_guild)
        self.ui_click(self.I_CM_SHRINE, self.I_CM_CM)
        self.ui_click(self.I_CM_CM, self.I_CM_RECORDS)
        logger.info('Start to detect missions')
        # 判断今天是否已经完成了， 还是多少次数的任务
        counter = self._mission_counter()
        if counter == (30, 30) and self._mission_counter() == (30, 30):
            logger.warning('Today\'s missions have been completed')
            complete_confirmed_collective()
            self._leave_missions()
            self._schedule_collective(success=True, waiting=True)
            raise TaskEnd('CollectiveMissions')
        feed_resume = False
        feed_pending = getattr(self.config.collective_missions.missions_config, 'pending_kind', '')
        feed_verification = gate.verify_only or (feed_pending.startswith('feed_') and
                getattr(self.config.collective_missions.missions_config, 'attempted_date', '') == closeout_date.isoformat())
        if feed_verification:
            feed_resume = self._feed_resume_allowed(counter)
        if feed_verification and not feed_resume:
            if getattr(self.config.collective_missions.missions_config, 'pending_kind', '') == 'bondling_reward':
                self._collect_pending_bondling_reward()
                if (self._return_to_missions()
                        and self._mission_counter() == (30, 30)
                        and self._mission_counter() == (30, 30)):
                    complete_confirmed_collective()
                    self._leave_missions()
                    self._schedule_collective(success=True, waiting=True)
                    raise TaskEnd('CollectiveMissions')
            logger.warning('Previous collective submission was not confirmed as 30/30; '
                           'do not submit again today')
            self._leave_missions()
            self._schedule_collective(waiting=True)
            report_collective('failed', 'previous_submission_not_confirmed')
            raise TaskEnd('CollectiveMissions')
        if counter is None or counter == (30, 30):
            logger.warning('Collective mission counter is not confirmed; skip submission')
            self._leave_missions()
            self._schedule_collective()
            raise TaskEnd('CollectiveMissions')
        # Claim already earned rewards before refreshing a card or spending items.
        # A previous unconfirmed submission only reaches the verify-only branch above.
        if self.appear(self.I_CM_REWARDS):
            self._collect_pending_bondling_reward()
            if not self._return_to_missions() or self.appear(self.I_CM_REWARDS):
                logger.warning('Existing collective reward could not be confirmed as claimed; skip selection')
                self._leave_missions()
                self._schedule_collective()
                raise TaskEnd('CollectiveMissions')
            counter = self._mission_counter()
            if counter == (30, 30) and self._mission_counter() == (30, 30):
                complete_confirmed_collective()
                self._leave_missions()
                self._schedule_collective(success=True, waiting=True)
                raise TaskEnd('CollectiveMissions')
            if counter is None or counter == (30, 30):
                logger.warning('Counter after collecting rewards is unconfirmed; skip submission')
                self._leave_missions()
                self._schedule_collective()
                raise TaskEnd('CollectiveMissions')
        # Switching cards does not spend items. A failed selection must remain
        # retryable today rather than consuming the daily submission guard.
        mission_name = self.config.collective_missions.missions_config.missions_select
        if not self.select_mission(mission_name):
            logger.warning('Collective mission selection failed; defer this task without submitting')
            self._leave_missions()
            self._schedule_collective()
            raise TaskEnd('CollectiveMissions')
        # 判断最优的任务是哪一个
        mission, index = self.detect_best()
        logger.info(f'Best mission is {mission}')
        logger.info(f'Best mission index is {index}')
        if mission not in (MC.BL, MC.FEED, MC.AW1, MC.AW2, MC.AW3,
                           MC.GR1, MC.GR2, MC.GR3, MC.SO1, MC.SO2):
            logger.warning('No supported collective mission was identified; skip submission')
            self._leave_missions()
            self._schedule_collective()
            raise TaskEnd('CollectiveMissions')
        # Persist immediately before a submission/child task can spend resources.
        if feed_resume and (mission != MC.FEED or not self._feed_resume_allowed(counter)):
            logger.warning('Verified feed continuation no longer matches the current card/counter')
            self._leave_missions()
            self._schedule_collective(waiting=True)
            raise TaskEnd('CollectiveMissions')
        if not feed_resume and not begin_collective_attempt(self.config):
            self._leave_missions()
            self._schedule_collective(waiting=True)
            raise TaskEnd('CollectiveMissions')
        if mission == MC.BL:
            # 契灵单独处理
            self._bondling_fairyland(index)
        elif mission == MC.FEED:
            self._feed_to_quota(index, counter)
        elif mission == MC.AW1 or mission == MC.AW2 or mission == MC.AW3 \
                or mission == MC.GR1 or mission == MC.GR2 or mission == MC.GR3:
            # 其他就捐材料
            self._donate(index)
        elif mission == MC.SO1 or mission == MC.SO2:
            # 御魂就捐御魂
            self._soul(index)
        completed = False
        if self._return_to_missions():
            completed = self._mission_counter() == (30, 30) and self._mission_counter() == (30, 30)
        if completed:
            complete_confirmed_collective()
        else:
            logger.warning('Collective missions did not confirm 30/30; not marked completed')
        self._leave_missions()
        if self._collective_pending_until is not None and not completed:
            target = collective_continuation_target(self.config, self._collective_pending_until)
            self._schedule_collective(target=target)
        else:
            self._schedule_collective(success=completed,
                                      waiting=completed or linked_mode(self.config))
            if not completed and linked_mode(self.config):
                report_collective('failed', 'submission_not_confirmed_30_of_30')
        raise TaskEnd('CollectiveMissions')

    def _mission_counter(self):
        self.screenshot()
        result = self.O_CM_NUMBER.ocr(self.device.image)
        if not isinstance(result, (tuple, list)) or len(result) != 3:
            return None
        current, remain, total = result
        if (any(type(value) is not int for value in result)
                or total != 30 or not 0 <= current <= total or remain != total - current):
            return None
        return current, total

    def _collect_pending_bondling_reward(self):
        """Recover only a visible reward, without creating another submission."""
        if not self.appear(self.I_CM_REWARDS):
            return False
        limit = Timer(12).start()
        quiet = Timer(3).start()
        clicks = 0
        while not limit.reached():
            self.screenshot()
            if self.ui_reward_appear_click(True):
                quiet.reset()
                continue
            if clicks >= 6 or not self._collective_click_available(self.I_CM_REWARDS):
                logger.warning('Collective reward remained visible after bounded claims')
                break
            if self.appear_then_click(self.I_CM_REWARDS, interval=1):
                clicks += 1
                quiet.reset()
                continue
            if quiet.reached():
                break
        return True

    def _return_to_missions(self):
        timer = Timer(15).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_CM_RECORDS):
                return True
            if self.appear(self.I_CM_SHRINE) or self.appear(self.I_CHECK_MAIN):
                return False
            if hasattr(self, 'I_FEED_N_PAGE') and self.appear(self.I_FEED_N_PAGE):
                if self.appear(self.I_FEED_SUBMIT):
                    self.click(self.C_FEED_CANCEL, interval=1)
                else:
                    self.click(self.C_FEED_BACK, interval=1)
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
        return False

    def _leave_missions(self):
        # Every successful/partial run shares this boundary. The collector is
        # read-only and rejects modals; a failure never affects game cleanup.
        try:
            from tasks.Component.daily_feedback_capture import capture_collective
            capture_collective(self)
        except Exception as exc:
            from tasks.Component.daily_feedback_capture import CONTROL_EXCEPTIONS
            if isinstance(exc, CONTROL_EXCEPTIONS):
                raise
            logger.warning(f'寮集体任务反馈采集未获取，任务继续 ({type(exc).__name__})')
        timer = Timer(15).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_CM_SHRINE) or self.appear(self.I_CHECK_MAIN):
                return True
            if hasattr(self, 'I_FEED_N_PAGE') and self.appear(self.I_FEED_N_PAGE):
                if self.appear(self.I_FEED_SUBMIT):
                    self.click(self.C_FEED_CANCEL, interval=1)
                else:
                    self.click(self.C_FEED_BACK, interval=1)
                continue
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
        logger.warning('Could not confirm leaving collective missions; continue with the next task')
        return False

    def _schedule_collective(self, success=False, waiting=False, target=None):
        target = target or next_collective_target(self.config, success=success, waiting=waiting)
        self.set_next_run(task='CollectiveMissions', finish=True, success=None,
                          server=False, target=target)


    def detect_one(self, ocr_1: RuleOcr, ocr_2: RuleOcr) -> MC:
        """
        检测某一个位置是什么的任务
        :param ocr_1:
        :param ocr_2:
        :return:
        """
        self.screenshot()
        result_1 = ocr_1.ocr(self.device.image)
        result_2 = ocr_2.ocr(self.device.image)
        result_1 = result_1.replace('·', '').strip()
        result_2 = result_2.strip()
        if result_1 == '结伴同行':
            return MC.FRIEND
        elif result_1 == '契灵探查':
            return MC.BL
        if result_2 == '觉醒一':
            return MC.AW1
        elif result_2 == '觉醒二':
            return MC.AW2
        elif result_2 == '觉醒三':
            return MC.AW3
        elif result_2 == '御灵一':
            return MC.GR1
        elif result_2 == '御灵二':
            return MC.GR2
        elif result_2 == '御灵三':
            return MC.GR3
        elif result_2 == '御魂一':
            return MC.SO1
        elif result_2 == '御魂二':
            return MC.SO2
        elif result_1 == '远远不够' and result_2 == '御魂三':
            return MC.SO3
        if result_1 == '远远不够' and result_2 == '养成':
            return MC.FEED
        return MC.UNKNOWN

    def detect_best(self) -> tuple:
        """
        自动寻找最好的任务并返回，期间记录三个任务的类型
        :return: 任务类型, 0/1/2
        """
        first_class = self.detect_one(self.O_CM_1, self.O_CM_2)
        second_class = self.detect_one(self.O_CM_3, self.O_CM_4)
        third_class = self.detect_one(self.O_CM_5, self.O_CM_6)
        first_order = self.rule.index(first_class) if first_class in self.rule else 100
        second_order = self.rule.index(second_class) if second_class in self.rule else 101
        third_order = self.rule.index(third_class) if third_class in self.rule else 102
        logger.info(f'first_class: {first_class}, second_class: {second_class}, third_class: {third_class}')
        logger.info(f'first_order: {first_order}, second_order: {second_order}, third_order: {third_order}')
        if first_order < second_order and first_order < third_order:
            best_index, best_class = 0, first_class
        elif second_order < first_order and second_order < third_order:
            best_index, best_class = 1, second_class
        elif third_order < first_order and third_order < second_order:
            best_index, best_class = 2, third_class
        return best_class, best_index


    def _bondling_fairyland(self, index: int):
        """
        如果御灵已经做了那么就领取奖励
        否则将契灵之境的任务设置为当前，同时两个小时后继续执行当前的任务收菜
        :return:
        """
        def bondling_finish():
            self.screenshot()
            if self.appear(self.I_CM_REWARDS):
                return True
            return False
        if not bondling_finish():
            if not self.config.bondling_fairyland.scheduler.enable:
                logger.error('The scheduler of bondling_fairyland is not enable')
                logger.error('Please enable it in config file')
                raise RequestHumanTakeover
            missions = self.config.collective_missions.missions_config
            self._collective_pending_until = datetime.now() + timedelta(hours=2)
            if hasattr(missions, 'pending_kind'):
                missions.pending_kind = 'bondling_reward'
            if hasattr(missions, 'pending_until'):
                missions.pending_until = self._collective_pending_until.isoformat(sep=' ', timespec='seconds')
            self.config.task_call('BondlingFairyland', force_call=False)
            return False
        # 领取奖励
        logger.info('Start to collect bondling rewards')
        check_timer = Timer(3)
        check_timer.start()
        while 1:
            self.screenshot()
            if self.ui_reward_appear_click(True):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CM_REWARDS, interval=1):
                check_timer.reset()
                continue
            if check_timer.reached():
                break
        logger.info('Finish to collect bondling rewards')

    def _donate(self, index: int):
        """
        捐赠材料
        :param index: 0, 1, 2 三个任务的位置
        :return:
        """
        match_click = {
            0: self.C_CM_1,
            1: self.C_CM_2,
            2: self.C_CM_3,
        }
        if not self._open_collective_submission(match_click[index], self.I_CM_PRESENT):
            return False
        # 开始捐材料
        logger.info('Start to donate')
        # 判断哪一个的材料最多
        self.screenshot()
        max_index = 0
        max_number = 0
        for i, ocr in enumerate([self.O_CM_1_MATTER, self.O_CM_2_MATTER,
                                 self.O_CM_3_MATTER, self.O_CM_4_MATTER]):
            count = ocr.ocr(self.device.image)
            if (not isinstance(count, (tuple, list)) or len(count) != 3
                    or any(type(value) is not int or value < 0 for value in count)):
                continue
            curr, remain, total = count
            if total > max_number:
                max_number = total
                max_index = i
        if max_number <= 30:
            logger.warning('Insufficient confirmed materials; skip this collective submission')
            return False

        match_swipe = {
            0: self.S_CM_MATTER_1,
            1: self.S_CM_MATTER_2,
            2: self.S_CM_MATTER_3,
            3: self.S_CM_MATTER_4,
        }
        # 滑动到最多的材料
        random_click = [self.I_CM_ADD_1, self.I_CM_ADD_2, self.I_CM_ADD_3, self.I_CM_ADD_4]
        window_control = self.config.script.device.control_method == 'window_message'
        swipe_count = 0
        click_count = 0
        fill_limit = Timer(20).start()
        ready = False
        while not fill_limit.reached():
            self.screenshot()
            if self.appear(self.I_CM_MATTER):
                ready = True
                break
            if not window_control and swipe_count >= 5:
                break
            if not window_control and self.swipe(match_swipe[max_index], interval=2.5):
                swipe_count += 1
                time.sleep(1.5)
                continue

            # 为什么使用window_message无法滑动
            if window_control and click_count >= 30:
                break
            if window_control:
                candidates = [button for button in random_click if self._collective_click_available(button)]
                if not candidates:
                    break
                if self.click(random.choice(candidates), interval=0.7):
                    click_count += 1
                    continue

        if not ready:
            logger.warning('Collective material selection did not settle; skip submission')
            return False
        logger.info('Swipe to the most matter')
        settled = self._submit_collective_once_and_claim(self.I_CM_PRESENT)
        if settled:
            logger.info('Donate reward phase finished; the counter will confirm completion')
        return settled

    def _open_collective_submission(self, card, marker):
        limit = Timer(10).start()
        clicks = 0
        while not limit.reached():
            self.screenshot()
            if self.appear(marker):
                return True
            if clicks >= 6 or not self._collective_click_available(card):
                break
            if self.click(card, interval=1.5):
                clicks += 1
        logger.warning('Collective submission panel did not open; skip this item')
        return False

    def _submit_collective_once_and_claim(self, button):
        """Submit once, then drain one or two rewards without another submission.

        True only means this phase settled. The caller must still confirm the
        game counter twice before recording today's completion.
        """
        self.screenshot()
        if not self.appear(button) or self.appear(self.I_UI_REWARD, threshold=0.6):
            logger.warning('Collective submission button is not confirmed; do not spend resources')
            return False
        if not self._collective_click_available(button):
            return False
        if not self.appear_then_click(button, interval=1):
            return False
        if button is getattr(self, 'I_FEED_SUBMIT', None):
            self._feed_submitted = True
        # Never click the submission button again, even if it stays visible.
        limit = Timer(15).start()
        quiet = Timer(3).start()
        rewards = settled_frames = 0
        while not limit.reached():
            self.screenshot()
            # Rewards can appear shortly after the overview reappears. Handle
            # them first and keep a quiet interval before leaving that overview.
            if self.appear(self.I_UI_REWARD, threshold=0.6):
                settled_frames = 0
                quiet.reset()
                if rewards >= 6 or not self._collective_click_available(self.I_UI_REWARD):
                    logger.warning('Collective reward popup did not close within the claim budget')
                    return False
                if self.ui_reward_appear_click(False):
                    rewards += 1
                continue
            button_visible = self.appear(button)
            overview = self.appear(self.I_CM_RECORDS)
            if not button_visible and (overview or rewards > 0):
                if settled_frames == 0:
                    quiet.reset()
                settled_frames += 1
                if settled_frames >= 2 and quiet.reached():
                    return True
            else:
                settled_frames = 0
        logger.warning('Collective reward phase timed out; do not submit again')
        return False

    @staticmethod
    def _mission_from_setting(value):
        """Accept both the upstream names and XY/Az's older enum value."""
        value = str(getattr(value, 'value', value) or '').strip()
        if value == '养成':
            value = MC.FEED.value
        for mission in (MC.AW1, MC.AW2, MC.AW3, MC.GR1, MC.GR2, MC.GR3,
                        MC.SO1, MC.SO2, MC.FEED):
            if value == mission.value:
                return mission
        return None

    def _collective_click_available(self, button):
        """Stop before the shared repeated-click guard, including prior attempts."""
        history = list(getattr(self.device, 'click_record', ()))
        name = getattr(button, 'name', str(button))
        count = history.count(name)
        return count < 8 and not (count >= 5 and any(
            history.count(other) >= 6 for other in set(history) if other != name))

    def _preferred_mission(self, value):
        mission = self._mission_from_setting(value)
        if mission is not None:
            return mission
        rule = getattr(self.config.collective_missions.missions_config, 'missions_rule', '') or ''
        for item in re.split(r'>', rule):
            mission = self._mission_from_setting(item)
            if mission is not None:
                break
        mission = mission or MC.AW3
        logger.warning(f'Invalid collective target {value!r}; use {mission.value}')
        return mission

    def select_mission(self, missions_select: str) -> bool:
        """Confirm the selected card, with finite refreshes and no history reset."""
        target = self._preferred_mission(missions_select)
        limit = Timer(25).start()
        click_cnt = unchanged = confirmed = unknown = 0
        last_switched = None
        logger.info(f'目标任务: {target.value}')
        while not limit.reached():
            self.screenshot()
            mission = self.detect_one(self.O_CM_1, self.O_CM_2)
            logger.info(f'当前任务: {mission}')
            if mission == target:
                confirmed += 1
                if confirmed >= 2:
                    logger.info('成功确认目标任务')
                    return True
                continue
            confirmed = 0
            if click_cnt >= 8 or unchanged >= 2:
                logger.warning(f'Collective refresh stopped: clicks={click_cnt}, unchanged={unchanged}')
                return False
            if mission == MC.UNKNOWN:
                unknown += 1
                if unknown >= 3:
                    logger.warning('Collective card is not recognized; do not refresh blindly')
                    return False
                continue
            unknown = 0
            if not (self.I_CM_SWITCH.match_brightness(self.device.image)
                    and self.I_CM_SWITCH.match_mean_color(self.device.image, color=(134, 107, 83))):
                logger.warning('Collective refresh button is unavailable; defer selection')
                return False
            if not self._collective_click_available(self.I_CM_SWITCH):
                logger.warning('Collective refresh history is already full; defer without clearing protection')
                return False
            if self.appear_then_click(self.I_CM_SWITCH, interval=2):
                unchanged = unchanged + 1 if mission == last_switched else 0
                last_switched = mission
                click_cnt += 1
        logger.warning('Collective selection timed out; defer without submitting')
        return False



    def _soul(self, index: int):
        """
        搞收御魂的任务
        :param index:
        :return:
        """
        match_click = {
            0: self.C_CM_1,
            1: self.C_CM_2,
            2: self.C_CM_3,
        }
        self.ui_click(match_click[index], self.I_SL_SUBMIT)
        while 1:
            self.screenshot()
            number_text = self.O_SL_NUMBER.ocr(self.device.image)
            submit_number = int(re.findall(r'\d+', number_text)[-1])
            if submit_number > 0:
                break

            if self.ocr_appear(self.O_SL_LEVEL):
                # 如果没有识别到这个，那就说明没有御魂可以提交了，要退出
                logger.warning('No soul can be submit')
                self.ui_click(self.I_UI_BACK_RED, self.I_CM_RECORDS)
                return False

            if self.click(self.L_SL_LONG, interval=2.5):
                time.sleep(1)
                continue
        # 领取奖励
        logger.info('Start to collect soul rewards')
        check_timer = Timer(3)
        check_timer.start()
        while 1:
            self.screenshot()
            if self.ui_reward_appear_click(True):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_SL_SUBMIT, interval=1):
                check_timer.reset()
                continue
            if check_timer.reached():
                break
        logger.info('Finish to collect soul rewards')
        self.wait_until_appear(self.I_CM_RECORDS)

    def _feed_resume_allowed(self, counter):
        missions = self.config.collective_missions.missions_config
        if getattr(missions, 'attempted_date', '') != datetime.now().date().isoformat():
            return False
        match = re.fullmatch(r'feed_confirmed:([0-9]+)', getattr(missions, 'pending_kind', ''))
        if match is None or not 0 <= int(match.group(1)) < 30:
            return False
        expected = (int(match.group(1)), 30)
        if counter != expected or self._preferred_mission(missions.missions_select) != MC.FEED:
            return False
        return self._mission_counter() == expected

    def _save_feed_progress(self, kind, count):
        missions = self.config.collective_missions.missions_config
        if not all(hasattr(missions, name) for name in ('pending_kind', 'pending_until', 'attempted_date')):
            logger.warning('Durable feed state is unavailable; do not submit N cards')
            return False
        missions.attempted_date = datetime.now().date().isoformat()
        missions.pending_kind = f'feed_{kind}:{count}'
        missions.pending_until = ''
        self.config.save()
        return True

    def _feed_to_quota(self, index, counter):
        """Continue only after a settled batch proves its exact counter increase."""
        self._collective_pending_until = datetime.now() + timedelta(minutes=3)
        if (not isinstance(counter, (tuple, list)) or len(counter) != 2 or counter[1] != 30
                or type(counter[0]) is not int or not 0 <= counter[0] < 30
                or self._mission_counter() != counter):
            logger.warning('Feed quota is not confirmed twice; do not submit N cards')
            return False
        started_date = datetime.now().date()
        current = counter[0]
        limit = Timer(180).start()
        for _batch in range(15):
            if limit.reached() or datetime.now().date() != started_date:
                return False
            # Re-open only the same positively identified N-card submission UI.
            # Persist inflight before input so a crash never authorizes another
            # batch until its result has been positively verified and saved.
            budget = min(2, 30 - current)
            if not self._save_feed_progress('inflight', current):
                return False
            self._feed_submitted = False
            settled = self._feed(index, max_items=budget)
            if not settled:
                if (not self._feed_submitted and self._return_to_missions()
                        and self._mission_counter() == (current, 30)
                        and self._mission_counter() == (current, 30)
                        and datetime.now().date() == started_date):
                    self._save_feed_progress('confirmed', current)
                return False
            if not self._return_to_missions():
                return False
            after = self._mission_counter()
            if (after != (current + budget, 30) or self._mission_counter() != after
                    or datetime.now().date() != started_date):
                logger.warning('N-card submission result is unconfirmed; do not submit another batch')
                return False
            current = after[0]
            if not self._save_feed_progress('confirmed', current):
                return False
            logger.info(f'Collective feed quota confirmed: {current}/30')
            if current == 30:
                self._collective_pending_until = None
                return True
        return False

    def _feed_selected_count(self):
        text = self.O_FEED_SUBMIT_COUNT.ocr(self.device.image)
        if not isinstance(text, str):
            return None
        match = re.fullmatch(r'将提交([0-9]+)次任务', re.sub(r'\s+', '', text))
        if match is None or not 1 <= int(match.group(1)) <= 30:
            return None
        return int(match.group(1))

    def _feed(self, index: int, max_items=2):
        self._feed_submitted = False
        if type(max_items) is not int or not 1 <= max_items <= 2:
            return False
        logger.info('Start to feed soul')
        match_click = {
            0: self.C_CM_1,
            1: self.C_CM_2,
            2: self.C_CM_3,
        }
        if not self._open_collective_submission(match_click[index], self.I_FEED_N_PAGE):
            return False
        # A stacked card can represent several N cards, even with a short tap.
        # Clear any leftover choice and positively confirm expanded mode first.
        self.screenshot()
        if self.appear(self.I_FEED_SUBMIT):
            self.click(self.C_FEED_CANCEL, interval=1)
        expand_limit = Timer(8).start()
        while not expand_limit.reached():
            self.screenshot()
            if not self.appear(self.I_FEED_N_PAGE) or self.appear(self.I_FEED_SUBMIT):
                return False
            if self.appear(self.I_FEED_EXPANDED):
                break
            if not self._collective_click_available(self.C_FEED_EXPAND):
                return False
            self.click(self.C_FEED_EXPAND, interval=1)
        else:
            logger.warning('Expanded N-card selection is not confirmed; do not submit')
            return False
        time.sleep(1.2)  # The card row animates after the mode indicator changes.
        logger.info('Submit to feed soul')
        click_list = random.sample([self.L_FEED_CLICK_1, self.L_FEED_CLICK_2, self.L_FEED_CLICK_3, self.L_FEED_CLICK_4], max_items)
        for expected, slot in enumerate(click_list, start=1):
            click = RuleClick(tuple(slot.roi_front), tuple(slot.roi_front), name=f'{slot.name}_single')
            if not self._collective_click_available(click) or not self.click(click, interval=1.5):
                return False
            time.sleep(.5)
            count_limit = Timer(5).start()
            verified = 0
            while not count_limit.reached():
                self.screenshot()
                if not self.appear(self.I_FEED_N_PAGE) or not self.appear(self.I_FEED_EXPANDED):
                    return False
                number = self._feed_selected_count()
                if number is not None and number > expected:
                    logger.warning('Actual N-card selection differs from budget; do not submit')
                    return False
                verified = verified + 1 if number == expected else 0
                if verified >= 2:
                    break
            else:
                return False
        for _frame in range(2):
            self.screenshot()
            if not self.appear(self.I_FEED_SUBMIT) or self._feed_selected_count() != max_items:
                return False
        logger.info('Finish to feed soul')
        settled = self._submit_collective_once_and_claim(self.I_FEED_SUBMIT)
        if settled:
            logger.info('Feed reward phase finished; the counter will confirm completion')
        return settled




if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

