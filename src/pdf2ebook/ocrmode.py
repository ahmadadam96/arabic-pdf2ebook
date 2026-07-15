"""OCR / auto mode: scanned pages → recognized text → reflowable RTL EPUB.

Per-page decision tree (auto mode):
    1. real PDF text layer (> TEXT_LAYER_SHORT_MIN_CHARS, healthy) → use it directly
    2. page looks like a photo/map                  → keep as cleaned image
    3. OCR; low-confidence pages get a rescue pass  → text
    4. still below --min-conf                       → keep as cleaned image

Junk removal happens at the *line* level before paragraphs are built, so an
OCR-mangled watermark can never be merged into a real paragraph.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from .book import Book, Chapter, PageImage, Paragraph
from .config import EpubMeta, PipelineOptions, parse_page_range
from .epub.reflow import build_reflow_epub
from .ocr.base import OcrPage
from .ocr.registry import get_backend
from .pdfio import PdfRasterizer
from .pipeline import (ConversionResult, Progress, default_title, default_work_dir,
                       ensure_output_outside_workdir, extract_pages, file_fingerprints)
from .preprocess import ops
from .preprocess.pipeline import detect_image_page, preprocess_for_image, preprocess_for_ocr
from .report import (ROUTE_BLANK, ROUTE_IMAGE, ROUTE_OCR, ROUTE_RESCUED, ROUTE_TEXT,
                     ConversionReport, PageReport, collect_warnings, coverage_key)
from .textproc import clean, footnotes
from .textproc.markdownize import (emit_elements, emit_front_matter, emit_page_break,
                                   emit_scan, markdown_to_book)
from .textproc.paragraphs import merge_page_boundary
from .textproc.structure import structure_page
from .workdir import WorkDir

TEXT_LAYER_MIN_CHARS = 200
TEXT_LAYER_SHORT_MIN_CHARS = 20
MIN_WORDS_PER_PAGE = 15
SCAN_MAX_HEIGHT = 1400
BAD_GLYPH_MAX = 0.05  # per page: above this share of U+FFFD/PUA chars → OCR instead

DropLine = Callable[[str, bool], bool]  # (line_text, is_page_edge) -> drop?


@dataclass
class PageData:
    index: int
    kind: str  # "text" | "ocr" | "image"
    payload: object = None  # OcrPage | None (text + ocr pages both carry an OcrPage)
    elements: list[tuple[str, str]] = field(default_factory=list)  # (kind, text)
    image_rel: str | None = None
    mean_conf: float = 100.0
    route: str = ""    # report route: text-layer | ocr | ocr-rescued | image-kept | blank
    reason: str = ""   # human-readable why-this-route

    def line_texts(self) -> list[str]:
        if self.kind in ("ocr", "text") and self.payload is not None:
            return [ln.text for ln in self.payload.lines if ln.text.strip()]
        return []


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def preprocess_ocr_pages(work: WorkDir, raw_paths: list[Path], force: bool = False,
                         progress: Progress | None = None) -> dict[str, Path]:
    settings = {"raw": file_fingerprints(raw_paths), "v": 2}
    if force:
        work.invalidate("pre-ocr")
    work.begin_stage("pre-ocr", settings)
    out: dict[str, Path] = {}
    for n, src in enumerate(raw_paths):
        dest = work.root / "pre-ocr" / src.name
        if not dest.exists():
            with Image.open(src) as img:
                preprocess_for_ocr(img).save(dest, format="PNG")
        out[src.name] = dest
        if progress:
            progress("preprocess", n + 1, len(raw_paths))
    return out


def _alternate_ocr_image(raw_path: Path) -> Image.Image:
    """Different binarization recipe for the rescue pass."""
    with Image.open(raw_path) as img:
        gray = ops.from_pil(img)
    gray = ops.deskew(gray)
    gray = ops.clahe(gray)
    binary = ops.otsu(gray)
    binary = ops.autocrop(binary)
    binary = ops.upscale_if_small(binary)
    return ops.to_pil(binary)


def _make_scan_image(work: WorkDir, raw_path: Path, index: int) -> str:
    """Cleaned grayscale rendition of a page kept as an image; returns rel path."""
    scans = work.stage_dir("scans")
    dest = scans / WorkDir.page_name(index, "png")
    if not dest.exists():
        with Image.open(raw_path) as img:
            cleaned = preprocess_for_image(img, 0, 0, style="gray")
            if cleaned.height > SCAN_MAX_HEIGHT:
                factor = SCAN_MAX_HEIGHT / cleaned.height
                cleaned = cleaned.resize(
                    (max(1, int(cleaned.width * factor)), SCAN_MAX_HEIGHT), Image.LANCZOS)
            cleaned.save(dest, format="PNG")
    # Posix separators so the ![scan](scans/…) ref is portable across platforms.
    return dest.relative_to(work.root).as_posix()


def _export_markdown(markdown: str, dest: Path, meta: EpubMeta, work_root: Path) -> None:
    """Write front matter + body to `dest` and copy every referenced scan into a
    sibling `scans/` folder, so the exported Markdown is self-contained and can
    be hand-edited then rebuilt with `pdf2ebook build`. Body bytes are unchanged.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    front = emit_front_matter({"title": meta.title, "author": meta.author,
                               "language": meta.language})
    dest.write_text("\n".join(front) + "\n" + markdown, encoding="utf-8")
    for match in re.finditer(r"^!\[[^\]]*\]\((scans/[^)]+)\)\s*$", markdown, re.MULTILINE):
        rel = match.group(1)
        src = work_root / rel
        if not src.exists():
            continue
        out = dest.parent / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)


