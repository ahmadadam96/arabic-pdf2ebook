"""Logical book model produced by text processing and consumed by the EPUB builder."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Paragraph:
    text: str
    kind: str = "p"  # p | h1 | h2 | h3 | ul | ol | verse | quran | footnote
    note_id: str = ""  # set only on footnote paragraphs (links body ref ⇄ note)


@dataclass
class PageImage:
    """A page kept as an image (map/photo or failed OCR)."""
    page_no: int
    image_path: str  # relative to the work dir


@dataclass
class Chapter:
    title: str
    elements: list[Paragraph | PageImage] = field(default_factory=list)


@dataclass
class Book:
    title: str
    author: str = ""
    language: str = "ar"
    chapters: list[Chapter] = field(default_factory=list)

    def to_json(self) -> str:
        def encode(obj):
            if isinstance(obj, Paragraph):
                data = {"t": "par", "text": obj.text, "kind": obj.kind}
                if obj.note_id:
                    data["note_id"] = obj.note_id
                return data
            if isinstance(obj, PageImage):
                return {"t": "img", "page_no": obj.page_no, "image_path": obj.image_path}
            raise TypeError(type(obj))

        return json.dumps(
            {
                "title": self.title,
                "author": self.author,
                "language": self.language,
                "chapters": [
                    {"title": c.title, "elements": [encode(e) for e in c.elements]}
                    for c in self.chapters
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, blob: str) -> "Book":
        data = json.loads(blob)
        chapters = []
        for c in data["chapters"]:
            elements: list[Paragraph | PageImage] = []
            for e in c["elements"]:
                if e["t"] == "par":
                    elements.append(Paragraph(e["text"], e["kind"], e.get("note_id", "")))
                else:
                    elements.append(PageImage(e["page_no"], e["image_path"]))
            chapters.append(Chapter(c["title"], elements))
        return cls(data["title"], data["author"], data["language"], chapters)

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Book":
        return cls.from_json(path.read_text(encoding="utf-8"))
