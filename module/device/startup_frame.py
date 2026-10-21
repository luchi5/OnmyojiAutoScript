"""Bounded first-frame recovery after a fresh emulator launch."""
import time

import cv2
import numpy as np
from adbutils.errors import AdbError

from module.exception import RequestHumanTakeover
from module.logger import logger


def wait_for_frame(device, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            data = device.adb_shell(['screencap', '-p'], stream=True,
                                    timeout=min(10, remaining))
            frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            # Match the screenshot layer's near-black threshold, not just any().
            if frame is not None and frame.shape == (720, 1280, 3) and frame.mean() >= 1:
                return True
        except (AdbError, OSError, cv2.error):
            pass
        time.sleep(max(0, min(2, deadline - time.monotonic())))
    return False


def ensure_startup_frame(device, launch_game):
    if not getattr(device, '_startup_frame_pending', False):
        return
    # Consume before recovery: failures or repeated app_start must not reset budget.
    device._startup_frame_pending = False
    logger.info('Waiting up to 60 seconds for startup image')
    if wait_for_frame(device):
        return
    logger.warning('No startup image after 60 seconds; restarting this emulator once')
    try:
        device.nemu_ipc_release()
        # emulator_start verifies shutdown itself; use exactly one launch attempt.
        if device.emulator_start(max_attempts=1) is not True:
            raise RequestHumanTakeover('Startup image recovery: emulator restart failed')
        launch_game()
        if not wait_for_frame(device):
            raise RequestHumanTakeover('No valid image after one emulator restart; stopped')
    finally:
        device._startup_frame_pending = False
