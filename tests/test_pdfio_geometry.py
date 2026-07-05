from __future__ import annotations

from pathlib import Path

from pdf2ebook.pdfio import PdfRasterizer


def _make_text_pdf(path: Path, items: list[tuple]) -> None:
    """Write a minimal one-page PDF with a real text layer.

    `items` is a list of (text, x, y, font_size[, font]) in PDF points
    (bottom-left origin); `font` is "F1" (Helvetica, default) or "F2"
    (Helvetica-Bold). ASCII only — exercises geometry/size/weight extraction.
    """
    def _op(item: tuple) -> str:
        text, x, y, size = item[0], item[1], item[2], item[3]
        font = item[4] if len(item) > 4 else "F1"
        return f"BT /{font} {size} Tf {x} {y} Td ({text}) Tj ET"

    ops = "\n".join(_op(it) for it in items)
    content = ops.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R /F2 6 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref_pos = len(pdf)
    count = len(objects) + 1
    pdf += b"xref\n0 %d\n" % count
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += b"%010d 00000 n \n" % off
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (count, xref_pos)
    path.write_bytes(pdf)


def test_extract_text_page_geometry(tmp_path):
    pdf_path = tmp_path / "text.pdf"
    _make_text_pdf(pdf_path, [
        ("Big Heading", 100, 700, 24),
        ("Some body text line one", 100, 650, 12),
        ("Some body text line two", 100, 630, 12),
    ])

    with PdfRasterizer(pdf_path) as pdf:
        page = pdf.extract_text_page(0)

    assert page is not None
    assert len(page.lines) == 3
    # Reading order: heading (highest on the page) comes first.
    assert page.lines[0].bbox[1] < page.lines[1].bbox[1] < page.lines[2].bbox[1]
    assert "Heading" in page.lines[0].text
    # Font size carried through; heading is bigger than body.
    assert 20 <= page.lines[0].size <= 28
    assert page.lines[0].size > page.lines[1].size
    # Text layer is full confidence.
    assert page.lines[0].words[0].conf == 100.0
    # bbox is top-left pixel space within the page.
    assert page.size == (612, 792)
    assert page.lines[0].bbox[0] >= 0 and page.lines[0].bbox[1] >= 0


def test_extract_text_page_bold_flag(tmp_path):
    pdf_path = tmp_path / "bold.pdf"
    _make_text_pdf(pdf_path, [
        ("Bold Heading Line", 100, 700, 18, "F2"),   # Helvetica-Bold
        ("regular body text line", 100, 660, 12, "F1"),
    ])
    with PdfRasterizer(pdf_path) as pdf:
        page = pdf.extract_text_page(0)

    assert page is not None
    by_y = sorted(page.lines, key=lambda ln: ln.bbox[1])
    assert by_y[0].bold >= 0.5     # bold heading detected (weight or name fallback)
    assert by_y[1].bold == 0.0     # regular text not bold
