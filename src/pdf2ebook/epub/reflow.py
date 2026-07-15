"""Reflowable RTL EPUB builder for OCR'd / extracted Arabic text."""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image

from ..book import Book, Chapter, PageImage, Paragraph
from .opf import ManifestItem, build_ncx, build_nav, build_opf
from .templates import FONT_FACE_CSS, REFLOW_CSS, xhtml_page
from .validate import validate_epub
from .zipwriter import EpubContainer

MAX_EMBED_HEIGHT = 1024
JPEG_QUALITY = 70

_NOTEREF_RE = re.compile(r"\[\^([^\]\s]+)\]")
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def _num(n: int, language: str) -> str:
    if language.startswith("ar"):
        return "".join(_ARABIC_DIGITS[int(d)] for d in str(n))
    return str(n)


def _chapter_xhtml(chapter: Chapter, work_root: Path, image_names: dict[int, str],
                   language: str = "ar") -> str:
    # Footnotes render together at the chapter end; number them in order and map
    # each note id → its display number so inline refs resolve.
    notes = [el for el in chapter.elements
             if isinstance(el, Paragraph) and el.kind == "footnote"]
    note_order: dict[str, int] = {}
    note_anchors: dict[str, str] = {}
    for ordinal, note in enumerate(notes, start=1):
        if note.note_id and note.note_id not in note_order:
            note_order[note.note_id] = ordinal
            # Hex is an injective UTF-8 encoding, so user-provided Markdown IDs
            # never become XHTML attribute syntax and distinct IDs cannot collide.
            note_anchors[note.note_id] = f"fn-{note.note_id.encode('utf-8').hex()}"
    body = [el for el in chapter.elements
            if not (isinstance(el, Paragraph) and el.kind == "footnote")]
    referenced: set[str] = set()   # notes with at least one body ref (→ backlink)
    ref_ided: set[str] = set()     # notes that already own the ref-fn-{id} anchor

    def render_text(text: str) -> str:
        def repl(m: re.Match) -> str:
            nid = m.group(1)
            if nid not in note_order:
                return ""  # ref with no matching note in this chapter → drop marker
            referenced.add(nid)
            num = _num(note_order[nid], language)
            anchor = note_anchors[nid]
            # Only the first citation of a note carries the id, so a note cited
            # more than once (hand-edited Markdown) never emits a duplicate id.
            if nid in ref_ided:
                return (f'<a epub:type="noteref" class="noteref" '
                        f'href="#{anchor}"><sup>{num}</sup></a>')
            ref_ided.add(nid)
            return (f'<a epub:type="noteref" class="noteref" href="#{anchor}" '
                    f'id="ref-{anchor}"><sup>{num}</sup></a>')

        parts: list[str] = []
        last = 0
        for match in _NOTEREF_RE.finditer(text):
            parts.append(escape(text[last:match.start()]))
            parts.append(repl(match))
            last = match.end()
        parts.append(escape(text[last:]))
        return "".join(parts)

    parts: list[str] = []
    n = len(body)
    i = 0
    while i < n:
        el = body[i]
        if isinstance(el, Paragraph):
            if el.kind in ("ul", "ol"):
                tag = el.kind
                items: list[str] = []
                while i < n and isinstance(body[i], Paragraph) and body[i].kind == tag:
                    items.append(f"<li>{render_text(body[i].text)}</li>")
                    i += 1
                parts.append(f"    <{tag}>{''.join(items)}</{tag}>")
                continue
            if el.kind in ("h1", "h2", "h3"):
                parts.append(f"    <{el.kind}>{render_text(el.text)}</{el.kind}>")
            elif el.kind in ("verse", "quran"):
                parts.append(f'    <p class="{el.kind}">{render_text(el.text)}</p>')
            else:
                parts.append(f"    <p>{render_text(el.text)}</p>")
        elif isinstance(el, PageImage):
            name = image_names[el.page_no]
            parts.append(
                f'    <figure class="scan"><img src="../{name}" alt="صفحة {el.page_no + 1}"/>'
                f"<figcaption>صفحة {el.page_no + 1}</figcaption></figure>"
            )
        i += 1

    if notes:
        parts.append('    <div class="footnotes"><hr/>')
        note_instances: dict[str, int] = {}
        for ordinal, note in enumerate(notes, start=1):
            nid = note.note_id
            num = _num(ordinal, language)
            anchor = ""
            if nid:
                note_instances[nid] = note_instances.get(nid, 0) + 1
                anchor = note_anchors[nid]
                if note_instances[nid] > 1:
                    anchor = f"{anchor}-{note_instances[nid]}"
            # Backlink only when a body ref actually points here.
            if nid and nid in referenced and note_instances[nid] == 1:
                label = f'<a href="#ref-{anchor}" epub:type="backlink">{num}.</a>'
            else:
                label = f"{num}."
            fid = f' id="{anchor}"' if anchor else ""
            parts.append(
                f'      <aside epub:type="footnote" class="footnote"{fid}>'
                f"<p>{label} {render_text(note.text)}</p></aside>"
            )
        parts.append("    </div>")
    return "\n".join(parts)


