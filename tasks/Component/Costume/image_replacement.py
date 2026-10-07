"""Replace costume templates without invalidating navigation references."""

from module.atom.image import RuleImage


def replace_image_asset(task, asset_name: str, replacement: RuleImage,
                        rp_roi_back: bool = True, *, force_reload: bool = False) -> None:
    """Keep the RuleImage identity, refreshing caches when its file changes.

    Global navigation pages hold the same rule objects as individual tasks.
    Updating only ``file`` leaves an already loaded image from the previous
    costume in use. Reset that image and its feature caches in place, while
    retaining caches for repeated replacements using the same file/method.
    ``force_reload`` repairs running workers whose old replacement already
    changed the filename but retained pixels from the previous template.
    """
    if not hasattr(task, asset_name):
        return
    target = getattr(task, asset_name)
    file_changed = target.file != replacement.file
    method_changed = target.method != replacement.method

    target.roi_front = list(replacement.roi_front)
    if rp_roi_back:
        target.roi_back = tuple(replacement.roi_back)
    target.threshold = replacement.threshold
    target.method = replacement.method
    target.file = replacement.file

    if file_changed or force_reload:
        target._image = None
        target._kp = None
        target._des = None
        # These properties cache themselves in __dict__ in addition to the
        # private feature fields. A cached name also refers to the old file.
        for name in ('name', 'kp', 'des'):
            target.__dict__.pop(name, None)
    if file_changed or method_changed or force_reload:
        target._match_init = False
        for name in ('is_template_match', 'is_sift_flann'):
            target.__dict__.pop(name, None)
