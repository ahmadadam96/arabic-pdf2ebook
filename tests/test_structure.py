from __future__ import annotations

from pdf2ebook.ocr.base import OcrLine, OcrPage, OcrWord
from pdf2ebook.textproc.dehyphen import join_wrapped
from pdf2ebook.textproc.headings import heading_tiers
from pdf2ebook.textproc.lists import detect_list_items
from pdf2ebook.textproc.structure import structure_page


def _line(text: str, x: int, y: int, w: int, h: int = 14, size: float = 0.0,
          bold: float = 0.0) -> OcrLine:
    return OcrLine(words=[OcrWord(text, 100.0, (x, y, w, h))], bbox=(x, y, w, h),
                   size=size, bold=bold)


def _no_drop(text: str, edge: bool) -> bool:
    return False


def test_font_size_heading_tiers():
    # Body size 12; sizes 24/18/14/12 → h1/h2/h3/none. Centered to pass the gate.
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("عنوان كبير", 400, 10, 200, 30, size=24),
        _line("عنوان متوسط", 400, 60, 200, 24, size=18),
        _line("عنوان صغير", 400, 110, 200, 18, size=14),
        _line("سطر نص عادي", 400, 160, 200, 14, size=12),
    ])
    tiers = heading_tiers(page, body_size=12.0)
    assert tiers == {0: "h1", 1: "h2", 2: "h3"}


def test_bold_body_size_line_is_heading():
    # Bold, body-size, off-centre, no keyword → still a heading (h3) via bold arm.
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("عنوان عريض بحجم المتن", 100, 10, 300, 14, size=12, bold=0.8),
        _line("نص عادي غير عريض يمتد هنا", 100, 60, 800, 14, size=12, bold=0.0),
    ])
    tiers = heading_tiers(page, body_size=12.0)
    assert tiers.get(0) == "h3"
    assert 1 not in tiers


def test_bold_bigger_line_is_h2():
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("عنوان عريض وكبير", 100, 10, 300, 20, size=18, bold=0.9),
        _line("نص عادي هنا", 100, 60, 800, 14, size=12),
    ])
    assert heading_tiers(page, body_size=12.0).get(0) == "h2"


def test_weak_bold_is_not_a_heading():
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("سطر بحبر خفيف نسبيا", 100, 10, 300, 14, size=12, bold=0.4),
        _line("نص عادي", 100, 60, 800, 14, size=12),
    ])
    assert heading_tiers(page, body_size=12.0) == {}


def test_bold_ignored_on_ocr_pages():
    # OCR page: size=0 → bold must be inert, behaviour unchanged.
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("سطر من OCR عريض", 100, 10, 300, 14, size=0.0, bold=1.0),
        _line("نص آخر عادي", 100, 60, 800, 14, size=0.0),
    ])
    assert 0 not in heading_tiers(page, body_size=0.0)


def test_ocrline_json_round_trip_with_bold():
    import json
    page = OcrPage(page_no=0, size=(100, 100),
                   lines=[_line("x", 0, 0, 10, size=12.0, bold=0.75)])
    blob = page.to_json()
    assert OcrPage.from_json(blob).lines[0].bold == 0.75
    # A legacy blob without the bold key loads as 0.0.
    data = json.loads(blob)
    for ln in data["lines"]:
        ln.pop("bold", None)
    assert OcrPage.from_json(json.dumps(data)).lines[0].bold == 0.0


def test_ocr_height_fallback_heading():
    # No font size (OCR): a tall, centered line is a heading via height ratio.
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("الباب الأول", 400, 10, 200, 45),   # ~2.6x median height
        _line("نص الفقرة العادية هنا", 100, 70, 800, 14),
        _line("سطر آخر من النص العادي", 100, 100, 800, 14),
    ])
    tiers = heading_tiers(page, body_size=0.0)
    assert 0 in tiers and tiers[0] in ("h1", "h2", "h3")
    assert 1 not in tiers and 2 not in tiers


def test_list_detection_bullets_and_numbers():
    bullets = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("• البند الأول", 100, 10, 400),
        _line("• البند الثاني", 100, 40, 400),
    ])
    items = detect_list_items(bullets)
    assert items == {0: ("ul", "البند الأول"), 1: ("ul", "البند الثاني")}

    numbered = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("١. أولا", 100, 10, 400),
        _line("٢. ثانيا", 100, 40, 400),
        _line("3) ثالثا", 100, 70, 400),
    ])
    items = detect_list_items(numbered)
    assert items[0] == ("ol", "أولا")
    assert items[2] == ("ol", "ثالثا")


def test_single_dash_line_is_not_a_list():
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("- سطر وحيد يبدأ بشرطة وليس قائمة", 100, 10, 600),
        _line("نص عادي يتبعه.", 100, 40, 600),
    ])
    assert detect_list_items(page) == {}


def test_dehyphenation_latin_only():
    assert join_wrapped(["exam-", "ple done."]) == "example done."
    # Arabic wraps are space-joined, never de-hyphenated.
    assert join_wrapped(["السطر الأول", "والسطر الثاني"]) == "السطر الأول والسطر الثاني"


