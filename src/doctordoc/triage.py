"""Look at each PDF page (without OCR) and decide what kind of page it is.

The answer decides which engines the router tries. It is cheap: it only reads the text
layer and the list of objects on the page, it never draws the page.
"""

from __future__ import annotations

from pydantic import BaseModel

from doctordoc.config import Config
from doctordoc.models import PageKind
from doctordoc.quality import text_quality

# A page with no text and no pictures but at least this many drawn shapes is treated as
# a vector scan (text converted to outlines), not as blank.
_MIN_PATHS_FOR_DRAWING = 5
_MIN_IMAGE_COVERAGE = 0.05


class PageTriage(BaseModel):
    number: int  # 1-based
    kind: PageKind
    chars: int  # non-space characters in the text layer
    image_coverage: float  # share of the page covered by pictures, 0..1
    quality: float | None  # text_quality of the text layer (None if there is no text)
    rotation: int  # page rotation in degrees (0, 90, 180, 270)
    width: float
    height: float


def triage_pdf(data: bytes, config: Config | None = None) -> list[PageTriage]:
    """Classify every page of a PDF. Raises pypdfium2.PdfiumError for unreadable PDFs."""
    import pypdfium2 as pdfium

    config = config or Config()
    pdf = pdfium.PdfDocument(data)
    try:
        return [triage_page(pdf[i], i + 1, config) for i in range(len(pdf))]
    finally:
        pdf.close()


def triage_page(page, number: int, config: Config) -> PageTriage:
    """Classify one pypdfium2 page."""
    import pypdfium2.raw as pdfium_raw

    width, height = page.get_size()
    text = _page_text(page)
    chars = sum(1 for c in text if not c.isspace())
    quality = text_quality(text) if chars else None

    images = list(page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE]))
    coverage = _image_coverage(images, width, height)
    paths = sum(1 for _ in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_PATH]))
    for obj in images:
        obj.close()

    kind = classify(chars, quality, coverage, paths, config)
    return PageTriage(
        number=number,
        kind=kind,
        chars=chars,
        image_coverage=round(coverage, 3),
        quality=quality,
        rotation=page.get_rotation(),
        width=width,
        height=height,
    )


def classify(
    chars: int, quality: float | None, image_coverage: float, path_count: int, config: Config
) -> PageKind:
    """The decision rules, separated from the PDF reading so they are easy to test."""
    if chars < config.min_page_chars:
        if image_coverage >= _MIN_IMAGE_COVERAGE or path_count >= _MIN_PATHS_FOR_DRAWING:
            return PageKind.SCANNED  # looks like a picture of a page: needs OCR
        return PageKind.BLANK
    if quality is not None and quality < config.garbled_threshold:
        return PageKind.GARBLED  # a text layer exists but it is junk
    if image_coverage >= config.mixed_image_coverage:
        return PageKind.MIXED  # good text plus big pictures that may hold more text
    return PageKind.DIGITAL


def _page_text(page) -> str:
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range()
    finally:
        textpage.close()


def _image_coverage(images, page_width: float, page_height: float) -> float:
    """Share of the page area covered by the union of all pictures."""
    if page_width <= 0 or page_height <= 0:
        return 0.0
    rects = []
    for obj in images:
        left, bottom, right, top = obj.get_bounds()
        # clip to the page, ignore pictures that are completely outside
        x0, x1 = max(0.0, min(left, right)), min(page_width, max(left, right))
        y0, y1 = max(0.0, min(bottom, top)), min(page_height, max(bottom, top))
        if x1 > x0 and y1 > y0:
            rects.append((x0, y0, x1, y1))
    return min(1.0, _union_area(rects) / (page_width * page_height))


def _union_area(rects: list[tuple[float, float, float, float]]) -> float:
    """Area covered by a set of rectangles, counting overlaps once (sweep over x)."""
    if not rects:
        return 0.0
    xs = sorted({x for r in rects for x in (r[0], r[2])})
    total = 0.0
    for left, right in zip(xs, xs[1:], strict=False):
        spans = sorted((r[1], r[3]) for r in rects if r[0] <= left and r[2] >= right)
        covered, current_end = 0.0, float("-inf")
        for y0, y1 in spans:
            y0 = max(y0, current_end)
            if y1 > y0:
                covered += y1 - y0
                current_end = y1
        total += covered * (right - left)
    return total
