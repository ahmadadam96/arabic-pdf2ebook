"""Cached, resumable work directory.

Layout:
    <stem>.workdir/
        manifest.json          pdf sha256, page count, tool version
        raw/settings.json      rasterization settings + page PNGs
        pre-ocr/               preprocessed pages for OCR
        pre-image/             preprocessed pages for image mode
        ocr/<engine>/          per-page OcrPage JSON
        text/book.json         cleaned Book model

A stage is valid when its settings.json matches the current settings hash.
Individual pages are skipped when their output file already exists, so an
interrupted run resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from . import __version__

CACHE_SCHEMA = 2
MARKER_KEY = "pdf2ebook_workdir"


def _hash_settings(settings: dict) -> str:
    blob = json.dumps(settings, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _hash_file(path: Path) -> str:
    """Full streaming SHA-256 so same-size tail edits cannot reuse stale cache."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class WorkDir:
    def __init__(self, root: Path, pdf_path: Path):
        self.root = root
        self.pdf_path = pdf_path
        self._validate_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._check_manifest()

    def _validate_root(self) -> None:
        root = self.root.resolve()
        pdf = self.pdf_path.resolve()
        if root == pdf:
            raise ValueError("Work dir cannot be the input PDF path")
        if root == pdf.parent:
            raise ValueError("Work dir cannot be the input PDF folder")
        for unsafe in (Path.cwd().resolve(), Path.home().resolve()):
            if root == unsafe:
                raise ValueError(f"Refusing unsafe work dir: {root}")
        if (root / ".git").exists():
            raise ValueError(f"Refusing to use a git repository as work dir: {root}")
        if root.exists() and root.is_file():
            raise ValueError(f"Work dir is a file: {root}")
        if root.exists() and any(root.iterdir()) and not (root / "manifest.json").exists():
            raise ValueError(
                f"Refusing non-empty folder without a pdf2ebook manifest as work dir: {root}"
            )

    # -- manifest ------------------------------------------------------
    def _check_manifest(self) -> None:
        manifest_path = self.root / "manifest.json"
        pdf_hash = _hash_file(self.pdf_path)
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {}
            if (manifest.get("pdf_sha256") != pdf_hash
                    or manifest.get("cache_schema") != CACHE_SCHEMA
                    or manifest.get("tool_version") != __version__):
                # Different source PDF: the whole cache is stale.
                for child in self.root.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    elif child.name != "manifest.json":
                        child.unlink()
        manifest_path.write_text(
            json.dumps({
                MARKER_KEY: True,
                "pdf_sha256": pdf_hash,
                "tool_version": __version__,
                "cache_schema": CACHE_SCHEMA,
            }, indent=2),
            encoding="utf-8",
        )

    # -- stages --------------------------------------------------------
    def stage_dir(self, stage: str) -> Path:
        d = self.root / stage
        d.mkdir(parents=True, exist_ok=True)
        return d

    def stage_valid(self, stage: str, settings: dict) -> bool:
        settings_path = self.root / stage / "settings.json"
        if not settings_path.exists():
            return False
        try:
            stored = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return stored.get("hash") == _hash_settings(settings)

    def begin_stage(self, stage: str, settings: dict) -> Path:
        """Return the stage dir, wiping it first when settings changed."""
        d = self.stage_dir(stage)
        if not self.stage_valid(stage, settings):
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            (d / "settings.json").write_text(
                json.dumps({"hash": _hash_settings(settings), "settings": settings}, indent=2),
                encoding="utf-8",
            )
        return d

    def invalidate(self, stage: str) -> None:
        settings_path = self.root / stage / "settings.json"
        if settings_path.exists():
            settings_path.unlink()

    # -- page paths ----------------------------------------------------
    @staticmethod
    def page_name(index: int, ext: str = "png") -> str:
        return f"page_{index + 1:04d}.{ext}"

    def page_path(self, stage: str, index: int, ext: str = "png") -> Path:
        return self.root / stage / self.page_name(index, ext)

    def cleanup(self) -> None:
        manifest_path = self.root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}
        if manifest.get(MARKER_KEY) is not True:
            raise RuntimeError(f"Refusing to delete unmarked work dir: {self.root}")
        shutil.rmtree(self.root, ignore_errors=True)

    def contains(self, path: Path) -> bool:
        return _is_relative_to(path.resolve(), self.root.resolve())
