# Adopt pdfmarkdown.app's proven techniques for clearer Arabic PDF → EPUB conversion

## Context

The user asked to research how https://pdfmarkdown.app/ works under the hood and implement/refine its approach in this project (`arabic-pdf2ebook`) to make Arabic PDF conversion easier and clearer.

**Research findings (reverse-engineered from its client-side JS bundles — closed-source, runs fully in-browser):** pdf.js extraction → **difficulty triage** (`badGlyphRatio`: pages whose text layer is ≥50% U+FFFD/private-use chars → broken ToUnicode → OCR; image-only pages → OCR) → in-browser OCR (PaddleOCR v5/ONNX with rotation retries) → **two Markdown builders** (PDF tagged structure tree when usable, else heuristics: font-size + bold-ratio headings, repeated header/footer stripping, footnotes as `[^n]`) → **coverage safety net** (if <95% of source text survived, fall back to the other builder) → **honest output** (stats, warnings, per-block provenance). Notably it has *no* Arabic/RTL handling — we already exceed it there.

**Where we stand:** commit `2bb154b` already replicated the core (unified geometry-rich `OcrPage` → shared structurer → in-memory Markdown pivot → EPUB). This plan adds the four missing pieces (user confirmed all four):

- **A. Markdown as a first-class user-facing format** — export the clean `.md`, hand-correct, rebuild the EPUB (`pdf2ebook build`) — pdfmarkdown.app's actual product.
- **B. Triage + honest conversion report** — script-agnostic bad-glyph gate, per-page routing reasons, text-coverage guard, report in CLI/web/JSON.
- **C. Footnotes** — missing entirely today; scholarly Arabic books need them. We have per-line font sizes on both paths → detect small bottom blocks with (١) markers → EPUB popup footnotes.
- **D. Bold-ratio heading signal** — Arabic has no capitalization; per-char font weight from pdfium makes heading detection robust on uniform-size PDFs.

**Constraints:** no new runtime deps (CI license gate; stdlib only), never call pdfmarkdown.app, keep Markdown round-trip parity tests green, respect workdir resume, bilingual (ar/en) user-facing messages per existing style.

**Verified corrections to build on:**
- `headings.py:66` gate is `(big and centered) or (keyword and centered) or (keyword and big)` — bold becomes a fourth self-sufficient `or` arm.
- **Bug to fix in A:** `ocrmode._make_scan_image` returns `str(dest.relative_to(work.root))` → `scans\page_0002.png` on Windows; must be `.as_posix()` for portable `.md`.
- `OcrPage.from_json` ignores unknown JSON keys → cache-compatible extensions (B's rescue flag, D's `bold`).
- CI has no Tesseract — new e2e tests must use text-layer PDFs (`_make_text_pdf` in `tests/test_pdfio_geometry.py`) or prebuilt Markdown.
- `pypdfium2` raw bindings expose `FPDFText_GetFontWeight` / `FPDFText_GetFontInfo` (verified on this machine).

---

## Phase A — Markdown export + `build` command

**Files:** `config.py`, `textproc/markdownize.py`, `ocrmode.py`, `cli.py`, new `src/pdf2ebook/buildmd.py`.

1. **Posix fix:** `_make_scan_image` → `dest.relative_to(work.root).as_posix()`.
2. **`config.py`:** rename `debug_markdown` → `markdown_out: Path | None` (only `cli.py`/`ocrmode.py` reference it).
3. **`markdownize.py` — front matter** (hand-rolled YAML subset, keys `title/author/language`):
   - `emit_front_matter(meta: dict[str, str]) -> list[str]` → `['---', 'title: …', …, '---', '']`
   - `parse_front_matter(md: str) -> tuple[dict[str, str], str]` — block must start at line 0 with `---`, `key: value` lines, closing `---` required else treated as body (lenient).
