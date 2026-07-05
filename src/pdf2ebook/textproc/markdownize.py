"""In-memory Markdown pivot: structured elements ⇄ Book.

The conversion routes everything through a clean Markdown string that lives
only in memory (it is never written to disk). Structuring already happened
upstream, so Markdown here is a faithful serialization of typed
`(kind, text)` elements, parsed straight back into the Book model.

Internal conventions (we emit and parse both ends):
    # / ## / ###            headings h1 / h2 / h3
    plain line              paragraph
    - text                  unordered list item
    1. text                 ordered list item
    :::verse … :::          poetry block (one bayt per line)
    :::quran … :::          Quranic quote block
    ![scan](scans/…png)     a page kept as an image
    [^id]: text             footnote definition (linked from an inline [^id] ref)
    <!-- page:N -->         source page boundary (0-based)
A paragraph whose text would collide with a marker is backslash-escaped.

The parse side is deliberately more tolerant than the emit side so a human can
hand-edit the exported Markdown (`convert --markdown-out`) and rebuild it with
`pdf2ebook build`: `####`–`######` clamp to h3, `*`/`•` bullets and `١-`-style
ordinals are accepted, page comments are optional, and an optional leading
`--- … ---` front-matter block carries the title/author/language.
"""

from __future__ import annotations

import re
from typing import Callable

from ..book import Book, Chapter, PageImage, Paragraph
from . import clean
from .footnotes import footnote_id

Element = tuple[str, str]
Warn = Callable[[str], None]

FRONT_MATTER_KEYS = ("title", "author", "language")

_PAGE_RE = re.compile(r"^<!--\s*page:(\d+)\s*-->$")
# Emit side stays narrow; parse side (below) accepts more forms for hand edits.
_SCAN_RE = re.compile(r"^!\[[^\]]*\]\((.+)\)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^[-*•‣◦·∙●▪٭]\s+(.*)$")
_OL_RE = re.compile(r"^[0-9٠-٩]{1,3}\s*[.)\-]\s+(.*)$")
_FOOTNOTE_RE = re.compile(r"^\[\^([^\]\s]+)\]:\s*(.*)$")
_FENCE_RE = re.compile(r"^:::\s*(\w+)?\s*$")
_KNOWN_FENCES = ("verse", "quran")
# A paragraph whose first characters would be read back as a marker is escaped.
_NEEDS_ESCAPE = re.compile(
    r"^(#{1,6}\s|[-*•‣◦·∙●▪٭]\s|[0-9٠-٩]{1,3}\s*[.)\-]\s|:::|<!--|!\[|\[\^|\\)"
)

_HEAD_PREFIX = {"h1": "# ", "h2": "## ", "h3": "### "}
_HEAD_LEVEL = {"h1": 1, "h2": 2, "h3": 3}
_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")


# ---------------------------------------------------------------------------
# Front matter (a tiny hand-rolled YAML subset: flat `key: value` lines)
# ---------------------------------------------------------------------------

def emit_front_matter(meta: dict[str, str]) -> list[str]:
    """['---', 'title: …', 'author: …', 'language: …', '---', '']."""
    lines = ["---"]
    for key in FRONT_MATTER_KEYS:
        value = (meta.get(key) or "").strip()
        if value:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    return lines


def parse_front_matter(md: str) -> tuple[dict[str, str], str]:
    """Split an optional leading '---' block of 'key: value' lines from the body.

    The block must start at line 0 with exactly '---' and close with another
    '---'. Unknown keys are kept; surrounding quotes are stripped from values.
    No block (or an unclosed one) → ({}, md) — lenient, nothing is lost.
    """
    lines = md.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, md
    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        if ":" in lines[i]:
            key, _, value = lines[i].partition(":")
            key = key.strip().lower()
            if key:
                meta[key] = value.strip().strip("'\"")
    return {}, md  # unclosed block → treat the whole thing as body


# ---------------------------------------------------------------------------
# Emit (elements → Markdown lines)
# ---------------------------------------------------------------------------

