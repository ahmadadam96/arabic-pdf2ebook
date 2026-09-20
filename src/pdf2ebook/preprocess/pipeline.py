"""Mode-specific preprocessing pipelines composed from ops.py."""

from __future__ import annotations

import numpy as np
from PIL import Image

from . import ops


def preprocess_for_ocr(img: Image.Image, fast: bool = False) -> Image.Image:
    """Deskew → denoise → CLAHE → Sauvola binarize → autocrop → upscale small print."""
    gray = ops.from_pil(img)
    gray = ops.deskew(gray)
    gray = ops.denoise_fast(gray) if fast else ops.denoise(gray)
    gray = ops.clahe(gray)
    binary = ops.sauvola(gray)
    binary = ops.autocrop(binary)
    binary = ops.upscale_if_small(binary)
    return ops.to_pil(binary)


def preprocess_for_image(img: Image.Image, width: int, height: int,
                         style: str = "gray", fast: bool = True,
                         fill: bool = False) -> Image.Image:
    """Deskew → denoise → CLAHE → autocrop → scale to device. Keeps grayscale tone."""
    gray = ops.from_pil(img)
    gray = ops.deskew(gray)
    gray = ops.denoise_fast(gray) if fast else ops.denoise(gray)
    gray = ops.clahe(gray)
    gray = ops.autocrop(gray)
    if style == "binary":
        gray = ops.sauvola(gray)
    if fill:
        return ops.scale_to_fill_width(ops.to_pil(gray), width)
    return ops.scale_to_device(ops.to_pil(gray), width, height)


def detect_image_page(img: Image.Image) -> bool:
    """Cheap screen for photo/map pages that should skip OCR.

    Text pages have moderate ink coverage made of many small components;
    image pages have either heavy ink coverage or a few huge components.
    """
    gray = ops.from_pil(img)
    if float(gray.mean()) < 110:  # predominantly dark page: photograph/plate
        return True
    binary = ops.otsu(gray)
    ink = binary < 128
    coverage = float(ink.mean())
    if coverage > 0.45:  # mostly dark after binarization
        return True
    if coverage < 0.005:  # blank page
        return False
    import cv2

    num, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    if num <= 1:
        return False
    areas = stats[1:, cv2.CC_STAT_AREA]
    page_area = gray.shape[0] * gray.shape[1]
    # One component swallowing >25% of the page = image/map, not text.
    return bool(areas.max() > 0.25 * page_area)
