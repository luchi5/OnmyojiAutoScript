"""公共确定/取消按钮的新旧素材兼容；由调用任务决定按钮操作。"""

from pathlib import Path

from module.atom.image import RuleImage


class DialogButtonRule(RuleImage):
    """保留旧规则名称与范围，旧图不匹配时使用新版按钮。"""

    def __init__(self, *, button: str, **kwargs):
        super().__init__(**kwargs)
        if button == 'confirm':
            front, back = (699, 418, 112, 42), (682, 400, 150, 78)
        elif button == 'cancel':
            front, back = (475, 418, 112, 42), (458, 400, 150, 78)
        else:
            raise ValueError(f'Unknown dialog button: {button}')
        self._button_v2 = RuleImage(
            roi_front=front, roi_back=back, threshold=0.85,
            method='Template matching',
            file=str(Path(__file__).resolve().parent / 'ui' / f'ui_ui_{button}_v2.png'),
        )

    def match(self, image, threshold=None):
        if super().match(image, threshold=threshold):
            # 从新版切回旧版时也恢复旧素材尺寸，点击区域始终对应当前帧。
            height, width = self.image.shape[:2]
            self.roi_front[2:4] = [width, height]
            return True
        requested_threshold = self.threshold if threshold is None else threshold
        new_threshold = max(0.85, requested_threshold)
        if self._button_v2.match(image, threshold=new_threshold):
            self.roi_front[:] = self._button_v2.roi_front
            return True
        return False
