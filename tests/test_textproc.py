from __future__ import annotations

from pdf2ebook.ocr.base import OcrLine, OcrPage, OcrWord
from pdf2ebook.textproc import clean
from pdf2ebook.textproc.chapters import looks_like_heading_text
from pdf2ebook.textproc.paragraphs import merge_page_boundary, page_paragraphs


def test_normalize_strips_tatweel_and_presentation_forms():
    assert clean.normalize_arabic("كــــتاب") == "كتاب"
    # U+FEDF/U+FEEA etc. presentation forms fold to base letters
    assert clean.normalize_arabic("ﻟﻠﻪ") == "لله"


def test_normalize_keeps_old_orthography():
    # hamza/alef variants must survive — old spelling is content
    assert clean.normalize_arabic("إسبانيا وأمراؤها") == "إسبانيا وأمراؤها"


def test_watermark_seeds():
    assert clean.is_watermark("http://kotob.has.it/")
    assert clean.is_watermark("kotob . has . it")
    assert clean.is_watermark("Noor-Book.com تم التحميل من")
    assert not clean.is_watermark("في سنة ١٥٦٣ م ثار فرج بن فرج")


def test_page_numbers_arabic_and_latin():
    assert clean.is_page_number(" ٧٧ ")
    assert clean.is_page_number("- 154 -")
    assert clean.is_page_number("( ٢٣ )")
    assert not clean.is_page_number("في سنة 1492 سقطت غرناطة")


def test_find_repeated_lines_catches_mangled_watermark():
    # The watermark OCRs slightly differently on every page.
    variants = ["http://kotob.has.it/", "http//kotob.has.it", "httb://kotob.has.lt/",
                "http://kotob.has.it", "http:/kotob.has.it/"]
    pages = []
    for i in range(20):
        pages.append([f"سطر أول مختلف تماما {i}", f"نص الصفحة {i}", variants[i % len(variants)]])
    repeated = clean.find_repeated_lines(pages)
    assert repeated, "watermark variants should be detected as one repeated line"
    assert clean.matches_repeated("httq://kotob.has.it/", repeated)
    assert not clean.matches_repeated("وكانت غرناطة تدفع الجزية غالبا", repeated)


def test_junk_line_catches_mangled_watermark_at_page_edge():
    # The kotob.has.it watermark often OCRs as digit/punctuation debris.
    assert clean.is_junk_line("0 :/0)0. 8 1/", edge=True)
    assert clean.is_junk_line("* * *", edge=False)
    assert clean.is_junk_line("ل١", edge=True)
    # Real short Arabic edge lines survive.
    assert not clean.is_junk_line("وكان من خيرة بني الأحمر", edge=True)
    assert not clean.is_junk_line("مقدمة", edge=True)


def test_clean_heading_fixes_ocr_defects():
    # split alef-lam rejoined
    assert clean.clean_heading("من الفتح ا لإسلامي إلى سقوط غرناطة") == "من الفتح الإسلامي إلى سقوط غرناطة"
    # stray ASCII quote + leading misread numbering stripped
    assert clean.clean_heading('١" - سجون التفتيش بإسبانيا') == "سجون التفتيش بإسبانيا"
    # leading section number + tatweel separator stripped
    assert clean.clean_heading("٣ ـ اضطهادات المسلمين ونفيهم") == "اضطهادات المسلمين ونفيهم"
    # already-clean heading untouched
    assert clean.clean_heading("بنو الأحمر") == "بنو الأحمر"


def test_arabic_ratio_separates_arabic_from_latin_glyph_soup():
    assert clean.arabic_ratio("وكان المسلمون بالأندلس يستنجدون بسلاطين المغرب") > 0.8
    assert clean.arabic_ratio("Histoire Critique de l'Inquisition d'Espagne 1923") < 0.2
    mixed_garbage = "0ا 5ب8 ÷ل .. 7ك9 xx ها 3" * 5
    assert clean.arabic_ratio(mixed_garbage) < 0.6


def test_looks_corrupted_arabic_detects_broken_lam_alef():
    broken = "وأنت الخر ل شيء بعدك وأنت الفردا ل شريك لك السإلمية يا واهب العقول " * 10
    healthy = "وكان المسلمون بالأندلس يستنجدون بسلاطين المغرب كلما اشتد ضغط الإسبانيين عليهم " * 10
    assert clean.looks_corrupted_arabic(broken)
    assert not clean.looks_corrupted_arabic(healthy)


def test_bad_glyph_ratio_flags_pua_and_replacement_chars():
    # Clean Arabic -> 0.
    assert clean.bad_glyph_ratio("وكان المسلمون") == 0.0
    # Pure BMP Private-Use Area (a fake non-Unicode font layer) -> 1.
    assert clean.bad_glyph_ratio("") == 1.0
    # U+FFFD replacement chars count as bad (2 of 4 -> 0.5).
    assert clean.bad_glyph_ratio("��ab") == 0.5
    # Plane-15 PUA counts too.
    assert clean.bad_glyph_ratio("󰀀󰀁") == 1.0
    # Half PUA, half real; whitespace ignored (a, b, PUA, PUA -> 0.5).
    assert clean.bad_glyph_ratio("ab ") == 0.5
    # Empty / whitespace-only -> 0.
    assert clean.bad_glyph_ratio("   ") == 0.0


def _line(text: str, x: int, y: int, w: int, h: int = 30) -> OcrLine:
    return OcrLine(words=[OcrWord(text, 90.0, (x, y, w, h))], bbox=(x, y, w, h))


def test_page_paragraphs_splits_on_big_gap():
    lines = [
        _line("السطر الأول من الفقرة الأولى", 100, 100, 600),
        _line("السطر الثاني من الفقرة الأولى.", 100, 140, 600),
        _line("فقرة جديدة بعد فراغ كبير", 100, 320, 600),
    ]
    page = OcrPage(page_no=0, size=(800, 1200), lines=lines)
    paragraphs = page_paragraphs(page)
    assert len(paragraphs) == 2


def test_merge_page_boundary_joins_unterminated():
    prev = ["هذه فقرة تنتهي بنقطة.", "وهذه فقرة تستمر إلى الصفحة"]
    nxt = ["التالية حيث تكتمل الجملة.", "فقرة أخرى"]
    merged_prev, merged_next = merge_page_boundary(prev, nxt)
    assert merged_prev[-1].endswith("تكتمل الجملة.")
    assert merged_next == ["فقرة أخرى"]


def test_heading_keywords():
    assert looks_like_heading_text("الفصل الأول")
    assert looks_like_heading_text("مقدمة")
    assert not looks_like_heading_text("وكان من أمر هؤلاء الأمراء أنهم لم يطهروا بقايا القوط")
