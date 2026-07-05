"""Build an EPUB from a (possibly hand-edited) Markdown file.

The reverse of `convert --markdown-out`: correct the extracted text and rebuild
the book without re-running OCR. Image references are resolved relative to the
Markdown file (a sibling `scans/` folder), exactly as the exporter writes them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .book import Book, PageImage
from .config import PipelineOptions
from .epub.reflow import build_reflow_epub
from .pipeline import ConversionResult, Progress, _volume_chunks, _volume_path, default_title
from .textproc.markdownize import markdown_to_book, parse_front_matter

Warn = Callable[[str], None]


class BuildError(RuntimeError):
    """Raised when a Markdown file cannot be built into an EPUB."""


def _missing_scans(book: Book, work_root: Path) -> list[str]:
    missing: list[str] = []
    for chapter in book.chapters:
        for el in chapter.elements:
            if isinstance(el, PageImage) and not (work_root / el.image_path).exists():
                missing.append(el.image_path)
    return missing


def run_build(
    md_path: Path,
    out_path: Path,
    opts: PipelineOptions,
    *,
    cli_title: str | None = None,
    cli_author: str | None = None,
    cli_language: str | None = None,
    on_warning: Warn | None = None,
    progress: Progress | None = None,
) -> ConversionResult:
    """Parse a Markdown file into a Book and build the EPUB volume(s)."""
    # Imported lazily: pulls in the OCR pipeline's heavy deps only when building.
    from .ocrmode import _element_weight, apply_preshape, finalize_chapters, resolve_fonts

    if not md_path.exists():
        raise BuildError(f"File not found — الملف غير موجود: {md_path}")

    text = md_path.read_text(encoding="utf-8-sig")  # tolerate a UTF-8 BOM
    front, body = parse_front_matter(text)

    # Precedence: explicit CLI option > front matter > filename / default.
    title = cli_title or front.get("title") or default_title(md_path)
    author = cli_author or front.get("author") or ""
    language = cli_language or front.get("language") or "ar"

    book = markdown_to_book(body, title=title, author=author, language=language,
                            split_every=opts.split_every, on_warning=on_warning)
    finalize_chapters(book)

    work_root = md_path.parent
    missing = _missing_scans(book, work_root)
    if missing:
        listed = ", ".join(missing[:10])
        raise BuildError(
            "Missing scan image(s) referenced by the Markdown — "
            f"صور ناقصة مشار إليها في الملف: {listed}. "
            "Keep the scans/ folder next to the .md file."
        )

    if opts.preshape:
        apply_preshape(book)

    font_files = resolve_fonts(opts.font)
    result = ConversionResult()
    result.pages_total = sum(1 for ch in book.chapters for _ in ch.elements)
    weights = [sum(_element_weight(el) for el in ch.elements) for ch in book.chapters]
    chunks = _volume_chunks(book.chapters, opts.split_volumes, weights)
    for vol, chunk in enumerate(chunks):
        vol_title = title if len(chunks) == 1 else f"{title} — {vol + 1}"
        vol_out = _volume_path(out_path, vol, len(chunks))
        vol_book = Book(title=vol_title, author=book.author, language=book.language,
                        chapters=chunk)
        build_reflow_epub(vol_book, vol_out, work_root, font_files)
        result.outputs.append(vol_out)
        if progress:
            progress("epub", vol + 1, len(chunks))
    return result
