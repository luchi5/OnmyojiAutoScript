"""Chess template matching adapted to runhey's local image implementation."""

from module.atom.image import RuleImage


class ChessImage(RuleImage):
    def match_all_any(self, image, threshold=None, roi=None, nms_threshold=0.3):
        """A smaller card ROI cannot contain a larger template.

        xy's image service enforced this contract before calling OpenCV.
        Keep that guard local to Chess when using runhey's in-process matcher.
        """
        source = self.corp(image, roi=roi)
        template = self.image
        if source.size == 0 or template.size == 0:
            return []
        if (template.shape[0] > source.shape[0]
                or template.shape[1] > source.shape[1]):
            return []
        return super().match_all_any(
            image, threshold=threshold, roi=roi, nms_threshold=nms_threshold,
        )
