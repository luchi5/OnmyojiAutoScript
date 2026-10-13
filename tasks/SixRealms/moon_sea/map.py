import cv2
import re
import numpy as np
from numpy import uint8, fromfile
from module.logger import logger
from module.atom.image import RuleImage
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

    def enter_island(self, isl_type, isl_roi):
        # Keep the caller's island choice, but never reuse its old OCR position.
        markers = {
            MoonSeaType.island101: self.I_LAND_SHOP,
            MoonSeaType.island102: self.I_LAND_MYSTERY,
            MoonSeaType.island103: self.I_LAND_CHAOS,
            MoonSeaType.island104: self.I_LAND_FIRE,
            MoonSeaType.island105: self.I_LAND_STAR,
        }
        target = markers.get(isl_type)
        candidates = [target] if target is not None else list(markers.values())
        timeout = Timer(18).start()
        clicks = 0
        while True:
            self.screenshot()
            if not self.in_main() and self.appear(self.I_BACK_EXIT):
                logger.info('Entering island')
                return
            if timeout.reached():
                raise GameStuckError('Moon sea island entry timed out: {}'.format(isl_type))
            if self.appear_then_click(self.I_UI_CANCEL, interval=1):
                continue
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
        logger.info('Activating store')
        self.screenshot()
        if self.appear(self.I_M_STORE) and not self.appear(self.I_M_STORE_ACTIVITY):
            logger.warning('Now you have not money to buy items')
            logger.warning('Store is not active')
            return False
        cnt_act = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_UI_CONFIRM):
                self.ui_click_until_disappear(self.I_UI_CONFIRM, interval=2)
                break
            if cnt_act >= 3:
                logger.warning('Store is not active')
                return False
            if self.appear_then_click(self.I_M_STORE_ACTIVITY, interval=1.5):
                cnt_act += 1
                continue

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

