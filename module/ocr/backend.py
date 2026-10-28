"""Explicit OCR selection. Importing this module never loads an OCR model."""
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def managed_runtime(index_url=None):
    from types import SimpleNamespace
    from deploy.ocr_installer import prepare_optional
    return prepare_optional(SimpleNamespace(PypiMirror=index_url))


def create_text_system(backend=None, *, input_color=None, **kwargs):
    """Explicit argument wins over the process-only environment override."""
    if backend is None:
        backend = os.environ.get("OAS_OCR_BACKEND", "legacy")
    if backend == "legacy":
        from module.ocr.ppocr import TextSystem
        return TextSystem(**kwargs)
    if backend == "rapid-small":
        from module.ocr.rapidocr_adapter import RapidTextSystem
        if input_color is not None:
            kwargs["input_color"] = input_color
        return RapidTextSystem(**kwargs)
    raise ValueError(f"Unknown OCR backend {backend!r}; expected legacy or rapid-small")


def configured_text_system(config, **kwargs):
    """A new process can opt in without changing the shared deploy configuration."""
    if "OAS_OCR_BACKEND" not in os.environ:
        from module.ocr.account_runtime import current_account_runtime
        runtime = current_account_runtime()
        if runtime is not None:
            from module.ocr.isolated_rapid import IsolatedRapidTextSystem
            model = IsolatedRapidTextSystem(runtime["python"], **kwargs)
            # Production logger is already loaded by the script launcher.
            import sys
            logging_module = sys.modules.get("module.logger")
            if logging_module is not None:
                logging_module.logger.info(f"OCR backend: rapid-small (isolated CPU; account={runtime['account']})")
            return model
    if "OAS_OCR_BACKEND" not in os.environ and getattr(config, "AutoInstallRapidOcr", True):
        from module.ocr.account_runtime import SELECTION_FILE
        if not SELECTION_FILE.exists():
            python = managed_runtime(getattr(config, 'PypiMirror', None))
            if python:
                from module.ocr.isolated_rapid import IsolatedRapidTextSystem
                import logging
                logging.getLogger(__name__).info('OCR backend: rapid-small (managed isolated CPU)')
                return IsolatedRapidTextSystem(python, **kwargs)
    backend = os.environ.get("OAS_OCR_BACKEND", getattr(config, "OcrBackend", "legacy"))
    return create_text_system(backend, **kwargs)
