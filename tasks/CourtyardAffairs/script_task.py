from module.exception import TaskEnd, GameStuckError
from datetime import datetime, timedelta, timezone, time as clock_time
import json
import os
import uuid
from tasks.CourtyardAffairs.selected_skin import CompletionAwareCourtyardMixin
from tasks.CourtyardAffairs.themes import CourtyardSkin
from tasks.CourtyardAffairs.rewards import CourtyardRewardMixin
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.Restart.assets import RestartAssets


from copy import copy
from pathlib import Path
import cv2
import numpy as np
from module.atom.image import RuleImage
from module.base.timer import Timer
from module.logger import logger
from tasks.GameUi.action import conditional_action


class LongyiPageRule(RuleImage):
    """Add a positively recognized Longyi page without changing legacy rules."""

    def __init__(self, legacy, page, target=None, daily=None, inactive=None):
        super().__init__(tuple(legacy.roi_front), legacy.roi_back,
                         legacy.method, legacy.threshold, legacy.file)
        self.legacy = copy(legacy)
        self.legacy.roi_front = list(legacy.roi_front)
        self.page, self.target, self.daily, self.inactive = page, target, daily, inactive

    def match(self, image, threshold=None):
        if self.page.match(image):
            if self.daily is not None and (not self.daily.match(image)
                    or self.inactive.match(image)):
                return False
            target = self.target if self.target is not None else self.page
        else:
            target = self.legacy
        if not target.match(image, threshold=threshold):
            return False
        self.roi_front = list(target.roi_front)
        return True


class LongyiCompletionDetector:
    def __init__(self, rules):
        self.rules = rules

    def ready(self, image):
        r = self.rules
        if (not r['page'].match(image) or not r['daily'].match(image)
                or r['inactive'].match(image) or not r['disabled'].match(image)
                or not all(rule.match(image) for rule in r['stamps'])):
            return False
        # Bright enabled and dark disabled buttons correlate almost equally.
        # Require the actual dark pixels and an unshaded blank task-card area.
        button = image[587:672, 1120:1205]
        card = image[290:315, 975:1010]
        return (button.size > 0 and card.size > 0
                and float(cv2.cvtColor(button, cv2.COLOR_RGB2HSV)[:, :, 2].mean()) <= 140
                and float(np.median(cv2.cvtColor(card, cv2.COLOR_RGB2HSV)[:, :, 2])) >= 190)


