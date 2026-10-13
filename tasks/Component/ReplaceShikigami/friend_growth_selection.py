"""Bounded friend-growth selection independent of cached parent task methods."""
from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.Utils.config_enum import ShikigamiClass

def switch_friend_growth_class(self, shikigami_class: ShikigamiClass = ShikigamiClass.N,
                          require_growth=True):
    """
    要求在式神育成的界面
    切换分类
    :param shikigami_class:
    :param shikigami_order:
    :return:
    """
    match_selected = {ShikigamiClass.MATERIAL: self.I_RS_MATERIAL_SELECTED,
                      ShikigamiClass.N: self.I_RS_N_SELECTED,
                      ShikigamiClass.R: self.I_RS_R_SELECTED,
                      ShikigamiClass.SR: self.I_RS_SR_SELECTED,
                      ShikigamiClass.SSR: self.I_RS_SSR_SELECTED,
                      ShikigamiClass.SP: self.I_RS_SP_SELECTED,
                      ShikigamiClass.UR: self.I_RS_UR_SELECTED}
    match_click = {ShikigamiClass.MATERIAL: self.I_RS_MATERIAL,
                   ShikigamiClass.N: self.I_RS_N,
                   ShikigamiClass.R: self.I_RS_R,
                   ShikigamiClass.SR: self.I_RS_SR,
                   ShikigamiClass.SSR: self.I_RS_SSR,
                   ShikigamiClass.SP: self.I_RS_SP,
                   ShikigamiClass.UR: self.I_RS_UR}
    check_selected = match_selected[shikigami_class]
    check_click = match_click[shikigami_class]
    # 选择式神的种类
    selection_timer = Timer(20).start()
    while not selection_timer.reached():
        self.screenshot()

        if self.appear(check_selected):
            break
        if self.appear(check_click, interval=1):
            if not (self.wait_until_pos_stable(check_click, stable_time=1.5, timeout=3.5)
                    and self.appear(check_click)):
                continue
            if self.appear(check_selected):
                break
            self.click(check_click, interval=2)
            continue
        if require_growth and not self.in_shikigami_growth():
            raise GameStuckError('Shikigami growth page lost before class selection')
        self.wait_animate_stable(rule=self.C_SHIKIGAMI_SWITCH_1, interval=0.8)
        self.screenshot()
        if require_growth and not self.in_shikigami_growth():
            raise GameStuckError('Shikigami growth page lost while waiting for class menu')
        if self.click(self.C_SHIKIGAMI_SWITCH_1, interval=3.5):
            continue
    else:
        raise GameStuckError('Shikigami class selection timed out after 20 seconds')
    logger.info('Select shikigami class: %s' % shikigami_class)
