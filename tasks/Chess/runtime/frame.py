"""Alternative image badges with anchored operation rectangles."""
import math
from copy import deepcopy
from dataclasses import dataclass
from os import PathLike, fspath
from pathlib import Path
from tasks.Chess.runtime.image import ChessImage as RuleImage
from module.atom.click import RuleClick


@dataclass(frozen=True)
class Image:
    """Inline image handler; its position comes exclusively from the frame's roi_range."""
    file: str | None = None
    method: str = RuleImage.METHOD_TEMPLATE_MATCH
    threshold: float = 0.8


@dataclass(frozen=True)
class Ocr:
    mode: str = 'Single'
    method: str = 'Default'
    keyword: str = ''


@dataclass(frozen=True)
class Click:
    pass


def normalize_operations(operations, count, allow_missing_image=False):
    """Normalize inline range handlers, shared by runtime, generator and annotator."""
    if operations is None:
        operations = ['click'] * count
    if not isinstance(operations, (list, tuple)) or len(operations) != count:
        raise ValueError('Range operations must align with ranges')
    result = []
    for value in operations:
        spec = {'type': value or 'click'} if isinstance(value, str) else deepcopy(value)
        if not isinstance(spec, dict):
            raise ValueError('Range operation must be click, image or ocr')
        kind = spec.get('type', 'click')
        if kind == 'click':
            spec = {'type': 'click'}
        elif kind == 'image':
            spec = {key: val for key, val in spec.items() if key in ('file', 'imageName', 'method', 'threshold')}
            spec.setdefault('method', RuleImage.METHOD_TEMPLATE_MATCH)
            spec['threshold'] = float(spec.get('threshold', 0.8))
            if spec['method'] not in (RuleImage.METHOD_TEMPLATE_MATCH, RuleImage.METHOD_MASKED_TEMPLATE_MATCH,
                                      RuleImage.METHOD_MULTI_SCALE_TEMPLATE_MATCH, RuleImage.METHOD_SIFT_FLANN):
                raise ValueError('Unsupported range image method')
            if not math.isfinite(spec['threshold']) or not 0 <= spec['threshold'] <= 1:
                raise ValueError('Range image threshold must be between 0 and 1')
            if not allow_missing_image and not (spec.get('file') or spec.get('imageName')):
                raise ValueError('Image range requires its own cropped template')
            spec['type'] = kind
        elif kind == 'ocr':
            spec = {'type': 'ocr', 'mode': str(spec.get('mode', 'Single')),
                    'method': str(spec.get('method', 'Default')), 'keyword': str(spec.get('keyword', ''))}
            if spec['mode'] != 'Single':
                raise ValueError('Frame OCR only supports Single mode')
        else:
            raise ValueError(f'Unsupported range operation: {kind}')
        result.append(spec)
    return result


class FrameResult(tuple):
    """Tuple in range order, with match/check metadata separate from exported values."""
    def __new__(cls, matched, data=(), checks=(), clicked=()):
        result = super().__new__(cls, data)
        result.matched = bool(matched)
        result.checks = tuple(checks)
        result.clicked = tuple(clicked)
        return result

    @property
    def data(self):
        return tuple(self)

    def __bool__(self):
        return self.matched and all(value is not False for value in self.checks)


