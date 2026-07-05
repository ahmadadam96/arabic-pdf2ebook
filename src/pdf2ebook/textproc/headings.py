"""Heading detection by font-size / line-height tiers.

pdfmarkdown.app-style structuring: a heading is recognised from its size
relative to the body text. When an exact font size is available (the PDF's
embedded text layer), tiers are crisp; for OCR pages we fall back to the
line-height-vs-page-median heuristic the project already used.

A short line qualifies as a heading when it satisfies any of four signals:
bigger-and-centred, keyword-and-centred, keyword-and-bigger, or bold. Arabic has
no capitalisation, so a bold body-size line is a strong standalone heading cue;
the bold signal is inert on OCR pages (which carry no weight data).
"""

from __future__ import annotations

import re
from statistics import median

from ..ocr.base import OcrPage

HEADING_WORD_RE = re.compile(
    r"^\s*(الباب|الفصل|باب|فصل|مقدمة|المقدمة|تمهيد|خاتمة|الخاتمة|فهرس|المراجع|ملحق)"
)
MAX_HEADING_WORDS = 8


def looks_like_heading_text(text: str) -> bool:
    words = text.split()
    return bool(words) and len(words) <= MAX_HEADING_WORDS and bool(HEADING_WORD_RE.match(text))


def _tier_font(ratio: float) -> str:
    """Heading tier from an exact font-size ratio (digital text layer)."""
    if ratio >= 1.7:
        return "h1"
    if ratio >= 1.35:
        return "h2"
    return "h3"


def heading_tiers(page: OcrPage, body_size: float = 0.0) -> dict[int, str]:
    """Map non-empty line index → heading tier ("h1" | "h2" | "h3").

    With `body_size > 0` and per-line font sizes (digital text layer), tiers use
    the exact size ratio. Otherwise they use line height vs the page's median,
    with the same `> 1.35×` threshold and a single "h2" tier the OCR path used
    before — so OCR results don't change.
    """
    lines = [ln for ln in page.lines if ln.text.strip()]
    if not lines:
        return {}
    med_height = median(ln.bbox[3] for ln in lines) or 1
    page_width = page.size[0] or 1
    out: dict[int, str] = {}
    for i, ln in enumerate(lines):
        text = ln.text.strip()
        words = len(text.split())
        if not text or words > MAX_HEADING_WORDS:
            continue
        bold = False
        if body_size > 0 and ln.size > 0:
            ratio = ln.size / body_size
            big = ratio >= 1.15
            tier = _tier_font(ratio)
            # Bold at (roughly) body size or larger is a heading on its own —
            # only when real weight data exists, so OCR pages are unaffected.
            bold = ln.bold >= 0.6 and ln.size >= body_size * 0.98
        else:
            ratio = ln.bbox[3] / med_height
            big = ratio > 1.35
            tier = "h2"  # OCR: one heading tier, preserving prior behaviour
        center = ln.bbox[0] + ln.bbox[2] / 2
        centered = abs(center - page_width / 2) < page_width * 0.12
        keyword = bool(HEADING_WORD_RE.match(text))
        if (big and centered) or (keyword and centered) or (keyword and big) or bold:
            out[i] = tier if big else ("h2" if keyword else _tier_font(ratio))
    return out
