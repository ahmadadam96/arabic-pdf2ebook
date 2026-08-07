"""Deterministic mutation smoke test.

Every corpus fixture is converted after byte mutations at pseudo-random
positions. A conversion may fail — malformed input is expected — but it must
fail as a typed `Pdf2EbookError`, never as a raw ctypes/attribute/index error
escaping from the PDF layer, and never by hanging or exhausting memory.

This is what turns the error taxonomy from paperwork into a contract: the
`except Exception` handlers in `pdfio` are allowed to *recover*, but nothing is
allowed to leak an untyped failure to the caller.

Ported from anydoc's `tests/robustness.rs`.
"""

from __future__ import annotations

import pytest
from corpus import CORPUS, build_fixture

from pdf2ebook.config import EpubMeta, PipelineOptions
from pdf2ebook.errors import Pdf2EbookError
from pdf2ebook.ocrmode import run_text_mode
from pdf2ebook.pdfio import PdfRasterizer
from pdf2ebook.pipeline import inspect_pdf

MUTATIONS_PER_FIXTURE = 24


class Rng:
    """xorshift64* — deterministic across runs, platforms and Python builds."""

    MASK = (1 << 64) - 1

    def __init__(self, seed: int = 0x5EED_1234_5678_9ABC):
        self.state = seed

    def next(self) -> int:
        x = self.state
        x ^= (x >> 12)
        x ^= (x << 25) & self.MASK
        x ^= (x >> 27)
        self.state = x & self.MASK
        return (self.state * 0x2545_F491_4F6C_DD1D) & self.MASK


def _mutations(data: bytes, count: int) -> list[bytes]:
    rng = Rng()
    out = []
    for _ in range(count):
        buf = bytearray(data)
        for _ in range(3):
            pos = rng.next() % len(buf)
            buf[pos] = rng.next() % 256
        out.append(bytes(buf))
    return out


@pytest.mark.parametrize("fixture", CORPUS, ids=lambda f: f.name)
def test_mutated_fixtures_raise_only_typed_errors(fixture, tmp_path):
    source = build_fixture(fixture, tmp_path / "src").read_bytes()
    for n, mutated in enumerate(_mutations(source, MUTATIONS_PER_FIXTURE)):
        path = tmp_path / f"mutant_{n}.pdf"
        path.write_bytes(mutated)
        try:
            with PdfRasterizer(path) as pdf:
                for index in range(min(pdf.page_count, 4)):
                    pdf.extract_text(index)
                    pdf.extract_text_page(index)
                    pdf.page_size_pts(index)
        except Pdf2EbookError:
            continue  # the documented outcome for unusable input
        except Exception as exc:  # noqa: BLE001 — the whole point of the test
            raise AssertionError(
                f"{fixture.name} mutant {n} raised an untyped "
                f"{type(exc).__name__}: {exc}"
            ) from exc


@pytest.mark.parametrize("fixture", CORPUS, ids=lambda f: f.name)
def test_mutated_fixtures_survive_inspect(fixture, tmp_path):
    source = build_fixture(fixture, tmp_path / "src").read_bytes()
    for n, mutated in enumerate(_mutations(source, MUTATIONS_PER_FIXTURE)):
        path = tmp_path / f"inspect_{n}.pdf"
        path.write_bytes(mutated)
        try:
            inspect_pdf(path)
        except Pdf2EbookError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"{fixture.name} mutant {n}: inspect raised an untyped "
                f"{type(exc).__name__}: {exc}"
            ) from exc


def test_truncated_pdf_fails_typed(tmp_path):
    fixture = CORPUS[0]
    source = build_fixture(fixture, tmp_path / "src").read_bytes()
    for cut in (8, len(source) // 3, len(source) // 2, len(source) - 20):
        path = tmp_path / f"cut_{cut}.pdf"
        path.write_bytes(source[:cut])
        try:
            with PdfRasterizer(path) as pdf:
                pdf.extract_text(0)
        except Pdf2EbookError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"truncation at {cut} raised untyped "
                                 f"{type(exc).__name__}: {exc}") from exc


def test_full_conversion_of_a_mutant_never_leaks_untyped(tmp_path):
    """The whole pipeline, not just the PDF layer."""
    fixture = CORPUS[0]
    source = build_fixture(fixture, tmp_path / "src").read_bytes()
    for n, mutated in enumerate(_mutations(source, 6)):
        path = tmp_path / f"full_{n}.pdf"
        path.write_bytes(mutated)
        opts = PipelineOptions(mode="auto", text_layer="always",
                               work_dir=tmp_path / f"wd_{n}",
                               meta=EpubMeta(title="ك", language="ar"))
        try:
            run_text_mode(path, tmp_path / f"out_{n}.epub", opts)
        except Pdf2EbookError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"mutant {n} conversion raised untyped "
                                 f"{type(exc).__name__}: {exc}") from exc
