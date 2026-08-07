from __future__ import annotations

import zipfile

from typer.testing import CliRunner

from pdf2ebook.cli import app
from pdf2ebook.config import parse_page_range
from test_pdfio_geometry import _make_text_pdf

runner = CliRunner()


def _make_text_layer_pdf(path):
    """One text-layer page with enough ASCII to pass the 200-char gate."""
    items = [("Chapter One Heading", 72, 720, 22)]
    items += [(f"Body text line number {i} carrying several plain words here", 72, 690 - i * 22, 12)
              for i in range(9)]
    _make_text_pdf(path, items)


def _make_short_text_layer_pdf(path):
    """Sparse text-layer page below the old 200-char gate."""
    _make_text_pdf(path, [
        ("Short Heading With Valid Text That Survives Edge Filtering", 72, 720, 20),
        ("Brief body line with enough words to survive filtering.", 72, 680, 12),
    ])


def test_devices_lists_profiles():
    result = runner.invoke(app, ["devices"])
    assert result.exit_code == 0
    assert "xteink-x4" in result.output


def test_inspect_detects_scan(tiny_pdf):
    result = runner.invoke(app, ["inspect", str(tiny_pdf)])
    assert result.exit_code == 0
    assert "text layer: no" in result.output


def test_inspect_reports_short_text_layer_as_usable(tmp_path):
    pdf = tmp_path / "short.pdf"
    _make_short_text_layer_pdf(pdf)
    result = runner.invoke(app, ["inspect", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "text layer: yes, usable" in result.output


def test_convert_image_mode_end_to_end(tiny_pdf, tmp_path):
    out = tmp_path / "out.epub"
    result = runner.invoke(app, [
        "convert", str(tiny_pdf), str(out), "--mode", "image",
        "--work-dir", str(tmp_path / "wd"), "--clean",
    ])
    assert result.exit_code == 0, result.output
    assert out.exists()
    zf = zipfile.ZipFile(out)
    assert zf.read("mimetype") == b"application/epub+zip"
    assert not (tmp_path / "wd").exists()  # --clean removed the cache


def test_convert_rejects_bad_mode(tiny_pdf):
    result = runner.invoke(app, ["convert", str(tiny_pdf), "--mode", "banana"])
    assert result.exit_code == 2


def test_convert_rejects_bad_enum_options(tiny_pdf):
    result = runner.invoke(app, ["convert", str(tiny_pdf), "--style", "sepia"])
    assert result.exit_code == 2
    assert "Invalid option" in result.output

    result = runner.invoke(app, ["convert", str(tiny_pdf), "--mode", "image", "--device", "missing"])
    assert result.exit_code == 2
    assert "device" in result.output


def test_convert_rejects_output_inside_cleaned_workdir(tiny_pdf, tmp_path):
    wd = tmp_path / "wd"
    out = wd / "out.epub"
    result = runner.invoke(app, [
        "convert", str(tiny_pdf), str(out), "--mode", "image",
        "--work-dir", str(wd), "--clean",
    ])
    # A bad path combination is a usage error (2), not a conversion failure (1).
    assert result.exit_code == 2
    assert "Output path" in result.output


def test_convert_empty_pdf_fails_cleanly(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    result = runner.invoke(app, ["convert", str(empty), "--mode", "image"])
    assert result.exit_code == 1
    assert "empty" in result.output.lower()


def test_volume_chunks_balance_by_weight():
    from pdf2ebook.pipeline import _volume_chunks

    items = ["a", "b", "c", "d", "e"]
    weights = [100, 100, 1000, 100, 100]  # one giant chapter in the middle
    chunks = _volume_chunks(items, 3, weights)
    assert [x for chunk in chunks for x in chunk] == items  # order preserved
    assert 1 < len(chunks) <= 3
    totals = [sum(weights[items.index(x)] for x in chunk) for chunk in chunks]
    # no chunk should hold nearly everything while others are empty-ish
    assert max(totals) <= 1200 and min(totals) >= 100


def test_convert_markdown_out_then_build(tmp_path):
    pdf = tmp_path / "text.pdf"
    _make_text_layer_pdf(pdf)
    epub = tmp_path / "out.epub"
    md = tmp_path / "out.md"
    wd = tmp_path / "wd"
    result = runner.invoke(app, [
        "convert", str(pdf), str(epub), "--markdown-out", str(md), "--work-dir", str(wd),
    ])
    assert result.exit_code == 0, result.output
    assert epub.exists() and md.exists()

    text = md.read_text(encoding="utf-8")
    assert text.startswith("---")            # front matter
    assert "<!-- page:0 -->" in text         # page boundary marker

    # Conversion report is printed and persisted.
    assert "coverage" in result.output
    report_json = wd / "text" / "report.json"
    assert report_json.exists()
    import json
    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert report["pages"] and report["pages"][0]["route"] == "text-layer"

    # Full circle: rebuild an EPUB straight from the exported Markdown.
    rebuilt = tmp_path / "rebuilt.epub"
    result2 = runner.invoke(app, ["build", str(md), str(rebuilt)])
    assert result2.exit_code == 0, result2.output
    assert zipfile.ZipFile(rebuilt).read("mimetype") == b"application/epub+zip"


def test_convert_short_text_layer_page_uses_direct_text(tmp_path):
    pdf = tmp_path / "short.pdf"
    _make_short_text_layer_pdf(pdf)
    epub = tmp_path / "out.epub"
    md = tmp_path / "out.md"
    wd = tmp_path / "wd"
    result = runner.invoke(app, [
        "convert", str(pdf), str(epub), "--markdown-out", str(md), "--work-dir", str(wd),
    ])
    assert result.exit_code == 0, result.output
    assert "1 direct text" in result.output

    import json
    report = json.loads((wd / "text" / "report.json").read_text(encoding="utf-8"))
    assert report["pages"][0]["route"] == "text-layer"
    assert "Short Heading" in md.read_text(encoding="utf-8")


def test_convert_warm_cache_rerun_still_reports(tmp_path):
    pdf = tmp_path / "text.pdf"
    _make_text_layer_pdf(pdf)
    epub = tmp_path / "out.epub"
    wd = tmp_path / "wd"
    for _ in range(2):  # second run reuses the warm work dir
        result = runner.invoke(app, ["convert", str(pdf), str(epub), "--work-dir", str(wd)])
        assert result.exit_code == 0, result.output
    assert (wd / "text" / "report.json").exists()


def test_footnotes_flag_toggles_detection(tmp_path):
    pdf = tmp_path / "fn.pdf"
    items = [(f"Body line number {i} here carrying several words", 72, 720 - i * 24, 12)
             for i in range(9)]
    items.append(("(1) this is a footnote at the page bottom", 72, 60, 8))  # small, bottom
    _make_text_pdf(pdf, items)

    md_on = tmp_path / "on.md"
    r1 = runner.invoke(app, ["convert", str(pdf), str(tmp_path / "on.epub"),
                             "--markdown-out", str(md_on), "--work-dir", str(tmp_path / "wd1")])
    assert r1.exit_code == 0, r1.output

    md_off = tmp_path / "off.md"
    r2 = runner.invoke(app, ["convert", str(pdf), str(tmp_path / "off.epub"), "--no-footnotes",
                             "--markdown-out", str(md_off), "--work-dir", str(tmp_path / "wd2")])
    assert r2.exit_code == 0, r2.output

    assert "[^" in md_on.read_text(encoding="utf-8")       # footnote detected & linked
    assert "[^" not in md_off.read_text(encoding="utf-8")  # detection disabled


def test_parse_page_range():
    assert parse_page_range("1-3", 10) == [0, 1, 2]
    assert parse_page_range("5", 10) == [4]
    assert parse_page_range("1-2,9-10", 10) == [0, 1, 8, 9]
    assert parse_page_range(None, 3) == [0, 1, 2]
    import pytest

    with pytest.raises(ValueError):
        parse_page_range(",", 10)
