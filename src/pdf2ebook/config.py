"""Option dataclasses shared by the CLI, the web UI and the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def parse_page_range(spec: str | None, page_count: int) -> list[int]:
    """Parse a 1-based page range like '5-20', '3', '1-10,15,20-25' into 0-based indices."""
    if not spec:
        return list(range(page_count))
    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            lo, hi = int(lo_s), int(hi_s)
        else:
            lo = hi = int(part)
        if lo < 1 or hi > page_count or lo > hi:
            raise ValueError(f"Page range '{part}' is outside 1-{page_count}")
        indices.update(range(lo - 1, hi))
    return sorted(indices)


@dataclass
class OcrOptions:
    engine: str = "tesseract"
    lang: str = "ara"
    psm: int = 4
    min_conf: float = 40.0
    rescue: bool = True
    keep_diacritics: bool = True
    strip_patterns: list[str] = field(default_factory=list)


@dataclass
class ImageOptions:
    device: str = "generic-6in"
    width: int | None = None
    height: int | None = None
    style: str = "gray"  # gray | binary
    layout: str = "flow"  # flow | fixed
    cbz: bool = False


@dataclass
class EpubMeta:
    title: str = ""
    author: str = ""
    language: str = "ar"


@dataclass
class PipelineOptions:
    mode: str = "auto"  # auto | ocr | image
    text_layer: str = "auto"  # auto (use when healthy) | always | never
    preshape: bool = False  # bake letter-joining into text (simple readers only)
    dpi: int = 300
    pages: str | None = None
    split_volumes: int = 1
    split_every: int = 10
    font: str = "amiri"  # amiri | scheherazade | none
    work_dir: Path | None = None
    force: str | None = None  # extract | preprocess | ocr | all
    clean: bool = False
    markdown_out: Path | None = None  # also write the editable Markdown (+ scans/) here
    footnotes: bool = True  # detect footnote blocks and link them in the EPUB
    ocr: OcrOptions = field(default_factory=OcrOptions)
    image: ImageOptions = field(default_factory=ImageOptions)
    meta: EpubMeta = field(default_factory=EpubMeta)
