"""Leave a verified delegation detail panel during unrelated navigation."""
from module.atom.image import RuleImage

DETAIL_TITLE = RuleImage(roi_front=(335,116,213,35), roi_back=(325,106,233,55),
    threshold=0.9, method='Template matching', file='./tasks/Delegation/rewards/detail_title.png')
DETAIL_BACK = RuleImage(roi_front=(971,424,70,33), roi_back=(950,408,112,65),
    threshold=0.9, method='Template matching', file='./tasks/Delegation/rewards/detail_back.png')

def close_delegation_detail(task):
    if not task.appear(DETAIL_TITLE) or not task.appear(DETAIL_BACK):
        return False
    return task.click(DETAIL_BACK, interval=1.5)
