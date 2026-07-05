from __future__ import annotations

from pdf2ebook.ocr.base import OcrLine, OcrPage, OcrWord
from pdf2ebook.textproc.footnotes import (
    Footnote,
    footnote_id,
    rewrite_body_refs,
    split_footnotes,
)
from pdf2ebook.textproc.structure import structure_page


def _line(text: str, y: int, size: float = 0.0, h: int = 14, x: int = 100, w: int = 400) -> OcrLine:
    return OcrLine(words=[OcrWord(text, 100.0, (x, y, w, h))], bbox=(x, y, w, h), size=size)


def _no_drop(text, edge):
    return False


# --- detection: text-layer font sizes -----------------------------------------

def test_split_footnotes_text_layer_with_wrapped_note():
    page = OcrPage(page_no=0, size=(600, 800), lines=[
        _line("نص المتن الأول في أعلى الصفحة", y=100, size=12),
        _line("نص المتن الثاني", y=140, size=12),
        _line("(١) هذه حاشية أولى", y=600, size=9),
        _line("تكملة الحاشية الأولى المفصّلة", y=622, size=9),
        _line("(٢) حاشية ثانية قصيرة", y=650, size=9),
    ])
    kept, notes = split_footnotes(page, body_size=12.0)
    assert len(notes) == 2
    assert notes[0].label == "١"
    assert "تكملة" in notes[0].text          # wrapped continuation joined in
    assert notes[1].label == "٢"
    assert len(kept.lines) == 2               # only the body lines survive


# --- detection: OCR line heights ----------------------------------------------

def test_split_footnotes_ocr_line_heights():
    page = OcrPage(page_no=1, size=(600, 800), lines=[
        _line("متن بحجم كبير للقراءة", y=100, h=20),
        _line("سطر متن آخر بنفس الحجم", y=140, h=20),
        _line("(1) حاشية بخط صغير في الأسفل", y=650, h=12),
    ])
    kept, notes = split_footnotes(page, body_size=0.0)
    assert len(notes) == 1 and notes[0].label == "1"
    assert len(kept.lines) == 2


# --- negatives ----------------------------------------------------------------

def test_marker_at_top_of_page_not_a_footnote():
    page = OcrPage(page_no=0, size=(600, 800), lines=[
        _line("(١) رقم في الأعلى وليس حاشية", y=50, size=9),
        _line("متن الصفحة العادي هنا", y=400, size=12),
    ])
    _, notes = split_footnotes(page, body_size=12.0)
    assert notes == []


def test_body_size_bottom_line_not_a_footnote():
    page = OcrPage(page_no=0, size=(600, 800), lines=[
        _line("متن", y=100, size=12),
        _line("(١) سطر في الأسفل لكن بحجم المتن", y=650, size=12),
    ])
    _, notes = split_footnotes(page, body_size=12.0)
    assert notes == []


def test_page_without_markers_untouched():
    page = OcrPage(page_no=0, size=(600, 800), lines=[
        _line("متن", y=100, size=12),
        _line("سطر صغير بلا علامة في الأسفل", y=650, size=9),
    ])
    kept, notes = split_footnotes(page, body_size=12.0)
    assert notes == []
    assert kept is page


# --- integration: dash-marker note is not read as an ordered list -------------

def test_dash_footnote_is_split_not_listed():
    page = OcrPage(page_no=0, size=(600, 800), lines=[
        _line("فقرة المتن الطويلة الكافية للقراءة هنا وهناك", y=100, size=12),
        _line("١- الحاشية الأولى", y=640, size=9),
        _line("٢- الحاشية الثانية", y=670, size=9),
    ])
    kept, notes = split_footnotes(page, body_size=12.0)
    assert len(notes) == 2
    els = structure_page(kept, keep_diacritics=True, drop_line=_no_drop, body_size=12.0)
    assert all(kind != "ol" for kind, _ in els)


# --- body-ref rewriting -------------------------------------------------------

def test_rewrite_unique_body_ref():
    notes = [Footnote(label="١", text="حاشية")]
    els = [("p", "في المتن (١) إشارة إلى الحاشية")]
    out = rewrite_body_refs(els, notes, page_no=0)
    assert footnote_id(0, 1) == "p1-1"
    assert "[^p1-1]" in out[0][1]
    assert "(١)" not in out[0][1]


def test_ambiguous_body_ref_left_alone():
    notes = [Footnote(label="١", text="حاشية")]
    els = [("p", "مرة (١) وأخرى (١) في نفس الصفحة")]
    out = rewrite_body_refs(els, notes, page_no=0)
    assert "[^" not in out[0][1]     # label appears twice → not rewritten


def test_arabic_label_matches_latin_body_marker():
    notes = [Footnote(label="١", text="حاشية")]
    els = [("p", "في المتن (1) إشارة")]      # body uses a Latin digit
    out = rewrite_body_refs(els, notes, page_no=0)
    assert "[^p1-1]" in out[0][1]
