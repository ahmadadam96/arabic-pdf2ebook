from __future__ import annotations

import xml.dom.minidom
import zipfile

from pdf2ebook.book import PageImage, Paragraph
from pdf2ebook.epub.reflow import build_reflow_epub
from pdf2ebook.textproc.markdownize import (
    emit_elements,
    emit_front_matter,
    emit_page_break,
    emit_scan,
    markdown_to_book,
    parse_front_matter,
)


def _md(*pages: list) -> str:
    """Build a markdown doc from pages, each a (page_no, elements) tuple."""
    lines: list[str] = []
    for page_no, elements in pages:
        lines.append(emit_page_break(page_no))
        lines.extend(emit_elements(elements))
    return "\n".join(lines)


def test_verse_quran_roundtrip_is_exact():
    elements = [
        ("verse", "شطر أول  شطر ثان"),          # hemistich double-space preserved
        ("verse", "بيت ثان هنا  وتكملته"),
        ("quran", "﴿ ذلك الكتاب لا ريب فيه ﴾"),  # ornate brackets preserved
        ("p", "نص عادي بعد الآيات."),
    ]
    md = _md((0, elements))
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    out = [(p.kind, p.text) for ch in book.chapters for p in ch.elements
           if isinstance(p, Paragraph)]
    assert out == elements


def test_paragraph_starting_with_markdown_char_stays_paragraph():
    # Real Arabic text that happens to start with '#'/'-' must survive the trip.
    elements = [("p", "# ليست عنوانا بل فقرة"), ("p", "- ولا قائمة هنا"), ("p", "1. ولا ترقيم")]
    md = _md((0, elements))
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    out = [(p.kind, p.text) for ch in book.chapters for p in ch.elements
           if isinstance(p, Paragraph)]
    assert out == elements


def test_headings_split_into_chapters():
    md = _md(
        (0, [("h2", "الفصل الأول"), ("p", "نص الفصل الأول.")]),
        (1, [("h2", "الفصل الثاني"), ("p", "نص الفصل الثاني.")]),
    )
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    assert len(book.chapters) == 2
    assert book.chapters[0].title == "الفصل الأول"
    assert book.chapters[1].title == "الفصل الثاني"


def test_split_every_fallback_without_headings():
    pages = [(i, [("p", f"نص الصفحة رقم {i}.")]) for i in range(4)]
    md = _md(*pages)
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=2)
    assert len(book.chapters) == 2  # 4 pages / 2 per chapter


def test_deeper_heading_stays_in_body():
    # Document uses ## for chapters; ### is a sub-heading inside the chapter.
    md = _md(
        (0, [("h2", "الباب الأول"), ("h3", "مبحث"), ("p", "نص.")]),
        (1, [("h2", "الباب الثاني"), ("p", "نص آخر.")]),
    )
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    assert len(book.chapters) == 2
    first_kinds = [p.kind for p in book.chapters[0].elements if isinstance(p, Paragraph)]
    assert "h3" in first_kinds  # the sub-heading did not open a new chapter


def test_break_heading_not_merged_into_deeper_heading():
    # A deeper (h3) heading appears before the first chapter (h2) heading.
    # The h2 must start its own chapter, not get merged into the h3 title.
    md = _md(
        (0, [("h3", "تصدير"), ("h2", "الفصل الأول"), ("p", "نص.")]),
        (1, [("h2", "الفصل الثاني"), ("p", "نص آخر.")]),
    )
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    titles = [ch.title for ch in book.chapters]
    assert "الفصل الأول" in titles
    assert "الفصل الثاني" in titles
    assert not any("تصدير" in t and "الفصل" in t for t in titles)


def test_scan_image_maps_to_pageimage():
    lines = [emit_page_break(2), emit_scan("scans/page_0003.png")]
    md = "\n".join(lines)
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    imgs = [el for ch in book.chapters for el in ch.elements if isinstance(el, PageImage)]
    assert len(imgs) == 1
    assert imgs[0].page_no == 2
    assert imgs[0].image_path == "scans/page_0003.png"


def test_front_matter_round_trip():
    front = emit_front_matter({"title": "كتاب", "author": "مؤلف", "language": "ar"})
    md = "\n".join(front) + "\n" + _md((0, [("p", "نص.")]))
    meta, body = parse_front_matter(md)
    assert meta == {"title": "كتاب", "author": "مؤلف", "language": "ar"}
    assert "نص." in body
    assert not body.lstrip().startswith("---")


def test_front_matter_absent_returns_body_unchanged():
    md = _md((0, [("p", "لا ترويسة هنا.")]))
    meta, body = parse_front_matter(md)
    assert meta == {}
    assert body == md


def test_front_matter_unclosed_treated_as_body():
    md = "---\ntitle: بلا إغلاق\nنص عادي بعده."
    meta, body = parse_front_matter(md)
    assert meta == {}          # lenient: no closing fence → nothing parsed
    assert body == md          # and nothing lost