class LongyiCourtyardMixin(CompletionAwareCourtyardMixin):
    def configure_courtyard_skin(self, courtyard_skin=CourtyardSkin.AUTO):
        skin = CourtyardSkin(courtyard_skin)
        if isinstance(getattr(self, 'I_PAGE', None), LongyiPageRule) and self._courtyard_skin == skin:
            return
        navigator = getattr(self, 'navigator', None)
        old = getattr(self, '_longyi_closers', ())
        if navigator is not None:
            navigator.local_unknown_closers[:] = [item for item in navigator.local_unknown_closers
                                                  if not any(item is prior for prior in old)]
        self._longyi_closers = ()
        super().configure_courtyard_skin(skin)
        self._longyi_rules = None
        if skin != CourtyardSkin.AUTO:
            return
        directory = Path(__file__).resolve().parent / 'longyi_skin'
        specs = {
            'page': ((731, 75, 266, 65), (700, 50, 335, 108)),
            'daily': ((1164, 164, 38, 68), (1150, 144, 62, 103)),
            'inactive': ((1164, 164, 38, 68), (1150, 144, 62, 103)),
            'complete': ((1110, 574, 110, 115), (1095, 559, 145, 145)),
            'disabled': ((1110, 574, 110, 115), (1095, 559, 145, 145)),
            'reward': ((716, 67, 302, 75), (690, 46, 360, 116)),
        }
        # New assets are installed before this single source file. A missing
        # resource set safely retains the original bounded legacy behavior.
        if not all((directory / (name + '.png')).is_file() for name in (*specs, 'stamp')):
            return
        rules = {name: RuleImage(front, back, 'Template matching', .9,
                                str(directory / (name + '.png')))
                 for name, (front, back) in specs.items()}
        rules['stamps'] = tuple(RuleImage((1031, 189 + 127*i, 68, 42),
                              (1005, 163 + 127*i, 139, 95), 'Template matching', .9,
                              str(directory / 'stamp.png')) for i in range(4))
        self._longyi_rules = rules
        self.I_PAGE = LongyiPageRule(self.I_PAGE, rules['page'])
        self.I_DAILY = LongyiPageRule(self.I_DAILY, rules['page'], rules['inactive'])
        self.I_COMPLETE_TASKS = LongyiPageRule(self.I_COMPLETE_TASKS, rules['page'],
                                             rules['complete'], rules['daily'], rules['inactive'])
        if navigator is not None:
            closers = (conditional_action(rules['page'], self.I_UI_BACK_YELLOW),
                       conditional_action(rules['reward'], self.C_BLUE_REWARD_CLOSE))
            self._longyi_closers = closers
            navigator.add_unknown_closer(*closers)

    def harvest_courtyard_affairs(self, timeout_seconds=60, max_complete_clicks=3,
                                 courtyard_skin=CourtyardSkin.AUTO):
        self.configure_courtyard_skin(courtyard_skin)
        rules = self._longyi_rules
        if rules is None:
            return super().harvest_courtyard_affairs(timeout_seconds, max_complete_clicks, courtyard_skin)
        if not self.ui_click_multi_scale(self.I_NOTE, self.I_PAGE, timeout=3, scale_range=(.8, 1.2)):
            logger.warning('Courtyard affairs entry was not found')
            return False
        if not rules['page'].match(self.device.image):
            # Default and blue skins keep their original completion workflow.
            return super().harvest_courtyard_affairs(timeout_seconds, max_complete_clicks, courtyard_skin)
        logger.info('Courtyard affairs skin: Longyi Xingyin (auto detected)')
        detector = LongyiCompletionDetector(rules)
        timer = Timer(timeout_seconds).start()
        clicks = stable_done = 0
        while True:
            self.screenshot()
            if timer.reached():
                logger.warning('Longyi courtyard timed out; keep the task unfinished')
                return False
            if self.handle_courtyard_reward():
                stable_done = 0
                continue
            # Rewards and daily sign-in overlays are intermediate states.
            # Never use any of them as completion proof.
            handled = (
                self.appear_then_click(rules['reward'], action=self.C_BLUE_REWARD_CLOSE, interval=1)
                or self.appear_then_click(self.I_HARVEST_SIGN_2, interval=1.5)
                or self.appear_then_click(self.I_HARVEST_SIGN_4, interval=1)
                or self.appear_then_click(self.I_HARVEST_SOUL_2, interval=1)
                or self.appear_then_click(self.I_HARVEST_SOUL_3, interval=1)
                or self.ui_reward_appear_click()
                or self.appear_then_click(self.I_UI_AWARD, interval=.2)
                or self.appear_then_click(self.I_CONFIRM, interval=1)
                or self.appear_then_click(self.I_DAILY, interval=1)
                or self.appear_then_click(self.I_SKIP, interval=1)
                or self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=1))
            if handled:
                stable_done = 0
                continue
            stable_done = stable_done + 1 if detector.ready(self.device.image) else 0
            if stable_done >= 2:
                logger.info('Longyi courtyard daily affairs completed; disabled button and completed rows confirmed')
                return True
            if stable_done:
                continue
            if clicks >= max_complete_clicks:
                logger.warning('Longyi courtyard click limit reached without completion proof')
                return False
            # Do not click a disabled button even if another row lacks proof.
            if (float(cv2.cvtColor(self.device.image[587:672, 1120:1205], cv2.COLOR_RGB2HSV)[:, :, 2].mean()) > 150
                    and self.appear_then_click(self.I_COMPLETE_TASKS, interval=2.3)):
                clicks += 1


