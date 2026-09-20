"""Option dataclasses shared by the CLI, the web UI and the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import InvalidOptionError

VALID_MODES = ("auto", "ocr", "image")
VALID_TEXT_LAYERS = ("auto", "always", "never")
VALID_ENGINES = ("tesseract", "surya")
VALID_IMAGE_STYLES = ("gray", "binary")
VALID_IMAGE_LAYOUTS = ("flow", "fixed")
VALID_FONTS = ("amiri", "none")
VALID_FORCE_STAGES = ("extract", "preprocess", "ocr", "all")


def validate_choice(name: str, value: str | None, allowed: tuple[str, ...],
                    *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if value not in allowed:
        valid = ", ".join(allowed)
        raise InvalidOptionError(f"{name} must be one of: {valid}")


def parse_page_range(spec: str | None, page_count: int) -> list[int]:
    """Parse a 1-based page range like '5-20', '3', '1-10,15,20-25' into 0-based indices."""
    if page_count < 1:
        raise InvalidOptionError("PDF has no pages")
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
            raise InvalidOptionError(f"Page range '{part}' is outside 1-{page_count}")
        indices.update(range(lo - 1, hi))
    if not indices:
        raise InvalidOptionError("Page range did not select any pages")
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
    split: int = 1  # slice each tall page into N vertical bands (0 = auto fit screen height)


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
    # Rebuild a page with the flat structurer when the smart one drops too
    # much of it — the actuator behind the coverage measurement.
    structure_fallback: bool = True
    book_id: str | None = None  # pin the EPUB identifier across rebuilds
    ocr: OcrOptions = field(default_factory=OcrOptions)
    image: ImageOptions = field(default_factory=ImageOptions)
    meta: EpubMeta = field(default_factory=EpubMeta)


def validate_pipeline_options(opts: PipelineOptions) -> None:
    validate_choice("mode", opts.mode, VALID_MODES)
    validate_choice("text_layer", opts.text_layer, VALID_TEXT_LAYERS)
    validate_choice("ocr engine", opts.ocr.engine, VALID_ENGINES)
    validate_choice("image style", opts.image.style, VALID_IMAGE_STYLES)
    validate_choice("image layout", opts.image.layout, VALID_IMAGE_LAYOUTS)
    validate_choice("font", opts.font, VALID_FONTS)
    validate_choice("force", opts.force, VALID_FORCE_STAGES, optional=True)
    if opts.split_volumes < 1:
        raise InvalidOptionError("split_volumes must be at least 1")
    if opts.split_every < 1:
        raise InvalidOptionError("split_every must be at least 1")
    if opts.dpi < 72:
        raise InvalidOptionError("dpi must be at least 72")
    if opts.image.width is not None and opts.image.width < 1:
        raise InvalidOptionError("width must be positive")
    if opts.image.height is not None and opts.image.height < 1:
        raise InvalidOptionError("height must be positive")
    if opts.image.split < 0:
        raise InvalidOptionError("split must be 0 (auto) or a positive band count")
