"""Fixed safety limits."""

from __future__ import annotations

import pytest
from corpus import CORPUS, build_fixture

from pdf2ebook import limits
from pdf2ebook.config import EpubMeta, PipelineOptions
from pdf2ebook.errors import ResourceLimitError
from pdf2ebook.ocrmode import run_text_mode
from pdf2ebook.pdfio import PdfRasterizer

CLEAN = next(f for f in CORPUS if f.name == "clean_book")


def test_check_passes_under_the_limit():
    limits.check("demo", 10, 100)  # no raise


def test_check_raises_with_both_numbers_and_the_knob():
    with pytest.raises(ResourceLimitError) as exc:
        limits.check("max_pages", 9000, 5000, "use --pages.")
    message = str(exc.value)
    assert "max_pages" in message and "9,000" in message and "5,000" in message
    assert "--pages" in message
    assert exc.value.code == "resource_limit"


def test_excessive_dpi_fails_before_rendering(tmp_path, monkeypatch):
    """A --dpi typo must fail in a second, not after an hour of rasterizing."""
    pdf = build_fixture(CLEAN, tmp_path)
    rendered: list[int] = []
    original = PdfRasterizer.render_page

    def spy(self, index, dpi=300, grayscale=True):
        rendered.append(index)
        return original(self, index, dpi=dpi, grayscale=grayscale)

    monkeypatch.setattr(PdfRasterizer, "render_page", spy)
    monkeypatch.setattr(limits, "MAX_TOTAL_RASTER_PIXELS", 1000)

    with pytest.raises(ResourceLimitError):
        run_text_mode(pdf, tmp_path / "out.epub",
                      PipelineOptions(work_dir=tmp_path / "wd",
                                      meta=EpubMeta(title="ك", language="ar")))
    assert rendered == [], "pages were rendered before the limit was checked"


def test_page_count_limit_is_checked_on_open(tmp_path, monkeypatch):
    pdf = build_fixture(CLEAN, tmp_path)
    monkeypatch.setattr(limits, "MAX_PAGES", 1)
    with pytest.raises(ResourceLimitError) as exc:
        PdfRasterizer(pdf)
    assert "max_pages" in str(exc.value)


def test_per_page_raster_limit(tmp_path, monkeypatch):
    pdf = build_fixture(CLEAN, tmp_path)
    monkeypatch.setattr(limits, "MAX_RASTER_PIXELS", 100)
    with PdfRasterizer(pdf) as doc, pytest.raises(ResourceLimitError):
        doc.render_page(0, dpi=300)


def test_embedded_scan_bytes_are_bounded(tmp_path, monkeypatch):
    """A book where every page failed OCR must not produce an unopenable EPUB."""
    from PIL import Image

    from pdf2ebook.book import Book, Chapter, PageImage
    from pdf2ebook.epub.reflow import build_reflow_epub

    scans = tmp_path / "scans"
    scans.mkdir()
    for n in range(3):
        Image.new("L", (800, 1200), 200).save(scans / f"page_{n:04d}.png")
    book = Book(title="ك", chapters=[Chapter(title="ف", elements=[
        PageImage(n, f"scans/page_{n:04d}.png") for n in range(3)])])

    monkeypatch.setattr(limits, "MAX_EMBEDDED_SCAN_BYTES", 100)
    with pytest.raises(ResourceLimitError) as exc:
        build_reflow_epub(book, tmp_path / "big.epub", tmp_path, font_files=[])
    assert "max_embedded_scan_bytes" in str(exc.value)


def test_footnote_block_runaway_is_bounded(monkeypatch):
    """A detector that latches onto body text must fail loudly, not silently."""
    from pagefixtures import NOTE, line, page

    from pdf2ebook.textproc import footnotes

    notes = [line(f"({n}) حاشية رقم {n} فيها كلام", 500 + n, NOTE) for n in range(1, 12)]
    monkeypatch.setattr(limits, "MAX_FOOTNOTES_PER_PAGE", 5)
    with pytest.raises(ResourceLimitError):
        footnotes.split_footnotes(page([line("متن الصفحة الأصلي هنا", 100)] + notes), 12.0)
