"""百鬼棋局运行时专用的按住并拖动操作。"""

import time

import numpy as np

import module.device.method.scrcpy.const as scrcpy_const
from module.base.utils import (
    ensure_int,
    ensure_time,
    point2str,
    random_rectangle_point,
)
from module.logger import logger


def Press_and_Drag(
    device,
    p1,
    p2,
    hold_duration=(0.2, 0.3),
    point_random=(-10, -10, 10, 10),
    swipe_duration=0.5,
    name='Press_and_Drag',
):
    """在起点按住后拖到终点；仅供 Chess 手牌、棋盘和御魂操作。"""
    device.handle_control_check(name)
    p1, p2 = ensure_int(p1, p2)
    hold_duration = ensure_time(hold_duration)
    swipe_duration = ensure_time(
        (swipe_duration * 0.92, swipe_duration * 1.08)
    )
    release_duration = ensure_time((0.04, 0.08))
    action_log = 'Press_and_Drag %s -> %s' % (
        point2str(*p1),
        point2str(*p2),
    )
    method = device.config.script.device.control_method
    start = time.perf_counter()

    if method == 'minitouch':
        _press_and_drag_minitouch(
            device,
            p1,
            p2,
            hold_duration=hold_duration,
            point_random=point_random,
            swipe_duration=swipe_duration,
            release_duration=release_duration,
        )
    elif method == 'uiautomator2':
        _press_and_drag_uiautomator2(
            device,
            p1,
            p2,
            hold_duration=hold_duration,
            point_random=point_random,
            swipe_duration=swipe_duration,
            release_duration=release_duration,
        )
    elif method == 'scrcpy':
        _press_and_drag_scrcpy(
            device,
            p1,
            p2,
            hold_duration=hold_duration,
            point_random=point_random,
            swipe_duration=swipe_duration,
            release_duration=release_duration,
        )
    else:
        logger.warning(
            f'Control method {method} cannot hold before moving; '
            'falling back to ADB swipe'
        )
        device.swipe_adb(
            p1,
            p2,
            duration=ensure_time(hold_duration + swipe_duration),
        )

    elapsed = time.perf_counter() - start
    logger.info(f'[{elapsed:.3f}s] {action_log}')


def _randomized_points(p1, p2, point_random):
    p1 = np.array(p1) - random_rectangle_point(point_random)
    p2 = np.array(p2) - random_rectangle_point(point_random)
    return p1, p2


def _left_bulged_curve(p1, p2, speed=20, min_distance=2):
    """生成带最小加加速度节奏的非对称左凸拖动轨迹。"""
    p1 = np.asarray(p1, dtype=float)
    p2 = np.asarray(p2, dtype=float)
    delta = p2 - p1
    distance = float(np.linalg.norm(delta))
    if distance <= 0:
        return [p1.astype(int).tolist(), p2.astype(int).tolist()]

    # 鼠标拖动通常不会画出完全对称的圆弧：左偏峰值放在全程
    # 42%～56% 的随机位置，并限制弧度以免离开卡牌的有效拖动区域。
    max_bulge = float(
        np.clip(distance * np.random.uniform(0.06, 0.10), 10, 55)
    )
    peak = float(np.random.uniform(0.42, 0.56))
    concentration = float(np.random.uniform(3.8, 4.4))
    alpha = peak * concentration
    beta = (1 - peak) * concentration
    peak_value = peak ** alpha * (1 - peak) ** beta
    vertical_drift = float(
        np.clip(np.random.normal(0, distance * 0.008), -5, 5)
    )

    segments = max(int(distance / speed) + 1, 10)
    timeline = np.linspace(0, 1, segments + 1)
    # Minimum-jerk 位移曲线：起步和落点慢，中段自然加速。
    progress_values = (
        10 * timeline ** 3 - 15 * timeline ** 4 + 6 * timeline ** 5
    )
    points = []
    for progress in progress_values:
        if progress <= 0 or progress >= 1:
            lateral_shape = 0.0
        else:
            lateral_shape = (
                progress ** alpha * (1 - progress) ** beta / peak_value
            )
        point = (
            p1
            + delta * progress
            + np.array(
                [-max_bulge * lateral_shape, vertical_drift * lateral_shape]
            )
        )
        point = point.astype(int).tolist()
        if (
            points
            and np.linalg.norm(np.subtract(point, points[-1]))
            < min_distance
        ):
            continue
        points.append(point)

    end = p2.astype(int).tolist()
    if points[-1] != end:
        points.append(end)
    return points


def _press_and_drag_minitouch(
    device,
    p1,
    p2,
    hold_duration,
    point_random,
    swipe_duration,
    release_duration,
):
    p1, p2 = _randomized_points(p1, p2, point_random)
    points = _left_bulged_curve(p1, p2, speed=20, min_distance=2)
    builder = device.minitouch_builder
    move_wait = max(
        1,
        int(swipe_duration * 1000 / max(len(points) - 1, 1)),
    )

    builder.down(*points[0]).commit().wait(int(hold_duration * 1000))
    device.minitouch_send()
    for point in points[1:]:
        builder.move(*point).commit().wait(move_wait)
    device.minitouch_send()
    builder.move(*p2).commit().wait(int(release_duration * 1000))
    device.minitouch_send()
    builder.up().commit()
    device.minitouch_send()


def _press_and_drag_uiautomator2(
    device,
    p1,
    p2,
    hold_duration,
    point_random,
    swipe_duration,
    release_duration,
):
    p1, p2 = _randomized_points(p1, p2, point_random)
    points = _left_bulged_curve(p1, p2, speed=35, min_distance=2)
    move_duration = swipe_duration / max(len(points) - 1, 1)
    path = [(int(points[0][0]), int(points[0][1]), hold_duration)]
    for index, point in enumerate(points[1:], start=1):
        wait = move_duration
        if index == len(points) - 1:
            wait += release_duration
        path.append((int(point[0]), int(point[1]), wait))
    path.append((int(p2[0]), int(p2[1]), 0))
    device._drag_along(path)


def _press_and_drag_scrcpy(
    device,
    p1,
    p2,
    hold_duration,
    point_random,
    swipe_duration,
    release_duration,
):
    device.scrcpy_ensure_running()
    with device._scrcpy_control_socket_lock:
        p1, p2 = _randomized_points(p1, p2, point_random)
        points = _left_bulged_curve(p1, p2, speed=6, min_distance=1)
        move_interval = swipe_duration / max(len(points) - 1, 1)
        device._scrcpy_control.touch(*p1, scrcpy_const.ACTION_DOWN)
        device.sleep(hold_duration)
        for point in points[1:]:
            device._scrcpy_control.touch(*point, scrcpy_const.ACTION_MOVE)
            device.sleep(move_interval)
        device.sleep(release_duration)
        device._scrcpy_control.touch(*p2, scrcpy_const.ACTION_UP)
        device.sleep(0.05)
