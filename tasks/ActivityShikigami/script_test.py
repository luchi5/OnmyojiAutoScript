
import datetime
from abc import ABC

from module.config.config import Config
from module.device.device import Device

from tasks.ActivityShikigami.page import page_climb_act, page_climb_ticket, page_climb_boss
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.ActivityShikigami.script_task import ScriptTask


class ImplScriptTask(ScriptTask, ABC):
    def test_boss_page(self) -> int:
        # Navigation/OCR only: do not click challenge or spend stamina.
        self.goto_page(page_climb_boss)
        return self.boss_read_bonus()

    def test_battle_main(self) -> bool:
        # 本期导航检查不点击挑战、不消耗门票。
        self.goto_page(page_climb_ticket)
        self.ticket_read_budget()
        # 从默认战斗页面到式神录 再返回
        self.ui_click(self.I_TICKET_RECORDS, stop=self.I_CHECK_RECORDS, interval=1)
        self.ui_click(self.I_UI_BACK_YELLOW, stop=self.I_TICKET_PAGE, interval=1)
        self.goto_page(page_climb_act)
        return True


class ScriptTest:
    def __init__(self, config: str = 'oas1'):
        self.config = Config(config)
        self.device = Device(self.config)
        self.task = ImplScriptTask(self.config, self.device)


if __name__ == '__main__':
    test = ScriptTest(config='oas1')
    assert test.task.test_battle_main()


























