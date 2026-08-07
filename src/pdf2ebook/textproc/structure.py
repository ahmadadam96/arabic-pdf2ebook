"""Unified per-page structurer: an OcrPage → a list of (kind, text) elements.

The same structurer runs on OCR pages and on the PDF's embedded text layer
(both presented as an OcrPage). It detects, in order: line-level junk, list
items, poetry/verse blocks, prose paragraphs with font-tier headings, and
Quranic quotes — reusing the existing detectors, so the OCR path's output is
unchanged while the text-layer path finally gets real structure.

`kind` is one of: h1 | h2 | h3 | p | ul | ol | verse | quran.
"""

from __future__ import annotations

from typing import Callable

from ..ocr.base import OcrPage
from . import clean, poetry
from .headings import MAX_HEADING_WORDS, heading_tiers, looks_like_heading_text
from .lists import detect_list_items
from .paragraphs import page_paragraphs

DropLine = Callable[[str, bool], bool]  # (line_text, is_page_edge) -> drop?
Element = tuple[str, str]


def _kept_lines(page: OcrPage, keep_diacritics: bool, drop_line: DropLine) -> list:
    """Visible lines minus junk, using the page-edge rule the whole pipeline shares.

    `report._page_char_counts` measures coverage against exactly this set, so the
    two must stay in step: a line dropped here counts as junk, not as lost text.
    """
    visible = [ln for ln in page.lines if ln.text.strip()]
    return [ln for i, ln in enumerate(visible)
            if not drop_line(clean.normalize_arabic(ln.text, keep_diacritics),
                             i < 2 or i >= len(visible) - 2)]


def structure_page_flat(page: OcrPage, keep_diacritics: bool,
                        drop_line: DropLine) -> list[Element]:
    """One paragraph per kept line. No merging, no verse/list/heading detection.

    The fallback for when the smart builder above drops too much of a page: it
    loses structure but cannot lose text, because every line it keeps is emitted
    verbatim. `run_text_mode` swaps to it when a page's measured coverage falls
    below `report.PAGE_COVERAGE_MIN` — the safety net that makes the coverage
    number an actuator rather than just a warning.
    """
    out: list[Element] = []
    for line in _kept_lines(page, keep_diacritics, drop_line):
        text = clean.normalize_arabic(line.text, keep_diacritics)
        if text:
            out.append(("p", text))
    return out


def structure_page(page: OcrPage, keep_diacritics: bool, drop_line: DropLine,
                   body_size: float = 0.0) -> list[Element]:
    kept = _kept_lines(page, keep_diacritics, drop_line)
    filtered = OcrPage(page_no=page.page_no, size=page.size, lines=kept)

    verse_idx = poetry.detect_verse_lines(filtered)
    list_items = detect_list_items(filtered)
    tiers = heading_tiers(filtered, body_size)

    out: list[Element] = []
    n = len(filtered.lines)
    i = 0
    while i < n:
        # Poetry blocks: keep one bayt per line.
        if i in verse_idx:
            j = i
            while j < n and j in verse_idx:
                text = clean.normalize_arabic(poetry.verse_text(filtered.lines[j]), keep_diacritics)
                if text:
                    out.append(("verse", text))
                j += 1
            i = j
            continue
        # List runs: one element per item, marker stripped.
        if i in list_items:
            j = i
            while j < n and j in list_items:
                marker, raw = list_items[j]
                text = clean.normalize_arabic(raw, keep_diacritics)
                if text:
                    out.append((marker, text))
                j += 1
            i = j
            continue
        # Heading lines are standalone — never merged into a paragraph.
        if i in tiers:
            text = clean.normalize_arabic(filtered.lines[i].text, keep_diacritics)
            if text:
                out.append((tiers[i], text))
            i += 1
            continue
        # Prose run up to the next verse/list/heading line.
        j = i
        while (j < n and j not in verse_idx and j not in list_items and j not in tiers):
            j += 1
        prose = OcrPage(page_no=page.page_no, size=page.size, lines=filtered.lines[i:j])
        for par in page_paragraphs(prose):
            normalized = clean.normalize_arabic(par, keep_diacritics)
            if not normalized:
                continue
            # A short keyword line that the tiers missed (not centred/bigger) is
            # still a chapter heading; everything else is prose.
            if len(normalized.split()) <= MAX_HEADING_WORDS and looks_like_heading_text(normalized):
                kind = "h2"
            else:
                kind = "p"
            out.append((kind, normalized))
        i = j

    # Quranic quotes: restore ornate brackets when a citation cue is adjacent,
    # and honour explicit attribution lines like 'قرآن كريم' beneath a quote.
    final: list[Element] = []
    for idx, (kind, text) in enumerate(out):
        if kind == "p":
            next_text = out[idx + 1][1] if idx + 1 < len(out) else ""
            if poetry.QURAN_ATTRIBUTION_RE.match(next_text):
                final.append(("quran", poetry.attributed_quran(text)))
                continue
            window = " ".join(t for _, t in out[max(0, idx - 1): idx + 2])
            has_cue = bool(poetry.QURAN_CUE_RE.search(window))
            text, dominated = poetry.mark_quran(text, has_cue)
            if dominated:
                kind = "quran"
        final.append((kind, text))
    return final