def finalize_chapters(book: Book) -> None:
    """Split oversized chapters and give any untitled chapter a default name.

    Shared by the PDF pipeline and `pdf2ebook build` so both produce identical
    chapter structure from the same Book.
    """
    book.chapters = _split_giant_chapters(book.chapters)
    for i, chapter in enumerate(book.chapters):
        if not chapter.title:
            chapter.title = f"قسم {i + 1}"


def apply_preshape(book: Book) -> None:
    """Bake Arabic letter-joining into every title/paragraph (simple readers).

    Only touches final Arabic glyphs, never markup, so it must run after the
    Markdown round-trip. Shared by the PDF pipeline and `pdf2ebook build`.
    """
    from .textproc.preshape import preshape_text

    for chapter in book.chapters:
        chapter.title = preshape_text(chapter.title)
        for el in chapter.elements:
            if isinstance(el, Paragraph):
                el.text = preshape_text(el.text)


# ---------------------------------------------------------------------------
# Chapter weighting / giant-chapter splitting
# ---------------------------------------------------------------------------

def _select_text_layer(samples: dict[int, str],
                       text_layer_opt: str) -> tuple[set[int], dict[int, str]]:
    """Decide which sampled pages may use their embedded text layer.

    Returns (allowed page indices, {excluded index: reason}).
    - 'always': keep every sampled page (no gating).
    - otherwise: per-page corruption and bad-glyph gates drop bad pages to OCR
      while keeping healthy pages, including sparse heading/front-matter pages.
    """
    if not samples:
        return set(), {}
    if text_layer_opt == "always":
        return set(samples), {}
    allowed: set[int] = set()
    excluded: dict[int, str] = {}
    for idx, text in samples.items():
        if clean.looks_corrupted_arabic(text):
            excluded[idx] = "text layer corrupted (ligature loss) — العتاد النصي تالف، سيُستخدم OCR"
            continue
        ratio = clean.bad_glyph_ratio(text)
        if ratio > BAD_GLYPH_MAX:
            excluded[idx] = f"bad glyphs {ratio:.0%} (non-Unicode font) — خط غير قياسي، سيُستخدم OCR"
        else:
            allowed.add(idx)
    return allowed, excluded


