"""Dismiss accidental exit prompts over an already visible reward screen."""
from module.atom.image import RuleImage
from module.atom.click import RuleClick

CONTINUE = RuleImage(roi_front=(548,677,190,32), roi_back=(538,667,210,52),
    threshold=0.85, method='Template matching', file='./tasks/Dokan/res/settlement_continue.png')
WARNING = RuleImage(roi_front=(440,281,400,66), roi_back=(430,271,420,86),
    threshold=0.9, method='Template matching', file='./tasks/Dokan/res/settlement_exit_warning.png')
CANCEL = RuleClick(roi_front=(487,423,84,28), roi_back=(487,423,84,28), name='dokan_settlement_cancel_exit')
ADVANCE = RuleClick(roi_front=(560,680,160,25), roi_back=(560,680,160,25), name='dokan_settlement_continue')

def handle_settlement(task):
    if not task.appear(CONTINUE):
        return False
    if task.appear(WARNING):
        task.click(CANCEL, interval=1.5)
    else:
        task.click(ADVANCE, interval=1.5)
    return True
