"""PDF access built on pypdfium2 (permissive license, prebuilt wheels).

Provides page rasterization for the OCR/image pipelines and direct text-layer
extraction so born-digital pages can skip OCR entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pypdfium2 as pdfium
from PIL import Image

if TYPE_CHECKING:
    from .ocr.base import OcrPage


class PdfError(RuntimeError):
    """Raised when the input PDF cannot be opened or rendered."""


_BOLD_WEIGHT = 600
_FORCE_BOLD_FLAG = 1 << 18  # PDF font-descriptor ForceBold bit (spec 5.7.1)


def _fontinfo_bold(textpage, pdfium_c, ci: int) -> float:
    """Fallback bold check for one char via FPDFText_GetFontInfo (name + flags).

    Used when per-char weight is unavailable (e.g. base-14 fonts): a font whose
    name contains 'bold' or whose descriptor sets ForceBold counts as bold.
    """
    if pdfium_c is None or not hasattr(pdfium_c, "FPDFText_GetFontInfo"):
        return 0.0
    try:
        import ctypes

        flags = ctypes.c_int(0)
        n = pdfium_c.FPDFText_GetFontInfo(textpage.raw, ci, None, 0, ctypes.byref(flags))
        name = ""
        if isinstance(n, int) and n > 0:
            buf = ctypes.create_string_buffer(n)
            pdfium_c.FPDFText_GetFontInfo(textpage.raw, ci, buf, n, ctypes.byref(flags))
            name = buf.raw.split(b"\x00", 1)[0].decode("latin-1", "ignore")
        if (flags.value & _FORCE_BOLD_FLAG) or "bold" in name.lower():
            return 1.0
    except Exception:
        return 0.0
    return 0.0


def _line_bold_ratio(textpage, pdfium_c, char_weights: list[int], idx_here: list[int]) -> float:
    """Fraction of a line's chars that are bold, from per-char weight (≥600).

    Falls back to a single FPDFText_GetFontInfo probe when no char reports a
    usable weight. Returns 0.0 when nothing is known (OCR pages leave it 0.0).
    """
    known = [char_weights[ci] for ci in idx_here if char_weights[ci] > 0]
    if known:
        return sum(1 for w in known if w >= _BOLD_WEIGHT) / len(known)
    if idx_here:
        return _fontinfo_bold(textpage, pdfium_c, idx_here[0])
    return 0.0


class PdfRasterizer:
    def __init__(self, pdf_path: Path):
        self.path = Path(pdf_path)
        if not self.path.exists():
            raise PdfError(f"File not found: {self.path}")
        if self.path.stat().st_size == 0:
            raise PdfError(f"File is empty (0 bytes): {self.path.name}")
        try:
            self._doc = pdfium.PdfDocument(str(self.path))
        except Exception as exc:  # pdfium raises its own error types
            raise PdfError(f"Cannot open PDF '{self.path.name}': {exc}") from exc

    @property
    def page_count(self) -> int:
        return len(self._doc)

    def page_size_pts(self, index: int) -> tuple[float, float]:
        page = self._doc[index]
        try:
            return page.get_size()
        finally:
            page.close()

    def render_page(self, index: int, dpi: int = 300, grayscale: bool = True) -> Image.Image:
        page = self._doc[index]
        try:
            bitmap = page.render(scale=dpi / 72.0, grayscale=grayscale)
            img = bitmap.to_pil()
        except Exception as exc:
            raise PdfError(f"Failed to render page {index + 1}: {exc}") from exc
        finally:
            page.close()
        if grayscale and img.mode != "L":
            img = img.convert("L")
        return img

    def extract_text(self, index: int) -> str:
        page = self._doc[index]
        try:
            textpage = page.get_textpage()
            try:
                return textpage.get_text_bounded() or ""
            finally:
                textpage.close()
        except Exception:
            return ""
        finally:
            page.close()

    def extract_text_page(self, index: int) -> "OcrPage | None":
        """Build an OcrPage from the page's embedded text layer.

        Each pdfium text rect becomes one OcrLine (bbox in top-left pixel space,
        conf=100), carrying a per-line font size in points so heading tiers can
        be detected the same way the OCR path does — only with exact sizes.
        Returns None when the page has no usable text layer.
        """
        from statistics import median

        from .ocr.base import OcrLine, OcrPage, OcrWord

        try:
            import pypdfium2.raw as pdfium_c
        except Exception:
            pdfium_c = None

        page = self._doc[index]
        try:
            w_pt, h_pt = page.get_size()
            textpage = page.get_textpage()
            try:
                n_chars = textpage.count_chars()
                char_boxes: list[tuple[float, float, float, float] | None] = []
                char_sizes: list[float] = []
                char_weights: list[int] = []
                for ci in range(n_chars):
                    try:
                        box = textpage.get_charbox(ci)
                    except Exception:
                        box = None
                    size = 0.0
                    weight = -1
                    if pdfium_c is not None:
                        try:
                            size = float(pdfium_c.FPDFText_GetFontSize(textpage.raw, ci))
                        except Exception:
                            size = 0.0
                        try:
                            weight = int(pdfium_c.FPDFText_GetFontWeight(textpage.raw, ci))
                        except Exception:
                            weight = -1
                    char_boxes.append(box)
                    char_sizes.append(size)
                    char_weights.append(weight)

                try:
                    n_rects = textpage.count_rects()
                except Exception:
                    n_rects = 0

                lines: list[OcrLine] = []
                for ri in range(n_rects):
                    try:
                        left, bottom, right, top = textpage.get_rect(ri)
                        text = textpage.get_text_bounded(
                            left=left, bottom=bottom, right=right, top=top) or ""
                    except Exception:
                        continue
                    text = text.strip()
                    if not text:
                        continue
                    # Real (non-newline) chars whose box-center lies in this rect.
                    idx_here = [
                        ci for ci, box in enumerate(char_boxes)
                        if box is not None and char_sizes[ci] > 1.0
                        and left <= (box[0] + box[2]) / 2 <= right
                        and bottom <= (box[1] + box[3]) / 2 <= top
                    ]
                    # Font size = median of those chars; fall back to the rect height.
                    sizes_here = [char_sizes[ci] for ci in idx_here]
                    font_size = median(sizes_here) if sizes_here else float(top - bottom)
                    bold = _line_bold_ratio(textpage, pdfium_c, char_weights, idx_here)
                    # PDF coords are bottom-left origin; convert to top-left pixels (1pt≈1px).
                    bbox = (round(left), round(h_pt - top),
                            round(right - left), round(top - bottom))
                    lines.append(OcrLine(words=[OcrWord(text, 100.0, bbox)],
                                         bbox=bbox, size=float(font_size), bold=bold))
                if not lines:
                    return None
                # Reading order: top-to-bottom, then right-to-left (Arabic).
                lines.sort(key=lambda ln: (ln.bbox[1], -ln.bbox[0]))
                return OcrPage(page_no=index, size=(round(w_pt), round(h_pt)), lines=lines)
            finally:
                textpage.close()
        except Exception:
            return None
        finally:
            page.close()

    def metadata(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("Title", "Author"):
            try:
                value = self._doc.get_metadata_value(key)
            except Exception:
                value = None
            if value:
                out[key.lower()] = value
        return out

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> "PdfRasterizer":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