def test_structure_page_emits_list_and_heading():
    page = OcrPage(page_no=0, size=(1000, 1000), lines=[
        _line("الباب الأول", 400, 10, 200, 30, size=24),
        _line("هذه فقرة افتتاحية تحتوي على نص كافٍ للقراءة.", 100, 60, 800, 12, size=12),
        _line("• أولا", 100, 100, 400, 12, size=12),
        _line("• ثانيا", 100, 130, 400, 12, size=12),
    ])
    els = structure_page(page, keep_diacritics=True, drop_line=_no_drop, body_size=12.0)
    kinds = [k for k, _ in els]
    assert kinds[0] == "h1"
    assert "p" in kinds
    assert kinds.count("ul") == 2


# --- flat fallback (the coverage safety net) -----------------------------------

def _no_drop(text: str, edge: bool) -> bool:
    return False


def test_flat_builder_keeps_every_kept_line():
    """It loses structure by design; it must not lose a single line."""
    from pagefixtures import line, page

    from pdf2ebook.textproc.structure import structure_page_flat

    texts = ["السطر الأول من الصفحة", "السطر الثاني من الصفحة", "والسطر الثالث والأخير"]
    elements = structure_page_flat(page([line(t, 100 + 22 * i) for i, t in enumerate(texts)]),
                                   True, _no_drop)
    assert [kind for kind, _ in elements] == ["p", "p", "p"]
    assert [text for _, text in elements] == texts


def test_flat_builder_still_honours_the_junk_filter():
    """Junk removal is not structure — a watermark must stay dropped."""
    from pagefixtures import line, page

    from pdf2ebook.textproc.structure import structure_page_flat

    def drop_watermark(text: str, edge: bool) -> bool:
        return "noor-book" in text

    pg = page([line("www.noor-book.com", 30), line("نص الصفحة الحقيقي هنا وفيه كلام", 100)])
    elements = structure_page_flat(pg, True, drop_watermark)
    assert [text for _, text in elements] == ["نص الصفحة الحقيقي هنا وفيه كلام"]


def test_low_coverage_page_falls_back_to_flat(monkeypatch):
    """The measurement becomes an actuator.

    The smart structurer is stubbed to drop half the page — the observable
    contract is that the fallback fires, recovers the text, and says so in the
    report rather than shipping a page with 50% of its words missing.
    """
    from pagefixtures import line, page

    from pdf2ebook import ocrmode
    from pdf2ebook.config import PipelineOptions
    from pdf2ebook.report import ROUTE_TEXT

    texts = [f"هذا هو السطر رقم {n} وفيه كلام كثير عن أحوال القوم وأخبارهم" for n in range(8)]
    pg = page([line(t, 100 + 22 * i) for i, t in enumerate(texts)])

    def lossy_structure_page(page_arg, keep_diacritics, drop_line, body_size=0.0):
        real = [ln for ln in page_arg.lines if ln.text.strip()]
        return [("p", ln.text) for ln in real[: len(real) // 2]]

    monkeypatch.setattr(ocrmode, "structure_page", lossy_structure_page)

    data = ocrmode.PageData(index=0, kind="text", payload=pg, route=ROUTE_TEXT)
    markdown, report = ocrmode.structure_pages([data], PipelineOptions(), 12.0)

    page_report = report.pages[0]
    assert page_report.coverage >= 0.99, "fallback should have recovered the whole page"
    assert any("structure fallback" in note for note in page_report.notes)
    for text in texts:
        assert text in markdown


def test_structure_fallback_can_be_turned_off(monkeypatch):
    from pagefixtures import line, page

    from pdf2ebook import ocrmode
    from pdf2ebook.config import PipelineOptions
    from pdf2ebook.report import ROUTE_TEXT

    texts = [f"سطر رقم {n} وفيه كلام كثير عن أحوال القوم وأخبارهم ومغازيهم" for n in range(8)]
    pg = page([line(t, 100 + 22 * i) for i, t in enumerate(texts)])
    monkeypatch.setattr(ocrmode, "structure_page",
                        lambda p, k, d, b=0.0: [("p", ln.text)
                                                for ln in list(p.lines)[: len(p.lines) // 2]])
    data = ocrmode.PageData(index=0, kind="text", payload=pg, route=ROUTE_TEXT)
    _, report = ocrmode.structure_pages([data], PipelineOptions(structure_fallback=False), 12.0)
    assert report.pages[0].coverage < 0.8
    assert not any("fallback" in note for note in report.pages[0].notes)


def test_fallback_keeps_footnote_definitions(monkeypatch):
    """The fallback rebuilds the body; the page's notes must come with it."""
    from pagefixtures import NOTE, line, page

    from pdf2ebook import ocrmode
    from pdf2ebook.config import PipelineOptions
    from pdf2ebook.report import ROUTE_TEXT

    body = [line(f"سطر المتن رقم {n} وفيه كلام طويل عن أخبار القوم وأنسابهم", 100 + 22 * n)
            for n in range(8)]
    notes = [line("(١) انظر جمهرة أنساب العرب صفحة مئة وعشرين", 700, NOTE)]
    monkeypatch.setattr(ocrmode, "structure_page",
                        lambda p, k, d, b=0.0: [("p", ln.text)
                                                for ln in list(p.lines)[: len(p.lines) // 2]])
    data = ocrmode.PageData(index=0, kind="text", payload=page(body + notes), route=ROUTE_TEXT)
    markdown, _ = ocrmode.structure_pages([data], PipelineOptions(), 12.0)
    assert "[^p1-1]: انظر جمهرة أنساب العرب" in markdown
