"""Minimal golden-file harness (the stdlib answer to Rust's `insta`).

Unit tests pin behaviour one assertion at a time; snapshots pin the *shape of
the output*. A one-line change to a heuristic in `headings.py`,
`paragraphs.py`, or `clean.py` keeps every unit test green while silently
restructuring every book — a snapshot turns that into a reviewable diff.

Usage::

    assert_snapshot("scholarly_page.md", markdown)

Record or update baselines with::

    UPDATE_SNAPSHOTS=1 python -m pytest tests/test_snapshots.py

Review the resulting diff before committing: an unexpected change there *is*
the regression this harness exists to catch.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

#: A snapshot must be committed to count. Creating one on the fly would make a
#: forgotten baseline look like a pass, so only this env var may write.
UPDATE_ENV = "UPDATE_SNAPSHOTS"


def is_updating() -> bool:
    return os.environ.get(UPDATE_ENV, "") not in ("", "0", "false")


def assert_snapshot(name: str, actual: str) -> None:
    """Compare *actual* against the committed snapshot *name*.

    Text is normalized to `\\n` endings and a single trailing newline, so a
    snapshot recorded on Windows matches one verified on Linux CI.
    """
    actual = actual.replace("\r\n", "\n").rstrip("\n") + "\n"
    path = SNAPSHOT_DIR / name

    if is_updating():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return

    if not path.exists():
        raise AssertionError(
            f"missing snapshot {path.relative_to(SNAPSHOT_DIR.parent)}\n"
            f"record it with:  {UPDATE_ENV}=1 python -m pytest tests/test_snapshots.py"
        )

    expected = path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n") + "\n"
    if actual == expected:
        return

    diff = "".join(difflib.unified_diff(
        expected.splitlines(keepends=True), actual.splitlines(keepends=True),
        fromfile=f"snapshot/{name}", tofile="actual", n=3,
    ))
    raise AssertionError(
        f"output no longer matches snapshot {name}:\n\n{diff}\n"
        f"If the change is intended, re-record with:  {UPDATE_ENV}=1 python -m pytest\n"
        "and review the diff in the commit."
    )
