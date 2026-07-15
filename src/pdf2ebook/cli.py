"""Typer CLI: convert / devices / inspect / preview / send / ui."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import (BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
                           TextColumn, TimeRemainingColumn)
from rich.table import Table

from . import __version__
from .config import EpubMeta, ImageOptions, OcrOptions, PipelineOptions, validate_pipeline_options
from .devices import DEFAULT_PROFILE, PROFILES
from .pdfio import PdfError

app = typer.Typer(
    name="pdf2ebook",
    help="Convert scanned Arabic PDF books into e-reader friendly EPUBs.",
    no_args_is_help=False,
    invoke_without_command=True,
)
console = Console()


@app.callback()
def _root(ctx: typer.Context,
          version: bool = typer.Option(False, "--version", help="Show version and exit")) -> None:
    if version:
        console.print(f"arabic-pdf2ebook {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        # Double-clicking the packaged .exe lands here: launch the friendly web UI.
        from .webui.app import run_ui

        run_ui()


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
    )


@app.command()
def convert(
    pdf: Path = typer.Argument(..., help="Input PDF file"),
    output: Optional[Path] = typer.Argument(None, help="Output EPUB path (default: beside the PDF)"),
    mode: str = typer.Option("auto", help="auto | ocr | image"),
    text_layer: str = typer.Option("auto", help="Use the PDF's embedded text layer: auto | always | never"),
    engine: str = typer.Option("tesseract", help="OCR engine: tesseract | surya"),
    lang: str = typer.Option("ara", help="Tesseract language string, e.g. 'ara' or 'ara+eng'"),
    device: str = typer.Option(DEFAULT_PROFILE, help="Device profile for image mode (see `devices`)"),
    width: Optional[int] = typer.Option(None, help="Override device width (px)"),
    height: Optional[int] = typer.Option(None, help="Override device height (px)"),
    dpi: int = typer.Option(300, help="Rasterization DPI"),
    pages: Optional[str] = typer.Option(None, help="Page subset, e.g. '5-20' or '1-10,15'"),
    psm: int = typer.Option(4, help="Tesseract page segmentation mode"),
    min_conf: float = typer.Option(40.0, help="Below this mean confidence a page becomes an image"),
    rescue: bool = typer.Option(True, help="Re-OCR low-confidence lines with alternate binarization"),
    split_volumes: int = typer.Option(1, help="Split output into N EPUB volumes"),
    split_every: int = typer.Option(10, help="Chapter fallback: one chapter per N pages (OCR mode)"),
    layout: str = typer.Option("flow", help="Image EPUB layout: flow | fixed"),
    style: str = typer.Option("gray", help="Image tone: gray | binary"),
    cbz: bool = typer.Option(False, "--cbz", help="Also write a CBZ (image mode)"),
    font: str = typer.Option("amiri", help="Embedded font: amiri | none"),
    preshape: bool = typer.Option(
        False, "--preshape",
        help="Bake Arabic letter-joining into the text (presentation forms). ONLY for simple "
             "readers like CrossPoint; normal readers (Kobo/Apple/phones) need it OFF.",
    ),
    title: Optional[str] = typer.Option(None, help="EPUB title (default: from filename)"),
    author: Optional[str] = typer.Option(None, help="EPUB author"),
    strip_pattern: list[str] = typer.Option([], "--strip-pattern", help="Extra watermark regex (repeatable)"),
    work_dir: Optional[Path] = typer.Option(None, help="Cache directory (default: <book>.workdir)"),
    force: Optional[str] = typer.Option(None, help="Recompute stage: extract | preprocess | ocr | all"),
    clean: bool = typer.Option(False, help="Delete the work dir after a successful conversion"),
    footnotes: bool = typer.Option(
        True, "--footnotes/--no-footnotes",
        help="Detect footnote blocks and link them (popup notes on modern readers)"),
    markdown_out: Optional[Path] = typer.Option(
        None, "--markdown-out",
        help="Also write the editable Markdown (with a scans/ folder) next to the EPUB; "
             "correct it by hand and rebuild with `pdf2ebook build`."),
    debug_markdown: Optional[Path] = typer.Option(
        None, "--debug-markdown", hidden=True,
        help="Deprecated alias for --markdown-out."),
) -> None:
    """Convert a PDF book to EPUB."""
    opts = PipelineOptions(
        mode=mode, text_layer=text_layer, dpi=dpi, pages=pages, preshape=preshape,
        split_volumes=split_volumes, split_every=split_every,
        font=font, work_dir=work_dir, force=force, clean=clean, footnotes=footnotes,
        markdown_out=markdown_out or debug_markdown,
        ocr=OcrOptions(engine=engine, lang=lang, psm=psm, min_conf=min_conf,
                       rescue=rescue, strip_patterns=list(strip_pattern)),
        image=ImageOptions(device=device, width=width, height=height, style=style,
                           layout=layout, cbz=cbz),
        meta=EpubMeta(title=title or "", author=author or ""),
    )
    try:
        validate_pipeline_options(opts)
        if opts.mode == "image" and device not in PROFILES:
            valid = ", ".join(sorted(PROFILES))
            raise ValueError(f"device must be one of: {valid}")
    except ValueError as exc:
        console.print(f"[red]Invalid option:[/red] {exc}")
        raise typer.Exit(2)
    out_path = output or pdf.with_suffix(".epub")

    from . import pipeline

    tasks: dict[str, object] = {}
    stage_labels = {
        "extract": "Extracting pages",
        "preprocess": "Cleaning images",
        "ocr": "Recognizing text (OCR)",
        "epub": "Building EPUB",
    }

    try:
        with _make_progress() as bar:
            def on_progress(stage: str, done: int, total: int) -> None:
                if stage not in tasks:
                    tasks[stage] = bar.add_task(stage_labels.get(stage, stage), total=total)
                bar.update(tasks[stage], completed=done, total=total)

            if mode == "image":
                result = pipeline.run_image_mode(pdf, out_path, opts, on_progress)
            else:
                from .ocrmode import run_text_mode

                result = run_text_mode(pdf, out_path, opts, on_progress)
    except (PdfError, RuntimeError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    console.print()
    for out in result.outputs:
        size_mb = out.stat().st_size / 1024 / 1024
        console.print(f"  [green]✔[/green] {out}  ({size_mb:.1f} MB)")
    if mode != "image":
        console.print(
            f"  pages: {result.pages_total} total — "
            f"{result.pages_direct_text} direct text, {result.pages_ocr} OCR, "
            f"{result.pages_image_fallback} kept as images"
        )
        if result.mean_confidences:
            avg = sum(result.mean_confidences) / len(result.mean_confidences)
            console.print(f"  mean OCR confidence: {avg:.0f}")
        if result.stripped_lines:
            console.print(f"  removed repeated watermark/header lines: {len(result.stripped_lines)}")
        if result.report is not None:
            _print_report(result.report)


_ROUTE_LABELS = {
    "text-layer": "نص مضمّن — text layer",
    "ocr": "OCR",
    "ocr-rescued": "OCR منقذ — rescued",
    "image-kept": "صور — kept as image",
    "blank": "فارغة — blank",
}


def _print_report(report) -> None:
    """Render the per-run conversion report (route counts, coverage, warnings)."""
    counts = report.route_counts()
    if counts:
        table = Table(title="تقرير التحويل — Conversion report")
        table.add_column("المسار — Route", style="bold")
        table.add_column("صفحات — Pages", justify="right")
        for route, n in counts.items():
            table.add_row(_ROUTE_LABELS.get(route, route), str(n))
        console.print(table)
    console.print(f"  تغطية النص — text coverage: {round(report.book_coverage * 100)}%")
    for warning in report.warnings:
        console.print(f"[yellow]⚠[/yellow] {warning}")


@app.command()
def build(
    markdown: Path = typer.Argument(..., help="Markdown file from `convert --markdown-out` (may be hand-edited)"),
    output: Optional[Path] = typer.Argument(None, help="Output EPUB path (default: beside the .md)"),
    title: Optional[str] = typer.Option(None, help="EPUB title (overrides the file's front matter)"),
    author: Optional[str] = typer.Option(None, help="EPUB author (overrides front matter)"),
    language: Optional[str] = typer.Option(None, help="Language code (default: front matter, then 'ar')"),
    split_every: int = typer.Option(10, help="Chapter fallback: one chapter per N pages when no headings"),
    split_volumes: int = typer.Option(1, help="Split output into N EPUB volumes"),
    font: str = typer.Option("amiri", help="Embedded font: amiri | none"),
    preshape: bool = typer.Option(
        False, "--preshape", help="Bake Arabic letter-joining (simple readers like CrossPoint only)"),
) -> None:
    """Build (or rebuild) an EPUB from a Markdown file — the reverse of `convert --markdown-out`."""
    from .buildmd import BuildError, run_build

    out_path = output or markdown.with_suffix(".epub")
    opts = PipelineOptions(
        split_every=split_every, split_volumes=split_volumes, font=font, preshape=preshape,
    )
    try:
        validate_pipeline_options(opts)
    except ValueError as exc:
        console.print(f"[red]Invalid option:[/red] {exc}")
        raise typer.Exit(2)
    warnings: list[str] = []
    try:
        result = run_build(markdown, out_path, opts, cli_title=title, cli_author=author,
                           cli_language=language, on_warning=warnings.append)
    except BuildError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    for warning in warnings:
        console.print(f"[yellow]تحذير — warning:[/yellow] {warning}")
    console.print()
    for out in result.outputs:
        size_mb = out.stat().st_size / 1024 / 1024
        console.print(f"  [green]✔[/green] {out}  ({size_mb:.1f} MB)")


@app.command()
def devices() -> None:
    """List device profiles for image mode."""
    table = Table(title="Device profiles")
    table.add_column("Key", style="bold")
    table.add_column("Label")
    table.add_column("Resolution")
    table.add_column("Notes")
    for p in PROFILES.values():
        res = f"{p.width}x{p.height}" if p.width else "source"
        table.add_row(p.key, p.label, res, p.notes)
    console.print(table)


@app.command()
def inspect(pdf: Path = typer.Argument(..., help="PDF file or folder of PDFs")) -> None:
    """Show page count, size, text-layer detection and a recommendation."""
    from .pipeline import inspect_pdf

    targets = sorted(pdf.glob("*.pdf")) if pdf.is_dir() else [pdf]
    if not targets:
        console.print("[yellow]No PDF files found.[/yellow]")
        raise typer.Exit(1)
    for target in targets:
        try:
            info = inspect_pdf(target)
        except PdfError as exc:
            console.print(f"[red]✘ {target.name}[/red]: {exc}")
            continue
        console.print(f"[bold]{info['file']}[/bold]")
        console.print(f"  pages: {info['pages']}, page size: {info['page_size_pts']} pts")
        if info["has_text_layer"]:
            state = "usable" if info.get("text_layer_usable") else "unhealthy"
            console.print(
                f"  text layer: yes, {state} "
                f"(~{info['avg_sample_text_chars']} chars/page sampled)"
            )
        else:
            console.print(f"  text layer: no (~{info['avg_sample_text_chars']} chars/page sampled)")
        console.print(f"  → {info['recommendation']}")


@app.command()
def preview(
    pdf: Path = typer.Argument(...),
    pages: str = typer.Option("1-3", help="Pages to preview"),
    mode: str = typer.Option("image", help="image | ocr preprocessing"),
    device: str = typer.Option(DEFAULT_PROFILE),
    dpi: int = typer.Option(300),
) -> None:
    """Run extract+preprocess on a few pages and open the folder to eyeball results."""
    import os

    from .config import parse_page_range
    from .devices import get_profile
    from .pdfio import PdfRasterizer
    from .pipeline import default_work_dir, extract_pages, preprocess_image_pages
    from .workdir import WorkDir

    work = WorkDir(default_work_dir(pdf), pdf)
    with PdfRasterizer(pdf) as doc:
        indices = parse_page_range(pages, doc.page_count)
        raw = extract_pages(doc, work, indices, dpi)
    if mode == "ocr":
        from .ocrmode import preprocess_ocr_pages

        out = preprocess_ocr_pages(work, raw)
        folder = work.root / "pre-ocr"
    else:
        profile = get_profile(device)
        out = preprocess_image_pages(work, raw, profile.width, profile.height, "gray")
        folder = work.root / "pre-image"
    console.print(f"[green]✔[/green] {len(out)} preview pages in: {folder}")
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606


@app.command()
def send(
    epub: Path = typer.Argument(..., help="EPUB file to upload"),
    host: Optional[str] = typer.Option(None, help="Reader IP, e.g. 192.168.1.50 (remembered)"),
) -> None:
    """Upload an EPUB to a CrossPoint e-reader over Wi-Fi."""
    from .send import send_to_reader

    try:
        target = send_to_reader(epub, host)
    except Exception as exc:
        console.print(f"[red]Upload failed:[/red] {exc}")
        console.print("Make sure the reader's Wi-Fi transfer mode is on and you are on the same network.")
        raise typer.Exit(1)
    console.print(f"[green]✔[/green] Uploaded {epub.name} to {target}")


fonts_app = typer.Typer(help="Arabic font pack for readers without Arabic support.")
app.add_typer(fonts_app, name="fonts")


@fonts_app.command("export")
def fonts_export(
    dest: Path = typer.Argument(Path("pdf2ebook-fonts"), help="Folder to write the font pack into"),
) -> None:
    """Write ready-to-install Arabic fonts (TTF + CrossPoint cpfont) with instructions."""
    from .fontkit import export_fonts

    written = export_fonts(dest)
    console.print(f"[green]✔[/green] Wrote {len(written)} files to {dest.resolve()}")
    console.print("  Read README.txt inside for per-device instructions (AR/EN).")


@fonts_app.command("install")
def fonts_install(
    host: Optional[str] = typer.Option(None, help="Reader IP, e.g. 192.168.1.50 (remembered)"),
) -> None:
    """Install the Arabic font on a CrossPoint e-reader over Wi-Fi (one time)."""
    from .fontkit import install_fonts_on_reader

    try:
        used_host, count, method = install_fonts_on_reader(host)
    except Exception as exc:
        console.print(f"[red]Font install failed:[/red] {exc}")
        console.print("Make sure the reader's Wi-Fi transfer mode is on and you are on the same network.")
        raise typer.Exit(1)
    console.print(f"[green]✔[/green] Installed {count} font files on {used_host}")
    if method == "manual":
        console.print("  [yellow]Restart the reader now[/yellow] (power off/on) so it finds the font.")
        console.print("  أعد تشغيل القارئ الآن (إطفاء وتشغيل) ليتعرف على الخط.")
    console.print("  Then on the reader: Settings → Reader → Font Family → Amiri")
    console.print("  Convert books with --preshape so letters join correctly.")


@app.command()
def ui(
    port: int = typer.Option(8765, help="Local port"),
    no_browser: bool = typer.Option(False, help="Don't open the browser automatically"),
) -> None:
    """Start the local web page (drag & drop PDF → EPUB)."""
    from .webui.app import run_ui

    run_ui(port=port, open_browser=not no_browser)


def main() -> None:
    # Legacy Windows consoles default to cp1252 which cannot print Arabic
    # titles or box-drawing characters; force UTF-8 output streams.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    app()


if __name__ == "__main__":
    main()
