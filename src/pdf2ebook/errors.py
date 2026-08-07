"""Typed conversion errors with stable, machine-readable codes.

An error means meaningful conversion was impossible: the input was unreadable
or structurally unusable, encrypted, or crossed a fixed safety limit.
*Recoverable* quality losses never surface here — they are recovered or skipped
and recorded on the page's :class:`~pdf2ebook.report.PageReport` notes, so the
conversion report can explain what was lost while the conversion continues.

Every class carries a ``code`` string. The web UI branches on it (it is sent as
``{"error": {"code": ..., "message": ...}}``) and the CLI maps it to an exit
status, so **changing a code breaks every caller that branches on it** — see
``tests/test_errors.py::test_codes_name_every_variant``.

The hierarchy multiply-inherits from :class:`RuntimeError` / :class:`ValueError`
so that pre-existing ``except RuntimeError`` / ``except ValueError`` handlers
keep working unchanged.
"""

from __future__ import annotations

from typing import ClassVar

__all__ = [
    "EncryptedDocumentError",
    "EpubValidationError",
    "InvalidOptionError",
    "MalformedDocumentError",
    "MissingAssetError",
    "OcrUnavailableError",
    "Pdf2EbookError",
    "PdfError",
    "ResourceLimitError",
    "SourceUnreadableError",
    "UnsupportedInputError",
]


class Pdf2EbookError(RuntimeError):
    """Base for every failure this package raises deliberately."""

    code: ClassVar[str] = "error"


class InvalidOptionError(Pdf2EbookError, ValueError):
    """A CLI/API option is missing, out of range, or not one of the valid choices.

    Also a :class:`ValueError`, so existing option-validation handlers still catch it.
    """

    code: ClassVar[str] = "invalid_option"


class UnsupportedInputError(Pdf2EbookError):
    """The input is a kind of document this pipeline cannot convert at all."""

    code: ClassVar[str] = "unsupported"


class PdfError(Pdf2EbookError):
    """Base for failures reading the source PDF.

    Kept as a distinct name (and re-exported from :mod:`pdf2ebook.pdfio`) so
    ``except PdfError`` stays scoped to source-document problems.
    """

    code: ClassVar[str] = "malformed"


class MalformedDocumentError(PdfError):
    """The PDF is structurally unusable — no meaningful content can be extracted."""

    code: ClassVar[str] = "malformed"


class EncryptedDocumentError(PdfError):
    """The PDF is encrypted or password-protected."""

    code: ClassVar[str] = "encrypted"


class SourceUnreadableError(PdfError):
    """The input file could not be read at all (missing, empty, permission denied)."""

    code: ClassVar[str] = "io"


class ResourceLimitError(Pdf2EbookError):
    """A fixed safety limit was exceeded — see :mod:`pdf2ebook.limits`.

    These are hard failures in every context: unlike other errors they must
    never be swallowed by a recovery path.
    """

    code: ClassVar[str] = "resource_limit"


class MissingAssetError(Pdf2EbookError):
    """A file the document references (a scan image, a font) is absent."""

    code: ClassVar[str] = "missing_part"


class OcrUnavailableError(Pdf2EbookError):
    """The requested OCR engine is not installed or cannot be started."""

    code: ClassVar[str] = "ocr_unavailable"


class EpubValidationError(Pdf2EbookError, ValueError):
    """A produced EPUB archive has an invalid internal reference.

    Also a :class:`ValueError` for backwards compatibility with callers that
    caught it before the taxonomy existed.
    """

    code: ClassVar[str] = "malformed"
