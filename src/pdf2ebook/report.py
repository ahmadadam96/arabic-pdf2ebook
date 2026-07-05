"""Per-page routing + coverage report for a text conversion.

Records how each page was handled (text layer / OCR / rescued / kept as image /
blank) and how much of its text survived structuring, so the CLI, the web UI and
a `report.json` can tell the user honestly what happened. Inspired by
pdfmarkdown.app's difficulty triage and coverage reporting.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from .textproc.clean import normalize_arabic

ROUTE_TEXT = "text-layer"
ROUTE_OCR = "ocr"
ROUTE_RESCUED = "ocr-rescued"
ROUTE_IMAGE = "image-kept"
ROUTE_BLANK = "blank"

# Routes whose text is expected to end up in the book (so coverage applies).
TEXT_ROUTES = (ROUTE_TEXT, ROUTE_OCR, ROUTE_RESCUED)

BOOK_COVERAGE_MIN = 0.95
PAGE_COVERAGE_MIN = 0.80
_MIN_PAGE_CHARS = 40      # ignore near-empty pages when flagging low coverage
_MAX_PAGE_WARNINGS = 10

_WS_RE = re.compile(r"\s+")


@dataclass
class PageReport:
    page_no: int          # 0-based source page index
    route: str
    reason: str = ""
    confidence: float = 0.0
    source_chars: int = 0
    dropped_chars: int = 0
    emitted_chars: int = 0
    coverage: float = 1.0


@dataclass
class ConversionReport:
    pages: list[PageReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def book_coverage(self) -> float:
        src = sum(p.source_chars for p in self.pages if p.route in TEXT_ROUTES)
        emitted = sum(p.emitted_chars for p in self.pages if p.route in TEXT_ROUTES)
        return emitted / src if src else 1.0

    def route_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for page in self.pages:
            counts[page.route] = counts.get(page.route, 0) + 1
        return counts

    def to_json(self) -> str:
        return json.dumps(
            {"pages": [asdict(p) for p in self.pages], "warnings": self.warnings},
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, blob: str) -> "ConversionReport":
        data = json.loads(blob)
        pages = [PageReport(**p) for p in data.get("pages", [])]
        return cls(pages=pages, warnings=list(data.get("warnings", [])))


def coverage_key(text: str) -> str:
    """Normalized, whitespace-free, diacritic-insensitive char basis for coverage.

    Diacritics are stripped on both sides so the count measures whether the
    *letters* survived structuring, independent of the run's keep-diacritics
    policy (verse spacing and Quran brackets are the only intended deltas).
    """
    return _WS_RE.sub("", normalize_arabic(text, keep_diacritics=False))


def collect_warnings(report: ConversionReport) -> list[str]:
    """Bilingual (ar/en) warnings for low book- or page-level text coverage."""
    warnings: list[str] = []
    coverage = report.book_coverage
    if coverage < BOOK_COVERAGE_MIN:
        pct = round(coverage * 100)
        floor = round(BOOK_COVERAGE_MIN * 100)
        warnings.append(
            f"التغطية الكلية {pct}% (أقل من {floor}%) — book coverage {pct}% "
            f"(below {floor}%): some text may have been lost while structuring."
        )
    low = [p for p in report.pages
           if p.route in TEXT_ROUTES and p.source_chars >= _MIN_PAGE_CHARS
           and p.coverage < PAGE_COVERAGE_MIN]
    for page in low[:_MAX_PAGE_WARNINGS]:
        pct = round(page.coverage * 100)
        warnings.append(
            f"صفحة {page.page_no + 1}: تغطية {pct}% — page {page.page_no + 1} coverage {pct}%"
        )
    if len(low) > _MAX_PAGE_WARNINGS:
        extra = len(low) - _MAX_PAGE_WARNINGS
        warnings.append(
            f"…و{extra} صفحة أخرى منخفضة التغطية — and {extra} more low-coverage page(s)"
        )
    return warnings
