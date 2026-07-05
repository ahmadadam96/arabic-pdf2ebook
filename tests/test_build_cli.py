from __future__ import annotations

import xml.dom.minidom
import zipfile

from PIL import Image
from typer.testing import CliRunner

from pdf2ebook.cli import app

runner = CliRunner()


def _write_md(path, *, with_scan=True):
    lines = [
        "---",
        "title: عنوان من الترويسة",
        "author: مؤلف",
        "language: ar",
        "---",
        "",
        "<!-- page:0 -->",
        "# الفصل الأول",
        "فقرة افتتاحية.",
        "- بند أول",
        "- بند ثان",
        ":::verse",
        "شطر  شطر",
        ":::",
        "<!-- page:1 -->",
        "# الفصل الثاني",
        "نص الفصل الثاني.",
    ]
    if with_scan:
        lines += ["<!-- page:2 -->", "![scan](scans/page_0003.png)"]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_build_from_markdown_produces_epub(tmp_path):
    md = tmp_path / "book.md"
    _write_md(md)
    scan = tmp_path / "scans" / "page_0003.png"
    scan.parent.mkdir()
    Image.new("L", (400, 600), 210).save(scan)

    out = tmp_path / "book.epub"
    result = runner.invoke(app, ["build", str(md), str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()

    zf = zipfile.ZipFile(out)
    assert zf.read("mimetype") == b"application/epub+zip"
    # Two heading chapters split the book; front-matter title flows to the OPF.
    opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert "عنوان من الترويسة" in opf
    chap1 = zf.read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert "الفصل الأول" in chap1
    xml.dom.minidom.parseString(chap1)


def test_build_cli_title_overrides_front_matter(tmp_path):
    md = tmp_path / "book.md"
    _write_md(md, with_scan=False)
    out = tmp_path / "book.epub"
    result = runner.invoke(app, ["build", str(md), str(out), "--title", "عنوان مخصص"])
    assert result.exit_code == 0, result.output
    opf = zipfile.ZipFile(out).read("OEBPS/content.opf").decode("utf-8")
    assert "عنوان مخصص" in opf
    assert "عنوان من الترويسة" not in opf


def test_build_missing_scan_fails_cleanly(tmp_path):
    md = tmp_path / "book.md"
    _write_md(md)  # references scans/page_0003.png but we never create it
    out = tmp_path / "book.epub"
    result = runner.invoke(app, ["build", str(md), str(out)])
    assert result.exit_code == 1
    assert "page_0003.png" in result.output
    assert not out.exists()