4. **`markdownize.py` — tolerant parse side only** (emit stays byte-identical except footnotes in C):
   - `_HEADING_RE = ^(#{1,6})\s+(.*)$` (#### – ###### clamp to h3); `_UL_RE = ^[-*•٭]\s+`; `_OL_RE = ^[0-9٠-٩]{1,3}\s*[.)\-]\s+` (accepts `١-`); `_SCAN_RE` accepts any alt text.
   - Unknown fence `:::x` → warn + treat line as paragraph (don't swallow following lines); unclosed fence → consume to EOF + warn; stray `:::` → warn + skip. CRLF tolerated (strip handles `\r`).
   - `markdown_to_book(..., on_warning: Callable[[str], None] | None = None)` — new optional kwarg; `_parse_items(md, warn=None)` internal.
5. **Exporter** in `ocrmode.py`: `_export_markdown(markdown, dest, meta, work_root)` — front matter + body (bytes unchanged), copy referenced scans from workdir into `dest.parent/'scans'/` so the `.md` is self-contained. Wire into step 5 replacing the `debug_markdown` write.
6. **CLI:** `convert --markdown-out PATH` (visible; keep hidden `--debug-markdown` alias, coalesce). New `build` command: `pdf2ebook build book.md [out.epub] --title --author --language --split-every --split-volumes --font --preshape`. Precedence: CLI option > front matter > filename stem. Warnings printed bilingually in yellow.
7. **`buildmd.py`:** `run_build(md_path, out_path, opts, progress=None) -> ConversionResult` — read UTF-8 (tolerate BOM) → `parse_front_matter` → `markdown_to_book` → shared `finalize_chapters(book)` (factor `_split_giant_chapters` + default "قسم N" titles out of `run_text_mode`) → `build_reflow_epub(..., work_root=md_path.parent, ...)` (reflow already resolves `work_root / image_path`). Pre-check missing scan files → bilingual error.

**Tests:** extend `test_markdownize.py` (front-matter round trip, clamping, new list markers, fence warnings lose no text, no-page-comment docs, CRLF, all-kinds parity guard); new `test_build_cli.py` (tmp .md + PNG → EPUB, front-matter title, `--title` override, missing scan → exit 1); extend `test_cli.py` (`convert --markdown-out` on text-layer PDF, then `build` on the result — full circle).

---

## Phase B — Triage + honest conversion report

**Files:** `textproc/clean.py`, `ocrmode.py`, new `src/pdf2ebook/report.py`, `pipeline.py` (ConversionResult), `cli.py`, `webui/app.py`, `webui/static/index.html`.

1. **`clean.bad_glyph_ratio(text) -> float`:** fraction of non-space chars in `[�-\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]` (U+FFFD + all PUA planes) — signature of legacy non-Unicode Arabic fonts.
2. **Gate:** factor text-layer selection (ocrmode.py:178–197) into `_select_text_layer(samples, text_layer_opt) -> tuple[set[int], list[str]]`. Book-level `looks_corrupted_arabic` as today + per-page `bad_glyph_ratio > 0.05` → route page to OCR with reason. `--text-layer always` bypasses.
3. **`report.py`:** route constants (`text-layer | ocr | ocr-rescued | image-kept | blank`); `@dataclass PageReport` (page_no, route, reason, confidence, source_chars, dropped_chars, emitted_chars, coverage); `@dataclass ConversionReport` (pages, warnings, `book_coverage`, `route_counts()`, JSON round trip); `coverage_key(text)` = `normalize_arabic` + strip whitespace; `collect_warnings(report)` — bilingual, thresholds `BOOK_COVERAGE_MIN=0.95`, `PAGE_COVERAGE_MIN=0.80`, cap per-page list ~10.
4. **Wiring:** `PageData` gains `route`/`reason`. Persist rescue/image flags into the OCR cache JSON as extra keys (`from_json` verified to ignore them; old caches → reason "cached"). **Coverage snapshot per page immediately after `structure_page`, before `merge_page_boundary`** (merge moves chars across pages but never loses them). Helper `_page_char_counts(page, keep_diacritics, drop_line)` mirrors structure_page's drop_line edge rule so junk-dropped lines count as `dropped_chars`, not lost coverage. Write `text/report.json` (additive — doesn't break resume); set `result.report`.
5. **Surfacing:** CLI rich table "تقرير التحويل — Conversion report" (route counts, mean conf, coverage %) + yellow warnings; web: extend `job.stats` with `routes`/`coverage`/`warnings`, show warnings div in `index.html` (reuse `.warn` styling).

**Tests:** extend `test_textproc.py` (bad_glyph_ratio cases incl. plane-15); new `test_report.py` (`_select_text_layer`, coverage_key, JSON round trip, warning thresholds, `_page_char_counts` vs dropped watermark line); extend `test_cli.py` (report printed + report.json written; warm-cache rerun still reports).

---

## Phase C — Footnotes

**Files:** new `src/pdf2ebook/textproc/footnotes.py`, `ocrmode.py`, `textproc/markdownize.py`, `book.py`, `epub/reflow.py`, `epub/templates.py`, `config.py`, `cli.py`, `webui/app.py`.

1. **`footnotes.py`:** `@dataclass(frozen=True) Footnote(label, text)`; `MARKER_RE` for `(١)` `١-` `١.` `١)` `(1)` `1-` + superscript digits; constants `ZONE_TOP=0.55`, `SIZE_RATIO_MAX=0.85` (text layer), `HEIGHT_RATIO_MAX=0.80` (OCR); `footnote_id(page_no, ordinal) -> "p{page+1}-{ordinal}"`;
   - `split_footnotes(page, body_size) -> tuple[OcrPage, list[Footnote]]` — maximal trailing run of visible lines that are (a) in bottom 45% of page, (b) clearly smaller than body (exact sizes when available, else height vs page median), (c) first line starts with MARKER_RE; marker-less small lines continue the previous note (wrapping). No block → unchanged.
   - `rewrite_body_refs(elements, notes, page_no)` — rewrite inline `(L)`/`[L]` body markers (Arabic-Indic/Latin digit equivalence) to `[^p{N}-{k}]` **only when the label occurs exactly once on the page**; otherwise the note renders unreferenced at chapter end. (Line-level bboxes can't see raised baselines — superscript body markers are out of scope for v1, documented.)
2. **Pipeline:** in step 3, **split footnotes before `structure_page`** (so `١-` note lines aren't eaten by `lists._OL_RE`), then structure, then `rewrite_body_refs`, then append `("footnote", normalized_text)` elements at page end (counts as emitted for B's coverage). Note: a trailing footnote element blocks the step-4 paragraph merge for that page pair — acceptable, comment it.
3. **Pivot:** `emit_elements(elements, page_no=0)` (default keeps existing callers/tests valid) emits `[^{id}]: text`; `_NEEDS_ESCAPE` gains `\[\^` arm; parse `_FOOTNOTE_RE = ^\[\^([^\]\s]+)\]:\s*(.*)$`; items grow a 4th field (note_id, `""` for others — private API). `markdown_to_book` → `Paragraph(text, "footnote", note_id=...)`, never a chapter trigger.
4. **`book.py`:** `Paragraph.note_id: str = ""`; JSON emits only when non-empty, `from_json` defaults — old `book.json` caches load.
5. **EPUB (`reflow.py`/`templates.py`):** body refs — after `escape()`, `re.sub(r"\[\^([^\]\s]+)\]", …)` → `<a epub:type="noteref" class="noteref" href="#fn-{id}" id="ref-fn-{id}"><sup>{n}</sup></a>` (Arabic-Indic numerals for `ar`); unknown id → unlinked `<sup>`. Chapter end always renders `<div class="footnotes"><hr/>` + `<aside epub:type="footnote" class="footnote" id="fn-{id}"><p><a href="#ref-…" epub:type="backlink">١.</a> text</p></aside>` (backlink only when a ref exists). Same-file ref+aside → popup notes on iBooks/Kobo/Thorium, graceful list elsewhere. Add CSS: `div.footnotes` (0.85em, top rule), `aside.footnote p`, `a.noteref`.
6. **Config/CLI/web:** `PipelineOptions.footnotes: bool = True`; `--footnotes/--no-footnotes`; `footnotes: bool = Form(True)`.

**Tests:** new `test_footnotes.py` (text-layer sizes split + wrapped continuation; OCR-height detection; negatives: top-of-page small line, body-size bottom line, markerless page; `١-` note not structured as `ol`; rewrite uniqueness rules; Arabic/Latin digit matching); extend `test_markdownize.py` (footnote round trip, `[^`-paragraph escape, body ref passthrough); extend EPUB chain test (noteref + aside + backlink, well-formed XML, unmatched note unlinked); `test_cli.py` `--no-footnotes`.

**Risk:** false positives (e.g. small dated signature line) — mitigated by requiring all three signals + trailing-run-only + `--no-footnotes` escape hatch; thresholds are module constants.

---

## Phase D — Bold-ratio heading signal

**Files:** `ocr/base.py`, `pdfio.py`, `textproc/headings.py`.

1. **`OcrLine.bold: float = 0.0`** (fraction of chars bold; OCR leaves 0.0); `from_json` → `ln.get("bold", 0.0)`.
2. **`pdfio.extract_text_page`:** per char alongside `FPDFText_GetFontSize`, collect `FPDFText_GetFontWeight` (try/except + hasattr guard → degrade to 0.0, never crash); per line over the same box-center-mapped chars: `bold_ratio = |weight≥600| / |known|`. Fallback when no usable weights: one `FPDFText_GetFontInfo` call for the line's first char — bold if name contains "bold" (ci) or descriptor flags bit `1<<18` (ForceBold) → 1.0.
3. **`headings.heading_tiers`:** fourth self-sufficient arm, inert on OCR pages:
   `bold = body_size > 0 and ln.size > 0 and ln.bold >= 0.6 and ln.size >= body_size * 0.98`
   `if (big and centered) or (keyword and centered) or (keyword and big) or bold:` → tier: `tier if big else ("h2" if keyword else _tier_font(ratio))` (bold body-size line → h3). Existing ≤8-words guard already excludes long bold emphasis lines. Update docstring to name all four signals.

**Tests:** extend `test_structure.py` (`_line` gains `bold=` kwarg: bold body-size off-center → h3; bold 1.5× → h2; bold 0.4 → not heading; OCR `size=0` + bold=1.0 → not heading; existing tier tests unchanged); extend `test_pdfio_geometry.py` (`_make_text_pdf` gains a `/F2 Helvetica-Bold` font object + per-item font choice; assert bold line `bold >= 0.5`, regular `== 0.0`); OcrLine JSON round trip incl. legacy blobs without `bold`.

---

## Order & verification

Implement A → B → C → D; each phase lands green independently.

After each phase: `ruff check src tests` + `pytest -q` (full suite; parity guards are the tripwire).

End-to-end verification (no Tesseract needed):
1. `pdf2ebook convert tests-generated text-layer PDF --markdown-out book.md` → inspect `book.md` (front matter, posix scan refs), confirm conversion report table + `workdir/text/report.json`.
2. Hand-edit `book.md` (change a heading, add a `####`), `pdf2ebook build book.md` → valid EPUB reflecting edits.
3. With a real Arabic PDF (user-supplied, e.g. one with footnotes): confirm footnote asides/noterefs in the EPUB XHTML (unzip + inspect), popup behavior in Thorium/calibre viewer, report coverage ≥95%, and `--no-footnotes` round trip.
4. Web UI: convert a PDF, confirm warnings/coverage appear in the job status panel.