def _image_route_and_reason(ocr_page: OcrPage, image_page: bool | None,
                            foreign_reason: str, min_conf: float) -> tuple[str, str]:
    """Route + honest reason for a page that fell back to an embedded image.

    `image_page` is True (detected photo/map), False (OCR ran, produced nothing),
    or None (old cache, unknown). `foreign_reason` is set when the arabic-ratio
    guard rejected an otherwise-good page.
    """
    if image_page:
        return ROUTE_IMAGE, "photo / map (no text)"
    if ocr_page.word_count == 0:
        if image_page is False:
            return ROUTE_BLANK, "blank page"
        return ROUTE_IMAGE, "no text recovered"
    if foreign_reason:
        return ROUTE_IMAGE, foreign_reason
    if ocr_page.mean_conf < min_conf:
        return ROUTE_IMAGE, f"low confidence {ocr_page.mean_conf:.0f} < {min_conf:.0f}"
    # Failed only the word-count arm — not a confidence problem.
    return ROUTE_IMAGE, f"too few words {ocr_page.word_count} < {MIN_WORDS_PER_PAGE}"


def _page_char_counts(page: OcrPage, keep_diacritics: bool,
                      drop_line: DropLine) -> tuple[int, int]:
    """(kept_chars, dropped_chars) for coverage, mirroring structure_page's filter.

    Uses the same visible-line set and edge rule (first/last two lines) as
    structure_page, so junk that structure_page drops counts as dropped — not as
    lost coverage.
    """
    visible = [ln for ln in page.lines if ln.text.strip()]
    kept = dropped = 0
    for i, ln in enumerate(visible):
        chars = len(coverage_key(ln.text))
        norm = clean.normalize_arabic(ln.text, keep_diacritics)
        edge = i < 2 or i >= len(visible) - 2
        if drop_line(norm, edge):
            dropped += chars
        else:
            kept += chars
    return kept, dropped


def _modal_body_size(pages: dict[int, OcrPage]) -> float:
    """Most common line font size across text-layer pages (anchors heading tiers)."""
    from collections import Counter

    counts: Counter[int] = Counter()
    for page in pages.values():
        for ln in page.lines:
            if ln.size > 0 and ln.text.strip():
                counts[round(ln.size)] += 1
    return float(counts.most_common(1)[0][0]) if counts else 0.0


def _element_weight(el: Paragraph | PageImage) -> int:
    """Approximate EPUB byte cost: an embedded scan ≈ 150k chars of text."""
    return len(el.text) if isinstance(el, Paragraph) else 150_000


# Readers struggle with giant chapter files (spec guidance ~300 KB/XHTML);
# huge undetected-heading books can produce multi-megabyte chapters.
MAX_CHAPTER_WEIGHT = 400_000
TARGET_CHAPTER_WEIGHT = 250_000


