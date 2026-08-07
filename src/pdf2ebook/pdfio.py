"""PDF access built on pypdfium2 (permissive license, prebuilt wheels).

Provides page rasterization for the OCR/image pipelines and direct text-layer
extraction so born-digital pages can skip OCR entirely.

Every recoverable failure below (a missing font metric, an unreadable text page)
degrades rather than raising — but it is never silent: each one is reported to
the optional ``on_degrade`` sink, which the pipeline drains into the page's
:class:`~pdf2ebook.report.PageReport` notes. A page whose font sizes went
missing structures differently from its neighbours, and the report has to be
able to say so.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

import pypdfium2 as pdfium
from PIL import Image

from . import limits
from .errors import EncryptedDocumentError, MalformedDocumentError, PdfError, SourceUnreadableError

if TYPE_CHECKING:
    from .ocr.base import OcrPage

__all__ = ["PdfError", "PdfRasterizer"]

# (page_index, note) — recoverable quality losses, surfaced in the report.
Degrade = Callable[[int, str], None]

_BOLD_WEIGHT = 600
_FORCE_BOLD_FLAG = 1 << 18  # PDF font-descriptor ForceBold bit (spec 5.7.1)
_FPDF_ERR_PASSWORD = 4


def _is_password_error(exc: Exception) -> bool:
    """Whether a pypdfium2 load failure means 'encrypted', not 'corrupt'.

    pdfium reports this through its global last-error code; the exception text
    is the fallback for builds/versions that do not expose the raw module.
    """
    try:
        import pypdfium2.raw as pdfium_c

        if int(pdfium_c.FPDF_GetLastError()) == _FPDF_ERR_PASSWORD:
            return True
    except Exception:
        pass
    return "password" in str(exc).lower()


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
    # pdfium is a C FFI boundary: narrowing this risks turning a recoverable
    # quirk into a crash on a real book. Recover, but let the caller record it.
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
    def __init__(self, pdf_path: Path, on_degrade: Degrade | None = None):
        self.path = Path(pdf_path)
        self._degrade = on_degrade
        if not self.path.exists():
            raise SourceUnreadableError(f"File not found — الملف غير موجود: {self.path}")
        if self.path.stat().st_size == 0:
            raise SourceUnreadableError(f"File is empty (0 bytes): {self.path.name}")
        try:
            self._doc = pdfium.PdfDocument(str(self.path))
        except Exception as exc:  # pdfium raises its own error types
            if _is_password_error(exc):
                raise EncryptedDocumentError(
                    f"الملف محمي بكلمة مرور — PDF is password-protected: {self.path.name}. "
                    "Remove the password with your PDF reader, then convert it."
                ) from exc
            raise MalformedDocumentError(
                f"Cannot open PDF '{self.path.name}': {exc}"
            ) from exc
        limits.check("max_pages", len(self._doc), limits.MAX_PAGES,
                     f"'{self.path.name}' — convert a subset with --pages.")

    def note_degraded(self, index: int, note: str) -> None:
        """Record a recoverable quality loss on page *index*."""
        if self._degrade is not None:
            self._degrade(index, note)

    @property
    def page_count(self) -> int:
        try:
            return len(self._doc)
        except Exception as exc:
            raise MalformedDocumentError(
                f"Cannot read the page count of '{self.path.name}': {exc}") from exc

    def _page(self, index: int):
        """Load one page, mapping pdfium's own error type into the taxonomy.

        Every page access goes through here: a damaged page object is the most
        common way a malformed PDF fails, and it must surface as a typed error
        rather than a raw PdfiumError from the C binding.
        """
        try:
            return self._doc[index]
        except Exception as exc:
            raise MalformedDocumentError(
                f"Cannot load page {index + 1} of '{self.path.name}': {exc}") from exc

    def page_size_pts(self, index: int) -> tuple[float, float]:
        page = self._page(index)
        try:
            return page.get_size()
        except Exception as exc:
            raise MalformedDocumentError(
                f"Cannot read the size of page {index + 1}: {exc}") from exc
        finally:
            page.close()

    def render_page(self, index: int, dpi: int = 300, grayscale: bool = True) -> Image.Image:
        scale = dpi / 72.0
        w_pt, h_pt = self.page_size_pts(index)
        limits.check("max_raster_pixels", w_pt * scale * h_pt * scale, limits.MAX_RASTER_PIXELS,
                     f"page {index + 1} at {dpi} DPI — lower --dpi.")
        page = self._page(index)
        try:
            bitmap = page.render(scale=scale, grayscale=grayscale)
            img = bitmap.to_pil()
        except Exception as exc:
            raise MalformedDocumentError(f"Failed to render page {index + 1}: {exc}") from exc
        finally:
            page.close()
        if grayscale and img.mode != "L":
            img = img.convert("L")
        return img

    def extract_text(self, index: int) -> str:
        # Degrades rather than raising: an unreadable page simply has no text
        # layer, so it routes to OCR. One damaged page must not fail the book.
        page = None
        try:
            page = self._page(index)
            textpage = page.get_textpage()
            try:
                return textpage.get_text_bounded() or ""
            finally:
                textpage.close()
        except Exception as exc:
            self.note_degraded(index, f"text layer unreadable ({exc}) — page treated as scanned")
            return ""
        finally:
            if page is not None:
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
            self.note_degraded(
                index, "pypdfium2.raw unavailable — font sizes and bold weights not extracted")

        # Degrades to None rather than raising, for the same reason as
        # extract_text: the page just goes to OCR instead.
        page = None
        try:
            page = self._page(index)
            w_pt, h_pt = page.get_size()
            textpage = page.get_textpage()
            try:
                n_chars = textpage.count_chars()
                char_boxes: list[tuple[float, float, float, float] | None] = []
                char_sizes: list[float] = []
                char_weights: list[int] = []
                missing_box = missing_size = missing_weight = 0
                for ci in range(n_chars):
                    try:
                        box = textpage.get_charbox(ci)
                    except Exception:
                        box = None
                        missing_box += 1
                    size = 0.0
                    weight = -1
                    if pdfium_c is not None:
                        try:
                            size = float(pdfium_c.FPDFText_GetFontSize(textpage.raw, ci))
                        except Exception:
                            size = 0.0
                            missing_size += 1
                        try:
                            weight = int(pdfium_c.FPDFText_GetFontWeight(textpage.raw, ci))
                        except Exception:
                            weight = -1
                            missing_weight += 1
                    char_boxes.append(box)
                    char_sizes.append(size)
                    char_weights.append(weight)

                # Losing these is not fatal, but it silently changes how the page
                # structures: without sizes the font-tier heading path degrades to
                # the coarser line-height one, and the bold signal goes inert.
                if missing_size and n_chars:
                    self.note_degraded(
                        index,
                        f"font size missing for {missing_size}/{n_chars} chars — "
                        "heading tiers fell back to line height")
                if missing_weight and n_chars:
                    self.note_degraded(
                        index,
                        f"font weight missing for {missing_weight}/{n_chars} chars — "
                        "bold heading signal inert")
                if missing_box and n_chars:
                    self.note_degraded(
                        index, f"glyph box missing for {missing_box}/{n_chars} chars")

                try:
                    n_rects = textpage.count_rects()
                except Exception as exc:
                    n_rects = 0
                    self.note_degraded(index, f"line rectangles unavailable ({exc})")

                lines: list[OcrLine] = []
                skipped_rects = 0
                for ri in range(n_rects):
                    try:
                        left, bottom, right, top = textpage.get_rect(ri)
                        text = textpage.get_text_bounded(
                            left=left, bottom=bottom, right=right, top=top) or ""
                    except Exception:
                        skipped_rects += 1
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
                if skipped_rects:
                    self.note_degraded(
                        index, f"{skipped_rects}/{n_rects} text lines unreadable and skipped")
                if not lines:
                    return None
                # Reading order: top-to-bottom, then right-to-left (Arabic).
                lines.sort(key=lambda ln: (ln.bbox[1], -ln.bbox[0]))
                return OcrPage(page_no=index, size=(round(w_pt), round(h_pt)), lines=lines)
            finally:
                textpage.close()
        except Exception as exc:
            self.note_degraded(
                index, f"text layer could not be read with geometry ({exc}) — page sent to OCR")
            return None
        finally:
            if page is not None:
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
