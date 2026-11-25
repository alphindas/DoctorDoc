"""pdf_text: read the text layer of a PDF page with pypdfium2. Fast, CPU only, no models.

It groups text pieces into lines and paragraphs, and calls a line a heading when its font
is clearly bigger than the body text. It does NOT find tables (Docling does that).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import BBox, Block, BlockType, Page, PageKind

_HEADING_RATIO = 1.25  # a line is a heading if its font is this much bigger than body text
_MAX_HEADING_CHARS = 150


@dataclass
class _Line:
    text: str
    x0: float
    y0: float  # bottom (PDF coordinates grow upward)
    x1: float
    y1: float  # top
    size: float


class PdfTextEngine(Engine):
    name = "pdf_text"
    uses_text_layer = True
    license = "Apache-2.0 / BSD-3 (pypdfium2 + PDFium)"
    extra = ""

    def missing_reason(self) -> str | None:
        return self.module_missing("pypdfium2")

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        return file_type == "pdf"

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as err:
            raise EngineError(f"PDFium could not open the file: {err}") from err
        try:
            numbers = pages if pages is not None else list(range(1, len(pdf) + 1))
            return [self._parse_page(pdf[n - 1], n) for n in numbers]
        finally:
            pdf.close()

    def _parse_page(self, page, number: int) -> Page:
        textpage = page.get_textpage()
        try:
            lines = _collect_lines(page, textpage)
            plain = textpage.get_text_range()
        finally:
            textpage.close()

        # If the line builder lost text (odd PDFs), fall back to the raw text of the page.
        if _count(lines) < 0.9 * _count_text(plain):
            blocks = _blocks_from_plain_text(plain)
        else:
            blocks = _blocks_from_lines(lines, page.get_size()[1])
        for block in blocks:
            block.engine = self.name
        return Page(number=number, engine=self.name, blocks=blocks)


# ---------------------------------------------------------------------------- helpers


def _count(lines: list[_Line]) -> int:
    return sum(len("".join(line.text.split())) for line in lines)


def _count_text(text: str) -> int:
    return len("".join(text.split()))


def _collect_lines(page, textpage) -> list[_Line]:
    """Merge the text objects of a page, in content-stream order, into lines."""
    import pypdfium2.raw as pdfium_raw

    lines: list[_Line] = []
    for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_TEXT], textpage=textpage):
        text = obj.extract().replace("\r", "").replace("\n", " ")
        if not text.strip():
            continue
        x0, y0, x1, y1 = obj.get_bounds()
        size = obj.get_font_size() or (y1 - y0)
        piece = _Line(text=text, x0=x0, y0=y0, x1=x1, y1=y1, size=size)
        if lines and _same_line(lines[-1], piece):
            _append_to_line(lines[-1], piece)
        else:
            lines.append(piece)
    for line in lines:
        line.text = " ".join(line.text.split())
    return lines


def _same_line(previous: _Line, piece: _Line) -> bool:
    height = max(previous.y1 - previous.y0, piece.y1 - piece.y0, 1.0)
    centre_gap = abs((previous.y0 + previous.y1) / 2 - (piece.y0 + piece.y1) / 2)
    moves_right = piece.x0 >= previous.x0
    return centre_gap < 0.4 * height and moves_right


def _append_to_line(line: _Line, piece: _Line) -> None:
    gap = piece.x0 - line.x1
    glue = " " if gap > 0.15 * max(piece.size, 1.0) else ""
    line.text = line.text + glue + piece.text
    line.x1 = max(line.x1, piece.x1)
    line.y0 = min(line.y0, piece.y0)
    line.y1 = max(line.y1, piece.y1)
    line.size = max(line.size, piece.size)


def _blocks_from_lines(lines: list[_Line], page_height: float) -> list[Block]:
    if not lines:
        return []
    body_size = _body_font_size(lines)
    blocks: list[Block] = []
    current: list[_Line] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(line.text for line in current)
        block_type, level = BlockType.PARAGRAPH, None
        size = max(line.size for line in current)
        if size >= _HEADING_RATIO * body_size and len(text) <= _MAX_HEADING_CHARS:
            block_type = BlockType.HEADING
            level = 1 if size >= 1.8 * body_size else 2 if size >= 1.4 * body_size else 3
        elif text[:2] in ("• ", "- ", "* ") or text[:2] == "– ":
            block_type, text = BlockType.LIST_ITEM, text[2:].strip()
        bbox = BBox(
            x0=min(line.x0 for line in current),
            y0=page_height - max(line.y1 for line in current),
            x1=max(line.x1 for line in current),
            y1=page_height - min(line.y0 for line in current),
        )
        blocks.append(Block(type=block_type, text=text, level=level, bbox=bbox))
        current.clear()

    for line in lines:
        if current and not _continues_paragraph(current[-1], line):
            flush()
        current.append(line)
        if line.text[:2] in ("• ", "- ", "* ", "– "):
            flush()  # every bullet is its own block
    flush()
    return blocks


def _continues_paragraph(previous: _Line, line: _Line) -> bool:
    gap = previous.y0 - line.y1  # vertical space between the two lines
    same_size = abs(previous.size - line.size) <= 0.1 * max(previous.size, 1.0)
    close = gap < 0.6 * max(previous.size, 1.0)
    return same_size and close and line.y1 < previous.y1


def _body_font_size(lines: list[_Line]) -> float:
    """The most common font size, weighted by how many characters use it."""
    weights: Counter[float] = Counter()
    for line in lines:
        weights[round(line.size, 1)] += len(line.text)
    return weights.most_common(1)[0][0] or 1.0


def _blocks_from_plain_text(text: str) -> list[Block]:
    paragraphs = [" ".join(p.split()) for p in text.replace("\r", "").split("\n\n")]
    if len(paragraphs) == 1:  # no blank lines: use single line breaks
        paragraphs = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    return [Block(type=BlockType.PARAGRAPH, text=p) for p in paragraphs if p]
