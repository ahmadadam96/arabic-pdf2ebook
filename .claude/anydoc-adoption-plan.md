# Adopting anydoc's engineering discipline for consistent Arabic PDF → EPUB output

Source of ideas: [firecrawl/anydoc](https://github.com/firecrawl/anydoc) (Rust, MIT) and its PDF
frontend [pdf-inspector](https://github.com/firecrawl/pdf-inspector). anydoc is not a replacement for
this project — it explicitly refuses OCR, which is our whole job. What transfers is its *discipline*:
one decision per document, one serializer, typed errors, hard limits, reproducible output, and a
fixture corpus that makes every heuristic change visible as a diff.

---

## Part 1 — Diagnosis: what makes our output inconsistent today

Nine findings, all verified against the current code on `master` (f2d23f5).

### F1. Mixed-provenance books — the biggest source of visible inconsistency

`ocrmode._select_text_layer` decides **per page** whether to trust the embedded text layer.
`clean.looks_corrupted_arabic` returns `False` when the page has fewer than 40 words
(`clean.py:198`), so short pages — headings, front matter, chapter openers, sparse verse pages —
**bypass corruption detection entirely** and are admitted with a corrupt text layer whenever the
corruption is ligature-loss rather than PUA glyphs (`bad_glyph_ratio` stays low for that failure
mode).

Result: page 12 comes from a broken text layer, page 13 from Tesseract. Different word spacing,
different diacritic handling, different heading detection (OCR pages have no `size`, so
`heading_tiers` silently drops to the one-tier line-height path). The book reads as if two people
typed it.

**anydoc's answer:** `detect.rs` establishes the container's identity **once**, from the marker the
specification designates, then every downstream stage trusts that one verdict. It never re-decides
per element.

### F2. Silent degradation with no record

`pdfio.py` has 11 bare `except Exception` handlers that return `""`, `None`, or `0.0`. Each is a
silent quality loss:

| Site | Swallowed failure | Invisible consequence |
|---|---|---|
| `pdfio.py:47` `_fontinfo_bold` | → `0.0` | bold heading signal dies for the page |
| `pdfio.py:151` `FPDFText_GetFontSize` | → `0.0` | font-size tiers collapse to line-height |
| `pdfio.py:155` `FPDFText_GetFontWeight` | → `-1` | bold arm of `heading_tiers` goes inert |
| `pdfio.py:200` `extract_text_page` | → `None` | page silently reroutes to OCR |
| `pdfio.py:129` `extract_text` | → `""` | page counted as "no text layer" |

A page that loses its font metrics produces *structurally different* Markdown from its neighbours,
and `report.json` records the route as a clean `text-layer` with reason `"embedded text layer"` —
it looks like everything worked.

**anydoc's answer:** recovery is allowed, silence is not. Every degrade path calls `log::warn!` with
what was skipped (`lib.rs` header: "Recovery and skipped-content events are reported through the
`log` facade"). `Err` is reserved for "no meaningful output was possible".

### F3. Coverage is measured but the safety net never fires

`report.py` implements `BOOK_COVERAGE_MIN = 0.95` / `PAGE_COVERAGE_MIN = 0.80` and
`collect_warnings` — and then **only warns**. The technique we took this from (pdfmarkdown.app,
recorded in memory) used coverage as a *trigger*: below 95%, rebuild with the other builder. We
built the gauge and left out the actuator. Pages that lose 40% of their text to an over-eager
paragraph merge or junk filter still ship that way.

### F4. Inline `[^id]` in body text is silently deleted — verified

`markdownize._NEEDS_ESCAPE` is anchored to `^`, so it only escapes markers at **line start**. A
paragraph containing `[^1]` mid-text passes through emit unescaped, parses back as an inline
noteref, and `reflow.render_text` drops it: `if nid not in note_order: return ""`
(`epub/reflow.py:57`).

Reproduced:

```
EMITTED  : 'AAA [^1] BBB'
PARSED   : 'AAA [^1] BBB'
RENDERED : <p>AAA  BBB</p>      ← the marker is gone from the book
```

Low frequency from OCR, but a near-certainty once a user hand-edits `--markdown-out` output.

**anydoc's answer:** a dedicated `render/markdown/escape.rs` (185 lines) with `EscapeOpts` and an
`InlineContext` (block vs inline), so escaping is decided by *position*, not by a line-start regex.

### F5. Four unrelated error types, no machine-readable code, no encrypted detection

We raise `PdfError(RuntimeError)`, `BuildError(RuntimeError)`, `EpubValidationError(ValueError)`,
and plain `ValueError` from six modules. `cli.py:142` catches `(PdfError, RuntimeError, ValueError)`;
`cli.py:317` and `cli.py:349` catch bare `Exception`; `webui/app.py` catches bare `Exception` at
three sites and prints `str(exc)` to the page.

Nothing can branch on *why* a conversion failed. Most concretely: **an encrypted PDF is not
detected at all** — `pdfio.py:76` collapses every pypdfium2 open failure into
`"Cannot open PDF '<name>': <raw pdfium message>"`.

**anydoc's answer:** one `ConvertError` with six variants and a stable `code()` string
(`"unsupported" | "malformed" | "encrypted" | "resourceLimit" | "missingPart" | "io"`), pinned by a
unit test whose comment reads "the bindings publish these verbatim, so changing one breaks every
caller that branches on it".

### F6. No fixture corpus, no snapshots — this is the core consistency lever

1,857 lines of tests across 15 files, **all synthetic units**. There is no test that converts a real
PDF and compares the output to a recorded baseline. So a one-line change to `headings.py`,
`paragraphs.py`, or `clean.py` has no visible blast radius: the unit tests stay green while every
book in the corpus silently restructures.

**anydoc's answer:** a committed fixture corpus (`tests/fixtures/`, 19 docx + 7 pptx + 6 rtf + …)
with 58 `insta` snapshots, so every heuristic change shows up as a reviewable diff.

Our pipeline is *unusually* well-suited to this: `run_text_mode` already produces a single Markdown
string in memory (`ocrmode.py:504`) before it becomes an EPUB. That string is a perfect snapshot
target — we get golden-file testing for the entire text pipeline with one assertion.

### F7. Output is not reproducible

`epub/reflow.py:139` calls `uuid.uuid4()` per build, and `zipwriter.EpubContainer.add` uses
`ZipInfo`'s default timestamp (now). Same PDF in → different EPUB bytes out, every run. That blocks
snapshotting the EPUB, blocks meaningful "did my change alter the book?" diffs, and makes the
workdir resume cache the only thing that is stable.

anydoc's workflow runtime bans `Date.now()` and `Math.random()` outright for exactly this reason.

### F8. Image mode bypasses the unified path — the same flaw anydoc has with PDF

`pipeline.run_image_mode` goes raw PNG → `build_image_epub` directly. No `Book`, no Markdown pivot,
no `ConversionReport`, no chapters, no coverage. It is architecturally the same hole anydoc has
where PDF skips the `Document` model — every benefit of the shared path evaporates for that one
mode. Worth naming so we don't grow a third one.

### F9. No resource limits on the real danger surfaces

We have scattered caps (`MAX_CHAPTER_WEIGHT`, `SCAN_MAX_HEIGHT`, `MAX_EMBED_HEIGHT`) but nothing
bounds: total rasterized bytes (page count × DPI²), total workdir size, total embedded scan bytes
(a book where every page fails OCR embeds *every* page as JPEG), or footnote/element counts. A
2,000-page book at `--dpi 600` will happily fill the disk.

**anydoc's answer:** `package/limits.rs` — nine documented constants, deliberately not configurable,
each sized from a measured worst case, and `ResourceLimit` is the one error `is_fatal()` marks as
unswallowable by recovery.

---

## Part 2 — The plan

Four phases, ordered by leverage. Phase 1 is the foundation: it makes the effect of Phases 2–4
*measurable*, so do it first even though it changes no user-visible behaviour.

### Phase 1 — Make output reproducible and observable

> Nothing else on this list can be verified without it.

**1.1 Deterministic builds** — `epub/reflow.py`, `epub/zipwriter.py`

- Replace `uuid.uuid4()` with `uuid.uuid5(NAMESPACE_URL, f"{title}|{author}|{source_fingerprint}")`.
  Take `source_fingerprint` from the existing `pipeline.file_fingerprints` shape, or the Markdown
  body hash in the `build` path.
- `EpubContainer.add`: pass an explicit `ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))`.
- Add `--book-id` to `convert`/`build` so a user can pin an id across re-runs.

*Test:* `test_epub.py::test_same_input_produces_identical_epub_bytes`.

**1.2 Golden-fixture harness** — new `tests/fixtures/`, `tests/snapshots/`, `tests/snapshot.py`

No new dependency — a ~40-line stdlib helper mirroring `insta`:

```python
# tests/snapshot.py
def assert_snapshot(name: str, actual: str) -> None:
    """Compare against tests/snapshots/<name>.md; write it when UPDATE_SNAPSHOTS=1."""
```

Corpus: 5–8 **small** PDFs committed to the repo (a few pages each), chosen to span the routes we
actually hit — clean text layer, ligature-corrupted text layer, PUA/non-Unicode font, pure scan,
mixed text+scan, footnote-heavy scholarly page, poetry page, foreign-script page. Build them with
the existing `_make_text_pdf` helper in `tests/test_pdfio_geometry.py` where synthetic will do;
commit real ones (a handful of pages extracted from a public-domain book) where it won't.

Snapshot **the Markdown string**, not the EPUB — it is stable, human-readable, and reviewable in a
PR. Two snapshots per fixture: `<name>.md` and `<name>.report.json`.

CI note: Tesseract is unavailable, so scan-route fixtures must assert route + report only, or ship a
pre-baked OCR cache JSON in the workdir (the `ocr-<engine>` stage cache format already supports
this, and `OcrPage.from_json` tolerates extra keys).

*Test:* `tests/test_snapshots.py::test_fixture_markdown_matches_snapshot` (parametrized over the
corpus).

**1.3 Degradation ledger** — `report.py`, `pdfio.py`, `ocrmode.py`

- `PageReport` gains `notes: list[str] = field(default_factory=list)`.
- New `report.degrade(page_no, note)` collector threaded into `PdfRasterizer` (or, simpler, a
  `list[str]` accumulator the rasterizer appends to and `run_text_mode` drains per page).
- Replace each bare `except Exception` in `pdfio.py` with a narrow catch that records what was lost:
  `"font sizes unavailable — heading tiers fell back to line height"`,
  `"font weight unavailable — bold heading signal inert"`, etc.
- Surface `notes` in the CLI report table and `report.json`.

*Test:* `test_report.py::test_degradation_note_recorded_when_font_metrics_missing`.

---

### Phase 2 — One decision per book, applied uniformly

**2.1 Book-level text-layer verdict** — `ocrmode.py`, `clean.py`

Replace the per-page gate with a two-stage decision that mirrors anydoc's "identify the container
once":

1. **Book verdict.** Sample every page with ≥ `TEXT_LAYER_SHORT_MIN_CHARS`. Compute
   `looks_corrupted_arabic` and `bad_glyph_ratio` over pages with **≥ 40 words only** (the pages
   where the signal is statistically valid). If the corrupt share of those pages exceeds
   `CORRUPT_BOOK_MAX = 0.25`, the **whole book** routes to OCR — including the short pages that
   individually looked fine.
2. **Per-page override.** Only when the book verdict is "text layer usable" do the existing per-page
   gates run, and only to *demote* a page to OCR — never to promote.

New signature:

```python
def _select_text_layer(samples: dict[int, str], text_layer_opt: str)
        -> tuple[set[int], dict[int, str], str]:   # (allowed, per-page reasons, book_verdict)
```

Record the book verdict in `report.json` and print it in the CLI header — the user should see
"text layer rejected book-wide: 38% of pages show ligature loss" rather than discovering it page by
page. `--text-layer always` bypasses both stages, as today.

*Tests:* `test_report.py::test_corrupt_majority_rejects_text_layer_book_wide`,
`::test_short_page_follows_book_verdict_not_its_own`.

**2.2 Typed error taxonomy** — new `src/pdf2ebook/errors.py`, plus `pdfio.py`, `buildmd.py`,
`epub/validate.py`, `config.py`, `cli.py`, `webui/app.py`

```python
class Pdf2EbookError(Exception):
    code: ClassVar[str] = "error"

class UnsupportedInputError(Pdf2EbookError):  code = "unsupported"
class MalformedDocumentError(Pdf2EbookError): code = "malformed"
class EncryptedDocumentError(Pdf2EbookError): code = "encrypted"
class ResourceLimitError(Pdf2EbookError):     code = "resource_limit"
class MissingAssetError(Pdf2EbookError):      code = "missing_part"
class OcrUnavailableError(Pdf2EbookError):    code = "ocr_unavailable"
class InvalidOptionError(Pdf2EbookError):     code = "invalid_option"
```

- Re-parent `PdfError`, `BuildError`, `EpubValidationError` under these (keep the old names as
  aliases so nothing external breaks).
- **Detect encryption** in `PdfRasterizer.__init__`: inspect the pypdfium2 error for the
  password/`FPDF_ERR_PASSWORD` case and raise `EncryptedDocumentError` with a bilingual message.
  This is a concrete user-facing win today.
- `cli.py`: one handler for `Pdf2EbookError` → bilingual message + `exit(1)`; `InvalidOptionError` →
  `exit(2)`. Delete the two bare `except Exception` blocks.
- `webui/app.py`: return `{"error": {"code": exc.code, "message": str(exc)}}` so the front end can
  branch (e.g. show a password prompt for `encrypted`).
- Follow anydoc's move exactly: pin the codes with
  `test_errors.py::test_codes_name_every_variant` and a comment saying the web UI branches on them.

*Tests:* `test_errors.py` (codes pinned), `test_cli.py::test_encrypted_pdf_reports_encrypted`.

---

### Phase 3 — Make the coverage safety net actually fire

**3.1 Flat-fallback builder** — `textproc/structure.py`, `ocrmode.py`

Add a second, deliberately dumb builder alongside `structure_page`:

```python
def structure_page_flat(page: OcrPage, keep_diacritics: bool,
                        drop_line: DropLine) -> list[Element]:
    """One paragraph per kept line. No merging, no verse/list/heading detection.
    Loses structure, never loses text — the fallback when the smart builder drops too much."""
```

In `run_text_mode` step 3, right where per-page coverage is already computed (`ocrmode.py:454`, the
comment there already notes coverage is exact pre-merge):

```python
if coverage < PAGE_COVERAGE_MIN and kept >= _MIN_PAGE_CHARS:
    data.elements = structure_page_flat(payload, opts.ocr.keep_diacritics, drop_line)
    data.route_note = f"structure fallback (smart builder kept {coverage:.0%})"
    # recompute coverage from the flat build
```

This is the piece pdfmarkdown.app actually shipped and we left out. Add `--no-structure-fallback` to
opt out, and count fallback pages in the report so the user sees how often it fired.

*Tests:* `test_structure.py::test_low_coverage_page_falls_back_to_flat`,
`::test_flat_builder_preserves_every_kept_line`.

**3.2 Position-aware escaping + the `[^id]` fix** — `textproc/markdownize.py`, `epub/reflow.py`

- Split escaping in two, the way `escape.rs` does:
  - **block context** (line start) — the current `_NEEDS_ESCAPE` behaviour;
  - **inline context** — escape `[^` anywhere in the line as `\[^`, and teach `_parse_items` to
    unescape it.
- `reflow.render_text`: an unresolvable `[^id]` must **never** return `""`. Render the literal text
  and record a warning: `f"unresolved footnote ref [^{nid}] kept as literal text"`.
- Add a "no text may vanish between Markdown and XHTML" guard: strip tags from the rendered chapter,
  compare `coverage_key` char counts against the source `Book`, and fail the test if they diverge.

*Tests:* `test_markdownize.py::test_inline_footnote_marker_survives_round_trip`,
`test_epub.py::test_no_characters_lost_between_book_and_xhtml`.

---

### Phase 4 — Hardening

**4.1 Central limits module** — new `src/pdf2ebook/limits.py`

Port `package/limits.rs` verbatim in spirit: one module, documented constants, each with the reason
and the measured basis, not configurable, raising `ResourceLimitError`.

```python
MAX_PAGES              = 5_000       # a book beyond this is a corpus, not a book
MAX_RASTER_PIXELS      = 40_000_000  # per page, at the requested DPI
MAX_WORKDIR_BYTES      = 20 * 1024**3
MAX_EMBEDDED_SCAN_BYTES = 256 * 1024**2   # every-page-failed-OCR guard
MAX_ELEMENTS_PER_BOOK  = 2_000_000
MAX_FOOTNOTES_PER_PAGE = 200
```

Move the existing scattered caps (`MAX_CHAPTER_WEIGHT`, `TARGET_CHAPTER_WEIGHT`, `SCAN_MAX_HEIGHT`,
`MAX_EMBED_HEIGHT`, `JPEG_QUALITY`) here so there is one place to read the pipeline's bounds.
Check `MAX_RASTER_PIXELS` in `extract_pages` **before** rendering, so a `--dpi 1200` typo fails in a
second instead of an hour.

*Test:* `test_limits.py::test_excessive_dpi_fails_before_rendering`.

**4.2 Mutation-robustness test** — new `tests/test_robustness.py`

Direct port of anydoc's `tests/robustness.rs`:

```python
class Rng:  # xorshift64* — deterministic across runs and platforms
    ...

def test_mutated_fixtures_never_crash():
    """Byte-mutate every corpus fixture; conversion may raise Pdf2EbookError,
    but must never raise an untyped exception, hang, or exhaust memory."""
```

This is what turns Phase 2.2's taxonomy from paperwork into a contract, and it is the fastest way to
flush out the `ctypes`-level failures currently hidden behind `except Exception`.

**4.3 Deferred — do not do yet**

- *Round-tripping our own EPUBs through anydoc as an external validator.* Attractive (`npx -y
  @firecrawl/anydoc book.epub` and diff against the source Markdown), but it adds a Node dependency
  to CI for a signal Phase 1.2 mostly already gives us. Revisit once the fixture corpus exists.
- *Replacing `pdfio` with pdf-inspector.* It claims ToUnicode CMap decoding for Type0/Identity-H and
  RTL support, which targets our exact failure surface — but it is a Rust crate with no Python
  binding published, and our no-new-runtime-deps constraint (CI license gate) rules it out for now.
  What we should steal is the *idea*, not the code: its `pages_needing_ocr` per-page routing is
  Phase 2.1.
- *Unifying image mode into the Book path (F8).* Real, but it is a mode we barely use and the work
  is disproportionate. Log it; do not schedule it.

---

## Sequencing and effort

| Phase | Change | Effort | Effect on output consistency |
|---|---|---|---|
| 1.1 | Deterministic EPUB bytes | S | none directly — unlocks all verification |
| 1.2 | Golden-fixture snapshots | M | **highest** — makes every future regression visible |
| 1.3 | Degradation ledger | S | high — explains inconsistency instead of hiding it |
| 2.1 | Book-level text-layer verdict | M | **highest** — kills mixed-provenance books |
| 2.2 | Typed errors + encrypted detection | M | medium — clarity, plus a real user-facing fix |
| 3.1 | Flat-fallback builder | S | high — stops silent text loss |
| 3.2 | Escaping + `[^id]` fix | S | medium — fixes a verified data-loss bug |
| 4.1 | Limits module | S | low — prevents runaway runs |
| 4.2 | Mutation test | M | low directly — enforces 2.2 |

**Recommended order:** 1.1 → 1.2 → 2.1 → 1.3 → 3.1 → 3.2 → 2.2 → 4.1 → 4.2.

1.2 before 2.1 is deliberate: the book-level verdict is the single change most likely to alter every
output in the corpus, and it should land as a **reviewable snapshot diff**, not as a leap of faith.

## Constraints this plan respects

- **No new runtime dependencies** (CI license gate) — the snapshot harness is hand-rolled stdlib;
  `syrupy` would be the alternative if a dev-only dep ever becomes acceptable.
- **Markdown round-trip parity tests stay green** — Phase 3.2 extends `_NEEDS_ESCAPE`/`_parse_items`
  symmetrically and adds a parity assertion rather than relaxing one.
- **Workdir resume is not broken** — no stage settings change except where a re-run is intended;
  `report.json` stays additive, as it is today.
- **Bilingual (ar/en) user-facing messages** — every new error, warning, and report line follows the
  existing style.
- **OCR cache compatibility** — `OcrPage.from_json` ignores unknown keys, so new per-page fields
  (degradation notes, fallback flags) extend the cache in both directions.

---

## What actually shipped (implemented 2026-08-07)

All four phases are implemented, tested and green: **201 tests, ruff clean**. Deviations from
the plan above, and why:

### 1. The corpus is split in two, not one set of PDFs

The plan called for 5–8 synthetic PDFs snapshotted end to end. Building them revealed that
pdfium's own line-grouping dominates the result: right-anchored Arabic lines of differing
widths get merged and interleaved into single rects, so a "footnote block" fixture came back
as garbled, run-together text. Snapshotting that would have made the corpus a test of pdfium,
not of our heuristics.

So the corpus is now two corpora:

- **`tests/pagefixtures.py`** — six fixtures expressed as `OcrPage`s with exact geometry, run
  through the new `ocrmode.structure_pages`. Covers every structuring rule: heading tiers
  (including the bold arm), indent-based paragraph splitting, page-boundary merge, verse
  detection, footnote splitting and ref rewriting, running-header removal, list detection.
- **`tests/corpus.py`** + **`tests/arabicpdf.py`** — two real PDFs (`clean_book`, `pua_book`)
  generated by a hand-rolled Type0/Identity-H writer with a ToUnicode CMap. Covers what only a
  real PDF can: extraction, font metrics, the book-level verdict, and byte-reproducibility.

This required extracting **`ocrmode.structure_pages`** out of `run_text_mode` (steps 3–5:
structure → coverage/fallback → boundary merge → serialize). `run_text_mode` is its only
production caller; the extraction also cut ~110 lines out of that function.

### 2. The mutation test found real bugs, as intended

`tests/test_robustness.py` immediately failed on both fixtures: every `self._doc[index]` call
in `pdfio` sat outside its `try`, so a damaged page leaked a raw `PdfiumError` straight past
the taxonomy. Fixed with a single guarded `PdfRasterizer._page()` accessor. `extract_text` and
`extract_text_page` deliberately *degrade* through it (an unreadable page just routes to OCR —
one bad page must not fail a 2,000-page book); `page_size_pts` and `render_page` raise typed.

### 3. Coverage denominator regression, caught by the snapshots

The `structure_pages` extraction initially reassigned `data.payload` to the footnote-stripped
page, which pulled note lines out of the coverage *denominator* while leaving their text in the
numerator — `footnote_page` snapshotted at **1.56 coverage**. That would have disabled the
Phase 3.1 safety net entirely (a page can never fall below threshold if the denominator is
understated). `PageData` now keeps `payload` (whole page, for measurement) and `body_page`
(footnote-stripped, for structuring) separately.

### 4. A pre-existing defect the corpus surfaced, deliberately left unfixed

`pages/poetry_page.md` records it: a short unpunctuated heading above a poem satisfies
`poetry._verse_candidate`, and `structure_page` tests `verse_idx` before `tiers`, so the
heading is swallowed into the `:::verse` block instead of being emitted as a heading. It is
outside this plan's scope, so it is pinned in the snapshot with a `KNOWN DEFECT` note rather
than changed — fixing the precedence would show up here as an intended diff.

### 5. Smaller deltas

- `ensure_output_outside_workdir` now exits **2** (usage error), not 1. It is purely an
  argument problem, detectable before any work; `test_convert_rejects_output_inside_cleaned_workdir`
  was updated with a comment saying so.
- The plan's `notes: list[str]` on `PageReport` shipped as designed, plus
  `ConversionReport.book_verdict` and `degraded_pages()`. `from_json` now ignores unknown keys
  so reports written by older versions still load.
- `epub/opf.py` also needed pinning: `dcterms:modified` used `datetime.now()`, a second source
  of non-determinism the plan had not spotted. It now stamps `FIXED_MODIFIED`.
- New CLI flags: `--book-id`, `--structure-fallback/--no-structure-fallback`.
- The web UI publishes `error_code`, `book_verdict` and `degraded_pages`, and prompts to remove
  the password when a conversion fails as `encrypted`.
- Phase 4.3's deferred items (anydoc as an external validator, pdf-inspector, unifying image
  mode) remain deferred, unchanged.
