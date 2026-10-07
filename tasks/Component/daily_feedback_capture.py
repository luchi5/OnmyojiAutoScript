"""Read-only daily proofs at existing task boundaries.

Captures never click. The automatic revisit only navigates to/away from the
overview, never submits/refreshes/claims or changes account settings/schedules.
Optional proof collection must not stop gameplay.
"""
from datetime import datetime
from pathlib import Path
import re
import time

from module.logger import logger
from module.exception import (
    RequestHumanTakeover, GameStuckError, GameTooManyClickError, ScriptEnd,
    TaskEnd, GameBugError, EmulatorNotRunningError, GameNotRunningError,
)


_TALISMAN_OCR = None
_TALISMAN_TARGET_OCR = None
_DAY_ACTIVITY_LABELS = ('今日获得经验', '今日活跃度', '每日活跃度', '今日活跃')
CONTROL_EXCEPTIONS = (RequestHumanTakeover, GameStuckError, GameTooManyClickError,
                      ScriptEnd, TaskEnd, GameBugError, EmulatorNotRunningError,
                      GameNotRunningError)


def _fresh(task):
    # BaseTask.screenshot runs _burst and can accept cooperation invitations.
    # Device.screenshot keeps device checks without that gameplay side effect.
    return task.device.screenshot()


def _store(task, kind, **values):
    # Import lazily: an older worker must not retain a placeholder store module.
    from tasks.Component.daily_feedback import save_evidence
    return save_evidence(task.config, kind, **values)


def _enabled(task):
    try:
        from tasks.Component.daily_feedback import feedback_enabled
        return feedback_enabled(task.config)
    except Exception:
        return False


def _failed(task, kind, detail, *, final=False):
    try:
        return _store(task, kind, verified=False, detail=detail, final=bool(final))
    except Exception as exc:
        logger.warning(f'Daily feedback {kind} unavailable ({type(exc).__name__})')
        return False


def _plain(text):
    return re.sub(r'\s+', '', str(text or '')).replace('：', ':')


def parse_activity(text):
    """Only an explicit daily activity label can supply a numeric proof.

    Do not use Digit OCR, which converts an empty/malformed result to zero.
    Never infer a total or replace OCR letters with numeric guesses.
    """
    clean = _plain(text)
    labels = '|'.join(re.escape(label) for label in _DAY_ACTIVITY_LABELS)
    match = re.fullmatch(rf'(?:{labels})[:：]?([0-9]{{1,3}})(?:/([0-9]{{1,3}}))?', clean)
    if match is None:
        return None
    current = int(match.group(1))
    total = int(match.group(2)) if match.group(2) else None
    if current > 300 or (total is not None and (not 1 <= total <= 300 or current > total)):
        return None
    return current, total


def _talisman_lines(task):
    global _TALISMAN_OCR
    if _TALISMAN_OCR is None:
        from module.atom.ocr import RuleOcr
        # Actual live daily header, not the season level's 97/100 at the top.
        _TALISMAN_OCR = RuleOcr(roi=(997, 174, 177, 40), area=(997, 174, 177, 40),
                                mode='Single', method='Default', keyword='',
                                name='daily_feedback_talisman_today_exp')
        _TALISMAN_OCR.score = .85
    from types import SimpleNamespace
    return [SimpleNamespace(ocr_text=_TALISMAN_OCR.ocr(task.device.image), box=None)]


def _talisman_target(task):
    global _TALISMAN_TARGET_OCR
    if _TALISMAN_TARGET_OCR is None:
        from module.atom.ocr import RuleOcr
        _TALISMAN_TARGET_OCR = RuleOcr(roi=(1120, 571, 47, 30), area=(1120, 571, 47, 30),
                                       mode='Single', method='Default', keyword='',
                                       name='daily_feedback_talisman_last_chest_target')
        _TALISMAN_TARGET_OCR.score = .85
    text = _plain(_TALISMAN_TARGET_OCR.ocr(task.device.image))
    if re.fullmatch(r'[1-9][0-9]{0,2}', text) is None:
        return None
    value = int(text)
    return value if value <= 300 else None