def test_front_matter_value_with_colon_survives():
    front = emit_front_matter({"title": "كتاب: عنوان فرعي", "author": "", "language": "ar"})
    meta, _ = parse_front_matter("\n".join(front))
    assert meta["title"] == "كتاب: عنوان فرعي"


def test_deep_headings_clamp_to_h3():
    md = "\n".join([emit_page_break(0), "#### عنوان عميق", "##### أعمق", "###### الأعمق"])
    items = [(k, t) for _, k, t, _n in _parse_public(md)]
    assert items == [("h3", "عنوان عميق"), ("h3", "أعمق"), ("h3", "الأعمق")]


def test_hand_edited_list_markers_accepted():
    md = "\n".join([emit_page_break(0), "* أولا", "٭ ثانيا", "١- بند مرقّم", "٢) بند آخر"])
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    kinds = [(p.kind, p.text) for ch in book.chapters for p in ch.elements
             if isinstance(p, Paragraph)]
    assert ("ul", "أولا") in kinds and ("ul", "ثانيا") in kinds
    assert ("ol", "بند مرقّم") in kinds and ("ol", "بند آخر") in kinds


def test_unknown_and_unclosed_fences_warn_but_keep_text():
    warnings: list[str] = []
    md = "\n".join([emit_page_break(0), ":::note", "محتوى ملاحظة", ":::",
                    ":::verse", "بيت بلا إغلاق"])
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10,
                            on_warning=warnings.append)
    texts = [p.text for ch in book.chapters for p in ch.elements if isinstance(p, Paragraph)]
    assert "محتوى ملاحظة" in texts       # inner content of unknown fence survived
    assert "بيت بلا إغلاق" in texts       # unclosed verse consumed to EOF, not lost
    assert len(warnings) >= 2             # unknown fence + unclosed block both reported


def test_no_page_comments_all_land_on_page_zero():
    md = "\n".join(["# عنوان", "فقرة بلا تعليق صفحة."])
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    kinds = [p.kind for ch in book.chapters for p in ch.elements if isinstance(p, Paragraph)]
    assert "h1" in kinds and "p" in kinds


def test_crlf_line_endings_parse():
    md = "\r\n".join([emit_page_break(0), "# عنوان", "- بند", "- بند ثان", "فقرة."])
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    kinds = [p.kind for ch in book.chapters for p in ch.elements if isinstance(p, Paragraph)]
    assert kinds[0] == "h1" and kinds.count("ul") == 2 and "p" in kinds


def test_all_kinds_round_trip_parity():
    elements = [
        ("h1", "الباب"), ("h2", "الفصل"), ("h3", "المبحث"),
        ("p", "فقرة عادية."), ("p", "# فقرة تبدأ بمربّع"), ("p", "- فقرة تبدأ بشرطة"),
        ("ul", "بند أول"), ("ul", "بند ثان"),
        ("ol", "أولا"), ("ol", "ثانيا"),
        ("verse", "شطر  شطر"), ("verse", "بيت  تكملة"),
        ("quran", "﴿ نص قرآني ﴾"),
    ]
    md = _md((0, elements))
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    out = [(p.kind, p.text) for ch in book.chapters for p in ch.elements
           if isinstance(p, Paragraph)]
    assert out == elements


def _parse_public(md: str):
    """Expose the private flat-item parser for assertions."""
    from pdf2ebook.textproc.markdownize import _parse_items
    return _parse_items(md)


def test_footnote_emit_parse_round_trip():
    lines = [emit_page_break(0)] + emit_elements(
        [("p", "متن فيه إشارة (١)"), ("footnote", "نص الحاشية")], page_no=0)
    md = "\n".join(lines)
    assert "[^p1-1]: نص الحاشية" in md
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    paras = [(p.kind, p.text, p.note_id) for ch in book.chapters for p in ch.elements
             if isinstance(p, Paragraph)]
    assert ("footnote", "نص الحاشية", "p1-1") in paras


def test_paragraph_starting_with_caret_bracket_escaped():
    elements = [("p", "[^ليس حاشية بل فقرة]")]
    md = _md((0, elements))
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    out = [(p.kind, p.text) for ch in book.chapters for p in ch.elements
           if isinstance(p, Paragraph)]
    assert out == elements


def test_inline_body_ref_passes_through_unchanged():
    elements = [("p", "متن فيه [^p1-1] إشارة داخلية")]
    md = _md((0, elements))
    book = markdown_to_book(md, title="t", author="", language="ar", split_every=10)
    out = [(p.kind, p.text) for ch in book.chapters for p in ch.elements
           if isinstance(p, Paragraph)]
    assert out == elements


