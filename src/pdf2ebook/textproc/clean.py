"""Arabic text cleanup: normalization, watermark/header/page-number removal.

Normalization is deliberately conservative — old orthography (hamza/alef
variants) is content, not noise. We only remove typographic artifacts.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

TATWEEL = "ـ"
ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

# Lines matching any of these are stripped wherever they appear.
SEED_WATERMARK_PATTERNS = [
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"www\.\S+", re.IGNORECASE),
    re.compile(r"kotob\s*\.?\s*has\s*\.?\s*it", re.IGNORECASE),
    re.compile(r"noor[\s-]*book", re.IGNORECASE),
]

PAGE_NUMBER_RE = re.compile(
    rf"^\s*[-–—(\[]?\s*[0-9{ARABIC_INDIC_DIGITS}]{{1,4}}\s*[-–—)\]]?\s*$"
)


def normalize_arabic(text: str, keep_diacritics: bool = True) -> str:
    """Fold presentation forms to base letters, strip tatweel, tidy whitespace."""
    # NFKC folds Arabic presentation forms (U+FB50..U+FEFF) into base letters.
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(TATWEEL, "")
    if not keep_diacritics:
        text = re.sub(r"[ً-ْٰ]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _fuzzy_key(line: str) -> str:
    """Reduce a line to a comparison key tolerant of OCR noise."""
    line = normalize_arabic(line, keep_diacritics=False).lower()
    return re.sub(r"[\W_]+", "", line, flags=re.UNICODE)


def is_watermark(line: str, extra_patterns: list[re.Pattern] | None = None) -> bool:
    candidates = SEED_WATERMARK_PATTERNS + (extra_patterns or [])
    return any(p.search(line) for p in candidates)


def is_page_number(line: str) -> bool:
    return bool(PAGE_NUMBER_RE.match(line))


def find_repeated_lines(pages_lines: list[list[str]], min_ratio: float = 0.4,
                        fuzzy: float = 0.85) -> list[str]:
    """Detect running headers/footers/watermarks: short lines whose fuzzy key
    appears on more than `min_ratio` of pages (catches OCR-mangled URLs)."""
    if len(pages_lines) < 5:
        return []
    # Only consider first/last two lines of each page — headers and footers.
    candidates: dict[str, int] = {}
    representative: dict[str, str] = {}
    for lines in pages_lines:
        edges = lines[:2] + lines[-2:]
        seen_keys: set[str] = set()
        for line in edges:
            if len(line) > 60 or not line.strip():
                continue
            key = _fuzzy_key(line)
            if not key or len(key) < 4:
                continue
            # Fuzzy-merge with an existing key when nearly identical.
            merged = key
            for known in candidates:
                if abs(len(known) - len(key)) <= 4 and SequenceMatcher(None, known, key).ratio() >= fuzzy:
                    merged = known
                    break
            if merged in seen_keys:
                continue
            seen_keys.add(merged)
            candidates[merged] = candidates.get(merged, 0) + 1
            representative.setdefault(merged, line.strip())

    threshold = max(3, int(len(pages_lines) * min_ratio))
    repeated_keys = {k for k, count in candidates.items() if count >= threshold}
    return [representative[k] for k in repeated_keys]


def matches_repeated(line: str, repeated: list[str], fuzzy: float = 0.85) -> bool:
    key = _fuzzy_key(line)
    if not key:
        return False
    for rep in repeated:
        rep_key = _fuzzy_key(rep)
        if rep_key and SequenceMatcher(None, rep_key, key).ratio() >= fuzzy:
            return True
    return False


def compile_extra_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_ARABIC_LETTER_RE = re.compile(r"[ء-ي٠-٩]")
_ARABIC_ONLY_RE = re.compile(r"[ء-ي]")


def arabic_ratio(text: str) -> float:
    """Share of Arabic letters among non-space characters."""
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    return len(_ARABIC_ONLY_RE.findall(compact)) / len(compact)


# U+FFFD replacement char + Private Use Area (BMP + planes 15/16). A high share
# means a legacy non-Unicode font is embedded as a fake text layer: the glyphs
# render on screen but the codepoints carry no meaning, so the page must be OCR'd
# from its rendered image instead.
def bad_glyph_ratio(text: str) -> float:
    """Fraction of non-space characters that are U+FFFD or Private-Use glyphs."""
    total = 0
    bad = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        cp = ord(ch)
        if (cp == 0xFFFD or 0xE000 <= cp <= 0xF8FF
                or 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD):
            bad += 1
    return bad / total if total else 0.0


def clean_heading(text: str) -> str:
    """Tidy an OCR'd chapter heading for use as a TOC label.

    OCR splits the definite article off words ('ا لإسلامي') and misreads
    section numerals as ASCII punctuation ('١" -'); headings are the most
    user-visible text in the book, so they get extra polish.
    """
    text = re.sub(r"\bا\s+ل(?=[ء-ي])", "ال", text)  # rejoin split alef-lam
    text = re.sub(r"['\"`*_|<>}{\\\\]+", " ", text)  # ASCII debris never belongs in a heading
    # Strip leading section numbering / separators (often misread anyway).
    text = re.sub(r"^[\s0-9٠-٩ـ\-–—.,:;)(‎‏]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—ـ")
    return text.strip()


def is_junk_line(line: str, edge: bool = False) -> bool:
    """Mostly non-Arabic debris (OCR'd watermarks, separators like '* * *').

    `edge=True` applies the stricter test used for the first/last lines of a
    page, where running footers and watermarks live.
    """
    stripped = line.strip()
    if not stripped:
        return True
    arabic = len(_ARABIC_LETTER_RE.findall(stripped))
    ratio = arabic / len(stripped)
    if arabic == 0 and not re.search(r"[A-Za-z0-9]", stripped):
        return True  # pure punctuation/symbols ('* * *' separators, debris)
    if len(stripped) <= 4 and arabic == 0:
        return True
    if edge and len(stripped) <= 3:
        return True  # catchwords / OCR debris at page edges
    if edge and len(stripped) <= 35 and ratio < 0.4:
        return True
    return False


# Signals of a broken embedded text layer (bad CMap / lossy OCR by the PDF
# producer). The classic symptom: the lam-alef ligature لا decomposes into a
# bare ل, so "لا شيء" turns into "ل شيء" and "الإسلامية" into "السإلمية".
_STANDALONE_LAM_RE = re.compile(r"(?:^|\s)ل(?:\s|$)")
_MISORDERED_HAMZA_RE = re.compile(r"[بتثجحخسشصضطظعغفقكمنهي][إأآ]")


def looks_corrupted_arabic(text: str, sample_chars: int = 4000) -> bool:
    sample = text[:sample_chars]
    words = sample.split()
    if len(words) < 40:
        return False
    standalone_lam = len(_STANDALONE_LAM_RE.findall(sample))
    misordered = len(_MISORDERED_HAMZA_RE.findall(sample))
    return (standalone_lam / len(words)) > 0.01 or (misordered / len(words)) > 0.02
