"""Read Chess cards with shared Frame layouts and generated portrait references."""
from copy import copy
from functools import cached_property
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from tasks.Chess.runtime.image import ChessImage as RuleImage
from module.logger import logger
from tasks.Chess.assets import ChessAssets
from tasks.Chess.strategy.shikigami_catalog import normalize_bond_ocr_text, bond_key, SHIKIGAMI_BONDS_BY_ROMAJI

ROOT = Path(__file__).resolve().parents[1] / 'resource'


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'Cannot decode {path}')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def frame_layout(name):
    rules = json.loads((ROOT/'c_frame.json').read_text(encoding='utf-8'))
    return next(rule for rule in rules if rule['itemName'] == name)


def layout_bytes(rule):
    return json.dumps(rule, ensure_ascii=False, sort_keys=True).encode('utf-8')

EMBLEM_ROOT = ROOT / 'fetter/portraits'


class ChessFrameCardsMixin:
    def runframe(self, target, *values, interval=None, threshold=None):
        return target.run(self, *values, interval=interval, threshold=threshold)

    # Only portrait identity matching uses grayscale; badge/OCR/Frame outputs stay RGB.
    FRAME_PORTRAIT_GRAYSCALE = True

    @cached_property
    def golden_copy_portrait(self):
        return self._frame_matching_portrait(read_image(ROOT / 'fetter/golden_copy.png'))

    def _golden_copy_score(self, portrait):
        portrait = self._frame_matching_portrait(portrait)
        front = self.golden_copy_portrait
        if portrait.shape[0] < front.shape[0] or portrait.shape[1] < front.shape[1]:
            return 0.0
        return float(cv2.minMaxLoc(cv2.matchTemplate(
            portrait, front, cv2.TM_CCOEFF_NORMED,
        ))[1])

    def _frame_matching_portrait(self, image):
        if self.FRAME_PORTRAIT_GRAYSCALE and image.ndim == 3:
            return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        return image

    @cached_property
    def frame_emblem_library(self):
        library = json.loads((EMBLEM_ROOT/'index.json').read_text(encoding='utf-8'))
        current = frame_layout('fetter_badge')
        if current != library['layout']:
            raise ValueError('Emblem Frame changed: rebuild with dev_tools.chess.build_fetter_library')
        return library

    @cached_property
    def frame_emblem_portraits(self):
        return {key: self._frame_matching_portrait(read_image(EMBLEM_ROOT/item['file']))
                for key, item in self.frame_emblem_library['entries'].items()}

    def _frame_emblem_identity(self, portrait, text):
        portrait = self._frame_matching_portrait(portrait)
        scored = []
        threshold = ChessAssets.F_FETTER_BADGE.operations[2].get('threshold', .8)
        for key, front in self.frame_emblem_portraits.items():
            if front.shape[0] > portrait.shape[0] or front.shape[1] > portrait.shape[1]:
                continue
            score = float(cv2.minMaxLoc(cv2.matchTemplate(portrait, front, cv2.TM_CCOEFF_NORMED))[1])
            if np.isfinite(score) and score >= threshold:
                scored.append((score, key))
        if not scored:
            return None, 0.0
        scored.sort(reverse=True)
        near = [item for item in scored if scored[0][0]-item[0] < .03]
        if len(near) == 1:
            return near[0][1], near[0][0]
        entries = self.frame_emblem_library['entries']
        text = ''.join(str(text).split())
        matching = [(score, key) for score, key in near
                    if text in [entries[key]['name'], *entries[key]['ocr_aliases']]]
        return (matching[0][1], matching[0][0]) if len(matching) == 1 else (None, scored[0][0])

    @cached_property
    def frame_card_library(self):
        library = json.loads((ROOT/'card_index.json').read_text(encoding='utf-8'))
        for kind in ('card', 'store'):
            digest = hashlib.sha256(layout_bytes(frame_layout({'card':'hand_card','store':'store_card'}[kind]))).hexdigest()
            if digest != library['layouts'][kind]:
                raise ValueError('Chess Frame layout changed: rebuild with dev_tools.chess.build_card_library')
        return library

    @cached_property
    def frame_portraits(self):
        return {
            kind: {name: self._frame_matching_portrait(read_image(ROOT/item['portrait']))
                   for name, item in entries.items()}
            for kind, entries in self.frame_card_library['cards'].items()
        }

    @cached_property
    def frame_badges(self):
        return {
            kind: [read_image(Path(file)) for file in frame.files]
            for kind, frame in (('card', ChessAssets.F_HAND_CARD), ('store', ChessAssets.F_STORE_CARD),
                                ('emblem', ChessAssets.F_FETTER_BADGE))
        }


    def _frame_card_identity(self, kind, portrait, text):
        """Require a portrait match; use name/traits to resolve near-score candidates."""
        portrait = self._frame_matching_portrait(portrait)
        frame = ChessAssets.F_HAND_CARD if kind == 'card' else ChessAssets.F_STORE_CARD
        threshold = frame.operations[-2].get('threshold', 0.8)
        scored = []
        for name, front in self.frame_portraits[kind].items():
            if front.shape[0] > portrait.shape[0] or front.shape[1] > portrait.shape[1]:
                continue
            score = float(cv2.minMaxLoc(cv2.matchTemplate(portrait, front, cv2.TM_CCOEFF_NORMED))[1])
            if np.isfinite(score) and score >= threshold:
                scored.append((score, name))
        if not scored:
            return None, 0.0
        scored.sort(reverse=True)
        near = [(score, name) for score, name in scored if scored[0][0]-score < 0.03]
        if len(near) == 1:
            return near[0][1], near[0][0]
        entries = self.frame_card_library['cards'][kind]
        if kind == 'store':
            raw = normalize_bond_ocr_text(text[0])
            names = [(score, name) for score, name in near if entries[name]['name'] == raw]
            if len(names) == 1:
                return names[0][1], names[0][0]
        observed = bond_key(tuple(normalize_bond_ocr_text(t) for t in text[-3:]))
        matching = [
            (score, name) for score, name in near
            if observed and observed == bond_key(SHIKIGAMI_BONDS_BY_ROMAJI[name])
        ]
        return (matching[0][1], matching[0][0]) if len(matching) == 1 else (None, scored[0][0])

    def _frame_card_scan(self, kind):
        """Locate all badges, then run one Frame per card; reuse outputs on the same screenshot."""
        image = self.device.image
        key = (getattr(self.device, 'image_frame_id', None), id(image))
        cache = getattr(self, '_chess_frame_card_cache', None)
        if cache is None or cache[0] != key:
            cache = (key, {})
            self._chess_frame_card_cache = cache
        if kind in cache[1]:
            return cache[1][kind]
        frame = {'card': ChessAssets.F_HAND_CARD, 'store': ChessAssets.F_STORE_CARD,
                 'emblem': ChessAssets.F_FETTER_BADGE}[kind]
        bx, by, bw, bh = frame.badge_references[0]
        x, y, w, h = frame.roi_back
        back = image[y:y+h, x:x+w]
        if frame.badge_grayscale:
            back = cv2.cvtColor(back, cv2.COLOR_RGB2GRAY)
        candidates = []
        for index, front in enumerate(self.frame_badges[kind]):
            if frame.badge_grayscale:
                front = cv2.cvtColor(front, cv2.COLOR_RGB2GRAY)
            if min(back.shape[:2]) == 0 or front.shape[0] > back.shape[0] or front.shape[1] > back.shape[1]:
                continue
            scores = cv2.matchTemplate(back, front, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(scores >= frame.threshold)
            candidates.extend((float(scores[dy, dx]), x+int(dx), y+int(dy), index) for dy, dx in zip(ys, xs))
        anchors = []
        for candidate in sorted(candidates, reverse=True):
            if not any(abs(candidate[1]-a[1]) < bw and abs(candidate[2]-a[2]) < bh for a in anchors):
                anchors.append(candidate)
        cards = []
        for score, ax, ay, index in sorted(anchors, key=lambda a: a[1]):
            local = copy(frame)
            # The third trait is optional; an empty OCR result is normal here.
            local.optional_ocr_ranges = {'card': (2,), 'store': (3,), 'emblem': ()}[kind]
            local.files = [frame.files[index]]
            local.badgefile = local.files
            local.file = local.files[0]
            local.badge_references = [frame.badge_references[0]]
            local._badge_reference = local.badge_references[0]
            local.roi_front = [bx, by, bw, bh]
            local.roi_back = (ax, ay, self.frame_badges[kind][index].shape[1], self.frame_badges[kind][index].shape[0])
            # Reject cards clipped by screen edges before executing any OCR.
            dx, dy = ax-bx, ay-by
            if any(rx+dx < 0 or ry+dy < 0 or rx+dx+rw > image.shape[1] or ry+dy+rh > image.shape[0]
                   for rx, ry, rw, rh in frame.roi_ranges):
                continue
            local.defer_ocr = True
            result = self.runframe(local)  # No True arguments: this path never clicks.
            if not result:
                continue
            if kind == 'emblem':
                raw_text, roi, portrait = result
                # Image identity first. Only an unknown/ambiguous portrait needs OCR.
                copy_score = self._golden_copy_score(portrait)
                golden_copy = copy_score >= .8
                key, identity_score = (None, copy_score) if golden_copy else self._frame_emblem_identity(portrait, '')
                discovery = None
                if key is None and not golden_copy:
                    _, targets = local.range_targets(image.shape, allow_missing_image=True)
                    raw_text = targets[0].ocr(image)
                    text = ''.join(str(raw_text).split()).strip('()（）[]【】')
                    discovery = text if text in ('发现纹章', '发现御魂') else None
                    golden_copy = text == '黄金复制术'
                    if not discovery and not golden_copy:
                        key, identity_score = self._frame_emblem_identity(portrait, raw_text)
                reference = self.frame_emblem_library['entries'].get(key, {})
                cx, cy, cw, ch = roi
                cards.append({
                    'type': 'duplicator' if golden_copy else ('discovery' if discovery else ('emblem' if key else 'unknown')),
                    'name': '黄金复制术' if golden_copy else (discovery or reference.get('name')),
                    'kind': '黄金复制术' if golden_copy else discovery,
                    'similarity': (copy_score if copy_score >= .8 else 1.0) if golden_copy else (1.0 if discovery else identity_score),
                    'score': identity_score, 'badge_score': score,
                    'position': (cx+cw//2, cy+ch//2), 'roi': roi, 'source': 'frame',
                    'text': raw_text, 'raw_text': (raw_text,), 'action': None,
                })
                logger.debug(f'Chess Frame emblem: name={reference.get("name")}, score={identity_score:.3f}, roi={roi}, text={raw_text}')
                continue
            text = tuple(result[:-2])
            name, identity_score = self._frame_card_identity(kind, result[-2], text)
            if name is None and identity_score > 0:
                local.defer_ocr = False
                result = self.runframe(local)
                if not result:
                    continue
                text = tuple(result[:-2])
                name, identity_score = self._frame_card_identity(kind, result[-2], text)
            roi = result[-1]
            cx, cy, cw, ch = roi
            reference = self.frame_card_library['cards'][kind].get(name, {})
            cards.append({
                'type': 'shikigami' if name else 'unknown', 'name': name,
                'score': identity_score, 'badge_score': score,
                'position': (cx+cw//2, cy+ch//2), 'roi': roi,
                'source': 'frame', 'price': reference.get('cost'),
                'portrait_roi': tuple(int(round(v)) for v in local.matched_ranges[-2]),
                'bonds': SHIKIGAMI_BONDS_BY_ROMAJI.get(name, ()) if name else tuple(normalize_bond_ocr_text(t) for t in text[-3:] if normalize_bond_ocr_text(t)),
                'raw_text': text, 'raw_name': text[0] if kind == 'store' else '',
                'star': None if kind == 'store' else self._frame_star(frame.files[index]),
                'action': None,
            })
            logger.debug(f'Chess Frame {kind}: name={name}, score={identity_score:.3f}, roi={roi}, text={text}')
        cache[1][kind] = cards
        return cards

    @staticmethod
    def _frame_star(file):
        return int(Path(file).stem.rsplit('_', 1)[1])

    def _frame_hand_in_roi(self, roi):
        x, y, w, h = roi
        matches = [card for card in self._frame_card_scan('card') + self._frame_card_scan('emblem')
                   if x <= card['position'][0] < x+w and y <= card['position'][1] < y+h]
        return max(matches, key=lambda card: card['score']) if matches else None

    def _frame_shop_in_slot(self, click_rule, expected_name=None):
        slot_key = tuple(click_rule.roi_back)
        references = getattr(self, '_chess_shop_slot_references', None)
        if references is None:
            references = self._chess_shop_slot_references = {}
        reference = references.get((slot_key, expected_name)) if expected_name else None
        if reference is not None:
            # After buying another slot, only recheck this already identified portrait.
            x, y, w, h = reference['portrait_roi']
            crop = self.device.image[y:y+h, x:x+w]
            front = self.frame_portraits['store'][expected_name]
            if crop.shape[:2] != (h, w) or front.shape[0] > h or front.shape[1] > w:
                return None
            crop = self._frame_matching_portrait(crop)
            score = float(cv2.minMaxLoc(cv2.matchTemplate(crop, front, cv2.TM_CCOEFF_NORMED))[1])
            if not np.isfinite(score) or score < ChessAssets.F_STORE_CARD.operations[-2].get('threshold', .8):
                return None
            return {**reference, 'score': score}
        x, y, w, h = slot_key
        matches = [card for card in self._frame_card_scan('store')
                   if card['name'] and x <= card['position'][0] < x+w and y <= card['position'][1] < y+h]
        result = max(matches, key=lambda card: card['score']) if matches else None
        if result is not None:
            references[(slot_key, result['name'])] = result
        if result is not None and expected_name is not None and result['name'] != expected_name:
            return None
        return result
