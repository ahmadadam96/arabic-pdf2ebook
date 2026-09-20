"""XHTML and CSS string templates shared by the EPUB builders."""

from __future__ import annotations

from xml.sax.saxutils import escape

from .opf import safe_language_tag

REFLOW_CSS = """\
html { direction: rtl; }
body {
  font-family: "Amiri", "Scheherazade New", serif;
  text-align: justify;
  line-height: 1.7;
  margin: 0.3em 0.5em;
}
h1, h2, h3 { text-align: center; font-weight: bold; }
p { margin: 0 0 0.4em 0; text-indent: 1em; }
p.verse { text-align: center; text-indent: 0; margin: 0 0 0.15em 0; }
p.quran { text-align: center; text-indent: 0; margin: 0.5em 1em; }
ul, ol { margin: 0.3em 0; padding-inline-start: 1.5em; }
li { margin: 0 0 0.2em 0; }
figure.scan { margin: 0.5em 0; page-break-inside: avoid; text-align: center; }
figure.scan img { max-width: 100%; }
figure.scan figcaption { font-size: 0.8em; color: #555; }
a.noteref { text-decoration: none; }
a.noteref sup { padding: 0 0.15em; }
div.footnotes { margin-top: 1.2em; font-size: 0.85em; }
div.footnotes hr { border: 0; border-top: 1px solid #999; width: 30%; margin-inline-start: 0; }
aside.footnote { margin: 0 0 0.3em 0; }
aside.footnote p { text-indent: 0; margin: 0; }
"""

IMAGE_CSS = """\
html, body { margin: 0; padding: 0; }
div.page { text-align: center; page-break-after: always; }
div.page img { max-width: 100%; max-height: 100%; }
"""

IMAGE_FILL_CSS = """\
html, body { margin: 0; padding: 0; }
div.page { text-align: center; page-break-after: always; }
div.page img { max-width: 100%; }
"""

FONT_FACE_CSS = """\
@font-face {{
  font-family: "{family}";
  src: url(../fonts/{filename});
}}
"""


def xhtml_page(title: str, body: str, language: str = "ar", css_href: str = "../styles/style.css",
               viewport: tuple[int, int] | None = None) -> str:
    language = safe_language_tag(language)
    viewport_meta = ""
    if viewport:
        viewport_meta = f'    <meta name="viewport" content="width={viewport[0]}, height={viewport[1]}"/>\n'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="{escape(language)}" xml:lang="{escape(language)}" dir="rtl">
  <head>
    <title>{escape(title)}</title>
    <meta charset="utf-8"/>
{viewport_meta}    <link rel="stylesheet" type="text/css" href="{css_href}"/>
  </head>
  <body>
{body}
  </body>
</html>
"""
