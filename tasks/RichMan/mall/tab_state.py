"""Locate the Special tab independently of newcomer menu ordering."""
from functools import lru_cache
from pathlib import Path
import cv2
import numpy as np

@lru_cache(maxsize=1)
def _label():
    return cv2.imread(str(Path(__file__).with_name('navbar')/'special_label.png'),cv2.IMREAD_GRAYSCALE)

def special_tab_state(image):
    if image is None or image.shape != (720,1280,3):
        return None
    gray=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
    score=cv2.matchTemplate(gray[75:585,1160:1270],_label(),cv2.TM_CCOEFF_NORMED)
    _,confidence,_,point=cv2.minMaxLoc(score)
    if confidence < 0.85:
        return None
    x,y=point[0]+1160,point[1]+75
    # Use both lantern margins; the central text is dark in both states.
    sides=np.concatenate((gray[y+2:y+48,x-11:x-3].ravel(),gray[y+2:y+48,x+39:x+47].ravel()))
    return x+18,y+29,bool(sides.mean()>175)