def _activity_from_lines(lines):
    """Handle one OCR line or an adjacent label/number on the same row."""
    activity = []
    day_label = False
    for line in lines:
        text = _plain(getattr(line, 'ocr_text', ''))
        labels = '|'.join(re.escape(label) for label in _DAY_ACTIVITY_LABELS)
        # A mission description mentioning activity does not identify a page.
        if re.fullmatch(rf'(?:{labels}):?[0-9/IOolS?]*', text) is None:
            continue
        day_label = True
        value = parse_activity(text)
        if value is not None:
            activity.append(value)
            continue
        # Adjacent boxes must be unambiguously on the same line. Otherwise
        # unrelated mission counts or weekly experience could be mistaken.
        if text not in _DAY_ACTIVITY_LABELS and text.rstrip(':') not in _DAY_ACTIVITY_LABELS:
            continue
        box = getattr(line, 'box', None)
        if box is None:
            continue
        x_right, y_top, y_bottom = float(box[:, 0].max()), float(box[:, 1].min()), float(box[:, 1].max())
        candidates = []
        for other in lines:
            if other is line or getattr(other, 'box', None) is None:
                continue
            number = _plain(getattr(other, 'ocr_text', ''))
            if re.fullmatch(r'[0-9]{1,3}(?:/[0-9]{1,3})?', number) is None:
                continue
            other_box = other.box
            left = float(other_box[:, 0].min())
            mid_y = float(other_box[:, 1].mean())
            if 0 <= left - x_right <= 90 and y_top <= mid_y <= y_bottom:
                parsed = parse_activity(text.rstrip(':') + number)
                if parsed is not None:
                    candidates.append(parsed)
        if len(candidates) == 1:
            activity.append(candidates[0])
    # Duplicate or conflicting labels are not a reliable reading.
    return day_label, activity[0] if len(activity) == 1 else None


def _reward_visible(task):
    reward = getattr(task, 'I_UI_REWARD', None)
    return bool(reward is not None and task.appear(reward, threshold=.6))


def ensure_talisman_today(task):
    """Enter the task tab only under a positive flower-pass header.

    None keeps the original behavior for accounts with feedback disabled.
    Daily page recognition remains strict; unknown layouts are diagnostic only.
    """
    if not _enabled(task):
        return None
    try:
        from module.base.timer import Timer
        from tasks.Component.daily_feedback_capture_assets import DailyFeedbackCaptureAssets as assets
        limit = Timer(10).start()
        clicks = 0
        while not limit.reached():
            _fresh(task)
            captured_at = datetime.now()
            if _reward_visible(task):
                _debug_talisman(task, captured_at)
                return False
            if not task.appear(assets.I_TALISMAN_HEADER):
                continue
            task_page = task.appear(task.I_TP_GOTO) or task.appear(task.I_TP_EXP)
            if task_page:
                # Activity OCR is evidence only. Its failure must never veto
                # the original task's positive reward-claim page marker.
                if task.appear(assets.I_TALISMAN_TODAY_SELECTED):
                    return True
                click = assets.C_TALISMAN_TODAY_TAB
            else:
                click = assets.C_TALISMAN_TASK_TAB
            if clicks >= 3 or not _navigation_available(task, click):
                break
            if task.click(click, interval=1.5):
                clicks += 1
        _debug_talisman(task, datetime.now())
        return False
    except CONTROL_EXCEPTIONS:
        raise
    except Exception as exc:
        logger.warning(f'Daily flower task tab unavailable ({type(exc).__name__})')
        return False


def _debug_talisman(task, captured_at):
    """Latest raw layout only, deliberately outside the verified proof store."""
    try:
        name = getattr(task.config, 'config_name', '')
        if (not isinstance(name, str) or not name or name in ('.', '..')
                or re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or name[-1:] in (' ', '.')):
            return
        from PIL import Image
        parent = (Path(__file__).resolve().parents[2] / 'log' / 'daily_feedback_debug').resolve()
        folder = (parent / name).resolve()
        if folder.parent != parent:
            return
        folder.mkdir(parents=True, exist_ok=True)
        Image.fromarray(task.device.image).save(folder / f'{captured_at.date().isoformat()}-talisman.png')
    except Exception:
        # Diagnostics are not evidence and must never block a task.
        pass


