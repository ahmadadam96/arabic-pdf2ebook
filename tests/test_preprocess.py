from __future__ import annotations

import numpy as np
from PIL import Image

from pdf2ebook.preprocess import ops
from pdf2ebook.preprocess.pipeline import detect_image_page, preprocess_for_image


def test_sauvola_returns_binary(text_page_image):
    gray = ops.from_pil(text_page_image)
    binary = ops.sauvola(gray)
    assert set(np.unique(binary)) <= {0, 255}


def test_autocrop_removes_margins_but_caps(text_page_image):
    gray = ops.from_pil(text_page_image)
    cropped = ops.autocrop(gray)
    assert cropped.shape[0] <= gray.shape[0]
    assert cropped.shape[1] <= gray.shape[1]
    # never crops more than 20% + margin per side
    assert cropped.shape[0] >= gray.shape[0] * 0.55
    assert cropped.shape[1] >= gray.shape[1] * 0.55


def test_autocrop_blank_page_untouched():
    blank = np.full((1000, 700), 255, dtype=np.uint8)
    out = ops.autocrop(blank)
    assert out.shape == blank.shape


def test_deskew_straightens_rotated_page(text_page_image):
    rotated = text_page_image.rotate(2.0, expand=False, fillcolor=255)
    gray = ops.from_pil(rotated)
    fixed = ops.deskew(gray)
    # After deskew, horizontal ink projection should be sharper (more empty rows).
    def empty_rows(arr):
        binary = ops.otsu(arr)
        return int(((binary < 128).mean(axis=1) < 0.01).sum())

    assert empty_rows(fixed) >= empty_rows(gray)


def test_scale_to_device_fits_and_keeps_ratio(text_page_image):
    out = ops.scale_to_device(text_page_image, 400, 600)
    assert out.width <= 400 and out.height <= 600
    ratio_in = text_page_image.width / text_page_image.height
    ratio_out = out.width / out.height
    assert abs(ratio_in - ratio_out) < 0.02


def test_scale_to_device_never_upscales(text_page_image):
    out = ops.scale_to_device(text_page_image, 5000, 5000)
    assert out.size == text_page_image.size


def test_scale_to_fill_width_fills_and_overflows(text_page_image):
    out = ops.scale_to_fill_width(text_page_image, 400)
    assert out.width == 400
    ratio_in = text_page_image.width / text_page_image.height
    ratio_out = out.width / out.height
    assert abs(ratio_in - ratio_out) < 0.02


def test_preprocess_for_image_split_scales_to_width(text_page_image):
    out = preprocess_for_image(text_page_image, 400, 600, fill=True)
    assert out.mode == "L"
    assert out.width == 400


def test_smart_split_never_bisects_text_lines():
    from pdf2ebook.pipeline import _smart_band_boundaries

    # 900px tall page: a text line (dark rows) at y=430..450, otherwise blank.
    img = np.full((900, 400), 255, dtype=np.uint8)
    img[430:451, 40:360] = 0
    # Split into 2 bands: the boundary must land in a blank row, not on the line.
    cuts = _smart_band_boundaries(Image.fromarray(img, mode="L"), 2, 450)
    assert len(cuts) == 1
    assert not 425 <= cuts[0] <= 455  # never through the dark text line


def test_smart_split_two_blank_rows_uses_clearest():
    from pdf2ebook.pipeline import _smart_band_boundaries

    img = np.full((800, 400), 255, dtype=np.uint8)
    img[100:200, 40:360] = 0   # top block
    img[550:700, 40:360] = 0   # bottom block
    cuts = _smart_band_boundaries(Image.fromarray(img, mode="L"), 2, 400)
    assert len(cuts) == 1
    # Boundary should prefer the wide blank middle (y 200-550), near target 400.
    assert 250 <= cuts[0] <= 500


def test_split_pages_keeps_photo_pages_whole(tmp_path):
    from pdf2ebook.pipeline import split_pages
    from pdf2ebook.workdir import WorkDir

    pdf = tmp_path / "book.pdf"
    pdf.touch()
    work = WorkDir(tmp_path / "work", pdf)
    src = tmp_path / "page_0001.png"
    Image.new("L", (1264, 2895), 60).save(src)  # mostly dark = photo/cover
    out = split_pages(work, [src], 0, 1680)
    assert len(out) == 1  # one full page, not split into bands
    with Image.open(out[0]) as im:
        assert im.size == (1264, 2895)


def test_preprocess_for_image_pipeline(text_page_image):
    out = preprocess_for_image(text_page_image, 758, 1024)
    assert out.mode == "L"
    assert out.width <= 758 and out.height <= 1024


def test_detect_image_page_text_vs_photo(text_page_image):
    assert detect_image_page(text_page_image) is False
    photo = Image.new("L", (800, 1200), 60)  # mostly dark plate
    assert detect_image_page(photo) is True