def _split_giant_chapters(chapters: list[Chapter]) -> list[Chapter]:
    from .pipeline import _volume_chunks

    out: list[Chapter] = []
    for chapter in chapters:
        weights = [_element_weight(el) for el in chapter.elements]
        total = sum(weights)
        if total <= MAX_CHAPTER_WEIGHT or len(chapter.elements) <= 3:
            out.append(chapter)
            continue
        parts = max(2, round(total / TARGET_CHAPTER_WEIGHT))
        for k, els in enumerate(_volume_chunks(chapter.elements, parts, weights)):
            part_title = chapter.title if k == 0 else f"{chapter.title} ({k + 1})"
            out.append(Chapter(part_title, els))
    return out


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_text_mode(
    pdf_path: Path,
    out_path: Path,
    opts: PipelineOptions,
    progress: Progress | None = None,
) -> ConversionResult:
    result = ConversionResult()
    work = WorkDir(opts.work_dir or default_work_dir(pdf_path), pdf_path)
    ensure_output_outside_workdir(out_path, work)
    extra_patterns = clean.compile_extra_patterns(opts.ocr.strip_patterns)

    with PdfRasterizer(pdf_path) as pdf:
        indices = parse_page_range(opts.pages, pdf.page_count)
        result.pages_total = len(indices)
        if not opts.meta.author:
            opts.meta.author = pdf.metadata().get("author", "")
        force_extract = opts.force in ("extract", "all")
        raw_paths = extract_pages(pdf, work, indices, opts.dpi, force_extract, progress)
        raw_by_index = dict(zip(indices, raw_paths))

        # 1. Which pages can use the PDF's own text layer? (auto mode only)
        #    Kept pages are extracted *with geometry* (an OcrPage with per-line
        #    font sizes) so they get the same structuring as OCR pages. Pages
        #    with a broken/non-Unicode layer are gated out (see _select_text_layer).
        direct_pages: dict[int, OcrPage] = {}
        text_layer_reasons: dict[int, str] = {}
        if opts.mode == "auto" and opts.text_layer != "never":
            samples: dict[int, str] = {}
            for idx in indices:
                stripped = pdf.extract_text(idx).strip()
                if len(stripped) >= TEXT_LAYER_SHORT_MIN_CHARS:
                    samples[idx] = stripped
            allowed, text_layer_reasons = _select_text_layer(samples, opts.text_layer)
            for idx in allowed:
                page = pdf.extract_text_page(idx)
                if page and page.lines:
                    direct_pages[idx] = page

    # Body font size (the modal line size across text-layer pages) anchors the
    # heading tiers; OCR pages have no font size and fall back to line height.
    body_size = _modal_body_size(direct_pages)

    # 2. Recognition pass: text layer / OCR / image per page.
    ocr_indices = [i for i in indices if i not in direct_pages]
    force_pre = opts.force in ("preprocess", "all") or force_extract
    pre_paths = preprocess_ocr_pages(
        work, [raw_by_index[i] for i in ocr_indices], force_pre, progress
    ) if ocr_indices else {}

    backend = None
    pages_data: list[PageData] = []
    ocr_stage = f"ocr-{opts.ocr.engine}"
    ocr_settings = {
        "engine": opts.ocr.engine,
        "lang": opts.ocr.lang,
        "psm": opts.ocr.psm,
        "rescue": opts.ocr.rescue,
        "pre": file_fingerprints(list(pre_paths.values())),
        "v": 2,
    }
    if opts.force in ("ocr", "all"):
        work.invalidate(ocr_stage)
    work.begin_stage(ocr_stage, ocr_settings)

    rescue_threshold = opts.ocr.min_conf + 15

    for n, idx in enumerate(indices):
        if idx in direct_pages:
            pages_data.append(PageData(index=idx, kind="text", payload=direct_pages[idx],
                                       route=ROUTE_TEXT, reason="embedded text layer"))
            result.pages_direct_text += 1
            continue

        raw_path = raw_by_index[idx]
        cache = work.page_path(ocr_stage, idx, "json")

        if cache.exists():
            blob = cache.read_text(encoding="utf-8")
            ocr_page = OcrPage.from_json(blob)
            meta = json.loads(blob)
            rescued = bool(meta.get("rescued", False))
            image_page = meta.get("image_page", None)  # None = unknown (old cache)
        else:
            rescued = False
            with Image.open(raw_path) as raw_img:
                image_page = detect_image_page(raw_img)
            if image_page:
                ocr_page = OcrPage(page_no=idx, size=(0, 0), lines=[])
            else:
                if backend is None:
                    backend = get_backend(opts.ocr.engine, lang=opts.ocr.lang, psm=opts.ocr.psm)
                ocr_page = backend.recognize(pre_paths[raw_path.name])
                ocr_page.page_no = idx
                if opts.ocr.rescue and ocr_page.mean_conf < rescue_threshold:
                    alt_path = work.stage_dir("pre-ocr-alt") / raw_path.name
                    if not alt_path.exists():
                        _alternate_ocr_image(raw_path).save(alt_path, format="PNG")
                    retry = backend.recognize(alt_path)
                    retry.page_no = idx
                    if retry.mean_conf > ocr_page.mean_conf:
                        ocr_page = retry
                        rescued = True
            # Persist routing flags alongside the page; OcrPage.from_json ignores
            # these extra keys, so old and new caches interoperate both ways.
            payload = json.loads(ocr_page.to_json())
            payload["rescued"] = rescued
            payload["image_page"] = image_page
            cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        good = (ocr_page.mean_conf >= opts.ocr.min_conf
                and ocr_page.word_count >= MIN_WORDS_PER_PAGE)
        reason = ""
        if good:
            # Foreign-script pages (Latin bibliographies, dot-leader indexes)
            # OCR into glyph soup under an Arabic model — keep them as images.
            # Calibrated on real books: genuine Arabic pages score >= 0.74,
            # an OCR'd French bibliography scored 0.60.
            page_text = " ".join(ln.text for ln in ocr_page.lines)
            if clean.arabic_ratio(page_text) < 0.65:
                good = False
                reason = f"arabic-ratio {clean.arabic_ratio(page_text):.0%} (foreign script)"
        if good:
            route = ROUTE_RESCUED if rescued else ROUTE_OCR
            pages_data.append(PageData(index=idx, kind="ocr", payload=ocr_page,
                                       mean_conf=ocr_page.mean_conf, route=route,
                                       reason="rescued OCR" if rescued else "OCR"))
            result.pages_ocr += 1
            result.mean_confidences.append(ocr_page.mean_conf)
        else:
            data = PageData(index=idx, kind="image", mean_conf=ocr_page.mean_conf)
            data.image_rel = _make_scan_image(work, raw_path, idx)
            data.route, data.reason = _image_route_and_reason(
                ocr_page, image_page, reason, opts.ocr.min_conf)
            pages_data.append(data)
            result.pages_image_fallback += 1
        if progress:
            progress("ocr", n + 1, len(indices))

    # 3. Detect repeated headers/footers, then build elements with the
    #    line-level junk filter (watermarks never reach paragraph building).
    repeated = clean.find_repeated_lines([p.line_texts() for p in pages_data
                                          if p.kind != "image"])
    result.stripped_lines = repeated

    def drop_line(text: str, edge: bool) -> bool:
        if clean.is_watermark(text, extra_patterns):
            return True
        if clean.is_page_number(text):
            return True
        if clean.is_junk_line(text, edge=edge):
            return True
        return bool(repeated) and clean.matches_repeated(text, repeated)

    for data in pages_data:
        if data.kind in ("ocr", "text"):
            payload = data.payload
            notes: list = []
            if opts.footnotes:
                # Split the footnote block *before* structuring so its "١-" lines
                # are not mistaken for an ordered list or merged into prose.
                payload, notes = footnotes.split_footnotes(payload, body_size)
            data.elements = structure_page(payload, opts.ocr.keep_diacritics,
                                           drop_line, body_size)
            if notes:
                data.elements = footnotes.rewrite_body_refs(data.elements, notes, data.index)
                data.elements += [
                    ("footnote", clean.normalize_arabic(note.text, opts.ocr.keep_diacritics))
                    for note in notes
                ]

    # 3b. Coverage snapshot — measure per page here, *before* the boundary merge
    #     moves a paragraph's chars from one page to the next (which would fake a
    #     per-page dip). The merge never loses chars, so pre-merge counts are exact.
    report = ConversionReport()
    for data in pages_data:
        page_reason = data.reason
        if data.index in text_layer_reasons and data.route != ROUTE_TEXT:
            # Explain *why* a page with a text layer ended up on OCR / image.
            page_reason = f"{text_layer_reasons[data.index]}; {data.reason}"
        if data.kind in ("ocr", "text"):
            kept, dropped = _page_char_counts(data.payload, opts.ocr.keep_diacritics, drop_line)
            emitted = sum(len(coverage_key(text)) for _, text in data.elements)
            coverage = emitted / kept if kept else 1.0
            report.pages.append(PageReport(
                page_no=data.index, route=data.route, reason=page_reason,
                confidence=round(data.mean_conf, 1), source_chars=kept,
                dropped_chars=dropped, emitted_chars=emitted, coverage=round(coverage, 3)))
        else:
            report.pages.append(PageReport(
                page_no=data.index, route=data.route, reason=page_reason,
                confidence=round(data.mean_conf, 1)))
    report.warnings = collect_warnings(report)
    result.report = report

    # 4. Merge paragraphs across page boundaries.
    for prev, cur in zip(pages_data, pages_data[1:]):
        if prev.kind == "image" or cur.kind == "image":
            continue
        if not prev.elements or not cur.elements:
            continue
        if prev.elements[-1][0] != "p" or cur.elements[0][0] != "p":
            continue
        merged_prev, merged_cur = merge_page_boundary(
            [prev.elements[-1][1]], [cur.elements[0][1]]
        )
        if len(merged_cur) == 0:  # merge happened
            prev.elements[-1] = ("p", merged_prev[-1])
            cur.elements.pop(0)

    # 5. Serialize the structured pages to an in-memory Markdown document, then
    #    parse it back into the Book model (PDF → Markdown → EPUB). The Markdown
    #    is written to disk only when --markdown-out is set.
    md_lines: list[str] = []
    for data in pages_data:
        md_lines.append(emit_page_break(data.index))
        if data.kind == "image":
            if data.image_rel:
                md_lines.append(emit_scan(data.image_rel))
        else:
            md_lines.extend(emit_elements(data.elements, data.index))
    markdown = "\n".join(md_lines)

    title = opts.meta.title or default_title(pdf_path)
    if opts.markdown_out:
        _export_markdown(markdown, Path(opts.markdown_out),
                         EpubMeta(title=title, author=opts.meta.author,
                                  language=opts.meta.language), work.root)

    book = markdown_to_book(markdown, title=title, author=opts.meta.author,
                            language=opts.meta.language, split_every=opts.split_every)

    finalize_chapters(book)

    # Optional final transform: bake letter-joining into the text for simple
    # renderers (CrossPoint etc.). Must come after the Markdown round-trip
    # (preshape only touches final Arabic glyphs, never markup).
    if opts.preshape:
        apply_preshape(book)

    text_dir = work.stage_dir("text")
    book.save(text_dir / "book.json")
    # report.json is additive in the text/ stage — it never gates resume.
    (text_dir / "report.json").write_text(report.to_json(), encoding="utf-8")

    # 6. Build EPUB volume(s).
    from .pipeline import _volume_chunks, _volume_path

    font_files = resolve_fonts(opts.font)
    # Weight chapters by content so multi-volume splits come out even.
    weights = [sum(_element_weight(el) for el in ch.elements) for ch in book.chapters]
    chunks = _volume_chunks(book.chapters, opts.split_volumes, weights)
    for vol, chunk in enumerate(chunks):
        vol_title = title if len(chunks) == 1 else f"{title} — {vol + 1}"
        vol_out = _volume_path(out_path, vol, len(chunks))
        vol_book = Book(title=vol_title, author=book.author, language=book.language,
                        chapters=chunk)
        build_reflow_epub(vol_book, vol_out, work.root, font_files)
        result.outputs.append(vol_out)
        if progress:
            progress("epub", vol + 1, len(chunks))

    if opts.clean:
        work.cleanup()
    return result


def resolve_fonts(choice: str) -> list[Path]:
    if choice == "none":
        return []
    fonts_dir = Path(__file__).parent / "fonts"
    mapping = {
        "amiri": ["Amiri-Regular.ttf"],
    }
    if choice not in mapping:
        valid = ", ".join(["amiri", "none"])
        raise ValueError(f"font must be one of: {valid}")
    files = [fonts_dir / name for name in mapping[choice]]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        raise ValueError(f"Requested font asset missing: {', '.join(missing)}")
    return files
