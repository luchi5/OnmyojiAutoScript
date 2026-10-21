"""拾此一瞬二阶段：神降尘世，额外奖励已完成次数达到100结束。"""

import re

from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger
from tasks.ActivityShikigami.boss_progress import BossProgressStore
from tasks.ActivityShikigami.current_event import CurrentEventAssets, _image
from tasks.Component.GeneralBattle.battle_wait import battle_wait_strategy, battle_wait_options


def parse_boss_bonus(text):
    """An unreadable counter is unknown, never an inferred zero."""
    text = re.sub(r'\s+', '', str(text or '')).replace('／', '/')
    text = text.replace('（', '(').replace('）', ')')
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
    match = re.fullmatch(r'(?:额外奖励)?(0|[1-9][0-9]{0,2})/100', text)
    if not match:
        return None
    completed = int(match.group(1))
    return completed if completed <= 100 else None


def resolve_boss_bonus(primary_text, backup_text, expected_min=None):
    """Cross-check fresh UI counters, without repairing malformed digits/slashes."""
    primary, backup = parse_boss_bonus(primary_text), parse_boss_bonus(backup_text)
    if primary is not None and backup is not None and primary != backup:
        return None, None
    completed = primary if primary is not None else backup
    source = 'both' if primary is not None and backup is not None else 'right' if primary is not None else 'left'
    if completed is None:
        return None, None
    # Several battles may have finished while OCR was unavailable. Accept a
    # fresh unchanged or advanced counter without requiring an exact +1.
    if expected_min is not None and completed < expected_min:
        return None, None
    return completed, source


class CurrentBossAssets:
    I_EVENT_BOSS_ENTRY = _image('boss_entry', (1070,303,115,33), (1035,275,195,95), 0.8)
    I_EVENT_BOSS_PAGE = _image('boss_page', (155,10,128,32), (130,0,190,65), 0.8)
    I_EVENT_BOSS_CHALLENGE = _image('boss_challenge', (1140,592,95,43), (1100,555,160,105), 0.8)
    I_EVENT_BOSS_RECORDS = _image('boss_records', (1025,558,55,69), (995,530,110,110), 0.8)
    O_EVENT_BOSS_BONUS = RuleOcr(
        roi=(1183,671,95,33), area=(1183,671,95,33), mode='Single',
        method='Default', keyword='', name='event_boss_bonus')
    # Wider left crop keeps all three digits of 100 and both parentheses.
    O_EVENT_BOSS_BONUS_BACKUP = RuleOcr(
        roi=(253,585,120,33), area=(253,585,120,33), mode='Single',
        method='Default', keyword='', name='event_boss_bonus_backup')


