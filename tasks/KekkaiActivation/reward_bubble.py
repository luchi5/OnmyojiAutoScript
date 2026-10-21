"""Locate a collectible card bubble by its rim, independent of the card art."""
import cv2
import numpy as np


def find_reward_bubble(image):
    """Return an inner click rectangle in a 1280x720 RGB realm frame.

    Only inspect the card side of the realm. A complete circular dark bubble
    with a bright gold rim is positive evidence; missing chest recognition
    alone must never turn scenery or an empty plaque into a click target.
    """
    if image is None or image.shape[:2] != (720, 1280):
        return None
    left, top, width, height = 720, 120, 300, 400
    crop = image[top:top + height, left:left + width]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    circles = cv2.HoughCircles(
        cv2.GaussianBlur(gray, (5, 5), 0), cv2.HOUGH_GRADIENT,
        dp=1, minDist=40, param1=100, param2=26,
        minRadius=28, maxRadius=55)
    if circles is None:
        return None

    angles = np.arange(72) * (2 * np.pi / 72)
    directions = np.stack((np.cos(angles), np.sin(angles)), axis=1)
    candidates = []
    for x, y, radius in circles[0]:
        # Do not infer a bubble from a partial circle at the search boundary.
        if (x - radius * 1.13 < 0 or x + radius * 1.13 >= width
                or y - radius * 1.13 < 0 or y + radius * 1.13 >= height):
            continue
        inner = np.rint((x, y) + directions * radius * .73).astype(int)
        dark = np.mean(np.max(crop[inner[:, 1], inner[:, 0]], axis=1) < 100)
        if dark < .7:
            continue
        gold = 0
        for scale in np.arange(.88, 1.13, .04):
            ring = np.rint((x, y) + directions * radius * scale).astype(int)
            red, green, blue = crop[ring[:, 1], ring[:, 0]].astype(float).T
            gold = max(gold, np.mean(
                (red > 150) & (green > 135) & (blue < green * .92)
                & (green > red * .65)))
        if gold >= .5:
            candidates.append((gold + dark, x, y, radius))
    # Multiple candidates can mean an overlay or a scene we do not support.
    if len(candidates) != 1:
        return None
    _, x, y, radius = candidates[0]
    half = max(8, round(radius * .3))
    return (left + round(x) - half, top + round(y) - half, half * 2, half * 2)
