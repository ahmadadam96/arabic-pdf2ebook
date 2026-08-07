"""Structuring fixtures expressed as `OcrPage`s, not as PDFs.

Everything interesting the pipeline does to text — heading tiers, paragraph
reconstruction, verse and Quran detection, footnote splitting, running-header
removal — operates on an `OcrPage`. Feeding those rules through a synthetic PDF
would test pdfium's line-grouping heuristics as much as our own, and pdfium
groups small or tightly-spaced runs in ways no fixture author controls.

So the structuring corpus starts here instead, with exact geometry. The PDF
corpus in `corpus.py` covers what only a real PDF can: extraction, font
metrics, and the book-level text-layer verdict.

Geometry matches what `PdfRasterizer.extract_text_page` produces: bbox is
`(x, y, w, h)` in top-left pixel space, and Arabic lines are anchored at the
right margin and run leftwards.
"""

from __future__ import annotations

from collections.abc import Callable

from pdf2ebook.ocr.base import OcrLine, OcrPage, OcrWord

PAGE_W, PAGE_H = 612, 792
RIGHT = 540           # right margin
BODY, HEAD, NOTE = 12.0, 20.0, 8.0
ADVANCE = 0.5         # glyph advance as a fraction of the font size
TOP = 90              # first body line, measured from the top
STEP = 22


def line(text: str, y: int, size: float = BODY, *, indent: int = 0,
         center: bool = False, bold: float = 0.0, conf: float = 100.0) -> OcrLine:
    """One right-anchored RTL line; `center` centres it, `indent` insets it."""
    width = max(1, round(len(text) * size * ADVANCE))
    height = max(1, round(size))
    x = round((PAGE_W - width) / 2) if center else RIGHT - indent - width
    bbox = (x, y, width, height)
    return OcrLine(words=[OcrWord(text, conf, bbox)], bbox=bbox, size=size, bold=bold)


def prose(texts: list[str], top: int = TOP, size: float = BODY,
          indent_first: int = 0) -> list[OcrLine]:
    return [line(t, top + i * STEP, size, indent=indent_first if i == 0 else 0)
            for i, t in enumerate(texts)]


def page(lines: list[OcrLine], page_no: int = 0) -> OcrPage:
    return OcrPage(page_no=page_no, size=(PAGE_W, PAGE_H), lines=lines)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def footnote_page() -> list[OcrPage]:
    """Body with (١)/(٢) citations and a small-font note block at the foot."""
    return [page([
        line("باب الأنساب", 50, HEAD, center=True),
        *prose([
            "قال المؤرخ إن نسب القبيلة يرجع إلى جد جامع (١) وقد اختلفوا",
            "في ضبط اسمه على أقوال أشهرها ما ذكره صاحب الجمهرة",
            "وليس في المسألة نص قاطع يرفع الخلاف بين أهل العلم (٢).",
        ]),
        line("(١) انظر جمهرة أنساب العرب صفحة مئة وعشرين", 700, NOTE),
        line("(٢) وقد تابعه على ذلك جماعة من المتأخرين وهو الأشهر", 715, NOTE),
        line("١٢٠", 750, NOTE, center=True),
    ])]


def poetry_page() -> list[OcrPage]:
    """Classical verse: a shared multi-letter rhyme, one bayt per line.

    The rhyme must be more than a single final letter — `detect_verse_lines`
    deliberately refuses to call prose "verse" on that evidence alone.

    KNOWN DEFECT, recorded deliberately in the snapshot: the heading above the
    poem is short, unpunctuated and rhymes closely enough that it satisfies
    `_verse_candidate`, so `structure_page` — which tests `verse_idx` before
    `tiers` — swallows it into the verse block instead of emitting a heading.
    The snapshot pins today's behaviour; fixing the precedence would show up
    here as an intended diff.
    """
    return [page([
        line("من شعر المتقدمين", 50, HEAD, center=True),
        *prose([
            "لكل زمان دولة ورجال",
            "وما دام في الأيام يبقى الحال",
            "فإن الليالي تنقضي وتزال",
            "وكل امرئ في دهره متعال",
        ]),
    ])]


