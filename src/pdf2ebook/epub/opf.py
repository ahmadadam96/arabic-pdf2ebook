"""OPF package document, EPUB 3 nav and legacy NCX builders with RTL defaults."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.sax.saxutils import escape


_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


def safe_language_tag(language: str) -> str:
    """Return a conservative, safe BCP-47-like tag for EPUB metadata."""
    language = language.strip()
    return language if _LANGUAGE_TAG_RE.fullmatch(language) else "und"


@dataclass(frozen=True)
class ManifestItem:
    item_id: str
    href: str
    media_type: str
    properties: str = ""


def build_opf(
    title: str,
    author: str,
    language: str,
    items: list[ManifestItem],
    spine_ids: list[str],
    book_id: str | None = None,
    pre_paginated: bool = False,
    viewport: tuple[int, int] | None = None,
    cover_id: str | None = None,
) -> str:
    book_id = book_id or f"urn:uuid:{uuid.uuid4()}"
    language = safe_language_tag(language)
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    meta_extra = ""
    if pre_paginated:
        meta_extra = (
            '    <meta property="rendition:layout">pre-paginated</meta>\n'
            '    <meta property="rendition:orientation">portrait</meta>\n'
            '    <meta property="rendition:spread">none</meta>\n'
        )
    if cover_id:
        # EPUB 2 fallback; EPUB 3 readers use the cover-image manifest property.
        meta_extra += f'    <meta name="cover" content="{escape(cover_id)}"/>\n'

    manifest_lines = []
    for item in items:
        props = f' properties="{item.properties}"' if item.properties else ""
        manifest_lines.append(
            f'    <item id="{escape(item.item_id)}" href="{escape(item.href)}" '
            f'media-type="{item.media_type}"{props}/>'
        )

    spine_lines = [f'    <itemref idref="{escape(sid)}"/>' for sid in spine_ids]
    author_xml = (
        f'    <dc:creator id="author">{escape(author)}</dc:creator>\n' if author else ""
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" unique-identifier="bookid" xml:lang="{escape(language)}" dir="rtl"
         xmlns="http://www.idpf.org/2007/opf" prefix="rendition: http://www.idpf.org/vocab/rendition/#">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{escape(book_id)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>{escape(language)}</dc:language>
{author_xml}    <meta property="dcterms:modified">{modified}</meta>
{meta_extra}  </metadata>
  <manifest>
{chr(10).join(manifest_lines)}
  </manifest>
  <spine page-progression-direction="rtl" toc="ncx">
{chr(10).join(spine_lines)}
  </spine>
</package>
"""


def build_nav(title: str, language: str, toc: list[tuple[str, str]]) -> str:
    """EPUB 3 navigation document. toc = [(label, href), ...]"""
    language = safe_language_tag(language)
    entries = "\n".join(
        f'        <li><a href="{escape(href)}">{escape(label)}</a></li>' for label, href in toc
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="{escape(language)}" xml:lang="{escape(language)}" dir="rtl">
  <head>
    <title>{escape(title)}</title>
    <meta charset="utf-8"/>
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>{escape(title)}</h1>
      <ol>
{entries}
      </ol>
    </nav>
  </body>
</html>
"""


def build_ncx(title: str, book_id: str, toc: list[tuple[str, str]]) -> str:
    """Legacy EPUB 2 NCX — small e-reader firmwares often read this first."""
    points = []
    for i, (label, href) in enumerate(toc, start=1):
        points.append(
            f"""    <navPoint id="navpoint-{i}" playOrder="{i}">
      <navLabel><text>{escape(label)}</text></navLabel>
      <content src="{escape(href)}"/>
    </navPoint>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{escape(book_id)}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{escape(title)}</text></docTitle>
  <navMap>
{chr(10).join(points)}
  </navMap>
</ncx>
"""
