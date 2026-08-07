"""Tesseract backend via pytesseract.

Binary discovery order: TESSERACT_CMD env var → PATH → default install dir.
Arabic language data: when `ara` is missing from the installed tessdata, the
backend downloads `ara.traineddata` (tessdata_best, ~1.5 MB, Apache-2.0) into
a per-user data directory and points Tesseract at it with --tessdata-dir.
"""

from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path

from ..errors import OcrUnavailableError
from .base import OcrBackend, OcrLine, OcrPage, OcrWord

DEFAULT_WINDOWS_PATHS = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]
TESSDATA_BEST_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata"
INSTALL_HINT = (
    "Tesseract OCR is not installed. On Windows run:\n"
    "    winget install UB-Mannheim.TesseractOCR\n"
    "or download it from https://github.com/UB-Mannheim/tesseract/wiki\n"
    "On Linux: sudo apt install tesseract-ocr"
)


def find_tesseract() -> Path | None:
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return Path(env)
    on_path = shutil.which("tesseract")
    if on_path:
        return Path(on_path)
    for candidate in DEFAULT_WINDOWS_PATHS:
        if candidate.exists():
            return candidate
    return None


def user_tessdata_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    d = Path(base) / "pdf2ebook" / "tessdata"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_language(lang: str, available_langs: list[str]) -> Path | None:
    """Return a --tessdata-dir to use when any requested language is missing.

    Downloads missing tessdata_best models into the per-user dir.
    """
    requested = [piece for piece in lang.split("+") if piece]
    missing = [piece for piece in requested if piece not in available_langs]
    if not missing:
        return None
    target = user_tessdata_dir()
    for piece in missing:
        dest = target / f"{piece}.traineddata"
        if not dest.exists():
            url = TESSDATA_BEST_URL.format(lang=piece)
            tmp = dest.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)  # noqa: S310 (official tessdata repo)
            tmp.replace(dest)
    # Tesseract needs every language of the run in the same tessdata dir.
    for piece in requested:
        dest = target / f"{piece}.traineddata"
        if not dest.exists() and piece in available_langs:
            # Copy from the system tessdata when possible.
            cmd = find_tesseract()
            if cmd:
                system_data = cmd.parent / "tessdata" / f"{piece}.traineddata"
                if system_data.exists():
                    shutil.copy2(system_data, dest)
        if not dest.exists():
            url = TESSDATA_BEST_URL.format(lang=piece)
            tmp = dest.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)  # noqa: S310
            tmp.replace(dest)
    return target


class TesseractBackend(OcrBackend):
    name = "tesseract"

    def __init__(self, lang: str = "ara", psm: int = 4, oem: int = 1):
        import pytesseract

        cmd = find_tesseract()
        if cmd is None:
            raise OcrUnavailableError(INSTALL_HINT)
        pytesseract.pytesseract.tesseract_cmd = str(cmd)
        self._pt = pytesseract
        self.lang = lang
        self.psm = psm
        self.oem = oem

        try:
            available = pytesseract.get_languages(config="")
        except Exception:
            available = []
        self._tessdata_dir = ensure_language(lang, available)
        if self._tessdata_dir is not None:
            # Env var instead of --tessdata-dir: immune to config-string
            # quoting issues and to spaces in the user profile path.
            os.environ["TESSDATA_PREFIX"] = str(self._tessdata_dir)

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return (find_tesseract() is not None, INSTALL_HINT)

    def _config(self, psm: int | None = None) -> str:
        parts = [
            f"--oem {self.oem}",
            f"--psm {psm if psm is not None else self.psm}",
            "-c preserve_interword_spaces=1",
            "--dpi 300",
        ]
        return " ".join(parts)

    def recognize(self, image_path: Path, psm: int | None = None) -> OcrPage:
        from PIL import Image

        from pytesseract import Output

        with Image.open(image_path) as img:
            size = img.size
            data = self._pt.image_to_data(
                img, lang=self.lang, config=self._config(psm), output_type=Output.DICT
            )

        lines: dict[tuple[int, int, int], OcrLine] = {}
        n = len(data["text"])
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            word = OcrWord(
                text=text,
                conf=float(data["conf"][i]),
                bbox=(data["left"][i], data["top"][i], data["width"][i], data["height"][i]),
            )
            line = lines.setdefault(key, OcrLine())
            line.words.append(word)

        for line in lines.values():
            xs = [w.bbox[0] for w in line.words]
            ys = [w.bbox[1] for w in line.words]
            x2s = [w.bbox[0] + w.bbox[2] for w in line.words]
            y2s = [w.bbox[1] + w.bbox[3] for w in line.words]
            line.bbox = (min(xs), min(ys), max(x2s) - min(xs), max(y2s) - min(ys))

        ordered = [lines[k] for k in sorted(lines)]
        return OcrPage(page_no=-1, size=size, lines=ordered)
