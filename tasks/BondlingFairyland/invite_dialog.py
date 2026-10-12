"""Continuation dialog captured on account 16; scoped by its invitation text."""
from pathlib import Path
from time import sleep

from module.atom.image import RuleImage
from module.base.timer import Timer
from module.exception import GameStuckError


def rule(name, front, back):
    return RuleImage(roi_front=front, roi_back=back, threshold=.88,
                     method='Template matching',
                     file=str(Path(__file__).with_suffix('') / (name + '.png')))


def handle_continue_invite(task, default_invite=True):
    """Return None for other dialogs so the legacy handler remains available."""
    title = rule('title', (475, 300, 336, 32), (460, 285, 365, 62))
    unchecked = rule('unchecked', (547, 347, 28, 27), (539, 339, 44, 43))
    confirm = rule('confirm', (703, 420, 106, 38), (690, 408, 133, 62))
    if not task.appear(title):
        return None
    limit = Timer(8).start()
    while not limit.reached():
        task.screenshot()
        if not task.appear(title):
            return True
        if default_invite and task.appear(unchecked):
            if task.appear_then_click(unchecked, interval=1):
                sleep(.5)
            continue
        task.appear_then_click(confirm, interval=1)
    raise GameStuckError('Bondling continue-invite dialog did not close')
