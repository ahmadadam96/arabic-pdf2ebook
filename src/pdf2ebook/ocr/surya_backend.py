"""Optional Surya backend (modern neural OCR, better on degraded old prints).

Heavy: pulls torch and downloads model weights (~1-2 GB) on first run.
Install with:  pip install "arabic-pdf2ebook[surya]"
Note: Surya's model weights ship under a modified Open Rail-M license (free
for personal use and organizations under the revenue cap) — see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import OcrUnavailableError
from .base import OcrBackend, OcrLine, OcrPage, OcrWord

INSTALL_HINT = (
    'Surya is not installed. Install with:\n    pip install "arabic-pdf2ebook[surya]"\n'
    "First run downloads ~1-2 GB of model weights; a GPU makes it much faster."
)


class SuryaBackend(OcrBackend):
    name = "surya"

    def __init__(self) -> None:
        try:
            from surya.detection import DetectionPredictor
            from surya.recognition import RecognitionPredictor
        except ImportError as exc:
            raise OcrUnavailableError(INSTALL_HINT) from exc
        self._recognition = RecognitionPredictor()
        self._detection = DetectionPredictor()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import surya  # noqa: F401

            return True, ""
        except ImportError:
            return False, INSTALL_HINT

    def recognize(self, image_path: Path) -> OcrPage:
        return self.recognize_batch([image_path])[0]

    def recognize_batch(self, paths: list[Path]) -> list[OcrPage]:
        from PIL import Image

        images = [Image.open(p).convert("RGB") for p in paths]
        try:
            predictions = self._recognition(images, det_predictor=self._detection)
        finally:
            sizes = [img.size for img in images]
            for img in images:
                img.close()

        pages: list[OcrPage] = []
        for size, pred in zip(sizes, predictions):
            lines: list[OcrLine] = []
            for text_line in pred.text_lines:
                text = (text_line.text or "").strip()
                if not text:
                    continue
                x1, y1, x2, y2 = (int(v) for v in text_line.bbox)
                conf = float(getattr(text_line, "confidence", 0.9) or 0.9) * 100
                bbox = (x1, y1, max(1, x2 - x1), max(1, y2 - y1))
                # Surya returns whole lines; represent each as a single word.
                lines.append(OcrLine(words=[OcrWord(text, conf, bbox)], bbox=bbox))
            pages.append(OcrPage(page_no=-1, size=size, lines=lines))
        return pages
