"""Dismiss the verified coin information tooltip during navigation."""
from module.atom.image import RuleImage
from module.atom.click import RuleClick

TITLE = RuleImage(roi_front=(743,151,55,32), roi_back=(733,141,75,52),
    threshold=0.9, method='Template matching', file='./tasks/GlobalGame/ui/coin_tooltip_title.png')
DESCRIPTION = RuleImage(roi_front=(743,263,292,27), roi_back=(733,253,312,47),
    threshold=0.9, method='Template matching', file='./tasks/GlobalGame/ui/coin_tooltip_description.png')
DISMISS = RuleClick(roi_front=(590,140,30,20), roi_back=(590,140,30,20), name='dismiss_coin_tooltip')

def close_coin_tooltip(task):
    if not task.appear(TITLE) or not task.appear(DESCRIPTION):
        return False
    return task.click(DISMISS, interval=1.5)
