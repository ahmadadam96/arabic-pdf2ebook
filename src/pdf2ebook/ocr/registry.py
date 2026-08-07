"""OCR backend registry with lazy imports (Surya's torch stack must not load
unless requested)."""

from __future__ import annotations

from ..errors import InvalidOptionError
from .base import OcrBackend


def get_backend(name: str, lang: str = "ara", psm: int = 4) -> OcrBackend:
    if name == "tesseract":
        from .tesseract_backend import TesseractBackend

        return TesseractBackend(lang=lang, psm=psm)
    if name == "surya":
        from .surya_backend import SuryaBackend

        return SuryaBackend()
    raise InvalidOptionError(f"Unknown OCR engine '{name}'. Use 'tesseract' or 'surya'.")


def backend_status() -> dict[str, tuple[bool, str]]:
    from .tesseract_backend import TesseractBackend

    status = {"tesseract": TesseractBackend.is_available()}
    try:
        from .surya_backend import SuryaBackend

        status["surya"] = SuryaBackend.is_available()
    except Exception:
        status["surya"] = (False, 'Install with: pip install "arabic-pdf2ebook[surya]"')
    return status
