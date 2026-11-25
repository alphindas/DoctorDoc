"""Find which way up a scanned page is, by reading it at 0, 90, 180 and 270 degrees.

The idea is to keep the angle where the OCR engine is most confident. Plain confidence does
not work, for two reasons measured on the benchmark pages:

* Both RapidOCR and Tesseract quietly straighten a line of text that runs top to bottom. So
  a sideways page reads with high confidence too, but the lines come out in the wrong order.
  What gives it away is the shape: on an upright page, text boxes are wider than they are tall.
  So we only count confidence for horizontal boxes.
* RapidOCR also flips single upside-down lines. That has to be switched off while trying
  angles (see ocr_rapid.py), or an upside-down page looks as good as an upright one.

Most pages are already upright, so a page that scores `early_stop` or more as it is gets
accepted after one read. On the 16 synthetic pages used to design this, the right angle scored
at least 0.84 (RapidOCR) and 0.65 (Tesseract), and a wrong angle at most 0.62 and 0.42. The
default cutoff, 0.75, was picked from those numbers.

Made for left-to-right scripts. Pages written top to bottom (some Chinese and Japanese) would
be turned wrongly: set `fix_rotation: false` for those.
"""

from __future__ import annotations

from collections.abc import Callable

from PIL import Image

from doctordoc.engines._ocr_layout import OcrBox

# How much better (on a 0..1 scale) another angle must score before we turn the page.
# On the benchmark pages the right angle led by 0.28 or more, so this only stops turns that
# would be decided by noise, e.g. on a page with two or three words.
MIN_GAIN = 0.05

_CLOCKWISE = {
    90: Image.Transpose.ROTATE_270,  # PIL names its turns counter-clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def upright_score(boxes: list[OcrBox]) -> float:
    """Average confidence per character, where text in tall (not horizontal) boxes counts 0."""
    total = sum(len(b.text) for b in boxes)
    if not total:
        return 0.0
    horizontal = sum(
        (b.confidence or 0.0) * len(b.text) for b in boxes if (b.x1 - b.x0) > (b.y1 - b.y0)
    )
    return horizontal / total


def read_upright(
    image: Image.Image,
    read: Callable[[Image.Image], list[OcrBox]],
    early_stop: float | None = None,
) -> tuple[int, list[OcrBox]]:
    """Read the picture as it is and, unless that already scores `early_stop`, at the other
    three angles too. Returns (degrees turned clockwise, boxes).

    The boxes are positions on the turned (upright) picture, not on the original one.
    """
    reads = {0: read(image)}
    if early_stop is not None and upright_score(reads[0]) >= early_stop:
        return 0, reads[0]
    for angle, turn in _CLOCKWISE.items():
        reads[angle] = read(image.transpose(turn))
    scores = {angle: upright_score(boxes) for angle, boxes in reads.items()}
    best = max(scores, key=lambda angle: scores[angle])
    if scores[best] < scores[0] + MIN_GAIN:
        best = 0
    return best, reads[best]
