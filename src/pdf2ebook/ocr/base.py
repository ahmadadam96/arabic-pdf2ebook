"""OCR backend contract shared by Tesseract and Surya implementations."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True)
class OcrWord:
    text: str
    conf: float
    bbox: tuple[int, int, int, int]  # x, y, w, h


@dataclass
class OcrLine:
    words: list[OcrWord] = field(default_factory=list)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    size: float = 0.0  # font size signal (points) from a text layer; 0.0 = unknown (OCR)
    bold: float = 0.0  # fraction of bold chars from a text layer; 0.0 = unknown (OCR)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words if w.text.strip())

    @property
    def height(self) -> int:
        return self.bbox[3]

    @property
    def conf(self) -> float:
        confs = [w.conf for w in self.words if w.conf >= 0]
        return sum(confs) / len(confs) if confs else 0.0


@dataclass
class OcrPage:
    page_no: int  # 0-based source page index
    size: tuple[int, int]
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def mean_conf(self) -> float:
        confs = [w.conf for line in self.lines for w in line.words if w.conf >= 0]
        return sum(confs) / len(confs) if confs else 0.0

    @property
    def word_count(self) -> int:
        return sum(1 for line in self.lines for w in line.words if w.text.strip())

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, blob: str) -> "OcrPage":
        data = json.loads(blob)
        lines = [
            OcrLine(
                words=[OcrWord(w["text"], w["conf"], tuple(w["bbox"])) for w in ln["words"]],
                bbox=tuple(ln["bbox"]),
                size=ln.get("size", 0.0),
                bold=ln.get("bold", 0.0),
            )
            for ln in data["lines"]
        ]
        return cls(page_no=data["page_no"], size=tuple(data["size"]), lines=lines)


class OcrBackend(ABC):
    name: ClassVar[str] = "base"

    @classmethod
    @abstractmethod
    def is_available(cls) -> tuple[bool, str]:
        """Return (available, human-readable hint when not)."""

    @abstractmethod
    def recognize(self, image_path: Path) -> OcrPage:
        ...

    def recognize_batch(self, paths: list[Path]) -> list[OcrPage]:
        return [self.recognize(p) for p in paths]