def emit_page_break(page_no: int) -> str:
    return f"<!-- page:{page_no} -->"


def emit_scan(image_rel: str) -> str:
    return f"![scan]({image_rel})"


def emit_elements(elements: list[Element], page_no: int = 0) -> list[str]:
    lines: list[str] = []
    note_ordinal = 0
    i, n = 0, len(elements)
    while i < n:
        kind, text = elements[i]
        if kind in ("verse", "quran"):
            lines.append(f":::{kind}")
            j = i
            while j < n and elements[j][0] == kind:
                lines.append(elements[j][1])
                j += 1
            lines.append(":::")
            i = j
            continue
        if kind == "footnote":
            note_ordinal += 1
            lines.append(f"[^{footnote_id(page_no, note_ordinal)}]: {text}")
        elif kind in _HEAD_PREFIX:
            lines.append(_HEAD_PREFIX[kind] + text)
        elif kind == "ul":
            lines.append(f"- {text}")
        elif kind == "ol":
            lines.append(f"1. {text}")
        else:  # paragraph
            lines.append(f"\\{text}" if _NEEDS_ESCAPE.match(text) else text)
        i += 1
    return lines


# ---------------------------------------------------------------------------
# Parse (Markdown → Book)
# ---------------------------------------------------------------------------

_HEADING_KIND = {1: "h1", 2: "h2", 3: "h3"}  # 4..6 clamp to h3


def _parse_items(md: str, warn: Warn | None = None) -> list[tuple[int, str, str, str]]:
    """Flat list of (page_no, kind, text, note_id) in source order.

    `note_id` is set only on footnote-definition items; it is "" for everything
    else. Tolerant of hand edits (see the module docstring). `warn` receives a
    message for each anomaly recovered from (unknown/unclosed fence, stray close)
    so the caller can surface it — nothing is ever silently dropped.
    """
    def _warn(msg: str) -> None:
        if warn is not None:
            warn(msg)

    lines = md.split("\n")
    items: list[tuple[int, str, str, str]] = []
    cur_page = 0
    i, n = 0, len(lines)
    while i < n:
        text = lines[i].strip()
        if not text:
            i += 1
            continue
        m = _PAGE_RE.match(text)
        if m:
            cur_page = int(m.group(1))
            i += 1
            continue
        fence = _FENCE_RE.match(text)
        if fence:
            name = fence.group(1)
            if name in _KNOWN_FENCES:
                closed = False
                i += 1
                while i < n:
                    inner = lines[i].strip()
                    if inner == ":::":
                        closed = True
                        break
                    if inner:
                        items.append((cur_page, name, inner, ""))
                    i += 1
                if not closed:
                    _warn(f"unclosed :::{name} block — treated the rest as {name}")
                i += 1  # skip closing fence (or step past EOF)
                continue
            if name is None:
                _warn("stray ':::' fence line ignored")
                i += 1
                continue
            # Unknown fence (e.g. ':::note'): keep the line as text, don't swallow.
            _warn(f"unknown fence ':::{name}' kept as a paragraph")
            items.append((cur_page, "p", text, ""))
            i += 1
            continue
        if text.startswith("\\"):
            items.append((cur_page, "p", text[1:].strip(), ""))
            i += 1
            continue
        m = _FOOTNOTE_RE.match(text)
        if m:
            items.append((cur_page, "footnote", m.group(2).strip(), m.group(1)))
            i += 1
            continue
        m = _SCAN_RE.match(text)
        if m:
            items.append((cur_page, "img", m.group(1).strip(), ""))
            i += 1
            continue
        m = _HEADING_RE.match(text)
        if m:
            kind = _HEADING_KIND.get(len(m.group(1)), "h3")
            items.append((cur_page, kind, m.group(2).strip(), ""))
            i += 1
            continue
        m = _UL_RE.match(text)
        if m:
            items.append((cur_page, "ul", m.group(1).strip(), ""))
            i += 1
            continue
        m = _OL_RE.match(text)
        if m:
            items.append((cur_page, "ol", m.group(1).strip(), ""))
            i += 1
            continue
        items.append((cur_page, "p", text, ""))
        i += 1
    return items


