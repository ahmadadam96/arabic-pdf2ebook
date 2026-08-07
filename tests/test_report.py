from __future__ import annotations

from pdf2ebook.ocr.base import OcrLine, OcrPage, OcrWord
from pdf2ebook.ocrmode import _image_route_and_reason, _page_char_counts, _select_text_layer
from pdf2ebook.report import (
    ROUTE_BLANK,
    ROUTE_IMAGE,
    ROUTE_OCR,
    ROUTE_TEXT,
    ConversionReport,
    PageReport,
    collect_warnings,
    coverage_key,
)


def _page(n_words: int, conf: float) -> OcrPage:
    line = OcrLine(words=[OcrWord("كلمة", conf, (0, 0, 10, 10)) for _ in range(n_words)],
                   bbox=(0, 0, 100, 10))
    return OcrPage(page_no=0, size=(100, 100), lines=[line] if n_words else [])


def test_image_reason_too_few_words_is_not_low_confidence():
    # High confidence, but under the 15-word minimum → reason must say so, not
    # "low confidence" (regression for the self-contradictory diagnostic).
    route, reason = _image_route_and_reason(_page(5, 90.0), image_page=False,
                                            foreign_reason="", min_conf=40.0)
    assert route == ROUTE_IMAGE
    assert "too few words" in reason and "low confidence" not in reason


def test_image_reason_low_confidence():
    route, reason = _image_route_and_reason(_page(30, 20.0), image_page=False,
                                            foreign_reason="", min_conf=40.0)
    assert route == ROUTE_IMAGE and "low confidence" in reason


def test_image_reason_blank_vs_photo_vs_foreign():
    assert _image_route_and_reason(_page(0, 0.0), False, "", 40.0)[0] == ROUTE_BLANK
    assert _image_route_and_reason(_page(0, 0.0), True, "", 40.0)[0] == ROUTE_IMAGE
    assert _image_route_and_reason(_page(30, 90.0), False, "arabic-ratio 41%", 40.0)[1] \
        == "arabic-ratio 41%"


def _line(text: str) -> OcrLine:
    return OcrLine(words=[OcrWord(text, 100.0, (0, 0, 100, 14))], bbox=(0, 0, 100, 14))


# --- text-layer selection gate -------------------------------------------------

def test_select_text_layer_keeps_healthy_pages():
    samples = {0: "وكان المسلمون بالأندلس يستنجدون بسلاطين المغرب", 1: "نص عربي سليم وواضح تماما"}
    allowed, reasons, _ = _select_text_layer(samples, "auto")
    assert allowed == {0, 1}
    assert reasons == {}


def test_select_text_layer_drops_bad_glyph_page():
    samples = {0: "نص عربي سليم وواضح", 1: " نص"}
    allowed, reasons, _ = _select_text_layer(samples, "auto")
    assert 0 in allowed and 1 not in allowed
    assert 1 in reasons and "bad glyph" in reasons[1].lower()


def test_select_text_layer_always_bypasses_gates():
    samples = {0: ""}  # pure PUA, would normally be dropped
    allowed, reasons, _ = _select_text_layer(samples, "always")
    assert allowed == {0}
    assert reasons == {}


def test_select_text_layer_corruption_discards_whole_layer():
    corrupted = "وأنت الخر ل شيء بعدك وأنت الفردا ل شريك لك السإلمية يا واهب العقول " * 10
    samples = {0: corrupted, 1: corrupted}
    allowed, reasons, _ = _select_text_layer(samples, "auto")
    assert allowed == set()
    assert set(reasons) == {0, 1}


def test_select_text_layer_drops_only_corrupted_pages():
    healthy = "نص عربي سليم وواضح في صفحة قصيرة لكنها صالحة"
    corrupted = "وأنت الخر ل شيء بعدك وأنت الفردا ل شريك لك السإلمية يا واهب العقول " * 10
    allowed, reasons, _ = _select_text_layer({0: healthy, 1: corrupted}, "auto")
    assert allowed == {0}
    assert set(reasons) == {1}


# --- coverage counting ---------------------------------------------------------

def test_coverage_key_strips_space_and_diacritics():
    assert coverage_key("كتاب  جميل") == coverage_key("كتابجميل")
    # diacritics ignored so counts do not depend on keep-diacritics policy
    assert coverage_key("كَتَبَ") == coverage_key("كتب")


def test_page_char_counts_treats_dropped_watermark_as_dropped():
    page = OcrPage(page_no=0, size=(600, 800), lines=[
        _line("http://kotob.has.it"),                 # watermark → dropped
        _line("هذه فقرة حقيقية فيها نص عربي مفيد"),
        _line("وهذه فقرة أخرى تكمل النص"),
        _line("سطر ثالث من النص الحقيقي"),
    ])

    def drop_line(text, edge):
        from pdf2ebook.textproc import clean
        return clean.is_watermark(text)

    kept, dropped = _page_char_counts(page, keep_diacritics=True, drop_line=drop_line)
    assert dropped > 0            # the watermark chars were counted as dropped
    assert kept > dropped         # real text dominates


# --- report model + warnings ---------------------------------------------------

def test_conversion_report_json_round_trip():
    report = ConversionReport(
        pages=[
            PageReport(0, ROUTE_TEXT, "embedded text layer", 100.0, 500, 10, 495, 0.99),
            PageReport(1, ROUTE_OCR, "OCR", 82.0, 300, 5, 250, 0.833),
        ],
        warnings=["تحذير — warning"],
    )
    restored = ConversionReport.from_json(report.to_json())
    assert restored == report
    assert restored.route_counts() == {ROUTE_TEXT: 1, ROUTE_OCR: 1}


