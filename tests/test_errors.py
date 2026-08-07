"""The error taxonomy and its stable codes."""

from __future__ import annotations

import pytest

from pdf2ebook import errors
from pdf2ebook.config import PipelineOptions, validate_pipeline_options
from pdf2ebook.errors import (EncryptedDocumentError, InvalidOptionError, MalformedDocumentError,
                              Pdf2EbookError, SourceUnreadableError)
from pdf2ebook.pdfio import PdfError, PdfRasterizer


def test_codes_name_every_variant():
    """The web UI publishes these verbatim as `error.code` and the CLI maps
    them to exit statuses, so changing one breaks every caller that branches
    on it."""
    assert errors.Pdf2EbookError.code == "error"
    assert errors.InvalidOptionError.code == "invalid_option"
    assert errors.UnsupportedInputError.code == "unsupported"
    assert errors.PdfError.code == "malformed"
    assert errors.MalformedDocumentError.code == "malformed"
    assert errors.EncryptedDocumentError.code == "encrypted"
    assert errors.SourceUnreadableError.code == "io"
    assert errors.ResourceLimitError.code == "resource_limit"
    assert errors.MissingAssetError.code == "missing_part"
    assert errors.OcrUnavailableError.code == "ocr_unavailable"
    assert errors.EpubValidationError.code == "malformed"


def test_every_public_error_is_a_pdf2ebook_error():
    for name in errors.__all__:
        assert issubclass(getattr(errors, name), Pdf2EbookError), name


def test_legacy_handlers_still_catch():
    """Pre-existing `except ValueError` / `except RuntimeError` sites must keep
    working — the taxonomy was added under them, not in front of them."""
    assert issubclass(InvalidOptionError, ValueError)
    assert issubclass(errors.EpubValidationError, ValueError)
    assert issubclass(Pdf2EbookError, RuntimeError)
    # PdfError stays scoped to source-document problems.
    assert issubclass(MalformedDocumentError, PdfError)
    assert issubclass(EncryptedDocumentError, PdfError)
    assert not issubclass(InvalidOptionError, PdfError)


def test_invalid_option_raises_typed_error():
    with pytest.raises(InvalidOptionError):
        validate_pipeline_options(PipelineOptions(mode="nonsense"))


def test_missing_file_is_io_not_malformed(tmp_path):
    with pytest.raises(SourceUnreadableError) as exc:
        PdfRasterizer(tmp_path / "nope.pdf")
    assert exc.value.code == "io"


def test_empty_file_is_io(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(SourceUnreadableError):
        PdfRasterizer(empty)


def test_garbage_file_is_malformed(tmp_path):
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"this is not a PDF at all" * 10)
    with pytest.raises(MalformedDocumentError) as exc:
        PdfRasterizer(junk)
    assert exc.value.code == "malformed"


def test_password_protected_pdf_reports_encrypted(tmp_path, monkeypatch):
    """An encrypted PDF used to collapse into a generic 'cannot open' string.

    pdfium's own error is mocked here because writing a genuinely encrypted PDF
    by hand is out of proportion to what is being tested: that we *classify*
    pdfium's password failure rather than lumping it in with corruption.
    """
    import pypdfium2 as pdfium

    from pdf2ebook import pdfio

    class Boom(Exception):
        pass

    def raise_password(*args, **kwargs):
        raise Boom("Failed to load document (PDFium: Incorrect password error).")

    monkeypatch.setattr(pdfium, "PdfDocument", raise_password)
    # Force the message-based branch: the global last-error code is not set
    # when the exception is mocked.
    monkeypatch.setattr(pdfio, "_is_password_error",
                        lambda exc: "password" in str(exc).lower())

    locked = tmp_path / "locked.pdf"
    locked.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(EncryptedDocumentError) as exc:
        PdfRasterizer(locked)
    assert exc.value.code == "encrypted"
    assert "password" in str(exc.value).lower()


def test_is_password_error_falls_back_to_message():
    from pdf2ebook.pdfio import _is_password_error

    assert _is_password_error(RuntimeError("Incorrect password error"))
    assert not _is_password_error(RuntimeError("some other pdfium failure"))