def paragraph_page() -> list[OcrPage]:
    """Two paragraphs separated by an RTL opening indent, plus a bold heading.

    Pins three rules at once: indent-based paragraph splitting, the bold arm of
    `heading_tiers` (Arabic has no capitalisation, so weight is the signal), and
    the merge of a paragraph continuing across a page break.
    """
    return [
        page([
            line("مقدمة المؤلف", 50, BODY, center=True, bold=1.0),
            *prose([
                "الحمد لله رب العالمين والصلاة والسلام على أشرف المرسلين",
                "أما بعد فهذا كتاب جمعت فيه ما تفرق من أخبار الأمم السالفة",
            ]),
            *prose([
                "وقد اعتمدت في ذلك على أوثق المصادر التي وقفت عليها",
                "مما رواه الثقات من أهل العلم بالتاريخ والسير والأخبار",
            ], top=TOP + 3 * STEP, indent_first=30),
        ], page_no=0),
        # Opens mid-sentence: the boundary merge should join it to the page above.
        page(prose([
            "وهو الذي عول عليه المتأخرون في هذا الباب",
            "ثم إن الكلام في الأنساب يطول ولا ينتهي إلى قرار.",
        ]), page_no=1),
    ]


def watermark_book() -> list[OcrPage]:
    """A running header and folio on every page, with distinct prose between.

    `find_repeated_lines` inspects only the first/last two lines of each page
    and needs the key on 40% of them, so this needs 5+ pages and body text that
    genuinely differs page to page.
    """
    bodies = [
        ["ثم قدم القوم إلى الوادي فنزلوا به أياما طوالا",
         "وكان فيهم شيخ يعرف مواقع الماء في تلك النواحي",
         "فدلهم على عين غزيرة لم تنضب في الجدب."],
        ["ولما استقر بهم المقام بنوا حصنا من الحجارة",
         "وجعلوا له بابا واحدا يغلق عند المساء",
         "وأقاموا عليه حرسا يتناوبون الليل كله."],
        ["وفي السنة التالية أصابهم قحط شديد فهلك أكثر الزرع",
         "فخرج أكثرهم يطلبون الكلأ في أطراف البادية",
         "ولم يبق في الحصن إلا الشيوخ والنساء."],
        ["ثم عادت الأمطار بعد غياب طويل فاخضرت الأرض",
         "ورجع الظاعنون إلى ديارهم يسوقون أنعامهم",
         "وكان ذلك عاما مشهودا يؤرخون به بعد ذلك."],
        ["وذكر بعض الرواة أن الحصن هدم في فتنة وقعت بينهم",
         "واختلف أهل الأخبار في سبب تلك الفتنة اختلافا كثيرا",
         "والذي عليه الأكثر أنها كانت على مورد ماء."],
        ["ثم تفرقت القبيلة في البلاد فلم يبق بذلك الوادي أحد",
         "وبقيت آثار الحصن ترى إلى زمن المؤلف",
         "وهذا آخر ما وقفنا عليه من أخبارهم."],
    ]
    return [
        page([
            line("www.noor-book.com", 30, NOTE, center=True),
            *prose(body),
            line(str(n + 1), 750, NOTE, center=True),
        ], page_no=n)
        for n, body in enumerate(bodies)
    ]


def literal_marker_page() -> list[OcrPage]:
    """Body text containing a literal `[^1]` that is *not* a footnote reference.

    Regression fixture for the silent-deletion bug: the marker has to survive
    the Markdown round trip instead of being parsed as a ref and dropped.
    """
    return [page([
        line("تعليق على الرموز", 50, HEAD, center=True),
        *prose([
            "يكتب الناسخ في هامش المخطوط علامة [^1] للدلالة على السقط",
            "وهي علامة قديمة يستعملها المحققون في ضبط النصوص القديمة.",
        ]),
    ])]


def mixed_structure_page() -> list[OcrPage]:
    """Headings, a numbered list, a Quranic quote with its cue, and prose."""
    return [page([
        line("الفصل الأول", 50, HEAD, center=True),
        line("في ذكر الأقسام", 80, 16.0, center=True),
        *prose([
            "وتنقسم هذه المسألة عند أهل النظر إلى ثلاثة أقسام",
            "1. القسم الأول ما دل عليه النص الصريح",
            "2. القسم الثاني ما استنبط بالقياس الجلي",
            "3. القسم الثالث ما اختلف فيه أهل الاجتهاد",
            "قال تعالى إن الله لا يغير ما بقوم حتى يغيروا ما بأنفسهم",
            "وفي هذه الآية دليل على أن التغيير مربوط بالسعي.",
        ], top=110),
    ])]


PAGE_CORPUS: dict[str, Callable[[], list[OcrPage]]] = {
    "footnote_page": footnote_page,
    "poetry_page": poetry_page,
    "paragraph_page": paragraph_page,
    "watermark_book": watermark_book,
    "literal_marker_page": literal_marker_page,
    "mixed_structure_page": mixed_structure_page,
}
