"""Local web UI: a friendly drag-and-drop page on http://127.0.0.1:8765.

Everything runs on the user's own machine — the browser page is just a front
end for the same pipeline the CLI uses. Jobs run in background threads and the
page polls /api/jobs/{id} for progress.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .. import __version__
from ..config import EpubMeta, ImageOptions, OcrOptions, PipelineOptions, validate_pipeline_options

STATIC_DIR = Path(__file__).parent / "static"


def jobs_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    d = Path(base) / "pdf2ebook" / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Job:
    job_id: str
    pdf_path: Path
    status: str = "running"  # running | done | error
    stage: str = "starting"
    done: int = 0
    total: int = 1
    error: str = ""
    outputs: list[Path] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()

app = FastAPI(title="arabic-pdf2ebook", version=__version__)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status() -> dict:
    from ..ocr.registry import backend_status

    engines = {name: {"available": ok, "hint": hint}
               for name, (ok, hint) in backend_status().items()}
    return {"version": __version__, "engines": engines}


def _run_job(job: Job, opts: PipelineOptions, out_path: Path) -> None:
    def on_progress(stage: str, done: int, total: int) -> None:
        job.stage, job.done, job.total = stage, done, total

    try:
        if opts.mode == "image":
            from ..pipeline import run_image_mode

            result = run_image_mode(job.pdf_path, out_path, opts, on_progress)
        else:
            from ..ocrmode import run_text_mode

            result = run_text_mode(job.pdf_path, out_path, opts, on_progress)
        job.outputs = result.outputs
        job.stats = {
            "pages_total": result.pages_total,
            "pages_direct_text": result.pages_direct_text,
            "pages_ocr": result.pages_ocr,
            "pages_image_fallback": result.pages_image_fallback,
        }
        if result.report is not None:
            job.stats["routes"] = result.report.route_counts()
            job.stats["coverage"] = round(result.report.book_coverage, 3)
            job.stats["warnings"] = result.report.warnings
        job.status = "done"
    except Exception as exc:  # surface anything to the page
        job.status = "error"
        job.error = str(exc)


@app.post("/api/convert")
async def convert(
    file: UploadFile = File(...),
    mode: str = Form("auto"),
    device: str = Form("generic-6in"),
    engine: str = Form("tesseract"),
    split_volumes: int = Form(1),
    font: str = Form("amiri"),
    preshape: bool = Form(False),
    footnotes: bool = Form(True),
) -> JSONResponse:
    job_id = uuid.uuid4().hex[:12]
    job_root = jobs_dir() / job_id
    job_root.mkdir(parents=True)
    filename = Path(file.filename or "book.pdf").name
    pdf_path = job_root / filename
    with pdf_path.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    opts = PipelineOptions(
        mode=mode, split_volumes=max(1, split_volumes), font=font, preshape=preshape,
        footnotes=footnotes, work_dir=job_root / "workdir",
        ocr=OcrOptions(engine=engine),
        image=ImageOptions(device=device),
        meta=EpubMeta(),
    )
    try:
        validate_pipeline_options(opts)
        from ..devices import get_profile

        get_profile(device)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    out_path = job_root / (pdf_path.stem + ".epub")
    job = Job(job_id=job_id, pdf_path=pdf_path)
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=_run_job, args=(job, opts, out_path), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return {
        "status": job.status,
        "stage": job.stage,
        "done": job.done,
        "total": job.total,
        "error": job.error,
        "stats": job.stats,
        "outputs": [{"index": i, "name": p.name, "size": p.stat().st_size}
                    for i, p in enumerate(job.outputs) if p.exists()],
    }


@app.get("/api/download/{job_id}/{index}")
def download(job_id: str, index: int) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or index >= len(job.outputs):
        raise HTTPException(404, "unknown output")
    path = job.outputs[index]
    return FileResponse(path, filename=path.name, media_type="application/epub+zip")


@app.post("/api/install-font")
def install_font(host: str = Form("")) -> dict:
    from ..fontkit import install_fonts_on_reader

    try:
        used_host, count, method = install_fonts_on_reader(host or None)
    except Exception as exc:
        raise HTTPException(502, f"Font install failed: {exc}") from exc
    return {"ok": True, "host": used_host, "files": count, "restart_needed": method == "manual"}


@app.post("/api/send")
def send(job_id: str = Form(...), index: int = Form(0), host: str = Form("")) -> dict:
    from ..send import send_to_reader

    job = JOBS.get(job_id)
    if job is None or index >= len(job.outputs):
        raise HTTPException(404, "unknown output")
    try:
        target = send_to_reader(job.outputs[index], host or None)
    except Exception as exc:
        raise HTTPException(502, f"Upload failed: {exc}") from exc
    return {"ok": True, "host": target}


def run_ui(port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"pdf2ebook web UI running at {url}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
