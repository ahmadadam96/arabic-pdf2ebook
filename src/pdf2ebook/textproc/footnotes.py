"""Footnote detection: split a page's trailing footnote block off the body.

Scholarly Arabic books print footnotes in a smaller font at the bottom of the
page, each starting with a marker like (١) or ١- . We detect that block from the
per-line font size (text layer) or line height (OCR), strip it before structuring
so it is not mistaken for a list or prose, and carry the notes through the
Markdown pivot as `[^id]: text` definitions linked from inline `[^id]` refs.

Line-level geometry cannot see raised superscript baselines, so body-side
references are only rewritten for explicit bracketed/inline markers — (١)/[1] —
that occur exactly once on the page; anything else becomes an unreferenced note
at the chapter end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from .. import limits
from ..ocr.base import OcrLine, OcrPage

# Leading footnote marker: (١) ١- ١. ١) (1) 1- and superscript digits ¹²³…
MARKER_RE = re.compile(
    r"^\s*(?:\(\s*([0-9٠-٩]{1,3})\s*\)"
    r"|([0-9٠-٩]{1,3})\s*[-–.)]"
    r"|([¹²³⁰-⁹]{1,3}))\s+"
)
PAGE_NUMBER_RE = re.compile(r"^\s*[-–—(\[]?\s*[0-9٠-٩]{1,4}\s*[-–—)\]]?\s*$")

ZONE_TOP = 0.55          # a footnote block lives in the bottom 45% of the page
SIZE_RATIO_MAX = 0.85    # text layer: line size vs body_size
HEIGHT_RATIO_MAX = 0.80  # OCR: line height vs the page's median line height

_ARABIC_TO_LATIN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


@dataclass(frozen=True)
class Footnote:
    label: str  # the printed marker digits (as-is)
    text: str   # note text, marker stripped


def footnote_id(page_no: int, ordinal: int) -> str:
    """Stable id shared by a body ref and its note definition (1-based ordinal)."""
    return f"p{page_no + 1}-{ordinal}"


def _is_small(line: OcrLine, body_size: float, med_height: float) -> bool:
    if body_size > 0 and line.size > 0:
        return line.size <= body_size * SIZE_RATIO_MAX
    return med_height > 0 and line.bbox[3] <= med_height * HEIGHT_RATIO_MAX


def _is_bottom_page_number(line: OcrLine, zone_y: float) -> bool:
    """Whether a page-number footer can be ignored while locating notes."""
    return line.bbox[1] >= zone_y and bool(PAGE_NUMBER_RE.match(line.text))


def split_footnotes(page: OcrPage, body_size: float) -> tuple[OcrPage, list[Footnote]]:
    """Return (page without its footnote block, notes) — ([]) when none found."""
    visible = [ln for ln in page.lines if ln.text.strip()]
    if len(visible) < 2:
        return page, []
    page_h = page.size[1] or max((ln.bbox[1] + ln.bbox[3] for ln in visible), default=1)
    med_height = median(ln.bbox[3] for ln in visible) or 1
    zone_y = page_h * ZONE_TOP

    # Page numbers sit below some footnote blocks. Exclude a trailing page-number
    # suffix only while detecting notes, then retain it for structure_page's
    # ordinary edge-line filtering.
    end = len(visible)
    while end and _is_bottom_page_number(visible[end - 1], zone_y):
        end -= 1

    # Maximal trailing run of small lines sitting in the bottom zone.
    start = end
    for i in range(end - 1, -1, -1):
        ln = visible[i]
        if ln.bbox[1] < zone_y or not _is_small(ln, body_size, med_height):
            break
        start = i
    if start >= end:
        return page, []
    # The block must begin (topmost line) with a footnote marker.
    if not MARKER_RE.match(visible[start].text):
        return page, []

    notes: list[Footnote] = []
    for ln in visible[start:end]:
        m = MARKER_RE.match(ln.text)
        if m:
            label = next((g for g in m.groups() if g), "")
            notes.append(Footnote(label=label, text=ln.text[m.end():].strip()))
        elif notes:
            # A marker-less small line continues the previous note (wrapped text).
            prev = notes[-1]
            notes[-1] = Footnote(prev.label, f"{prev.text} {ln.text.strip()}".strip())
    if not notes:
        return page, []
    limits.check("max_footnotes_per_page", len(notes), limits.MAX_FOOTNOTES_PER_PAGE,
                 f"page {page.page_no + 1} — the footnote block detector latched "
                 "onto body text.")

    kept = OcrPage(page_no=page.page_no, size=page.size, lines=visible[:start] + visible[end:])
    return kept, notes


def rewrite_body_refs(elements: list[tuple[str, str]], notes: list[Footnote],
                      page_no: int) -> list[tuple[str, str]]:
    """Rewrite a *uniquely* occurring inline (L)/[L] body marker into `[^id]`.

    Only rewrites when the label occurs exactly once across the page's body
    elements; otherwise the note still renders at the chapter end without a ref.
    """
    result = list(elements)
    for ordinal, note in enumerate(notes, start=1):
        latin = note.label.translate(_ARABIC_TO_LATIN)
        if not latin:
            continue
        ref = f"[^{footnote_id(page_no, ordinal)}]"
        variants = "|".join({re.escape(note.label), re.escape(latin)})
        pattern = re.compile(r"[(\[]\s*(?:" + variants + r")\s*[)\]]")
        hits: list[tuple[int, int, int]] = []
        for idx, (kind, text) in enumerate(result):
            if kind == "footnote":
                continue
            for m in pattern.finditer(text):
                hits.append((idx, m.start(), m.end()))
        if len(hits) != 1:
            continue
        idx, s, e = hits[0]
        kind, text = result[idx]
        result[idx] = (kind, text[:s] + ref + text[e:])
    return result