def test_collect_warnings_book_coverage_below_threshold():
    report = ConversionReport(pages=[
        PageReport(0, ROUTE_OCR, "OCR", 80.0, source_chars=100, emitted_chars=94, coverage=0.94),
    ])
    warnings = collect_warnings(report)
    assert any("coverage" in w for w in warnings)


def test_collect_warnings_flags_single_low_page_not_book():
    report = ConversionReport(pages=[
        PageReport(0, ROUTE_OCR, "OCR", 90.0, source_chars=1000, emitted_chars=1000, coverage=1.0),
        PageReport(1, ROUTE_OCR, "OCR", 70.0, source_chars=100, emitted_chars=79, coverage=0.79),
    ])
    warnings = collect_warnings(report)
    assert report.book_coverage >= 0.95            # book-level fine
    assert any("page 2" in w or "صفحة 2" in w for w in warnings)


def test_collect_warnings_silent_when_healthy():
    report = ConversionReport(pages=[
        PageReport(0, ROUTE_OCR, "OCR", 95.0, source_chars=1000, emitted_chars=1000, coverage=1.0),
        PageReport(1, ROUTE_OCR, "OCR", 90.0, source_chars=100, emitted_chars=90, coverage=0.90),
    ])
    assert collect_warnings(report) == []


def test_collect_warnings_ignores_near_empty_pages():
    # A tiny page (below _MIN_PAGE_CHARS) with low coverage must not warn.
    report = ConversionReport(pages=[
        PageReport(0, ROUTE_OCR, "OCR", 90.0, source_chars=1000, emitted_chars=1000, coverage=1.0),
        PageReport(1, ROUTE_OCR, "OCR", 90.0, source_chars=10, emitted_chars=2, coverage=0.20),
    ])
    assert collect_warnings(report) == []


# --- book-level text-layer verdict ---------------------------------------------

def _long(text: str, words: int = 60) -> str:
    """A page long enough for the ligature-loss signal to be statistically valid."""
    return " ".join([text] * (words // max(1, len(text.split())) + 1))


CORRUPT = _long("وأنت الخر ل شيء بعدك وأنت الفردا ل شريك لك السإلمية يا واهب العقول")
HEALTHY = _long("وقد ذكر أهل السير أن القوم نزلوا بهذا الوادي وأقاموا فيه دهرا طويلا")


def test_corrupt_majority_rejects_text_layer_book_wide():
    """A book that is mostly corrupt goes to OCR *whole*.

    Interleaving clean OCR pages with corrupt extracted ones is the single
    biggest source of a book that reads like two different books.
    """
    samples = {0: CORRUPT, 1: CORRUPT, 2: CORRUPT, 3: HEALTHY}
    allowed, reasons, verdict = _select_text_layer(samples, "auto")
    assert allowed == set(), "no page may use the text layer once the book is rejected"
    assert set(reasons) == set(samples)
    assert "book-wide" in verdict


def test_healthy_majority_keeps_text_layer_and_demotes_only_bad_pages():
    samples = {0: HEALTHY, 1: HEALTHY, 2: HEALTHY, 3: CORRUPT}
    allowed, reasons, verdict = _select_text_layer(samples, "auto")
    assert allowed == {0, 1, 2}
    assert set(reasons) == {3}
    assert "accepted" in verdict


def test_short_page_follows_the_book_verdict_not_its_own():
    """The regression this whole verdict exists for.

    `looks_corrupted_arabic` needs ~40 words, so a sparse heading page can
    never be judged on its own. It used to sail through the per-page gate and
    land corrupt text next to clean OCR; now it follows the book.
    """
    short_corrupt = "وأنت الخر ل شيء بعدك"          # too short to measure
    samples = {0: CORRUPT, 1: CORRUPT, 2: CORRUPT, 3: short_corrupt}
    allowed, _, _ = _select_text_layer(samples, "auto")
    assert 3 not in allowed

    # …and the mirror case: a healthy book keeps its sparse pages.
    samples = {0: HEALTHY, 1: HEALTHY, 2: HEALTHY, 3: "عنوان الفصل الأول"}
    allowed, _, _ = _select_text_layer(samples, "auto")
    assert 3 in allowed


def test_bad_glyphs_are_conclusive_at_any_length():
    """PUA codepoints carry no meaning regardless of how few there are."""
    samples = {0: HEALTHY, 1: HEALTHY, 2: HEALTHY, 3: "\ue001\ue002 \ue003"}
    allowed, reasons, _ = _select_text_layer(samples, "auto")
    assert 3 not in allowed
    assert "bad glyph" in reasons[3].lower()


def test_always_bypasses_the_book_verdict():
    samples = {0: CORRUPT, 1: CORRUPT, 2: CORRUPT}
    allowed, reasons, verdict = _select_text_layer(samples, "always")
    assert allowed == set(samples) and reasons == {}
    assert "forced" in verdict


# --- degradation ledger --------------------------------------------------------

def test_page_report_carries_notes_through_json():
    report = ConversionReport(book_verdict="ok", pages=[
        PageReport(page_no=0, route=ROUTE_TEXT, notes=["font size missing for 3/10 chars"]),
        PageReport(page_no=1, route=ROUTE_TEXT),
    ])
    restored = ConversionReport.from_json(report.to_json())
    assert restored.book_verdict == "ok"
    assert restored.pages[0].notes == ["font size missing for 3/10 chars"]
    assert [p.page_no for p in restored.degraded_pages()] == [0]


def test_from_json_tolerates_reports_written_by_older_versions():
    legacy = '{"pages": [{"page_no": 0, "route": "ocr", "coverage": 0.9, "gone": 1}]}'
    restored = ConversionReport.from_json(legacy)
    assert restored.pages[0].coverage == 0.9
    assert restored.pages[0].notes == []
    assert restored.book_verdict == ""
