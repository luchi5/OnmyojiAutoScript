"""October 2026 wanted cards and route sheet; no configuration schema changes."""
import re
from time import time_ns, monotonic, sleep
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.atom.click import RuleClick
from module.base.timer import Timer
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger


def image(name, front, back, threshold=.85):
    return RuleImage(roi_front=front, roi_back=back, threshold=threshold,
                     method='Template matching', file=f'./tasks/WantedQuests/new_ui/{name}.png')


def text(name, roi):
    return RuleOcr(roi=roi, area=roi, mode='Single', method='Default', keyword='', name=name)


class WantedNewUi:
    def new_wanted_rules(self):
        if not hasattr(self, '_new_wanted_rules'):
            self._new_wanted_rules = {
                'home': image('home', (570,34,140,46), (550,20,190,80), .9),
                'trace': image('trace', (1144,591,87,89), (1125,575,125,125)),
                'traced': image('traced', (1141,589,90,93), (1125,575,125,125)),
                'explore': image('explore', (1032,631,85,73), (1010,610,115,105)),
                'close_home': image('close_home', (1206,65,58,55), (1190,45,85,90)),
                'detail': image('detail', (639,162,127,36), (620,145,170,65), .9),
                'close_detail': image('close_detail', (1180,107,60,48), (1165,90,90,85)),
                'route': image('goto', (1058,284,87,38), (1045,270,120,300)),
                'detail_traced': image('traced', (1141,550,90,93), (1125,535,125,150)),
                'detail_trace': image('trace', (1144,550,87,89), (1125,535,125,150)),
                'own_marker': image('own_marker', (619,201,35,34), (605,190,65,60), .9),
                'partner_marker': image('partner_marker', (618,324,36,35), (605,310,65,65), .9),
                'invite': image('invite_icon', (154,399,38,36), (90,375,1110,85), .85),
            }
        return self._new_wanted_rules

    def new_wanted_home(self):
        rules = self.new_wanted_rules()
        frame = self.device.image
        if rules['home'].match(frame):
            return True
        # An expanded cooperation card may cover the title. Require all three
        # home-only controls, rather than accepting a tracking button alone.
        return (rules['close_home'].match(frame) and rules['explore'].match(frame)
                and (rules['trace'].match(frame) or rules['traced'].match(frame)))

    def new_wanted_detail(self):
        rules = self.new_wanted_rules()
        frame = self.device.image
        if not rules['close_detail'].match(frame):
            return False
        tracked = rules['detail_traced'].match(frame) or rules['detail_trace'].match(frame)
        # Ordinary headings remain useful; cooperation headings describe varying goals.
        paired_progress = rules['own_marker'].match(frame) and rules['partner_marker'].match(frame)
        return tracked and (rules['detail'].match(frame) or rules['route'].match(frame) or paired_progress)

    def open_wanted_detail(self, target):
        timer = Timer(12).start()
        clicks = 0
        while not timer.reached():
            self.screenshot()
            if self.new_wanted_detail():
                self.bind_new_wanted_detail()
                return
            if not getattr(self, '_using_new_wanted', False) and self.appear(self.I_TRACE_TRUE):
                return
            if clicks < 3 and self.click(target, interval=2):
                clicks += 1
        self.device.image_save(f'log/wanted-detail-unknown-{time_ns()}.png')
        raise RequestHumanTakeover('Wanted detail unrecognized after at most 3 clicks; screenshot saved')

    @staticmethod
    def parse_own_progress(goal, progress):
        # Do not use a partner's row or guess a missing slash/number.
        if not (goal.startswith('自己') or goal.startswith('悬赏目标')):
            raise RequestHumanTakeover('Wanted detail does not identify our own goal')
        match = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', progress)
        if not match:
            raise RequestHumanTakeover('Wanted own progress is unreadable')
        current, total = map(int, match.groups())
        if not 0 <= current <= total or total <= 0:
            raise RequestHumanTakeover('Wanted own progress is invalid')
        return current, total

    def read_own_wanted_progress(self):
        goal_rule = text('new_wanted_own_goal', (640,158,490,42))
        progress_rule = text('new_wanted_own_progress', (830,202,110,30))
        result = None
        for _ in range(2):
            self.screenshot()
            if not self.new_wanted_detail():
                raise RequestHumanTakeover('Wanted detail changed while reading own progress')
            goal = str(goal_rule.ocr(self.device.image)).strip()
            progress = str(progress_rule.ocr(self.device.image)).strip()
            value = self.parse_own_progress(goal, progress)
            if result is not None and result != (goal, value):
                raise RequestHumanTakeover('Wanted own progress disagrees between frames')
            result = (goal, value)
        logger.info(f'Wanted own goal confirmed: {result[0]}, progress={result[1]}')
        return result[1]

    def read_partner_wanted_progress(self):
        rules = self.new_wanted_rules()
        if not rules['partner_marker'].match(self.device.image):
            return None
        goal_rule = text('new_wanted_partner_goal', (640,282,490,43))
        progress_rule = text('new_wanted_partner_progress', (830,327,110,30))
        result = None
        for _ in range(2):
            self.screenshot()
            if not self.new_wanted_detail() or not rules['partner_marker'].match(self.device.image):
                raise RequestHumanTakeover('Wanted partner progress layout changed')
            goal = str(goal_rule.ocr(self.device.image)).strip()
            if not goal.startswith('好友'):
                raise RequestHumanTakeover('Wanted partner goal is unreadable')
            value = self.parse_own_progress('自己', str(progress_rule.ocr(self.device.image)).strip())
            if result is not None and result != value:
                raise RequestHumanTakeover('Wanted partner progress disagrees between frames')
            result = value
        logger.info(f'Wanted partner progress confirmed: {result}')
        return result

    def skip_wanted_detail(self):
        if not getattr(self, '_using_new_wanted', False):
            self.ui_click(self.I_TRACE_TRUE, self.I_TRACE_FALSE, timeout=8)
            return
        rules = self.new_wanted_rules()
        timer = Timer(10).start()
        clicks = 0
        while not timer.reached():
            self.screenshot()
            if not rules['close_detail'].match(self.device.image):
                raise RequestHumanTakeover('Wanted detail changed while skipping its tracking')
            if not rules['detail_traced'].match(self.device.image):
                self.ui_click_until_disappear(rules['close_detail'])
                return
            if clicks < 2 and self.click(rules['detail_traced'], interval=2):
                clicks += 1
        raise RequestHumanTakeover('Wanted tracking cancellation was not confirmed')

    def prepare_new_wanted(self, cooperation_only=False):
        if cooperation_only:
            raise RequestHumanTakeover('New wanted cooperation-only flow needs verification; no invitations sent')
        rules = self.new_wanted_rules()
        timer = Timer(20).start()
        missing_since = None
        missing_frames = 0
        while not timer.reached():
            self.screenshot()
            if not self.new_wanted_home():
                missing_since = None
                missing_frames += 1
                if missing_frames >= 5:
                    raise GameStuckError('New wanted home missing in 5 fresh frames before tracking was confirmed')
                sleep(0.5)
                continue
            missing_frames = 0
            if rules['traced'].match(self.device.image):
                break
            if rules['trace'].match(self.device.image):
                self.click(rules['trace'], interval=2)
                missing_since = None
                continue
            # User's completion rule: title remains but neither tracking button exists.
            # Let entrance animation settle before treating absence as completion.
            now = monotonic()
            if missing_since is None:
                missing_since = now
            elif now - missing_since >= 2:
                self.close_completed_wanted_home()
                self._wanted_home_complete = True
                logger.info('Wanted home has title but no tracking buttons; finish this run')
                return False
        else:
            raise GameStuckError('New wanted tracking state was not confirmed')
        self.invite_new_wanted()
        # A fresh frame must confirm the destination before the task reads its list.
        timer = Timer(20).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_CHECK_EXPLORATION):
                self._using_new_wanted = True
                return True
            if self.new_wanted_home() and rules['explore'].match(self.device.image):
                self.click(rules['explore'], interval=2)
        raise GameStuckError('New wanted did not enter exploration')

    def close_completed_wanted_home(self):
        rules = self.new_wanted_rules()
        timer = Timer(8).start()
        while not timer.reached():
            self.screenshot()
            if not rules['home'].match(self.device.image):
                return
            if rules['close_home'].match(self.device.image):
                self.click(rules['close_home'], interval=1)
        raise GameStuckError('Completed wanted home did not close')

    def new_wanted_invite_buttons(self):
        rule = self.new_wanted_rules()['invite']
        matches = sorted(rule.match_all(self.device.image), key=lambda item: -item[0])
        positions = []
        for score, x, y, w, h in matches:
            if any(abs(x - px) < 30 and abs(y - py) < 30 for px, py, _, _ in positions):
                continue
            positions.append((int(x), int(y), int(w), int(h)))
        return [image('invite_icon', (x,y,w,h), (x-8,y-8,w+16,h+16))
                for x,y,w,h in sorted(positions)]

    def bind_new_wanted_invite_dialog(self):
        self.I_WQ_INVITE_ENSURE = image('invite_confirm', (505,547,130,57), (485,530,170,90))
        self.I_WQ_INVITE_CANCEL = image('invite_cancel', (236,547,127,57), (215,530,170,90))
        self.I_WQ_INVITE_SELECTED = image('friend_selected', (397,182,30,33), (140,175,585,355))
        self.I_WQ_INVITE_FRIEND_LIST_APPEAR = self.I_WQ_INVITE_ENSURE
        for n, (x,y) in enumerate(((270,220),(560,220),(270,320),(560,320),(270,415)), 1):
            setattr(self, f'I_WQ_FRIEND_{n}', RuleClick(roi_front=(x,y,60,25), roi_back=(x,y,60,25),
                                                      name=f'new_wanted_friend_{n}'))

    def invite_new_wanted(self):
        self.screenshot()
        if not self.new_wanted_home():
            raise GameStuckError('Wanted home changed before checking invitation icons')
        buttons = self.new_wanted_invite_buttons()
        if not buttons:
            return
        self.bind_new_wanted_invite_dialog()
        for button in buttons:
            self.screenshot()
            if not self.new_wanted_home() or not button.match(self.device.image):
                continue
            if self.need_invite_vip():
                # The named-friend path must not reuse old card/reward coordinates.
                raise RequestHumanTakeover('New wanted named-friend reward filtering requires adaptation')
            if not self.invite_random(button):
                self.screenshot()
                if self.appear(self.I_WQ_INVITE_CANCEL):
                    self.ui_click_until_disappear(self.I_WQ_INVITE_CANCEL)

    def bind_new_wanted_detail(self):
        # Shadow only this task's attributes, leaving other tasks and workers intact.
        self._using_new_wanted = True
        rules = self.new_wanted_rules()
        self.I_TRACE_TRUE = rules['detail_traced']
        self.I_TRACE_FALSE = rules['detail_trace']
        self.O_WQ_MONSTER_TYPE = text('new_wanted_monster', (645,160,460,42))
        for n, y in enumerate((284,359,434,509), 1):
            setattr(self, f'I_GOTO_{n}', image('goto', (1058,y,87,38), (1045,y-10,120,62)))
            setattr(self, f'O_WQ_TYPE_{n}', text(f'new_wanted_type_{n}', (640,y,63,34)))
            setattr(self, f'O_WQ_INFO_{n}', text(f'new_wanted_info_{n}', (713,y-2,330,39)))
