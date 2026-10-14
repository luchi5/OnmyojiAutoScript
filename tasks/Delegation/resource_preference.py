"""Handle the first-use delegation resource preference before reward controls."""
from time import monotonic
from module.atom.ocr import RuleOcr
from module.atom.click import RuleClick
from module.exception import GameStuckError

class ResourcePreference:
    def __init__(self):
        self.reader = RuleOcr(roi=(260,190,740,360), area=(260,190,740,360),
            mode='Full', method='Default', keyword='', name='delegation_resource_preference')
        self.phase = 'choose'
        self.started = None
        self.selected_at = None

    def handle(self, task):
        results = self.reader.detect_and_ocr(task.device.image)
        title = any('偏向' in r.ocr_text and '资源' in r.ocr_text for r in results)
        if not title:
            self.phase, self.started, self.selected_at = 'choose', None, None
            return False
        now = monotonic()
        if self.started is None:
            self.started = now
        if now - self.started > 15:
            raise GameStuckError('Delegation resource preference did not complete within 15 seconds')
        # Never click background completion/return controls while this modal is visible.
        target_text = '金币' if self.phase == 'choose' else '选定'
        if self.phase == 'submitted':
            return True
        if self.phase == 'confirm' and now - self.selected_at < 0.8:
            return True
        target = next((r for r in results if r.ocr_text.strip() == target_text), None)
        if target is None:
            return True
        x = sum(float(p[0]) for p in target.box)/len(target.box) + 260
        y = sum(float(p[1]) for p in target.box)/len(target.box) + 190
        if self.phase == 'choose':
            y -= 60  # Coin icon immediately above its exact label.
        button = RuleClick(roi_front=(int(x)-8,int(y)-8,16,16),
            roi_back=(int(x)-8,int(y)-8,16,16), name='delegation_preference_' + self.phase)
        if task.click(button, interval=1):
            if self.phase == 'choose':
                self.phase, self.selected_at = 'confirm', now
            else:
                self.phase = 'submitted'
        return True