def capture_talisman(task, *, final=False):
    """Capture after daily claims and before switching to level rewards."""
    try:
        if not _enabled(task):
            return False
        started = datetime.now().date()
        if getattr(task, '_daily_feedback_started_date', started) != started:
            return _failed(task, 'talisman', '任务跨过每日重置时间，不采集新一天证明', final=final)
        values = []
        targets = []
        image = None
        for frame in range(2):
            if frame:
                time.sleep(.2)
            _fresh(task)
            captured_at = datetime.now()
            task_page = (task.appear(task.I_TP_GOTO) or task.appear(task.I_TP_EXP))
            if not task_page or _reward_visible(task):
                _debug_talisman(task, captured_at)
                return _failed(task, 'talisman', '今日任务页或领取后画面未确认', final=final)
            from tasks.Component.daily_feedback_capture_assets import DailyFeedbackCaptureAssets as assets
            if not task.appear(assets.I_TALISMAN_TODAY_SELECTED):
                _debug_talisman(task, captured_at)
                return _failed(task, 'talisman', '今日选中页签未正向确认', final=final)
            day_page, value = _activity_from_lines(_talisman_lines(task))
            if not day_page:
                _debug_talisman(task, captured_at)
                return _failed(task, 'talisman', '未正向识别今日活跃度页面', final=final)
            image = task.device.image.copy()
            values.append(value)
            targets.append(_talisman_target(task))
        if datetime.now().date() != started:
            return _failed(task, 'talisman', '采集跨过每日重置时间', final=final)
        verified = values[0] is not None and values[0] == values[1]
        current, total = values[1] if verified else (None, None)
        target = targets[1] if targets[0] is not None and targets[0] == targets[1] else None
        return _store(task, 'talisman', image=image, current=current, total=total,
                      verified=verified, detail='花合战今日经验双帧确认' if verified else '截图已获取，今日经验未核验',
                      final=bool(final), now=captured_at, target=target)
    except CONTROL_EXCEPTIONS:
        raise
    except Exception as exc:
        logger.warning(f'Daily talisman capture skipped ({type(exc).__name__})')
        return _failed(task, 'talisman', '今日反馈采集异常，游戏任务继续', final=final)


def _collective_counter(task):
    result = task.O_CM_NUMBER.ocr(task.device.image)
    if not isinstance(result, (tuple, list)) or len(result) != 3:
        return None
    current, remain, total = result
    if (any(type(value) is not int for value in result) or total != 30
            or not 0 <= current <= total or remain != total - current):
        return None
    return current, total


def capture_collective(task, *, final=False):
    """Positive overview + two fresh exact counters, before any exit input."""
    try:
        if not _enabled(task):
            return False
        started = datetime.now().date()
        if getattr(task, '_daily_feedback_started_date', started) != started:
            return _failed(task, 'collective', '任务跨过每日重置时间，不采集新一天证明', final=final)
        values = []
        image = None
        for frame in range(2):
            if frame:
                time.sleep(.2)
            _fresh(task)
            captured_at = datetime.now()
            if not task.appear(task.I_CM_RECORDS) or _reward_visible(task):
                return _failed(task, 'collective', '寮任务总览或领取后画面未确认', final=final)
            image = task.device.image.copy()
            values.append(_collective_counter(task))
        if datetime.now().date() != started:
            return _failed(task, 'collective', '采集跨过每日重置时间', final=final)
        verified = values[0] is not None and values[0] == values[1]
        current, total = values[1] if verified else (None, None)
        result = _store(task, 'collective', image=image, current=current, total=total,
                      verified=verified, detail='寮集体任务次数双帧确认' if verified else '截图已获取，任务次数未核验',
                      final=bool(final), now=captured_at)
        if result and verified and (current, total) == (30, 30):
            from tasks.Component.daily_closeout import report_closeout_outcome
            report_closeout_outcome(task.config, 'CollectiveMissions', 'completed',
                                    now=captured_at, detail='feedback_counter_30_of_30_confirmed_twice')
        return result
    except CONTROL_EXCEPTIONS:
        raise
    except Exception as exc:
        logger.warning(f'Daily collective capture skipped ({type(exc).__name__})')
        return _failed(task, 'collective', '寮任务反馈采集异常，游戏任务继续', final=final)


def _navigation_available(task, marker):
    history = list(getattr(task.device, 'click_record', ()))
    name = getattr(marker, 'name', str(marker))
    return history.count(name) < 5


def _open_feedback_panel(task, entry, destination):
    from module.base.timer import Timer
    limit = Timer(10).start()
    clicks = 0
    while not limit.reached():
        _fresh(task)
        if _reward_visible(task):
            return False
        if task.appear(destination):
            return True
        if clicks >= 3:
            return False
        if task.appear(entry) and _navigation_available(task, entry):
            if task.appear_then_click(entry, interval=1.5):
                clicks += 1
    return False


def _exit_feedback_panel(task, assets):
    from module.base.timer import Timer
    limit = Timer(8).start()
    clicks = 0
    while not limit.reached():
        _fresh(task)
        if _reward_visible(task):
            return False
        overview = task.appear(assets.I_CM_RECORDS)
        shrine = task.appear(assets.I_CM_CM)
        if not overview and not shrine:
            return True
        if clicks >= 3:
            return False
        # Only close a positively identified collective/shrine panel. Never
        # click a material/card/submission control or dismiss a reward here.
        marker = task.I_UI_BACK_RED if overview else task.I_UI_BACK_YELLOW
        if task.appear(marker) and _navigation_available(task, marker):
            if task.appear_then_click(marker, interval=1):
                clicks += 1
    return False


