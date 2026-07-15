from __future__ import annotations

import xml.dom.minidom
import zipfile
from pathlib import Path, PurePosixPath
from posixpath import normpath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import pytest
from PIL import Image

from pdf2ebook.book import Book, Chapter, PageImage, Paragraph
from pdf2ebook.epub.fixedlayout import build_image_epub
from pdf2ebook.epub.reflow import build_reflow_epub
from pdf2ebook.epub.validate import EpubValidationError, validate_epub
from pdf2ebook.epub.zipwriter import EpubContainer


def _make_pages(tmp_path: Path, n: int = 2) -> list[Path]:
    paths = []
    for i in range(n):
        p = tmp_path / f"page_{i + 1:04d}.png"
        Image.new("L", (400, 600), 240).save(p)
        paths.append(p)
    return paths


def _resolve_epub_href(source: str, href: str) -> tuple[str, str] | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    target = source if not parsed.path else normpath(
        str(PurePosixPath(source).parent / unquote(parsed.path))
    )
    return target, unquote(parsed.fragment)


def _assert_epub_graph(zf: zipfile.ZipFile) -> None:
    names = set(zf.namelist())
    opf_path = "OEBPS/content.opf"
    opf = ElementTree.fromstring(zf.read(opf_path))
    namespace = {"opf": "http://www.idpf.org/2007/opf"}
    manifest = {
        item.attrib["id"]: item
        for item in opf.findall("opf:manifest/opf:item", namespace)
    }
    for item in manifest.values():
        target = _resolve_epub_href(opf_path, item.attrib["href"])
        assert target is not None and target[0] in names

    for itemref in opf.findall("opf:spine/opf:itemref", namespace):
        assert itemref.attrib["idref"] in manifest

    xhtml_ids: dict[str, set[str]] = {}
    xhtml_documents: dict[str, ElementTree.Element] = {}
    for item in manifest.values():
        if item.attrib["media-type"] != "application/xhtml+xml":
            continue
        source = _resolve_epub_href(opf_path, item.attrib["href"])
        assert source is not None
        path = source[0]
        document = ElementTree.fromstring(zf.read(path))
        ids = [element.attrib["id"] for element in document.iter() if "id" in element.attrib]
        assert len(ids) == len(set(ids)), f"duplicate IDs in {path}"
        xhtml_ids[path] = set(ids)
        xhtml_documents[path] = document

    for path, document in xhtml_documents.items():
        for element in document.iter():
            for attr in ("href", "src"):
                if attr not in element.attrib:
                    continue
                target = _resolve_epub_href(path, element.attrib[attr])
                if target is None:
                    continue
                target_path, fragment = target
                assert target_path in names, f"missing {attr} target {element.attrib[attr]} in {path}"
                if fragment:
                    assert fragment in xhtml_ids.get(target_path, set()), (
                        f"missing fragment {fragment} in {target_path}"
                    )

    for item in manifest.values():
        if item.attrib["media-type"] != "application/x-dtbncx+xml":
            continue
        source = _resolve_epub_href(opf_path, item.attrib["href"])
        assert source is not None
        for content in ElementTree.fromstring(zf.read(source[0])).iter():
            if not content.tag.endswith("content") or "src" not in content.attrib:
                continue
            target = _resolve_epub_href(source[0], content.attrib["src"])
            assert target is not None and target[0] in names
            if target[1]:
                assert target[1] in xhtml_ids.get(target[0], set())


def _assert_valid_epub(path: Path, language: str = "ar") -> zipfile.ZipFile:
    validate_epub(path)
    zf = zipfile.ZipFile(path)
    infos = zf.infolist()
    assert infos[0].filename == "mimetype"
    assert infos[0].compress_type == zipfile.ZIP_STORED
    assert zf.read("mimetype") == b"application/epub+zip"
    opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert 'page-progression-direction="rtl"' in opf
    assert 'dir="rtl"' in opf
    assert f"<dc:language>{language}</dc:language>" in opf
    xml.dom.minidom.parseString(opf)
    xml.dom.minidom.parseString(zf.read("OEBPS/nav.xhtml"))
    xml.dom.minidom.parseString(zf.read("OEBPS/toc.ncx"))
    _assert_epub_graph(zf)
    return zf


