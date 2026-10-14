
from module.logger import logger
from module.base.timer import Timer
from module.atom.image import RuleImage
from module.exception import GameStuckError
from tasks.SixRealms.moon_sea.skills import MoonSeaSkills


class MoonSeaL102(MoonSeaSkills):
    def run_l102(self):
        logger.hr('Start Island battle')
        logger.info('Island 102')
        is_imitation = None
        while 1:
            self.screenshot()
            if self.appear(self.I_COIN_RIGHT_TOP):
                is_imitation = False
                break
            if self.appear(self.I_IMITATE):
                is_imitation = True
                break
        if not is_imitation:
            logger.info('Transfer skill')
            logger.info('Do not transfer skill and exit')
            self.leave_mystery_island()
            logger.info('Finish Island 102')
            return
        self.imitate()

    def leave_mystery_island(self):
        # Identify this dialog explicitly; do not click through other warnings.
        title = RuleImage(roi_front=(515,280,250,38), roi_back=(500,270,280,60),
                          threshold=0.9, method='Template matching',
                          file='./tasks/SixRealms/l102/leave_title.png')
        unchecked = RuleImage(roi_front=(567,343,33,36), roi_back=(560,335,50,50),
                              threshold=0.9, method='Template matching',
                              file='./tasks/SixRealms/l102/leave_unchecked.png')
        confirm = RuleImage(roi_front=(698,418,114,43), roi_back=(680,400,155,80),
                            threshold=0.9, method='Template matching',
                            file='./tasks/SixRealms/l102/leave_confirm.png')
        timeout = Timer(20).start()
        while not timeout.reached():
            self.screenshot()
            if self.in_main():
                return
            if self.appear(title):
                if self.appear(unchecked):
                    self.click(unchecked, interval=1)
                    continue
                self.appear_then_click(confirm, interval=1.5)
                continue
            self.appear_then_click(self.I_BACK_EXIT, interval=3)
        raise GameStuckError('Cannot leave mystery island within 20 seconds')

    def imitate(self):
        # 仿造
        logger.info('Imitate')
        if self.cnt_skill101 == 0 or self.cnt_skill101 >= 5:
            logger.info('Skill 101 is max level; skip imitation')
            self.leave_mystery_island()
            return
        selected = False
        submitted = False
        dialog_clicked = False
        success = False
        timeout = Timer(25).start()
        while not timeout.reached():
            self.screenshot()
            if self.in_main():
                break
            if self.appear(self.I_IMITATE_SUCCESS):
                success = True
            # Modal dialogs take priority over all background controls.
            confirm = next((rule for rule in (self.I_UI_CONFIRM_SAMLL, self.I_UI_CONFIRM)
                            if self.appear(rule)), None)
            if confirm is not None:
                if not dialog_clicked:
                    self.click(confirm)
                    dialog_clicked = True
                continue
            dialog_clicked = False
            if success:
                continue
            # Each island gets one selection and one submission. A delayed
            # animation is not permission to open a second imitation dialog.
            if submitted:
                continue
            if not selected:
                if self.appear(self.I_IMITATE_1):
                    self.click(self.I_IMITATE_1)
                    selected = True
                continue
            if self.appear(self.I_IMITATE):
                self.click(self.I_IMITATE)
                submitted = True
        else:
            raise GameStuckError("Mystery imitation result not confirmed after one submission")
        if success:
            self.cnt_skill101 = min(5, self.cnt_skill101 + 1)
        logger.info(f'Skill 101 level: {self.cnt_skill101}')
        logger.info('Finish Imitate')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = MoonSeaL102(c, d)
    t.screenshot()

    t.run_l102()