def test_footnote_relocated_to_ref_chapter():
    # Chapter 2 starts mid-page above nothing — the ref is in chapter 1 but the
    # page-end definition would land in chapter 2. It must be relocated back.
    md = "\n".join([
        emit_page_break(0),
        "## الفصل الأول",
        "نص فيه [^p1-1] إشارة",
        "## الفصل الثاني",
        "[^p1-1]: نص الحاشية",
    ])
    book = markdown_to_book(md, title="ك", author="", language="ar", split_every=10)
    ch0 = [p for p in book.chapters[0].elements
           if isinstance(p, Paragraph) and p.kind == "footnote"]
    ch1 = [p for p in book.chapters[1].elements
           if isinstance(p, Paragraph) and p.kind == "footnote"]
    assert len(ch0) == 1 and ch0[0].note_id == "p1-1"
    assert ch1 == []


def test_split_footnote_ref_resolves_in_epub(tmp_path):
    md = "\n".join([
        emit_page_break(0),
        "## الفصل الأول",
        "نص فيه [^p1-1] إشارة",
        "## الفصل الثاني",
        "[^p1-1]: نص الحاشية",
    ])
    book = markdown_to_book(md, title="ك", author="", language="ar", split_every=10)
    out = tmp_path / "split.epub"
    build_reflow_epub(book, out, tmp_path, font_files=[])
    chap = zipfile.ZipFile(out).read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert "<sup>111</sup>" not in chap        # no fabricated page+ordinal number
    assert 'href="#fn-p1-1"' in chap           # ref resolves
    assert 'id="fn-p1-1"' in chap              # aside relocated into this chapter
    assert 'epub:type="backlink"' in chap
    xml.dom.minidom.parseString(chap)


def test_note_cited_twice_has_no_duplicate_id(tmp_path):
    from pdf2ebook.book import Book, Chapter
    chapter = Chapter(title="ف", elements=[
        Paragraph("مرة [^p1-1] وأخرى [^p1-1]", "p"),
        Paragraph("نص الحاشية", "footnote", "p1-1"),
    ])
    out = tmp_path / "dup.epub"
    build_reflow_epub(Book(title="ك", chapters=[chapter]), out, tmp_path, font_files=[])
    chap = zipfile.ZipFile(out).read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert chap.count('id="ref-fn-p1-1"') == 1   # only the first citation owns the id
    assert chap.count('href="#fn-p1-1"') == 2     # both citations link to the note
    xml.dom.minidom.parseString(chap)             # valid XHTML: no duplicate id


def test_unknown_footnote_ref_drops_marker(tmp_path):
    from pdf2ebook.book import Book, Chapter
    chapter = Chapter(title="ف", elements=[Paragraph("نص فيه [^ghost] بلا حاشية", "p")])
    out = tmp_path / "ghost.epub"
    build_reflow_epub(Book(title="ك", chapters=[chapter]), out, tmp_path, font_files=[])
    chap = zipfile.ZipFile(out).read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert "ghost" not in chap and "<sup>" not in chap   # no fabricated marker
    xml.dom.minidom.parseString(chap)


def test_footnotes_render_in_epub(tmp_path):
    md = "\n".join([
        emit_page_break(0),
        "متن فيه [^p1-1] إشارة",
        "[^p1-1]: نص الحاشية الأولى",
        "[^p1-9]: حاشية بلا إشارة",
    ])
    book = markdown_to_book(md, title="كتاب", author="", language="ar", split_every=10)
    out = tmp_path / "book.epub"
    build_reflow_epub(book, out, tmp_path, font_files=[])
    chap = zipfile.ZipFile(out).read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert 'epub:type="noteref"' in chap and 'href="#fn-p1-1"' in chap
    assert 'epub:type="footnote"' in chap and 'id="fn-p1-1"' in chap
    assert 'epub:type="backlink"' in chap        # matched note is back-linked
    assert 'id="fn-p1-9"' in chap                # unmatched note still rendered
    xml.dom.minidom.parseString(chap)            # well-formed XHTML


def test_full_chain_to_epub(tmp_path):
    scan = tmp_path / "scans" / "page_0002.png"
    scan.parent.mkdir()
    from PIL import Image
    Image.new("L", (400, 600), 200).save(scan)

    md = _md(
        (0, [("h1", "الباب الأول"), ("p", "فقرة افتتاحية."),
             ("ul", "أولا"), ("ul", "ثانيا"), ("h3", "مبحث"), ("p", "ختام.")]),
    )
    # add an image page after the text page
    md += "\n" + emit_page_break(1) + "\n" + emit_scan("scans/page_0002.png")
    book = markdown_to_book(md, title="كتاب", author="", language="ar", split_every=10)
    out = tmp_path / "book.epub"
    build_reflow_epub(book, out, tmp_path, font_files=[])

    zf = zipfile.ZipFile(out)
    assert zf.read("mimetype") == b"application/epub+zip"
    chap = zf.read("OEBPS/text/chap_001.xhtml").decode("utf-8")
    assert "<h1>" in chap
    assert "<ul>" in chap and chap.count("<li>") == 2
    assert "<h3>" in chap
    assert "figure" in chap  # the embedded scan
    xml.dom.minidom.parseString(chap)  # well-formed XHTML
