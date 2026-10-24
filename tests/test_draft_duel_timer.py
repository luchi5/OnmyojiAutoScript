"""Deployment timers accept the live forty-second selection window."""
import cv2
import numpy as np
from unittest.mock import Mock
from tasks.DraftDuel.deployment import DeploymentVision


def synthetic_timer(value, offset=0):
    """Rendered test digits, not a screenshot or a live game observation."""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    text = str(value)
    width = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, .6, 1)[0][0]
    cv2.putText(image, text, (642-width//2+offset, 86), cv2.FONT_HERSHEY_SIMPLEX,
                .6, (220, 220, 220), 1, cv2.LINE_AA)
    return image


def test_forty_second_timer_range():
    for seconds in (31, 35, 40):
        vision = DeploymentVision(Mock(return_value=(str(seconds), .99)))
        assert vision.timer(synthetic_timer(seconds)) == seconds
    vision = DeploymentVision(Mock(return_value=("41", .99)))
    assert vision.timer(synthetic_timer(40)) is None
