"""Finish or dismiss a positively identified daily sign popup before navigation."""
import time
from module.atom.image import RuleImage
from module.exception import GameStuckError
from module.logger import logger


def handle_sign_popup(task):
    marker = RuleImage(roi_front=(610,208,66,155), roi_back=(570,140,150,260),
                       threshold=0.85, method='Template matching',
                       file='./tasks/Restart/harvest/daily_sign_panel.png')
    if not task.appear(marker):
        return False
    enabled = task.config.restart.harvest_config.enable and task.config.restart.harvest_config.enable_sign
    logger.info('Daily sign popup: ' + ('finish sign' if enabled else 'dismiss without signing'))
    deadline = time.monotonic() + 20
    next_click = 0
    clicks = 0
    while time.monotonic() < deadline:
        if not task.appear(marker):
            return True
        if clicks < 3 and time.monotonic() >= next_click:
            x, y = (640, 285) if enabled else (1040, 550)
            task.device.click(x=x, y=y, control_name='daily_sign_popup')
            clicks += 1
            next_click = time.monotonic() + 6
        time.sleep(0.5)
        task.device.screenshot()
    raise GameStuckError('Daily sign popup remained after bounded recovery; navigation cancelled')