def _encode_scan(src: Path) -> bytes:
    with Image.open(src) as img:
        img = img.convert("L")
        if img.height > MAX_EMBED_HEIGHT:
            factor = MAX_EMBED_HEIGHT / img.height
            img = img.resize((max(1, int(img.width * factor)), MAX_EMBED_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()


def build_reflow_epub(
    book: Book,
    out_path: Path,
    work_root: Path,
    font_files: list[Path] | None = None,
) -> Path:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    items: list[ManifestItem] = [
        ManifestItem("nav", "nav.xhtml", "application/xhtml+xml", "nav"),
        ManifestItem("ncx", "toc.ncx", "application/x-dtbncx+xml"),
        ManifestItem("css", "styles/style.css", "text/css"),
    ]
    spine_ids: list[str] = []
    toc: list[tuple[str, str]] = []

    css = REFLOW_CSS
    font_files = font_files or []

    with EpubContainer(out_path) as epub:
        for font in font_files:
            family = "Amiri" if "amiri" in font.name.lower() else "Scheherazade New"
            css = FONT_FACE_CSS.format(family=family, filename=font.name) + css
            items.append(ManifestItem(
                f"font-{font.stem.lower()}", f"fonts/{font.name}",
                "application/font-sfnt",
            ))
            epub.add_file(f"OEBPS/fonts/{font.name}", font)
        epub.add("OEBPS/styles/style.css", css)

        # Collect and embed all scan images referenced by the book. The scan
        # of the book's first page (the cover, when kept as an image) is
        # declared as the EPUB cover so readers show a thumbnail.
        image_names: dict[int, str] = {}
        cover_id: str | None = None
        first_image_page = min(
            (el.page_no for ch in book.chapters for el in ch.elements
             if isinstance(el, PageImage)), default=None,
        )
        for chapter in book.chapters:
            for el in chapter.elements:
                if isinstance(el, PageImage) and el.page_no not in image_names:
                    src = work_root / el.image_path
                    name = f"images/scan_{el.page_no + 1:04d}.jpg"
                    item_id = f"scan{el.page_no + 1:04d}"
                    is_cover = el.page_no == first_image_page and el.page_no <= 1
                    if is_cover:
                        cover_id = item_id
                    epub.add(f"OEBPS/{name}", _encode_scan(src))
                    items.append(ManifestItem(item_id, name, "image/jpeg",
                                              "cover-image" if is_cover else ""))
                    image_names[el.page_no] = name

        for i, chapter in enumerate(book.chapters):
            name = f"text/chap_{i + 1:03d}.xhtml"
            body = _chapter_xhtml(chapter, work_root, image_names, book.language)
            heading = f"    <h2>{escape(chapter.title)}</h2>\n" if chapter.title else ""
            first = chapter.elements[0] if chapter.elements else None
            starts_with_heading = isinstance(first, Paragraph) and first.kind in ("h1", "h2", "h3")
            if not starts_with_heading:
                body = heading + body
            epub.add(f"OEBPS/{name}", xhtml_page(chapter.title or book.title, body,
                                                 book.language, css_href="../styles/style.css"))
            chap_id = f"c{i + 1:03d}"
            items.append(ManifestItem(chap_id, name, "application/xhtml+xml"))
            spine_ids.append(chap_id)
            toc.append((chapter.title or f"فصل {i + 1}", name))

        epub.add("OEBPS/nav.xhtml", build_nav(book.title, book.language, toc))
        epub.add("OEBPS/toc.ncx", build_ncx(book.title, book_id, toc))
        epub.add("OEBPS/content.opf",
                 build_opf(book.title, book.author, book.language, items, spine_ids,
                           book_id=book_id, cover_id=cover_id))
    validate_epub(out_path)
    return out_path
