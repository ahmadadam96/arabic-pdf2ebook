"""Golden-file tests over the fixture corpora.

These are the blast-radius tests: unit tests pin one decision each, while these
pin what a whole book actually comes out looking like. Change a threshold in
`headings.py` or a rule in `clean.py` and the diff shows up here.

Two corpora, because they answer different questions:

* `pagefixtures` — `OcrPage`s with exact geometry, run through
  `ocrmode.structure_pages`. Covers every structuring rule.
* `corpus` — real PDFs. Covers extraction, font metrics, the book-level
  text-layer verdict, and byte-reproducibility of the EPUB.

Re-record with `UPDATE_SNAPSHOTS=1 python -m pytest tests/test_snapshots.py`,
then *read the diff* before committing it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from corpus import CORPUS, build_fixture
from pagefixtures import PAGE_CORPUS
from snapshot import assert_snapshot

from pdf2ebook.config import EpubMeta, PipelineOptions
from pdf2ebook.ocrmode import PageData, run_text_mode, structure_pages
from pdf2ebook.pipeline import inspect_pdf
from pdf2ebook.report import ROUTE_TEXT
from pdf2ebook.textproc.markdownize import markdown_to_book

CONVERTIBLE = [f for f in CORPUS if f.text_layer_usable]
BODY_SIZE = 12.0


def _opts(markdown_out: Path | None = None, work_dir: Path | None = None) -> PipelineOptions:
    return PipelineOptions(
        mode="auto", markdown_out=markdown_out, work_dir=work_dir, split_every=10,
        meta=EpubMeta(title="كتاب الاختبار", author="مؤلف", language="ar"),
    )


def _format_report(report) -> str:
    """Stable, readable rendering of a ConversionReport for snapshotting."""
    lines = [f"verdict: {report.book_verdict}",
             f"coverage: {report.book_coverage:.2f}", ""]
    for page in report.pages:
        lines.append(f"page {page.page_no + 1}: route={page.route} coverage={page.coverage:.2f} "
                     f"src={page.source_chars} dropped={page.dropped_chars} "
                     f"emitted={page.emitted_chars}")
        if page.reason:
            lines.append(f"  reason: {page.reason}")
        for note in page.notes:
            lines.append(f"  note: {note}")
    if report.warnings:
        lines.append("")
        lines.extend(f"warning: {w}" for w in report.warnings)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structuring corpus (OcrPage in, Markdown out)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PAGE_CORPUS), ids=lambda n: n)
def test_page_structuring_matches_snapshot(name):
    """Markdown for a whole fixture book, straight from exact page geometry."""
    pages = PAGE_CORPUS[name]()
    pages_data = [PageData(index=p.page_no, kind="text", payload=p, route=ROUTE_TEXT)
                  for p in pages]
    markdown, report = structure_pages(pages_data, _opts(), BODY_SIZE)
    assert_snapshot(f"pages/{name}.md", markdown)
    assert_snapshot(f"pages/{name}.report.txt", _format_report(report))


@pytest.mark.parametrize("name", sorted(PAGE_CORPUS), ids=lambda n: n)
def test_page_structuring_round_trips_through_book(name):
    """Every element the structurer emits survives the Markdown → Book parse.

    The Markdown pivot is the pipeline's single serializer; anything it cannot
    represent is text that silently never reaches the EPUB.
    """
    pages = PAGE_CORPUS[name]()
    pages_data = [PageData(index=p.page_no, kind="text", payload=p, route=ROUTE_TEXT)
                  for p in pages]
    markdown, _ = structure_pages(pages_data, _opts(), BODY_SIZE)
    book = markdown_to_book(markdown, title="ك", author="", language="ar", split_every=10)

    emitted = "".join(text for data in pages_data for _, text in data.elements)
    parsed = "".join(
        el.text for chapter in book.chapters for el in chapter.elements
        if getattr(el, "text", None) is not None
    )
    # Compare on letters alone: the Book carries the same characters, only
    # regrouped into chapters and stripped of Markdown syntax.
    strip = str.maketrans("", "", " \n\t")
    assert set(emitted.translate(strip)) - set(parsed.translate(strip)) == set(), (
        f"{name}: characters lost between the structurer and the Book model")


# ---------------------------------------------------------------------------
# PDF corpus (real PDF in)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", CORPUS, ids=lambda f: f.name)
def test_fixture_bytes_are_stable(fixture, tmp_path):
    """The corpus generator itself is pinned.

    Without this, editing `arabicpdf`/`corpus` would quietly change what every
    other PDF snapshot is a snapshot *of*.
    """
    pdf = build_fixture(fixture, tmp_path)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert_snapshot(f"{fixture.name}.sha256", f"{digest}\n{fixture.description}")


@pytest.mark.parametrize("fixture", CORPUS, ids=lambda f: f.name)
def test_fixture_routing_matches_snapshot(fixture, tmp_path):
    """Routing decisions for every fixture, including the ones we reject.

    Runs no OCR, so the rejected fixtures are covered here even on CI.
    """
    pdf = build_fixture(fixture, tmp_path)
    info = inspect_pdf(pdf)
    rendered = "\n".join([
        f"pages: {info['pages']}",
        f"has_text_layer: {info['has_text_layer']}",
        f"text_layer_usable: {info['text_layer_usable']}",
        f"book_verdict: {info['book_verdict']}",
        f"recommendation: {info['recommendation']}",
    ])
    assert_snapshot(f"{fixture.name}.routing.txt", rendered)


@pytest.mark.parametrize("fixture", CONVERTIBLE, ids=lambda f: f.name)
def test_fixture_markdown_matches_snapshot(fixture, tmp_path):
    """End-to-end: a real PDF through the whole pipeline to Markdown."""
    pdf = build_fixture(fixture, tmp_path / "src")
    md_out = tmp_path / "out" / f"{fixture.name}.md"
    result = run_text_mode(pdf, tmp_path / "out" / f"{fixture.name}.epub",
                           _opts(md_out, tmp_path / "work"))
    assert result.outputs, "conversion produced no EPUB"
    assert_snapshot(f"{fixture.name}.md", md_out.read_text(encoding="utf-8"))
    assert_snapshot(f"{fixture.name}.report.txt", _format_report(result.report))


@pytest.mark.parametrize("fixture", CONVERTIBLE, ids=lambda f: f.name)
def test_conversion_is_byte_reproducible(fixture, tmp_path):
    """Same PDF in, byte-identical EPUB out — twice, from cold work dirs.

    This is what makes the snapshots above trustworthy and what lets a user
    tell whether a rebuild actually changed their book.
    """
    pdf = build_fixture(fixture, tmp_path / "src")
    digests = []
    for run in ("a", "b"):
        out = tmp_path / run / f"{fixture.name}.epub"
        run_text_mode(pdf, out, _opts(tmp_path / run / "book.md", tmp_path / run / "work"))
        digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests[0] == digests[1], "EPUB output is not reproducible"
