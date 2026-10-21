import cv2
import numpy as np

from module.atom.image import RuleImage


class ActivationButton(RuleImage):
    """Match the button shape and its enabled (gold) foreground."""

    def __init__(self):
        super().__init__(
            roi_front=(1047, 560, 100, 100),
            roi_back=(1010, 530, 200, 170),
            threshold=0.85, method='Template matching',
            file='./tasks/KekkaiActivation/a/a_a_activate_yellow.png',
        )

    def match(self, image, threshold=None):
        if not super().match(image, threshold):
            return False
        hsv = cv2.cvtColor(self.corp(image, self.roi_front), cv2.COLOR_RGB2HSV)
        gold = ((hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 40)
                & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 120))
        return bool(np.mean(gold) >= 0.15)
