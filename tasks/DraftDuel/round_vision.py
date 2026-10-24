"""Read saved/live frames without device access for one already-admitted series.

Lobby, detail, deployment and two-loss final overlays have archived DraftDuel
pixel evidence (res/round/provenance.json). Battle anchors come from the existing
Duel/GeneralBattle assets. Matching and reconnect labels use detected text boxes,
not guessed click coordinates. Ordinary battle/matching/reward full screenshots
are still missing; synthetic tests of these branches are not live validation.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import re
import time

import cv2
import numpy as np

from tasks.DraftDuel.round_model import RoundView
from tasks.DraftDuel.recognition import observe_lobby


ROOT = Path(__file__).resolve().parents[2]
RES = Path(__file__).with_name('res')


@dataclass(frozen=True)
class TextBox:
    text: str
    confidence: float
    bounds: tuple

    @property
    def center(self):
        x1, y1, x2, y2 = self.bounds
        return ((x1+x2)//2, (y1+y2)//2)


class RoundVision:
    """Return only contextual, observed actions; never admit another series."""
    def __init__(self, ocr):
        self.ocr = ocr
        self.templates = {}
        self.last_evidence = ()
        for name in ('match', 'roster_title', 'roster_close', 'series_fan',
                     'series_one', 'loss_red', 'loss_gray', 'continue',
                     'deploy_confirm', 'member_waiting', 'wins_zero'):
            self.templates[name] = self._load(RES/'round'/(name+'.png'))
        for name, path in {
            'title': RES/'team/title.png', 'roster': RES/'my_roster_heading.png',
            'teammate': RES/'deployment/teammate.png',
            'network': ROOT/'tasks/GlobalGame/gg/gg_network_error.png',
            'connecting': ROOT/'tasks/GlobalGame/gg/gg_network_abnormal.png',
            'duel_win': ROOT/'tasks/Duel/duel/duel_d_victory.png',
            'duel_loss': ROOT/'tasks/Duel/duel/duel_d_fail.png',
            'win': ROOT/'tasks/Component/GeneralBattle/gb/gb_win.png',
            'loss': ROOT/'tasks/Component/GeneralBattle/gb/gb_false.png',
        }.items():
            self.templates[name] = self._load(path)
        # Existing battle themes declare their observed search regions.
        self.battle_anchors = []
        manifests = [ROOT/'tasks/Component/GeneralBattle/gb/image.json']
        manifests += sorted((ROOT/'tasks/Component/CostumeBattle').glob('battle*/image.json'))
        for manifest in manifests:
            for row in json.loads(manifest.read_text(encoding='utf-8-sig')):
                if not re.fullmatch(r'(?:battle_info|exit)(?:_\d+)?', row['itemName']):
                    continue
                image = self._load(manifest.parent/row['imageName'])
                xywh = tuple(map(int, row['roiBack'].split(',')))
                x, y, w, h = xywh
                self.battle_anchors.append((row['itemName'].split('_')[0], image,
                                            (x, y, x+w, y+h)))

    @staticmethod
    def _load(path):
        if not path.exists():
            return None
        return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)

    @staticmethod
    def _locate(image, template, region, threshold=.9, brightness=36):
        if template is None:
            return None
        x1, y1, x2, y2 = region
        roi = image[y1:y2, x1:x2]
        h, w = template.shape[:2]
        if roi.shape[0] < h or roi.shape[1] < w or float(template.std()) < 1:
            return None
        _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))
        error = np.mean(cv2.absdiff(roi[y:y+h, x:x+w], template))
        if not np.isfinite(score) or score < threshold or error > brightness:
            return None
        return (x1+x+w//2, y1+y+h//2)

    def _hit(self, image, name, region, **kwargs):
        return self._locate(image, self.templates[name], region, **kwargs)

    def _texts(self, image):
        model = getattr(self.ocr, 'model', self.ocr)
        detect = getattr(model, 'detect_and_ocr', None)
        if detect is None:
            return ()
        output = []
        for item in detect(image):
            confidence = float(item.score)
            if confidence < .85:
                continue
            box = np.asarray(item.box)
            # ppocronnx returns quadrilaterals; accepting xyxy helps adapters.
            if box.shape == (4,):
                x1, y1, x2, y2 = box
            elif box.ndim == 2 and box.shape[1] == 2:
                x1, y1 = box.min(axis=0)
                x2, y2 = box.max(axis=0)
            else:
                continue
            if not (0 <= x1 < x2 <= image.shape[1] and 0 <= y1 < y2 <= image.shape[0]):
                continue
            output.append(TextBox(re.sub(r'\s+', '', item.ocr_text), confidence,
                                  tuple(map(int, (x1, y1, x2, y2)))))
        return tuple(output)

    @staticmethod
    def _find(texts, pattern, region=None):
        hits = []
        for item in texts:
            if not re.fullmatch(pattern, item.text):
                continue
            x, y = item.center
            if region is None or region[0] <= x <= region[2] and region[1] <= y <= region[3]:
                hits.append(item)
        return hits[0] if len(hits) == 1 else None

    def read(self, image, captured=None):
        captured = time.monotonic() if captured is None else captured
        def view(phase, actions=None, wins=None, losses=None, outcome=None, evidence=()):
            self.last_evidence = tuple(evidence)
            actions = actions or {}
            signature = repr((phase, tuple(sorted(actions.items())), wins, losses, outcome))
            return RoundView(phase, captured, actions, wins, losses, outcome, signature,
                             tuple(evidence))

        if (not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3
                or not (690 <= image.shape[0] <= 720 and 1270 <= image.shape[1] <= 1280)
                or image.dtype != np.uint8):
            return view('unknown', evidence=('unsupported_frame',))

        # Network dialog template includes the exact disconnection sentence and
        # its button. Its declared source crop is 431,253,416,208; the button's
        # 145,120..278,186 rectangle is measured inside that stored asset.
        network = self._hit(image, 'network', (420, 240, 860, 475))
        if network:
            tx = network[0]-self.templates['network'].shape[1]//2
            ty = network[1]-self.templates['network'].shape[0]//2
            return view('disconnected', {'reconnect': (tx+211, ty+153)},
                        evidence=('observed_server_disconnect_dialog',))
        if self._hit(image, 'connecting', (570, 315, 820, 410)):
            return view('disconnected', evidence=('server_reconnecting',))

        title = self._hit(image, 'title', (130, 5, 285, 60), brightness=24)
        detail = self._hit(image, 'roster_title', (540, 8, 730, 72), brightness=25)
        close = self._hit(image, 'roster_close', (1155, 65, 1265, 172), brightness=25)
        if detail and close:
            return view('roster_detail', {'close_roster': close}, evidence=('roster_detail_title', 'observed_close'))

        fan = self._hit(image, 'series_fan', (580, 260, 790, 375))
        continuing = self._hit(image, 'continue', (525, 658, 755, 720))
        red1 = self._hit(image, 'loss_red', (604, 360, 648, 405))
        red2 = self._hit(image, 'loss_red', (650, 360, 695, 405))
        if fan and continuing and red1 and red2:
            one = self._hit(image, 'series_one', (543, 270, 607, 368))
            return view('series_result', {'collect': continuing},
                        wins=1 if one else None, losses=2,
                        evidence=('central_series_banner', 'two_red_loss_marks', 'continue_hint'))

        # Deployment is deliberately before generic battle recognition.
        marker = self._hit(image, 'teammate', (75, 20, 600, 115), brightness=45)
        confirm = self._hit(image, 'deploy_confirm', (1085, 530, 1245, 645), brightness=45)
        if marker and confirm:
            return view('deploying', evidence=('teammate_selecting_marker', 'deployment_confirm'))

        roster = self._hit(image, 'roster', (398, 4, 465, 110), brightness=22)
        match = self._hit(image, 'match', (1125, 555, 1280, 715), brightness=25)
        member_waiting = self._hit(image, 'member_waiting', (1135, 585, 1270, 667), brightness=25)
        # Full-frame text is needed only after the known fast paths. Positions
        # always remain attached to the exact text box that was recognized.
        texts = self._texts(image)
        dialog_region = (250, 180, 1040, 580)
        danger = any(re.search(r'购买|消耗|花费|充值|勾玉|确认退出|退出战斗|放弃', t.text)
                     and dialog_region[0] < t.center[0] < dialog_region[2]
                     and dialog_region[1] < t.center[1] < dialog_region[3] for t in texts)
        if danger:
            return view('unknown', evidence=('unapproved_spend_or_exit_dialog',))
        reconnect = self._find(texts, r'(?:重新连接|重试)', dialog_region)
        disconnected = any(re.search(r'(?:网络|连接).*(?:异常|中断|断开|失败)|已断开游戏服务器', t.text)
                           for t in texts if 200 <= t.center[1] <= 480)
        if disconnected and reconnect:
            return view('disconnected', {'reconnect': reconnect.center},
                        evidence=('disconnect_text', 'reconnect_button'))
        # Only exact benign notices can expose a generic confirmation button.
        benign = self._find(texts, r'(?:匹配超时|匹配失败|对方已取消匹配|对方取消匹配|队友取消了匹配)', dialog_region)
        ok = self._find(texts, r'(?:确定|确认|知道了|关闭)', dialog_region)
        if benign and ok:
            return view('popup', {'close': ok.center}, evidence=(benign.text, 'observed_notice_button'))

        matching = self._find(texts, r'(?:正在匹配(?:对手)?|匹配中[.。…]*|寻找对手[.。…]*|等待对手[.。…]*)')
        cancel = self._find(texts, r'(?:取消匹配|取消)', (200, 160, 1100, 690))
        if matching and cancel:
            return view('matching', {'cancel_match': cancel.center}, evidence=('matching_status', 'cancel_button'))

        # Six wins is terminal even without two red loss marks; require the
        # central banner plus the continuation control, never lobby/rules text.
        six = self._find(texts, r'6番胜', (480, 250, 800, 370))
        if fan and continuing and six:
            return view('series_result', {'collect': continuing}, wins=6,
                        evidence=('central_six_wins', 'continue_hint'))

        if title and roster and (match or member_waiting):
            wins = None
            win_label = self._find(texts, r'([0-6])番胜', (100, 80, 350, 190))
            if win_label:
                wins = int(win_label.text[0])
            elif self._hit(image, 'wins_zero', (130, 96, 190, 168), brightness=20):
                wins = 0
            gray1 = self._hit(image, 'loss_gray', (165, 170, 200, 207), brightness=20)
            gray2 = self._hit(image, 'loss_gray', (198, 170, 235, 207), brightness=20)
            # Only the genuinely sampled two-grey-marker state is asserted.
            # A notification can cover these marks; absent evidence stays None.
            losses = 0 if gray1 and gray2 else None
            # The first visible roster portrait is the known detail affordance;
            # the 502,60 center is measured on the same archived roster strip.
            actions = {'open_roster': (502, 60)}
            if match:
                actions['match'] = match
            return view('lobby_ready', actions, wins=wins, losses=losses,
                        evidence=('mode_title', 'own_roster_heading',
                                  'observed_battle_button' if match else 'member_waiting_for_leader'))
        lobby = observe_lobby(image, color_order='BGR')
        if lobby.is_lobby and lobby.round_not_started:
            return view('lobby_unopened', evidence=tuple(lobby.evidence))

        hint = self._find(texts, r'(?:点击(?:屏幕|任意位置)?继续|点击屏幕结束)', (200, 580, 1100, 720))
        result_markers = []
        for key, outcome, area in (('duel_win', 'win', (400, 45, 555, 195)),
                                   ('duel_loss', 'loss', (390, 40, 545, 195)),
                                   ('win', 'win', (296, 33, 710, 257)),
                                   ('loss', 'loss', (390, 100, 540, 250))):
            if self._hit(image, key, area, brightness=45):
                result_markers.append(outcome)
        if continuing:
            hint_center = continuing
        else:
            hint_center = hint.center if hint else None
        if hint_center and len(set(result_markers)) == 1:
            return view('result', {'result_continue': hint_center}, outcome=result_markers[0],
                        evidence=('existing_battle_result_asset', 'continue_hint'))

        anchors = {name for name, asset, area in self.battle_anchors
                   if self._locate(image, asset, area, brightness=45)}
        if {'battle', 'exit'} <= anchors:
            mode = self._find(texts, r'(?:自动|手动)', (15, 630, 120, 710))
            actions = {'auto': mode.center} if mode and mode.text == '手动' else {}
            return view('battle', actions, evidence=('battle_info_anchor', 'battle_exit_anchor',
                        'mode_'+mode.text if mode else 'mode_unreadable'))
        return view('unknown', evidence=('reread_unrecognized_frame',))
