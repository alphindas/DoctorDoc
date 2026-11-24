"""Turn a flat list of OCR text boxes into Blocks (one per text line, prose lines merged)."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from doctordoc.models import BBox, Block, BlockType


@dataclass
class OcrBox:
    """One piece of text found by an OCR engine. Coordinates are image pixels, y grows down."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None = None


def boxes_to_blocks(boxes: list[OcrBox], engine: str, scale: float = 1.0) -> list[Block]:
    """Group boxes into reading-order lines, then merge wrapped prose lines.

    `scale` is image pixels per PDF point, so bounding boxes are returned in PDF points.
    """
    boxes = [b for b in boxes if b.text.strip()]
    if not boxes:
        return []
    rows = _group_rows(boxes)
    page_right = max(b.x1 for b in boxes)
    merged = _merge_prose_rows(rows, page_right)
    return [_row_to_block(row, engine, scale) for row in merged]


def _group_rows(boxes: list[OcrBox]) -> list[list[OcrBox]]:
    typical_height = median(b.y1 - b.y0 for b in boxes) or 1.0
    rows: list[list[OcrBox]] = []
    for box in sorted(boxes, key=lambda b: (b.y0 + b.y1) / 2):
        centre = (box.y0 + box.y1) / 2
        if rows and abs(centre - _row_centre(rows[-1])) < 0.5 * typical_height:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [sorted(row, key=lambda b: b.x0) for row in rows]


def _row_centre(row: list[OcrBox]) -> float:
    return sum((b.y0 + b.y1) / 2 for b in row) / len(row)


def _merge_prose_rows(rows: list[list[OcrBox]], page_right: float) -> list[list[OcrBox]]:
    """A row that runs to the right edge, followed by a row starting at the same left edge
    with a small gap, is a wrapped paragraph. Everything else stays one line per block
    (that keeps forms and tables readable)."""
    merged: list[list[OcrBox]] = []
    for row in rows:
        if merged and _is_wrapped(merged[-1], row, page_right):
            merged[-1] = merged[-1] + row
        else:
            merged.append(row)
    return merged


def _is_wrapped(previous: list[OcrBox], row: list[OcrBox], page_right: float) -> bool:
    if len(previous) != 1 or len(row) != 1:
        return False
    a, b = previous[-1], row[0]
    height = max(a.y1 - a.y0, 1.0)
    runs_to_edge = a.x1 >= 0.85 * page_right
    same_left = abs(a.x0 - b.x0) < 1.5 * height
    close = 0 <= b.y0 - a.y1 < 0.8 * height
    same_size = abs((b.y1 - b.y0) - height) < 0.25 * height  # a big title is not prose
    long_enough = len(a.text.strip()) >= 50 and not a.text.rstrip().endswith(":")
    return runs_to_edge and same_left and close and same_size and long_enough


def _row_to_block(row: list[OcrBox], engine: str, scale: float) -> Block:
    text = " ".join(b.text.strip() for b in row)
    scores = [b.confidence for b in row if b.confidence is not None]
    bbox = BBox(
        x0=min(b.x0 for b in row) / scale,
        y0=min(b.y0 for b in row) / scale,
        x1=max(b.x1 for b in row) / scale,
        y1=max(b.y1 for b in row) / scale,
    )
    return Block(
        type=BlockType.PARAGRAPH,
        text=text,
        bbox=bbox,
        confidence=round(sum(scores) / len(scores), 3) if scores else None,
        engine=engine,
    )
