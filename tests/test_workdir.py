from __future__ import annotations

import pytest

from pdf2ebook.workdir import WorkDir


def test_stage_caching_and_invalidation(tmp_path, tiny_pdf):
    work = WorkDir(tmp_path / "wd", tiny_pdf)
    settings = {"dpi": 300}
    stage = work.begin_stage("raw", settings)
    (stage / "page_0001.png").write_bytes(b"fake")
    assert work.stage_valid("raw", settings)

    # Same settings: file survives.
    work.begin_stage("raw", settings)
    assert (stage / "page_0001.png").exists()

    # Changed settings: stage is wiped.
    work.begin_stage("raw", {"dpi": 200})
    assert not (stage / "page_0001.png").exists()
    assert work.stage_valid("raw", {"dpi": 200})
    assert not work.stage_valid("raw", settings)


def test_pdf_change_clears_cache(tmp_path, tiny_pdf):
    work = WorkDir(tmp_path / "wd", tiny_pdf)
    stage = work.begin_stage("raw", {"dpi": 300})
    (stage / "page_0001.png").write_bytes(b"fake")

    # Same PDF re-opened: cache survives.
    WorkDir(tmp_path / "wd", tiny_pdf)
    assert (stage / "page_0001.png").exists()

    # Different content at the same path: cache is wiped.
    other = tmp_path / "other.pdf"
    other.write_bytes(tiny_pdf.read_bytes() + b"x")
    WorkDir(tmp_path / "wd", other)
    assert not (stage / "page_0001.png").exists()


def test_same_size_pdf_change_clears_cache(tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"a" * 128)
    work = WorkDir(tmp_path / "wd", pdf)
    stage = work.begin_stage("raw", {"dpi": 300})
    (stage / "page_0001.png").write_bytes(b"fake")

    pdf.write_bytes(b"a" * 127 + b"b")
    WorkDir(tmp_path / "wd", pdf)
    assert not (stage / "page_0001.png").exists()


def test_workdir_rejects_non_empty_unmarked_folder(tmp_path, tiny_pdf):
    root = tmp_path / "not-a-workdir"
    root.mkdir()
    (root / "notes.txt").write_text("user data", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        WorkDir(root, tiny_pdf)
