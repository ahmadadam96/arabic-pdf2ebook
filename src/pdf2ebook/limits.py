"""Fixed safety limits, applied identically to every conversion.

These are hard caps against runaway inputs and typos (a 2,000-page book at
``--dpi 1200``, a book whose every page fails OCR and is embedded as a JPEG).
Crossing one raises :class:`~pdf2ebook.errors.ResourceLimitError` — always;
a recovery path must never swallow it.

They are deliberately *not* configurable: real books sit orders of magnitude
below every value here, and a limit a user can raise is a limit that stops
bounding anything. Each value records the reasoning that sized it.
"""

from __future__ import annotations

from .errors import ResourceLimitError

# --- Source document ------------------------------------------------------

#: Beyond this a "book" is a corpus; the workdir and EPUB stop being usable.
MAX_PAGES = 5_000

#: Pixels rendered for one page at the requested DPI. 40 MP ≈ an A4 page at
#: 1700 DPI — far past anything OCR benefits from, and the point where a single
#: page bitmap costs ~40 MB grayscale.
MAX_RASTER_PIXELS = 40_000_000

#: Pixels rendered across the whole run. Bounds total workdir growth: 20 G
#: grayscale pixels ≈ 20 GB of PNG input at worst-case entropy.
MAX_TOTAL_RASTER_PIXELS = 20_000_000_000

# --- Structured content ---------------------------------------------------

#: Elements (paragraphs, headings, list items, scans) in one book. Sized well
#: above a 5,000-page book at ~40 elements/page.
MAX_ELEMENTS_PER_BOOK = 2_000_000

#: Notes detected in one page's footnote block. A printed page cannot carry
#: more; a larger count means the block detector latched onto body text.
MAX_FOOTNOTES_PER_PAGE = 200

# --- EPUB output ----------------------------------------------------------

#: Total bytes of embedded page scans in one EPUB volume. A book where OCR
#: fails on every page would otherwise embed every page and produce a
#: multi-gigabyte file that no reader can open.
MAX_EMBEDDED_SCAN_BYTES = 256 * 1024 * 1024

#: Reader guidance puts a comfortable XHTML part at ~300 KB; a book whose
#: headings were never detected can otherwise produce one multi-megabyte
#: chapter. Weight is measured by :func:`pdf2ebook.ocrmode._element_weight`.
MAX_CHAPTER_WEIGHT = 400_000
TARGET_CHAPTER_WEIGHT = 250_000

#: A page kept as an image is stored in the workdir at this height, then
#: re-encoded to JPEG at this height for embedding.
SCAN_MAX_HEIGHT = 1400
MAX_EMBED_HEIGHT = 1024
JPEG_QUALITY = 70

#: Image mode stores the page *as* the content, so it is encoded a little
#: less aggressively than a fallback scan embedded in a text book.
FIXED_LAYOUT_JPEG_QUALITY = 75


def check(limit_name: str, value: float, maximum: float, detail: str = "") -> None:
    """Raise :class:`ResourceLimitError` when *value* exceeds *maximum*.

    The message names the limit, both numbers and — where the caller supplies
    *detail* — the knob the user can turn to get under it.
    """
    if value <= maximum:
        return
    suffix = f" {detail}" if detail else ""
    raise ResourceLimitError(
        f"تجاوز الحد المسموح ({limit_name}) — resource limit exceeded ({limit_name}): "
        f"{value:,.0f} > {maximum:,.0f}.{suffix}"
    )