def test_validate_epub_reports_missing_manifest_target(tmp_path):
    out = tmp_path / "broken.epub"
    with EpubContainer(out) as epub:
        epub.add(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf">
  <manifest><item id="chapter" href="text/missing.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )

    with pytest.raises(EpubValidationError, match="manifest item 'chapter'.*missing file"):
        validate_epub(out)


def test_image_epub_structure(tmp_path):
    pages = _make_pages(tmp_path)
    out = tmp_path / "book.epub"
    build_image_epub(pages, out, title="كتاب تجريبي", style="gray", layout="flow")
    zf = _assert_valid_epub(out)
    assert len([n for n in zf.namelist() if n.startswith("OEBPS/pages/")]) == 2
    page1 = zf.read("OEBPS/pages/page_0001.xhtml").decode("utf-8")
    assert 'dir="rtl"' in page1 and 'lang="ar"' in page1


def test_image_epub_fixed_layout(tmp_path):
    pages = _make_pages(tmp_path)
    out = tmp_path / "fixed.epub"
    build_image_epub(pages, out, title="ت", layout="fixed", viewport=(480, 800))
    zf = _assert_valid_epub(out)
    opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert "pre-paginated" in opf


def test_image_epub_rejects_empty_page_list(tmp_path):
    with pytest.raises(ValueError, match="without page images"):
        build_image_epub([], tmp_path / "empty.epub", title="Empty")


def test_epub_invalid_language_falls_back_to_und(tmp_path):
    out = tmp_path / "language.epub"
    build_image_epub(_make_pages(tmp_path, 1), out, title="t", language='ar\"/><bad')
    zf = _assert_valid_epub(out, language="und")
    assert 'lang="und"' in zf.read("OEBPS/nav.xhtml").decode("utf-8")
    assert 'lang="und"' in zf.read("OEBPS/pages/page_0001.xhtml").decode("utf-8")


def test_reflow_epub_renders_headings_and_lists(tmp_path):
    book = Book(
        title="كتاب القوائم",
        chapters=[
            Chapter("الباب الأول", [
                Paragraph("الباب الأول", "h1"),
                Paragraph("فقرة افتتاحية.", "p"),
                Paragraph("البند الأول", "ul"),
                Paragraph("البند الثاني", "ul"),
                Paragraph("مبحث فرعي", "h3"),
                Paragraph("أولا", "ol"),
                Paragraph("ثانيا", "ol"),
            ]),
        ],
    )
    out = tmp_path / "lists.epub"
    build_reflow_epub(book, out, tmp_path, font_files=[])
    zf = _assert_valid_epub(out)
    chap = zf.read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert "<h1>" in chap and "<h3>" in chap
    assert "<ul>" in chap and "<ol>" in chap
    assert chap.count("<li>") == 4  # 2 ul + 2 ol items, each its own <li>


def test_reflow_epub_with_scan_fallback(tmp_path):
    scan = tmp_path / "scans" / "page_0002.png"
    scan.parent.mkdir()
    Image.new("L", (400, 600), 200).save(scan)
    book = Book(
        title="محاكم التفتيش",
        chapters=[
            Chapter("الفصل الأول", [
                Paragraph("الفصل الأول", "h2"),
                Paragraph("نص الفقرة الأولى من الكتاب."),
                PageImage(1, "scans/page_0002.png"),
            ]),
            Chapter("الفصل الثاني", [Paragraph("نص الفصل الثاني.")]),
        ],
    )
    out = tmp_path / "reflow.epub"
    build_reflow_epub(book, out, tmp_path, font_files=[])
    zf = _assert_valid_epub(out)
    chap1 = zf.read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert "نص الفقرة الأولى" in chap1
    assert "figure" in chap1  # embedded scan fallback
    assert any(n.startswith("OEBPS/images/scan_") for n in zf.namelist())
    # two chapters in spine and toc
    assert len([n for n in zf.namelist() if n.startswith("OEBPS/text/")]) == 2