def refresh_collective_feedback(task, *, expected_date=None):
    """Automatic closeout revisit, without constructing another ScriptTask.

    Enabled weekday guild tasks only; the task adapter keeps the original
    device, task identity, costume selection, and click protections.
    """
    assets = None
    entered = False
    try:
        if not _enabled(task):
            return False
        today = datetime.now().date()
        collective = getattr(task.config, 'collective_missions', None)
        if (collective is None or not getattr(collective.scheduler, 'enable', False)
                or today.weekday() > 3):
            return False
        if expected_date is not None and expected_date != today:
            return _failed(task, 'collective', '收尾任务跨过每日重置时间', final=True)
        from types import SimpleNamespace
        from tasks.CollectiveMissions.assets import CollectiveMissionsAssets
        assets = CollectiveMissionsAssets
        if not _goto_guild_readonly(task):
            return _failed(task, 'collective', '未确认安全起始页，不自动登录或领取弹层', final=True)
        entered = True
        if (not _open_feedback_panel(task, assets.I_CM_SHRINE, assets.I_CM_CM)
                or not _open_feedback_panel(task, assets.I_CM_CM, assets.I_CM_RECORDS)):
            return _failed(task, 'collective', '收尾只读复查未确认寮任务总览', final=True)
        if datetime.now().date() != today:
            return _failed(task, 'collective', '收尾采集跨过每日重置时间', final=True)
        adapter = SimpleNamespace(config=task.config, device=task.device,
                                  appear=task.appear,
                                  _daily_feedback_started_date=today,
                                  I_CM_RECORDS=assets.I_CM_RECORDS,
                                  I_UI_REWARD=getattr(task, 'I_UI_REWARD', None),
                                  O_CM_NUMBER=assets.O_CM_NUMBER)
        return capture_collective(adapter, final=True)
    except CONTROL_EXCEPTIONS:
        entered = False
        raise
    except Exception as exc:
        logger.warning(f'Daily collective read-only revisit skipped ({type(exc).__name__})')
        return _failed(task, 'collective', '收尾寮任务反馈采集异常，游戏任务继续', final=True)
    finally:
        if entered and assets is not None:
            try:
                _exit_feedback_panel(task, assets)
            except CONTROL_EXCEPTIONS:
                raise
            except Exception:
                pass


def _goto_guild_readonly(task):
    """Safe daily->main->guild edges, without navigator enter/recovery hooks."""
    from module.base.timer import Timer
    from tasks.Component.daily_feedback_capture_assets import DailyFeedbackCaptureAssets as assets
    limit = Timer(15).start()
    clicks = 0
    while not limit.reached():
        _fresh(task)
        detector = getattr(task, 'detect_random_main_costume', None)
        if callable(detector):
            detector()
        if _reward_visible(task):
            return False
        if task.appear(task.I_CHECK_GUILD):
            return True
        if task.appear(assets.I_TALISMAN_HEADER):
            marker = task.I_BACK_DAILY
        elif task.appear(task.I_CHECK_MAIN):
            marker = task.I_MAIN_GOTO_GUILD
        else:
            # Unknown/login/loading screens are never acted on by the report.
            continue
        if clicks >= 3:
            return False
        if task.appear(marker) and _navigation_available(task, marker):
            if task.appear_then_click(marker, interval=1.5):
                clicks += 1
    return False


def goto_talisman_readonly(task):
    """Capture-only daily entry without login/reward navigator hooks."""
    from module.base.timer import Timer
    from tasks.Component.daily_feedback_capture_assets import DailyFeedbackCaptureAssets as assets
    limit = Timer(10).start()
    clicks = 0
    while not limit.reached():
        _fresh(task)
        detector = getattr(task, 'detect_random_main_costume', None)
        if callable(detector):
            detector()
        if _reward_visible(task):
            return False
        if task.appear(assets.I_TALISMAN_HEADER):
            return True
        if not task.appear(task.I_CHECK_MAIN):
            continue
        marker = task.I_MAIN_GOTO_DAILY
        if clicks >= 3:
            return False
        if task.appear(marker) and _navigation_available(task, marker):
            if task.appear_then_click(marker, interval=1.5):
                clicks += 1
    return False
