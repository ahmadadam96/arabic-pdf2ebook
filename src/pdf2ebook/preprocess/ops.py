"""Pure image operations on grayscale numpy arrays (uint8, white background).

Every function takes and returns an ndarray so they compose freely and are
trivially unit-testable.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image


def from_pil(img: Image.Image) -> np.ndarray:
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img)


def to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr, mode="L")


def deskew(gray: np.ndarray, max_angle: float = 5.0) -> np.ndarray:
    """Estimate skew from the ink pixels and rotate upright (capped at ±max_angle)."""
    # Ink mask: dark pixels on light paper.
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(mask)
    if coords is None or len(coords) < 100:
        return gray
    rect = cv2.minAreaRect(coords)
    angle = rect[2]
    # minAreaRect angles are in [-90, 0); map to the smallest rotation.
    if angle < -45:
        angle += 90
    if abs(angle) < 0.05 or abs(angle) > max_angle:
        return gray
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        gray, matrix, (w, h), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=255,
    )


def denoise(gray: np.ndarray, strength: int = 8) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, None, h=strength, templateWindowSize=7, searchWindowSize=21)


def denoise_fast(gray: np.ndarray) -> np.ndarray:
    return cv2.medianBlur(gray, 3)


def clahe(gray: np.ndarray, clip: float = 2.0, tile: int = 8) -> np.ndarray:
    return cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile)).apply(gray)


def sauvola(gray: np.ndarray, window: int = 31, k: float = 0.2) -> np.ndarray:
    """Sauvola adaptive binarization — handles uneven old paper far better than Otsu."""
    if window % 2 == 0:
        window += 1
    try:
        return cv2.ximgproc.niBlackThreshold(
            gray, 255, cv2.THRESH_BINARY, window, k,
            binarizationMethod=cv2.ximgproc.BINARIZATION_SAUVOLA,
        )
    except (AttributeError, cv2.error):
        # Fallback without opencv-contrib: integral-image Sauvola.
        img = gray.astype(np.float64)
        mean = cv2.boxFilter(img, ddepth=-1, ksize=(window, window), borderType=cv2.BORDER_REPLICATE)
        sqmean = cv2.boxFilter(img * img, ddepth=-1, ksize=(window, window), borderType=cv2.BORDER_REPLICATE)
        std = np.sqrt(np.maximum(sqmean - mean * mean, 0))
        threshold = mean * (1 + k * (std / 128.0 - 1))
        return np.where(img > threshold, 255, 0).astype(np.uint8)


def otsu(gray: np.ndarray) -> np.ndarray:
    _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return out


def autocrop(gray: np.ndarray, ink_thresh: float = 0.005, margin_pct: float = 2.0,
             max_crop_pct: float = 20.0) -> np.ndarray:
    """Trim white margins using row/column ink-density projections.

    Refuses to remove more than max_crop_pct per side so full-page images and
    maps survive intact.
    """
    h, w = gray.shape
    binary = sauvola(gray) if min(h, w) >= 64 else otsu(gray)
    ink = binary < 128
    rows = ink.mean(axis=1)
    cols = ink.mean(axis=0)

    def first_above(profile: np.ndarray) -> int:
        hits = np.nonzero(profile > ink_thresh)[0]
        return int(hits[0]) if hits.size else 0

    def last_above(profile: np.ndarray) -> int:
        hits = np.nonzero(profile > ink_thresh)[0]
        return int(hits[-1]) if hits.size else len(profile) - 1

    top, bottom = first_above(rows), last_above(rows)
    left, right = first_above(cols), last_above(cols)

    margin_y, margin_x = int(h * margin_pct / 100), int(w * margin_pct / 100)
    top = max(0, top - margin_y)
    bottom = min(h - 1, bottom + margin_y)
    left = max(0, left - margin_x)
    right = min(w - 1, right + margin_x)

    max_y, max_x = int(h * max_crop_pct / 100), int(w * max_crop_pct / 100)
    top = min(top, max_y)
    left = min(left, max_x)
    bottom = max(bottom, h - 1 - max_y)
    right = max(right, w - 1 - max_x)

    if bottom - top < h * 0.2 or right - left < w * 0.2:
        return gray
    return gray[top:bottom + 1, left:right + 1]


def upscale_if_small(gray: np.ndarray, target_char_height: int = 35) -> np.ndarray:
    """Upscale 2x when the median text-line height suggests characters are tiny.

    Tesseract's sweet spot is roughly 30-40 px character height.
    """
    est = estimate_line_height(gray)
    if est == 0 or est >= target_char_height * 0.8:
        return gray
    factor = min(2.0, target_char_height / est)
    if factor <= 1.05:
        return gray
    h, w = gray.shape
    return cv2.resize(gray, (int(w * factor), int(h * factor)), interpolation=cv2.INTER_LANCZOS4)


def estimate_line_height(gray: np.ndarray) -> int:
    """Median height of text-line runs from the horizontal ink projection."""
    binary = otsu(gray)
    ink_rows = (binary < 128).mean(axis=1) > 0.01
    heights: list[int] = []
    run = 0
    for flag in ink_rows:
        if flag:
            run += 1
        elif run:
            heights.append(run)
            run = 0
    if run:
        heights.append(run)
    heights = [h for h in heights if 4 <= h <= 200]
    if not heights:
        return 0
    return int(np.median(heights))


def scale_to_device(img: Image.Image, width: int, height: int) -> Image.Image:
    """Fit the page inside width x height preserving aspect ratio (no upscaling)."""
    if width <= 0 or height <= 0:
        return img
    factor = min(width / img.width, height / img.height)
    if factor >= 1.0:
        return img
    new_size = (max(1, math.floor(img.width * factor)), max(1, math.floor(img.height * factor)))
    return img.resize(new_size, Image.LANCZOS)


def scale_to_fill_width(img: Image.Image, width: int) -> Image.Image:
    """Scale the page so it spans exactly `width`, preserving aspect ratio.

    Unlike scale_to_device this upscales too and lets the height overflow the
    target screen: on narrow scanned pages the text block then fills the full
    reader width and you scroll vertically through the page.
    """
    if width <= 0 or img.width == width:
        return img
    factor = width / img.width
    new_size = (width, max(1, math.floor(img.height * factor)))
    return img.resize(new_size, Image.LANCZOS)
