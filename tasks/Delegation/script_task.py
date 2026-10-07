# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import time, datetime, timedelta
from copy import copy
import time as task_time

from module.logger import logger
from module.exception import (
    GamePageUnknownError, GameStuckError, GameTooManyClickError, TaskEnd,
)
from module.atom.click import RuleClick
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_delegation
from tasks.Delegation.config import DelegationConfig
from tasks.Delegation.assets import DelegationAssets


class ScriptTask(GameUi, DelegationAssets):

    def run(self):
        self.goto_page(page_delegation)
        if not self.check_reward():
            # Unconfirmed rewards stay pending. A routine UI miss must not
            # stop this account or be reported as a completed delegation.
            self.set_next_run(
                task='Delegation', success=None, server=False,
                target=datetime.now() + timedelta(minutes=30),
            )
            logger.warning('Delegation rewards unconfirmed; defer 30 minutes and continue other tasks')
            try:
                self.goto_page(page_main)
            except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as exc:
                logger.warning(f'Delegation return to courtyard deferred: {exc}')
            raise TaskEnd('Delegation reward pending')
        con: DelegationConfig = self.config.delegation.delegation_config
        if con.miyoshino_painting:
            self.delegate_one('画')
        if con.bird_feather:
            self.delegate_one('鸟羽')
        if con.find_earring:
            self.delegate_one('寻找耳环')
        if con.cat_boss:
            self.delegate_one('猫老大')
        if con.miyoshino:
            self.delegate_one('接送')
        if con.strange_trace:
            self.delegate_one('痕迹')


        self.set_next_run(task='Delegation', success=True, finish=True)
        raise TaskEnd

    def delegate_one(self, name: str) -> bool:
        """
        委派一个任务
        :param name:
        :return:
        """
        def ui_click(click, stop):
            while 1:
                self.screenshot()
                if self.appear(stop):
                    break
                if self.click(click, interval=1.5):
                    continue
        logger.hr('Delegation one', 2)
        self.O_D_NAME.keyword = name
        self.screenshot()
        if not self.ocr_appear(self.O_D_NAME):
            logger.warning(f'Delegation: {name} not found')
            return False
        while 1:
            self.screenshot()
            if self.appear(self.I_D_START):
                break
            # 如果出现’召回‘ ’返回‘ 说明这个是现在委派中
            # 需要退出
            if self.appear(self.I_D_BACK):
                logger.warning(f'Delegation: {name} is in delegation')
                self.ui_click_until_disappear(self.I_D_BACK)
                self.wait_until_appear(self.I_REWARDS_MIN)
                return False
            if self.appear_then_click(self.I_D_SKIP, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_CONFIRM, interval=0.8):
                continue
            if self.ocr_appear_click(self.O_D_NAME, interval=1):
                continue
        # 进入委派  fefe e  fe
        logger.info(f'Enter Delegation: {name}')
        ui_click(self.C_D_1, self.I_D_SELECT_1)
        ui_click(self.C_D_2, self.I_D_SELECT_2)
        ui_click(self.C_D_3, self.I_D_SELECT_3)
        ui_click(self.C_D_4, self.I_D_SELECT_4)
        # 委派开始
        logger.info(f'Delegation: {name} start')
        while 1:
            self.screenshot()
            if not self.appear(self.I_D_START):
                break
            if self.click(self.C_D_5, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_START, interval=1.8):
                continue
        # ui_click(self.C_D_5, self.I_D_SELECT_5)
        # self.ui_click_until_disappear(self.I_D_START)

    def _completed_reward_candidates(self):
        """Select one task at a time; never merge unrelated '完成' labels."""
        candidates = []
        self._reward_scan_valid = False
        for kind, roi in (
            ('list', (956, 116, 308, 534)),
            ('map', (60, 116, 896, 544)),
        ):
            # Keep the existing ONNX session shared; it is not copyable.
            reader = copy(self.O_D_DONE)
            reader.roi = list(roi)
            results = reader.detect_and_ocr(self.device.image)
            if results:
                self._reward_scan_valid = True
            for result in results:
                if result.ocr_text.strip() != '完成':
                    continue
                box = result.box
                left = min(float(point[0]) for point in box) + roi[0]
                right = max(float(point[0]) for point in box) + roi[0]
                top = min(float(point[1]) for point in box) + roi[1]
                bottom = max(float(point[1]) for point in box) + roi[1]
                if kind == 'list':
                    # The flag is a status label. Open its task portrait below.
                    area = (995, int(bottom + 9), 60, 34)
                else:
                    # The map flag sits below its interactive character marker.
                    area = (int((left + right) / 2 - 18), int(top - 40), 36, 28)
                x, y, width, height = area
                if x < 0 or y < 0 or x + width > 1280 or y + height > 720:
                    continue
                key = (kind, int((top + bottom) / 2 // 24))
                if kind == 'map':
                    key += (int((left + right) / 2 // 24),)
                candidates.append((key, RuleClick(
                    roi_front=area, roi_back=area, name='delegation_completed_task',
                )))
        return candidates

    def _reward_dialog_visible(self):
        return any(self.appear(target) for target in (
            self.I_REWARDS_GET, self.I_REWARDS_DONE, self.I_REWARDS_FALSE,
            self.I_CHAT_1, self.I_CHAT_2,
        ))

    def _wait_reward_dialog(self):
        deadline = task_time.monotonic() + 5
        while task_time.monotonic() < deadline:
            self.screenshot()
            if self._reward_dialog_visible():
                self.device.click_record_clear()
                return True
            # A changed view with the dialogue arrow is also a verified
            # transition; the arrow alone on the map is not progress.
            if not self.appear(self.I_REWARDS_MIN) and self.appear(self.I_REWARDS_CHAT):
                self.device.click_record_clear()
                return True
            sleep(0.2)
        return False

    def check_reward(self):
        deadline = task_time.monotonic() + 75
        attempted = set()
        empty_checks = 0
        awaiting_reward = False
        reward_clicked = False
        try:
            while task_time.monotonic() < deadline:
                self.screenshot()
                on_map = self.appear(self.I_REWARDS_MIN)
                dialog_visible = self._reward_dialog_visible()
                if on_map and not dialog_visible and reward_clicked:
                    # Confirmation has left the screen and the map is back.
                    self.device.click_record_clear()
                    attempted.clear()
                    awaiting_reward = False
                    reward_clicked = False
                if dialog_visible or not on_map:
                    for target in (
                        self.I_REWARDS_GET, self.I_REWARDS_FALSE,
                        self.I_CHAT_1, self.I_CHAT_2, self.I_REWARDS_CHAT,
                        self.I_REWARDS_DONE,
                    ):
                        if self.appear_then_click(target, interval=1):
                            awaiting_reward = True
                            if target is self.I_REWARDS_DONE:
                                reward_clicked = True
                            break
                    else:
                        sleep(0.2)
                    continue
                if awaiting_reward:
                    # Returning to the map alone does not prove a reward was
                    # claimed. Keep the pending task for a later retry.
                    logger.warning('Delegation returned without reward confirmation')
                    return False
                candidates = self._completed_reward_candidates()
                if not candidates:
                    if not self._reward_scan_valid:
                        logger.warning('Delegation text unreadable; do not assume rewards are empty')
                        return False
                    empty_checks += 1
                    if empty_checks >= 2:
                        return True
                    sleep(0.5)
                    continue
                empty_checks = 0
                candidate = next((item for item in candidates if item[0] not in attempted), None)
                if candidate is None or len(attempted) >= 6:
                    logger.warning('Completed delegation labels did not open a reward dialog')
                    return False
                key, click = candidate
                attempted.add(key)
                self.click(click, interval=1)
                if self._wait_reward_dialog():
                    awaiting_reward = True
            logger.warning('Delegation reward flow timed out; rewards remain unconfirmed')
            return False
        except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as exc:
            # AccountLoggedInElsewhere/RequestHumanTakeover intentionally pass
            # through, so the account owner always retains control.
            logger.warning(f'Delegation reward UI skipped for this run: {exc}')
            return False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    # t.delegate_one('弥助的画')
    t.run()



