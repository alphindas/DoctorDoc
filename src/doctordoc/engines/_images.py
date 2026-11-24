"""Turn PDF pages and image files into PIL images, for the OCR engines."""

from __future__ import annotations

import io
from collections.abc import Iterator

from PIL import Image, ImageOps

from doctordoc.engines.base import EngineError

MAX_SIDE_PIXELS = 3500  # keeps memory small; a page at 200 DPI is about 1700 x 2300
MAX_FRAMES = 500


def iter_page_images(
    data: bytes, file_type: str, pages: list[int] | None, dpi: int
) -> Iterator[tuple[int, Image.Image, float]]:
    """Yield (page_number, RGB image, scale). scale = pixels per PDF point (1.0 for photos)."""
    if file_type == "pdf":
        yield from _render_pdf(data, pages, dpi)
    else:
        yield from _open_image(data, pages)


def _render_pdf(
    data: bytes, pages: list[int] | None, dpi: int
) -> Iterator[tuple[int, Image.Image, float]]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as err:
        raise EngineError(f"PDFium could not open the file: {err}") from err
    try:
        for number in pages if pages is not None else range(1, len(pdf) + 1):
            page = pdf[number - 1]
            width, height = page.get_size()
            scale = min(dpi / 72, MAX_SIDE_PIXELS / max(width, height, 1))
            image = page.render(scale=scale).to_pil().convert("RGB")  # pdfium applies /Rotate
            yield number, image, scale
    finally:
        pdf.close()


def _open_image(data: bytes, pages: list[int] | None) -> Iterator[tuple[int, Image.Image, float]]:
    try:
        image = Image.open(io.BytesIO(data))
        frames = min(getattr(image, "n_frames", 1), MAX_FRAMES)
    except Exception as err:
        raise EngineError(f"Pillow could not open the image: {err}") from err
    for index in range(frames):
        number = index + 1
        if pages is not None and number not in pages:
            continue
        image.seek(index)
        frame = ImageOps.exif_transpose(image.copy()).convert("RGB")  # honour camera rotation
        longest = max(frame.size)
        if longest > MAX_SIDE_PIXELS:
            factor = MAX_SIDE_PIXELS / longest
            frame = frame.resize((int(frame.width * factor), int(frame.height * factor)))
        yield number, frame, 1.0
