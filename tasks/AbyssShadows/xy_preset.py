"""XY preset selection, isolated to Abyss Shadows.

Ported from the local XY GeneralBattle implementation. Keep its bounded
waits and fallback to the current team on timeout.
"""
import time
from module.base.timer import Timer
from module.base.utils import get_color, color_similar
from module.logger import logger


class XYPresetMixin:
    def switch_preset_team(self, enable: bool = False, preset_group: int = 1, preset_team: int = 1):
        """
        切换预设的队伍，要求是在不锁定队伍时的情况下。

        Args:
            enable: 是否启用切换预设。
            preset_group: 目标预设组编号。
            preset_team: 目标预设队伍编号。

        Returns:
            None: 成功时切换到目标预设，失败时保留当前阵容。
        """
        if not enable:
            logger.info("Preset is disable")
            return

        logger.info("Preset is enable")
        timeout_warning = "Switch preset timeout, use current team"

        wait_preset_timer = Timer(4).start()
        while 1:
            if wait_preset_timer.reached():
                logger.warning(timeout_warning)
                return
            self.screenshot()

            if self.appear(self.I_PRESET_ENSURE):
                break
            if self.appear(self.I_PRESENT_LESS_THAN_5):
                break
            if self.appear_then_click(self.I_PRESET, interval=1):
                continue
            if self.appear_then_click(self.I_PRESET_WIT_NUMBER, interval=1):
                continue
            if self.appear_then_click(self.O_PRESET, interval=1):
                continue
            if self.appear_then_click(self.O_PRESET_FULL, interval=1):
                continue
        logger.info("Click preset button")

        tmp = self.__getattribute__("C_PRESET_GROUP_" + str(preset_group))
        if tmp is None:
            tmp = self.C_PRESET_GROUP_1
        color_size = [self.C_PRESET_GROUP_1.roi_back[2], self.C_PRESET_GROUP_1.roi_back[3]]
        unselected_color = (224.9, 208.3, 187.4)
        logger.info("Select preset group")
        choose_group_timer = Timer(4).start()
        while True:
            if choose_group_timer.reached():
                logger.warning(timeout_warning)
                return
            self.screenshot()
            color_tmp = get_color(
                self.device.image,
                (tmp.roi_back[0], tmp.roi_back[1], tmp.roi_back[0] + color_size[0], tmp.roi_back[1] + color_size[1]),
            )
            if color_similar(color_tmp, unselected_color):
                self.click(tmp, interval=0.2)
                continue
            break

        time.sleep(0.5)
        tmp = self.__getattribute__("C_PRESET_TEAM_" + str(preset_team))
        if tmp is None:
            tmp = self.C_PRESET_TEAM_1
        color_size = [5, 5]
        unselected_color = (216.8, 185.0, 146.8)
        logger.info("Select preset team")
        choose_team_timer = Timer(4).start()
        while True:
            if choose_team_timer.reached():
                logger.warning(timeout_warning)
                return
            self.screenshot()
            color_tmp = get_color(
                self.device.image,
                (tmp.roi_back[0], tmp.roi_back[1], tmp.roi_back[0] + color_size[0], tmp.roi_back[1] + color_size[1]),
            )
            if color_similar(color_tmp, unselected_color):
                self.click(tmp, interval=0.2)
                continue
            break
        self.click(tmp)
        wait_ensure_timer = Timer(3).start()
        preset_clicked = False
        while True:
            if wait_ensure_timer.reached():
                logger.warning(timeout_warning)
                return
            self.screenshot()
            if preset_clicked and not self.appear(self.I_PRESET_ENSURE):
                logger.info("Click preset ensure success")
                return
            if self.appear_then_click(self.I_PRESET_ENSURE, interval=1):
                preset_clicked = True
                continue