def markdown_to_book(md: str, *, title: str, author: str, language: str,
                     split_every: int, on_warning: Warn | None = None) -> Book:
    items = _parse_items(md, on_warning)

    # Chapter break level = the shallowest heading tier present; deeper tiers
    # become in-body headings. Mirrors the heading-count≥2 vs split_every rule.
    present = [k for _, k, _, _ in items if k in _HEAD_LEVEL]
    break_kind = min(present, key=lambda k: _HEAD_LEVEL[k]) if present else None
    heading_count = sum(1 for _, k, _, _ in items if k == break_kind) if break_kind else 0
    use_headings = heading_count >= 2

    # Group items by source page (markers are emitted in order).
    pages: list[tuple[int, list[tuple[str, str, str]]]] = []
    for page_no, kind, txt, note_id in items:
        if not pages or pages[-1][0] != page_no:
            pages.append((page_no, []))
        pages[-1][1].append((kind, txt, note_id))

    chapters: list[Chapter] = []
    current = Chapter(title="")
    pages_in_chapter = 0

    def flush() -> None:
        nonlocal current, pages_in_chapter
        if current.elements:
            chapters.append(current)
        current = Chapter(title="")
        pages_in_chapter = 0

    for page_no, els in pages:
        for kind, txt, note_id in els:
            if kind == "img":
                current.elements.append(PageImage(page_no, txt))
            elif kind in _HEAD_LEVEL and use_headings and kind == break_kind:
                # Two-line headings arrive as consecutive break-level headings;
                # merge them into one chapter title instead of an empty chapter.
                # Only merge into a *break-level* heading — a deeper in-body
                # heading must not swallow the chapter heading that follows it.
                only_heading_so_far = (
                    len(current.elements) == 1
                    and isinstance(current.elements[0], Paragraph)
                    and current.elements[0].kind == break_kind
                )
                if only_heading_so_far:
                    first = current.elements[0]
                    assert isinstance(first, Paragraph)
                    merged = f"{first.text} {txt}".strip()
                    current.elements[0] = Paragraph(merged, first.kind)
                    current.title = clean.clean_heading(merged) or current.title
                else:
                    flush()
                    current.title = clean.clean_heading(txt) or txt
                    current.elements.append(Paragraph(txt, kind))
            else:
                current.elements.append(Paragraph(txt, kind, note_id))
        pages_in_chapter += 1
        if not use_headings and pages_in_chapter >= max(1, split_every):
            flush()
    flush()

    _relocate_footnotes_to_refs(chapters)

    if not chapters:
        chapters = [Chapter(title="", elements=[Paragraph("(لم يُتعرف على نص)", "p")])]
    return Book(title=title, author=author, language=language, chapters=chapters)


def _relocate_footnotes_to_refs(chapters: list[Chapter]) -> None:
    """Move each footnote definition into the chapter that cites it.

    A chapter can begin mid-page (a break-level heading), landing the body ref in
    one chapter and the page-end footnote definition in the next. Moving the
    definition to its ref's chapter keeps the EPUB backlink resolvable; an
    unreferenced note stays where it is.
    """
    ref_chapter: dict[str, int] = {}
    for ci, chapter in enumerate(chapters):
        for el in chapter.elements:
            if isinstance(el, Paragraph) and el.kind != "footnote":
                for m in _REF_RE.finditer(el.text):
                    ref_chapter.setdefault(m.group(1), ci)
    for ci, chapter in enumerate(chapters):
        keep: list[Paragraph | PageImage] = []
        for el in chapter.elements:
            target = (ref_chapter.get(el.note_id, ci)
                      if isinstance(el, Paragraph) and el.kind == "footnote" else ci)
            if target != ci:
                chapters[target].elements.append(el)
            else:
                keep.append(el)
        chapter.elements = keep