class CurrentEventBoss(CurrentBossAssets, CurrentEventAssets):
    def boss_fixed_limit(self):
        con = getattr(getattr(self, 'conf', None), 'general_climb', None)
        if getattr(con, 'boss_count_mode', 'boss_auto_progress') == 'boss_fixed_count':
            return con.boss_limit
        return None

    def boss_budget_exhausted(self):
        fixed = self.boss_fixed_limit()
        if fixed is not None:
            return self.count_map['boss'] >= fixed
        # Even without any readable game counter, never start win 101 in
        # this execution. A known anchor can only reduce the remaining budget.
        return getattr(self, '_boss_counted', 0) >= 100 or self.count_map['boss'] >= 100

    def boss_progress_store(self):
        account = getattr(getattr(self, 'config', None), 'config_name', None)
        if not account:
            return None
        if not hasattr(self, '_boss_progress_store'):
            self._boss_progress_store = BossProgressStore('data/ActivityShikigami/event_202610_boss', account)
        return self._boss_progress_store

    def boss_load_progress(self):
        store = self.boss_progress_store()
        if store is None:
            return
        try:
            record = store.load()
            self._boss_local_progress = record
            if record:
                self._boss_completed = record['confirmed']
                self._boss_counted = record['counted']
                logger.info(f"BOSS checkpoint: confirmed {record['confirmed']}, "
                            f"counted with wins {record['counted']}/100")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning(f'BOSS checkpoint unreadable; verify current game counter: {exc}')

    def boss_record_progress(self, confirmed, expected=None):
        counted = max(getattr(self, '_boss_counted', 0), confirmed or 0, expected or 0)
        self._boss_counted = counted
        state = (confirmed, expected, counted)
        if getattr(self, '_boss_last_record', None) == state:
            return
        # Keep the OCR observation distinct from progress counted by wins.
        self._boss_local_progress = dict(confirmed=confirmed, expected_after_settlement=expected,
                                        counted=counted)
        store = self.boss_progress_store()
        if store is not None:
            try:
                store.save(confirmed, expected, counted=counted)
            except (OSError, ValueError) as exc:
                logger.warning(f'BOSS checkpoint save failed; current run keeps its counted wins: {exc}')
                return
        self._boss_last_record = state

    def boss_page_visible(self):
        return self.appear(self.I_EVENT_BOSS_PAGE) and self.appear(self.I_EVENT_BOSS_CHALLENGE)

    def boss_read_bonus(self, expected_min=None):
        """Read progress briefly; unknown digits never prevent a challenge."""
        timeout = Timer(3).start()
        interval = Timer(0.6)
        previous = None
        consecutive = 0
        page_seen = False
        while not timeout.reached():
            self.screenshot()
            if not self.boss_page_visible():
                previous = None
                consecutive = 0
                continue
            page_seen = True
            if not interval.reached():
                continue
            interval.reset()
            completed, source = resolve_boss_bonus(
                self.O_EVENT_BOSS_BONUS.ocr_single(self.device.image),
                self.O_EVENT_BOSS_BONUS_BACKUP.ocr_single(self.device.image),
                expected_min)
            if completed is None:
                consecutive = 0
            elif completed == previous:
                consecutive += 1
            else:
                consecutive = 1
            # A terminal OCR value from one location needs another fresh frame.
            required = 3 if completed == 100 and source != 'both' else 2
            if completed is not None and consecutive >= required:
                self.boss_record_progress(completed)
                logger.info(f'BOSS extra rewards completed: {completed}/100; remaining: {100-completed}; source: {source}')
                return completed
            previous = completed
        if not page_seen or not self.boss_page_visible():
            raise GameStuckError('神降尘世未确认首领挑战页')
        logger.warning('BOSS奖励数字暂未读清，继续挑战；识别恢复后校准进度')
        return None

    def boss_wait_return(self):
        timeout = Timer(30).start()
        while not timeout.reached():
            self.screenshot()
            if self.boss_page_visible():
                return
        raise GameStuckError('神降尘世结算后未返回挑战页')

    def boss_battle_wait(self):
        """This timed boss can outlast the device's default five-minute wait."""
        original = self.device.stuck_timer_long
        self.device.stuck_timer_long = Timer(600, count=300).start()
        try:
            return self.battle_wait()
        finally:
            self.device.stuck_timer_long = original
            self.device.stuck_record_clear()

    def boss_battle_once(self):
        fixed = self.boss_fixed_limit()
        if self.boss_budget_exhausted():
            return False
        if fixed is None:
            before = self.boss_read_bonus(expected_min=getattr(self, '_boss_completed', None))
            if before is not None:
                self._boss_completed = before
                # boss_read_bonus also records real observations; keep this
                # explicit so callers supplying an observation behave alike.
                self.boss_record_progress(before)
            if self.boss_budget_exhausted():
                logger.info('BOSS可靠进度加后续胜场已达100，停止挑战')
                return False
        self.put_status()
        # Only one challenge click; keep all loading/unknown dialogs intact.
        self.click(self.I_EVENT_BOSS_CHALLENGE)
        timeout = Timer(45).start()
        while True:
            self.screenshot()
            if (self.is_in_battle(False) or self.appear(self.I_PREPARE_HIGHLIGHT)
                    or self.appear(self.I_REWARD) or self.appear(self.I_WIN)):
                break
            if timeout.reached():
                raise GameStuckError('神降尘世点击挑战后未进入战斗，不重复点击挑战')

        strategies, options = self.loadout_from_config(self.get_general_battle_conf())
        strategies['success'] = 'soul'
        # Reuse the verified ticket settlement exclusions and shared battle
        # preparation/preset/wait hooks; return directly to this boss page.
        safe = ['C_TICKET_REWARD_BODY', 'C_TICKET_REWARD_HEADER', 'C_TICKET_REWARD_BUFF']
        options['success'].excludes_1 = safe.copy()
        options['success'].excludes_2 = safe.copy()
        options['completion'].check_imgs = [self.I_EVENT_BOSS_PAGE]
        options['completion'].excludes = safe + ['C_TICKET_REWARD_DETAILS']
        options['completion'].fallback_interval = 3
        options['completion'].fallback_limit = 6
        with battle_wait_strategy(**strategies), battle_wait_options(**options):
            win = self.boss_battle_wait()
        if not win:
            raise RequestHumanTakeover('神降尘世挑战失败，请检查阵容后再继续')
        self.boss_wait_return()
        # A confirmed victory counts even if its reward digits are unreadable.
        self.count_map['boss'] += 1
        if fixed is not None:
            logger.info(f"BOSS指定场次: {self.count_map['boss']}/{fixed}；完全忽略奖励数字")
            return not self.boss_budget_exhausted()
        self._boss_counted = getattr(self, '_boss_counted', 0) + 1
        self.boss_record_progress(getattr(self, '_boss_completed', None))
        if self.boss_budget_exhausted():
            logger.info('BOSS可靠进度加后续胜场或本次胜场已达100，停止挑战')
            return False
        after = self.boss_read_bonus(expected_min=getattr(self, '_boss_completed', None))
        if after is not None:
            self._boss_completed = after
            self.boss_record_progress(after)
        progress = f'{after}/100' if after is not None else '暂未识别'
        logger.info(f"BOSS completed this run: {self.count_map['boss']}; extra rewards: {progress}; "
                    f"counted: {self._boss_counted}/100")
        return not self.boss_budget_exhausted()