class ScriptTask(LongyiCourtyardMixin, CourtyardRewardMixin, GameUi, RestartAssets):
    """An independently scheduled daily task; login no longer runs it."""

    def appear_then_click(self, target, *args, **kwargs):
        clicked = super().appear_then_click(target, *args, **kwargs)
        if clicked and target is self.I_COMPLETE_TASKS:
            self._courtyard_one_click_count = getattr(self, '_courtyard_one_click_count', 0) + 1
        return clicked

    def ui_click_multi_scale(self, click, stop, *args, **kwargs):
        opened = super().ui_click_multi_scale(click, stop, *args, **kwargs)
        if click is self.I_NOTE and stop is self.I_PAGE and not opened:
            self.screenshot()
            self._courtyard_no_entry = (
                self.appear(self.I_CHECK_MAIN) and not self.appear(self.I_NOTE))
        return opened

    def _write_courtyard_outcome(self, outcome):
        now = datetime.now(timezone(timedelta(hours=8)))
        name = self.config.config_name
        if (not isinstance(name, str) or not name or name in ('.', '..')
                or any(c in name for c in '\\/:*?"<>|')
                or any(ord(c) < 32 for c in name) or name[-1:] in (' ', '.')):
            raise ValueError('Invalid courtyard evidence account name')
        root = Path(__file__).resolve().parents[2]
        directory = root / 'log' / 'courtyard_affairs' / name
        target = directory / (now.date().isoformat() + '.json')
        for path in (root, root / 'log', root / 'log' / 'courtyard_affairs', directory, target):
            if path.exists() and (path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
                raise ValueError('Linked courtyard evidence path')
        if target.exists() and target.stat().st_nlink != 1:
            raise ValueError('Hardlinked courtyard evidence path')
        directory.mkdir(parents=True, exist_ok=True)
        record = {'schema_version': 1, 'account': name, 'task': 'CourtyardAffairs',
                  'date': now.date().isoformat(), 'at': now.isoformat(timespec='seconds'),
                  'outcome': outcome, 'one_click_count': self._courtyard_one_click_count,
                  'source': 'courtyard_affairs_task_v1'}
        # A later empty check must not erase an earlier confirmed claim today.
        if target.exists():
            previous = json.loads(target.read_text(encoding='utf-8'))
            if (previous.get('account') == name and previous.get('date') == record['date']
                    and previous.get('outcome') == 'completed'
                    and type(previous.get('one_click_count')) is int and previous['one_click_count'] > 0
                    and outcome != 'completed'):
                previous['last_check'] = record
                record = previous
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            fd = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists() and (target.is_symlink() or getattr(target.lstat(), 'st_file_attributes', 0) & 0x400
                                    or target.stat().st_nlink != 1):
                raise ValueError('Unsafe courtyard evidence target')
            os.replace(str(temporary), str(target))
        finally:
            if temporary.exists():
                temporary.unlink()

    def _schedule_courtyard_tomorrow(self):
        now = datetime.now(timezone(timedelta(hours=8)))
        setting = self.config.courtyard_affairs.scheduler.server_update
        if isinstance(setting, str):
            setting = clock_time.fromisoformat(setting)
        target = datetime.combine(now.date() + timedelta(days=1),
                                  clock_time(setting.hour, setting.minute, setting.second))
        self.set_next_run('CourtyardAffairs', target=target, server=False)

    def run(self):
        options = self.config.courtyard_affairs.courtyard_affairs_config
        courtyard_skin = getattr(options, 'courtyard_skin', CourtyardSkin.AUTO)
        # Register task-local recovery before navigation, including when a
        # manually retried task starts on a leftover blue claim-result page.
        self._courtyard_one_click_count = 0
        self._courtyard_no_entry = False
        self.configure_courtyard_skin(courtyard_skin)
        # A manually resumed Longyi page may already be complete and no
        # longer expose its courtyard note. Handle that page before leaving.
        self.screenshot()
        rules = getattr(self, '_longyi_rules', None)
        if rules is None or not rules['page'].match(self.device.image):
            self.goto_page(page_main)
        success = self.harvest_courtyard_affairs(
            timeout_seconds=options.timeout_seconds,
            max_complete_clicks=options.max_complete_clicks,
            courtyard_skin=courtyard_skin,
        )
        # Like AZ, finish the sign-in/reward flow before leaving the task.
        # A missing note starts from main, so no-entry checks can end directly.
        if not self._courtyard_no_entry and not self.finish_courtyard_rewards(
                timeout_seconds=options.timeout_seconds):
            raise GameStuckError('Courtyard sign-in/rewards did not return to main')
        self.goto_page(page_main)
        outcome = ('completed' if success and self._courtyard_one_click_count > 0
                   else 'already_complete' if success else 'no_entry' if self._courtyard_no_entry
                   else 'failed')
        self._write_courtyard_outcome(outcome)
        self._schedule_courtyard_tomorrow()
        raise TaskEnd('CourtyardAffairs')