class RuleFrame(RuleImage):
    def __init__(self, roi_badge=None, roi_range=None, roi_back=None, method=None, threshold=None, file=None,
                 profile='High', operations=None, *, badgefile=None, range=None, name=None,
                 roibadge=None, roirange=None, roiback=None, badge_grayscale=False):
        if any(old is not None and alias is not None for old, alias in
               ((roi_badge, roibadge), (roi_range, roirange), (roi_back, roiback))):
            raise ValueError('Do not supply both roi_* and roi* for the same area')
        roi_badge = roi_badge if roibadge is None else roibadge
        roi_range = roi_range if roirange is None else roirange
        roi_back = roi_back if roiback is None else roiback
        if badgefile is not None:
            if file is not None:
                raise ValueError('Use badgefile or legacy file, not both')
            file = badgefile
        if range is not None:
            if operations is not None:
                raise ValueError('Use range or legacy operations, not both')
            operations = []
            for item in range:
                if item is Image or isinstance(item, Image):
                    operations.append({'type':'image', **vars(Image() if item is Image else item)})
                elif item is Ocr or isinstance(item, Ocr):
                    operations.append({'type':'ocr', **vars(Ocr() if item is Ocr else item)})
                elif item is Click or item is None or isinstance(item, Click):
                    operations.append({'type':'click'})
                else:
                    raise ValueError('range accepts Image, Ocr, Click or their inline configurations')
        method = method or RuleImage.METHOD_TEMPLATE_MATCH
        threshold = 0.8 if threshold is None else threshold
        files = list(file) if isinstance(file, (list, tuple)) else [file]
        badges = list(roi_badge) if roi_badge and isinstance(roi_badge[0], (list, tuple)) else [roi_badge]
        if len(badges) == 1 and len(files) > 1:
            badges = badges * len(files)
        ranges = list(roi_range) if roi_range and isinstance(roi_range[0], (list, tuple)) else [roi_range]
        if not files or any(not f for f in files) or len(files) != len(badges) or not ranges or ranges[0] is None:
            raise ValueError('Frame requires at least one badge and range; badge files and ROIs must align')
        for roi in [*badges, *ranges, roi_back]:
            if not isinstance(roi, (list, tuple)) or len(roi) != 4 or not all(math.isfinite(v) for v in roi) or roi[2] <= 0 or roi[3] <= 0:
                raise ValueError('Frame ROI must be finite x,y,w,h with positive size')
        self.files = files
        self.badge_grayscale = bool(badge_grayscale)
        self.grayscale = self.badge_grayscale
        self.badgefile = self.files
        self.badge_references = [tuple(roi) for roi in badges]
        self.roi_ranges = [tuple(roi) for roi in ranges]
        self.roi_range = self.roi_ranges[0]  # old callers
        self.operations = list(['click'] * len(ranges) if operations is None else operations)
        if len(self.operations) != len(ranges):
            raise ValueError('Range operations must align with ranges')
        self.badge_index = 0
        self.last_results = None
        self._badge_reference = self.badge_references[0]
        super().__init__(roi_front=badges[0], roi_back=roi_back, method=method,
                         threshold=threshold, file=files[0], profile=profile)
        if name:
            self.name = str(name).upper()

    @property
    def roi_badge(self):
        return self.roi_front

    @property
    def matched_ranges(self):
        bx, by, bw, bh = self._badge_reference
        x, y, w, h = self.roi_badge
        sx, sy = w / bw, h / bh
        return [(x + (rx-bx)*sx, y + (ry-by)*sy, rw*sx, rh*sy)
                for rx, ry, rw, rh in self.roi_ranges]

    @property
    def matched_range(self):
        return self.matched_ranges[0]

    @property
    def ranges(self):
        result = []
        for index, area in enumerate(self.matched_ranges):
            target = RuleClick(roi_front=area, roi_back=area,
                               name=f'{self.name}_RANGE_{index}')
            target.operation = self.operations[index]
            result.append(target)
        return result

    def click(self, task, index=0, **kwargs):
        return task.click(self.ranges[index], **kwargs)

    @property
    def range(self):
        """Rebased first-level rules; use execute() to obtain their evaluated values."""
        return self.range_targets()[1]

    def coord(self):
        return self.ranges[0].coord()

    def coord_more(self):
        return self.coord()

    def front_center(self):
        x,y,w,h = self.matched_range
        return int(x+w/2), int(y+h/2)

    def range_targets(self, image_shape=None, operations=None, allow_missing_image=False):
        """Build standalone rules at the matched relative positions, without mutating annotations."""
        specs = normalize_operations(self.operations if operations is None else operations, len(self.roi_ranges), allow_missing_image=allow_missing_image)
        targets = []
        for index, (area, spec) in enumerate(zip(self.matched_ranges, specs)):
            area = tuple(int(round(v)) for v in area)
            x, y, w, h = area
            if w <= 0 or h <= 0 or (image_shape is not None and
                    (x < 0 or y < 0 or x+w > image_shape[1] or y+h > image_shape[0])):
                raise ValueError(f'Range {index + 1} is outside the screenshot')
            name = f'{self.name}_RANGE_{index + 1}'
            if spec['type'] == 'image' and (spec.get('file') or spec.get('imageName')):
                target = RuleImage(area, area, spec['method'], spec['threshold'],
                                   spec.get('file') or spec['imageName'])
                target.name = name
            elif spec['type'] == 'ocr':
                from module.atom.ocr import RuleOcr
                target = RuleOcr(name=name, roi=area, area=area, mode=spec['mode'],
                                 method=spec['method'], keyword=spec['keyword'])
                target.log_empty = index not in getattr(self, 'optional_ocr_ranges', ())
            else:
                target = RuleClick(area, area, name=name)
            targets.append(target)
        return specs, targets

    def evaluate_ranges(self, image, image_match=None, ocr_read=None):
        """Read all ranges on one screenshot; click entries return None without sending input."""
        specs, targets = self.range_targets(image.shape)
        results = []
        for spec, target in zip(specs, targets):
            if spec['type'] == 'image':
                results.append(bool(image_match(target) if image_match else target.match(image)))
            elif spec['type'] == 'ocr':
                results.append(ocr_read(target) if ocr_read else target.ocr(image))
            else:
                results.append(None)
        return results, specs, targets

    def execute(self, task, interval=None, threshold=None):
        """Gate on badge with appear, read ranges, then perform configured clicks. None means no badge."""
        if not task.appear(self, interval=interval, threshold=threshold):
            return None
        results, specs, targets = self.evaluate_ranges(
            task.device.image, image_match=lambda target: task.appear(target))
        # Read-only results must all come from the pre-click frame.
        for index, (spec, target) in enumerate(zip(specs, targets)):
            if spec['type'] == 'click':
                task.click(target)
                results[index] = True
        return results

    def bind_runframe(self, values):
        """Bind positional requirements without changing the shared asset definition."""
        if len(values) > len(self.roi_ranges):
            raise ValueError(f'{self.name}: too many runframe arguments')
        specs = normalize_operations(self.operations, len(self.roi_ranges), allow_missing_image=True)
        expected = []
        for index, spec in enumerate(specs):
            value = values[index] if index < len(values) else None
            kind = spec['type']
            if kind == 'click':
                if value is not None and value is not True and value is not False:
                    raise ValueError(f'{self.name} range {index + 1}: Click accepts True, False or None')
                expected.append(value is True)
                continue
            elif kind == 'image':
                if value is None:
                    spec.pop('file', None)
                    spec.pop('imageName', None)
                    expected.append(None)
                    continue
                if not isinstance(value, (str, PathLike)) or not fspath(value):
                    raise ValueError(f'{self.name} range {index + 1}: Image requires a template file path')
                if not Path(value).is_file():
                    raise ValueError(f'{self.name} range {index + 1}: image file does not exist: {value}')
                spec.pop('imageName', None)
                spec['file'] = fspath(value)
            else:
                if value is None:
                    expected.append(None)
                    continue
                if isinstance(value, str):
                    candidates = (value,)
                elif isinstance(value, list) and value and all(isinstance(item, str) for item in value):
                    candidates = tuple(value)
                else:
                    raise ValueError(f'{self.name} range {index + 1}: Ocr requires a string or a non-empty list of strings')
                expected.append(candidates)
                continue
            expected.append(None)
        return specs, expected

    def run(self, task, *values, interval=None, threshold=None):
        """Read first; only explicit True on Click requests input after all checks pass."""
        self.last_results = None
        specs, expected = self.bind_runframe(values)
        if not task.appear(self, interval=interval, threshold=threshold):
            return FrameResult(False)
        image = task.device.image
        specs, targets = self.range_targets(image.shape, operations=specs, allow_missing_image=True)
        data, checks, clicks = [], [], []
        for index, (spec, target, requirement) in enumerate(zip(specs, targets, expected)):
            area = tuple(int(round(v)) for v in self.matched_ranges[index])
            x, y, w, h = area
            if spec['type'] == 'image':
                back = image[y:y+h, x:x+w].copy()
                if spec.get('file'):
                    # The supplied front is searched only within this range crop.
                    # Crops have their own coordinate system and RPC frame identity.
                    target.load_image()
                    fh, fw = target.image.shape[:2]
                    if fw > w or fh > h:
                        raise ValueError(
                            f'{self.name} range {index + 1}: front {fw}x{fh} exceeds back {w}x{h}'
                        )
                    target.roi_front = [0, 0, fw, fh]
                    target.roi_back = (0, 0, w, h)
                    matched = bool(target.match(back))
                    data.append(matched)
                    checks.append(matched)
                else:
                    data.append(back)
                    checks.append(None)
            elif spec['type'] == 'ocr':
                # Internal read-only preview can defer OCR until image identity is ambiguous.
                text = '' if getattr(self, 'defer_ocr', False) and requirement is None else target.ocr(image)
                data.append(text)
                checks.append(text in requirement if requirement is not None else None)
            else:
                data.append(area)
                checks.append(None)
                if requirement is True:
                    clicks.append(index)
        self.last_results = list(checks)
        clicked = []
        if not any(value is False for value in checks):
            for index in clicks:
                task.click(targets[index])
                clicked.append(index)
                self.last_results[index] = True
        return FrameResult(True, tuple(data), tuple(checks), tuple(clicked))

    def match(self, image, threshold=None, frame_id=None):
        import cv2
        import numpy as np
        threshold = self.threshold if threshold is None else threshold
        for index, (file, roi) in enumerate(zip(self.files, self.badge_references)):
            target = RuleImage(roi, self.roi_back, self.method, threshold, file)
            front, back = target.image, target.corp(image)
            if back.size == 0 or front.shape[0] > back.shape[0] or front.shape[1] > back.shape[1]:
                continue
            if self.badge_grayscale:
                front = cv2.cvtColor(front, cv2.COLOR_RGB2GRAY)
                back = cv2.cvtColor(back, cv2.COLOR_RGB2GRAY)
            _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(back, front, cv2.TM_CCOEFF_NORMED))
            if np.isfinite(score) and score >= threshold:
                self.badge_index = index
                self._badge_reference = roi
                self.roi_front = [self.roi_back[0]+location[0], self.roi_back[1]+location[1], front.shape[1], front.shape[0]]
                return True
        return False
