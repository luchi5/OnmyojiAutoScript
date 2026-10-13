"""Read the violet selected-team outline without inspecting portraits."""
import numpy as np


def selected_team(image):
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return None
    matches = []
    for team, y in enumerate((235, 355, 475, 595), 1):
        height = min(100, 630 - y)
        coverage = []
        for x in (184, 660):
            pixels = image[y:y + height, x:x + 22].astype(np.float32)
            # Violet has strong red AND blue in both RGB and BGR images.
            low = np.minimum(pixels[:, :, 0], pixels[:, :, 2])
            high = np.maximum(pixels[:, :, 0], pixels[:, :, 2])
            green = pixels[:, :, 1]
            purple = (low > 90) & (high > 120) & (low > green * 1.1) & (high > green * 1.25)
            coverage.append(float(np.mean(np.mean(purple, axis=1) > .15)))
        if min(coverage) >= .2 and max(coverage) >= .6:
            matches.append(team)
    return matches[0] if len(matches) == 1 else None
