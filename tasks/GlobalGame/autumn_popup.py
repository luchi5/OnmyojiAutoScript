"""Close only verified guild autumn event overlays before background actions."""
from module.atom.image import RuleImage

def rule(name, roi):
    x,y,w,h=roi
    return RuleImage(roi_front=roi,roi_back=(x-8,y-8,w+16,h+16),threshold=0.9,
                     method='Template matching',file='./tasks/GlobalGame/ui/'+name+'.png')

POPUPS = (
    (rule('autumn_photo_title',(560,215,362,86)),rule('autumn_photo_close',(941,187,53,53))),
    (rule('autumn_level_title',(548,436,166,31)),rule('autumn_level_close',(774,81,51,53))),
)

def close_autumn_popup(task):
    for title,close in POPUPS:
        if task.appear(title) and task.appear(close):
            task.click(close,interval=1.5)
            return True
    return False
