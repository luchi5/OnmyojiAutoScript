import cv2
import re
import numpy as np
from numpy import uint8, fromfile
from module.logger import logger
from module.atom.image import RuleImage
from module.atom.click import RuleClick
from module.base.timer import Timer
from module.exception import GameStuckError

from tasks.base_task import BaseTask
from tasks.SixRealms.moon_sea.skills import MoonSeaSkills
from tasks.SixRealms.assets import SixRealmsAssets
from tasks.SixRealms.common import MoonSeaType
from tasks.SixRealms.oas_ocr import StoneOcr


class MoonSeaMap(MoonSeaSkills):
    # Island markers from AzurTian/OnmyojiAutoScript (GPL-3.0), commit
    # 49eab5bfa827552aa6bbbff9c05f9de832cd037b. Re-match every frame:
    # map animations move islands after the OCR-based decision.
    I_LAND_SHOP = RuleImage(roi_front=(650, 406, 18, 26), roi_back=(124, 140, 1013, 515), threshold=0.8, method="Template matching", file="./tasks/SixRealms/moon_sea/markers/shop.png")
    I_LAND_MYSTERY = RuleImage(roi_front=(651, 430, 18, 26), roi_back=(124, 140, 1013, 515), threshold=0.8, method="Template matching", file="./tasks/SixRealms/moon_sea/markers/mystery.png")
    I_LAND_CHAOS = RuleImage(roi_front=(650, 373, 17, 20), roi_back=(124, 140, 1013, 515), threshold=0.8, method="Template matching", file="./tasks/SixRealms/moon_sea/markers/chaos.png")
    I_LAND_STAR = RuleImage(roi_front=(651, 379, 17, 20), roi_back=(124, 140, 1013, 515), threshold=0.8, method="Template matching", file="./tasks/SixRealms/moon_sea/markers/star.png")
    I_LAND_FIRE = RuleImage(roi_front=(289, 313, 18, 26), roi_back=(124, 140, 1013, 515), threshold=0.8, method="Template matching", file="./tasks/SixRealms/moon_sea/markers/fire.png")

    map_ocr = StoneOcr(
        roi=(0, 0, 1280, 720),
        area=(0, 0, 1280, 720),
        mode="Full",
        method="Default",
        keyword="",
        name="map_ocr")

    @staticmethod
    def contains_any_char(string, chars):
        return not set(string).isdisjoint(set(chars))

    def visible_islands(self, remain_turns=99):
        # Match Azur's priority and never filter the only available island.
        markers = [
            (MoonSeaType.island101, self.I_LAND_SHOP),
            (MoonSeaType.island102, self.I_LAND_MYSTERY),
            (MoonSeaType.island103, self.I_LAND_CHAOS),
            (MoonSeaType.island105, self.I_LAND_STAR),
            (MoonSeaType.island104, self.I_LAND_FIRE),
        ]
        visible = [(kind, marker) for kind, marker in markers if self.appear(marker)]
        # This version has no reliable map coin counter. The disabled summon
        # button is usable only when its enabled variant does NOT also match.
        # Overlapping templates previously excluded the sole shop at 464 coins.
        if (len(visible) > 1 and remain_turns > 1
                and self.appear(self.I_M_STORE)
                and not self.appear(self.I_M_STORE_ACTIVITY)):
            visible = [(kind, marker) for kind, marker in visible
                       if kind != MoonSeaType.island101]
        return visible

    def decide(self) -> tuple:
        """Read turns with OCR; identify and prioritize islands by their icons."""
        self.screenshot()
        if self.appear(self.I_BOSS_FIRE):
            return MoonSeaType.island106, 0, (0, 0, 0, 0)
        remain_turns = 99
        for result in self.map_ocr.detect_and_ocr(image=self.device.image):
            x, y = result.box[0]
            if x > 1000 and result.box[2][1] < 300:
                if self.contains_any_char(result.ocr_text, '回合国'):
                    match = re.search(r'\d{1,2}', result.ocr_text)
                    if match:
                        remain_turns = int(match.group())
        self._island_remain_turns = remain_turns
        visible = self.visible_islands(remain_turns)
        if visible:
            kind, marker = visible[0]
            result = kind, remain_turns, tuple(marker.roi_front)
        else:
            result = MoonSeaType.island100, remain_turns, (0, 0, 0, 0)
        logger.info('Island type: {}, Residue: {}, ROI: {}'.format(*result))
        return result

    def handle_poor_store_entry(self):
        if not self.appear(self.I_UI_CANCEL):
            return False
        results = self.map_ocr.detect_and_ocr(image=self.device.image)
        text = ''.join(result.ocr_text for result in results)
        if '万相铃不足' not in text or '宁息之屿' not in text:
            return False
        if not getattr(self, '_poor_store_route_checked', False):
            self.click(self.I_UI_CANCEL, interval=2)
            self._poor_store_reselect = True
            logger.info('Insufficient coins: cancel and inspect alternative islands')
            return True
        for result in results:
            if result.ocr_text.strip() != '进入':
                continue
            xs = [float(point[0]) for point in result.box]
            ys = [float(point[1]) for point in result.box]
            x, y = int(sum(xs)/len(xs)), int(sum(ys)/len(ys))
            if not (640 < x < 900 and 350 < y < 510):
                continue
            button = RuleClick(roi_front=(x-10,y-8,20,16), roi_back=(x-10,y-8,20,16),
                               name='poor_store_enter')
            self.click(button, interval=2)
            self._skip_poor_store_purchase = True
            logger.info('Only store remains: enter and exit without purchasing')
            break
        # Keep this recognized dialog out of the generic cancel branch.
        return True

    def enter_island(self, isl_type, isl_roi):
        # Keep the caller's island choice, but never reuse its old OCR position.
        markers = {
            MoonSeaType.island101: self.I_LAND_SHOP,
            MoonSeaType.island102: self.I_LAND_MYSTERY,
            MoonSeaType.island103: self.I_LAND_CHAOS,
            MoonSeaType.island104: self.I_LAND_FIRE,
            MoonSeaType.island105: self.I_LAND_STAR,
        }
        self._poor_store_route_checked = False
        self._poor_store_reselect = False
        self._skip_poor_store_purchase = False
        poor_scan_frames = 0
        target = markers.get(isl_type)
        candidates = [target] if target is not None else list(markers.values())
        timeout = Timer(18).start()
        clicks = 0
        while True:
            self.screenshot()
            if timeout.reached():
                raise GameStuckError('Moon sea island entry timed out: {}'.format(isl_type))
            if target == self.I_LAND_SHOP and self.handle_poor_store_entry():
                continue
            if not self.in_main() and self.appear(self.I_BACK_EXIT):
                logger.info('Entering island')
                return
            if timeout.reached():
                raise GameStuckError('Moon sea island entry timed out: {}'.format(isl_type))
            if self.appear_then_click(self.I_UI_CANCEL, interval=1):
                continue
            if self._poor_store_reselect:
                if not self.in_main():
                    continue
                alternatives = [marker for _, marker in self.visible_islands(
                    getattr(self, '_island_remain_turns', 99)) if marker != self.I_LAND_SHOP]
                poor_scan_frames += 1
                if alternatives:
                    target = alternatives[0]
                elif poor_scan_frames < 3:
                    continue
                else:
                    target = self.I_LAND_SHOP
                    self._skip_poor_store_purchase = True
                candidates = [target]
                self._poor_store_route_checked = True
                self._poor_store_reselect = False
                clicks = 0
            if not self.in_main() or clicks >= 6:
                continue
            if target is None:
                candidates = [marker for _, marker in self.visible_islands(
                    getattr(self, '_island_remain_turns', 99))]
            for marker in candidates:
                if self.appear_then_click(marker, interval=2.5):
                    # Once selected, don't switch islands during transition frames.
                    target = marker
                    candidates = [marker]
                    clicks += 1
                    break

    def activate_store(self) -> bool:
        """
        最后打boss前面激活一次商店买东西
        @return: 有钱够就是True
        """
        if not self.needs_skill101():
            logger.info('Skip summon: skill is max level or resumed level is unknown')
            return False
        # A single animated frame can read 17 as 1. Revalidate before spending.
        store_frames = 0
        for _ in range(3):
            self.screenshot()
            if self.appear(self.I_LAND_SHOP):
                store_frames += 1
            texts = [result.ocr_text for result in
                     self.map_ocr.detect_and_ocr(image=self.device.image)]
            turns = [int(match.group(1)) for text in texts
                     for match in [re.search(r'(\d+)回合后迎战', text)] if match]
            if turns != [1]:
                logger.info('Skip summon: last-island count is not stable: {}'.format(turns))
                return False
        if store_frames >= 2:
            logger.info('Store island already visible; skip summon')
            return False
        confirm = RuleImage(
            roi_front=(698,418,114,43), roi_back=(680,400,155,80),
            threshold=0.9, method='Template matching',
            file='./tasks/SixRealms/moon_sea/markers/summon_confirm.png')
        logger.info('Activating store')
        timeout = Timer(20).start()
        clicked = False
        confirmed = False
        store_frames = 0
        while not timeout.reached():
            self.screenshot()
            text = ''.join(result.ocr_text for result in
                           self.map_ocr.detect_and_ocr(image=self.device.image))
            if '召唤' in text and '300' in text:
                if self.appear(confirm):
                    self.click(confirm, interval=1.5)
                    confirmed = True
                continue
            store_frames = store_frames + 1 if self.appear(self.I_LAND_SHOP) else 0
            if store_frames >= 2 and self.in_main():
                logger.info("Store island is available; continue route selection")
                return True
            if confirmed and self.in_main():
                return True
            if not clicked and self.appear(self.I_M_STORE) and not self.appear(self.I_M_STORE_ACTIVITY):
                return False
            if not clicked and self.appear_then_click(self.I_M_STORE_ACTIVITY, interval=2):
                clicked = True
        if self.in_main() and not self.appear(self.I_UI_CANCEL):
            logger.warning('Optional summon produced no dialog; continue existing routes')
            return False
        raise GameStuckError('Store summon was not confirmed; do not continue with an open dialog')

    def entry_island_random(self, area: list = None):
        """Choose a visible island instead of clicking obsolete fixed slots."""
        return self.enter_island(MoonSeaType.island100, None)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from module.base.utils import load_image

    c = Config('oas1')
    d = Device(c)
    t = MoonSeaMap(c, d)
    t.device.image = load_image(r'C:\Users\Ryland\Desktop\Desktop\34.png')

    match = re.search(r'\d{1,2}', '<17回合后迎战月读')
    if match:
        isl_num = int(match.group())
        print(isl_num)

