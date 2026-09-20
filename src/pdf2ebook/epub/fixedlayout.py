"""Image-mode EPUB builder: one cleaned page scan per XHTML page, RTL spine.

`layout="flow"` (default) keeps a normal reflowable spine with full-width
images — the most compatible choice for simple e-reader firmwares.
`layout="fixed"` emits a true pre-paginated EPUB with a device viewport.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

from PIL import Image

from ..errors import InvalidOptionError
from ..limits import FIXED_LAYOUT_JPEG_QUALITY as JPEG_QUALITY
from .opf import ManifestItem, build_ncx, build_nav, build_opf, stable_book_id
from .templates import IMAGE_CSS, IMAGE_FILL_CSS, xhtml_page
from .validate import validate_epub
from .zipwriter import EpubContainer


def _encode_page(src: Path, style: str) -> tuple[bytes, str, str]:
    """Return (data, extension, media_type); JPEG for gray pages, PNG for binary."""
    img = Image.open(src)
    if style == "binary":
        buf = io.BytesIO()
        img.convert("1").save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "png", "image/png"
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), "jpg", "image/jpeg"


def build_image_epub(
    page_paths: list[Path],
    out_path: Path,
    title: str,
    author: str = "",
    language: str = "ar",
    style: str = "gray",
    layout: str = "flow",
    viewport: tuple[int, int] | None = None,
    toc_every: int = 20,
    progress: Callable[[int], None] | None = None,
    fill: bool = False,
) -> Path:
    if not page_paths:
        raise InvalidOptionError("Cannot build an image EPUB without page images")

    pre_paginated = layout == "fixed"
    book_id = None

    items: list[ManifestItem] = [
        ManifestItem("nav", "nav.xhtml", "application/xhtml+xml", "nav"),
        ManifestItem("ncx", "toc.ncx", "application/x-dtbncx+xml"),
        ManifestItem("css", "styles/style.css", "text/css"),
    ]
    spine_ids: list[str] = []
    toc: list[tuple[str, str]] = []

    with EpubContainer(out_path) as epub:
        epub.add("OEBPS/styles/style.css", IMAGE_FILL_CSS if fill else IMAGE_CSS)
        for i, src in enumerate(page_paths):
            data, ext, media_type = _encode_page(src, style)
            img_name = f"images/page_{i + 1:04d}.{ext}"
            page_name = f"pages/page_{i + 1:04d}.xhtml"
            epub.add(f"OEBPS/{img_name}", data, compress=(ext == "png"))

            page_viewport = None
            if pre_paginated:
                with Image.open(io.BytesIO(data)) as im:
                    page_viewport = im.size

            body = f'    <div class="page"><img src="../{img_name}" alt="صفحة {i + 1}"/></div>'
            epub.add(
                f"OEBPS/{page_name}",
                xhtml_page(f"{title} — {i + 1}", body, language,
                           css_href="../styles/style.css", viewport=page_viewport),
            )

            img_id, page_id = f"img{i + 1:04d}", f"p{i + 1:04d}"
            props = "rendition:layout-pre-paginated" if pre_paginated else ""
            items.append(ManifestItem(img_id, img_name, media_type,
                                      "cover-image" if i == 0 else ""))
            items.append(ManifestItem(page_id, page_name, "application/xhtml+xml", props))
            spine_ids.append(page_id)

            if i % toc_every == 0:
                toc.append((f"صفحة {i + 1}", page_name))
            if progress:
                progress(i + 1)

        if not toc:
            toc = [(title, "pages/page_0001.xhtml")]

        # Derived from the page set, so rebuilding the same book is byte-identical.
        book_id = stable_book_id(title, author, language,
                                 "\n".join(p.name for p in page_paths))
        epub.add("OEBPS/nav.xhtml", build_nav(title, language, toc))
        epub.add("OEBPS/toc.ncx", build_ncx(title, book_id, toc))
        epub.add(
            "OEBPS/content.opf",
            build_opf(title, author, language, items, spine_ids, book_id=book_id,
                      pre_paginated=pre_paginated, viewport=viewport,
                      cover_id="img0001" if page_paths else None),
        )
    validate_epub(out_path)
    return out_path
