"""The common output format. Every engine, whatever it is, returns these objects."""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PageKind(str, Enum):
    """What a page looks like inside. This decides which engines we try."""

    DIGITAL = "digital"  # has a good text layer
    SCANNED = "scanned"  # just a picture, needs OCR
    MIXED = "mixed"  # text layer plus large pictures
    GARBLED = "garbled"  # has a text layer, but it is junk
    BLANK = "blank"  # nothing on it
    NOT_PAGED = "not_paged"  # formats without pages (docx, csv, json ...)


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"
    KEY_VALUE = "key_value"


class BBox(BaseModel):
    """A box on the page. Units depend on the engine (PDF points or image pixels)."""

    x0: float
    y0: float
    x1: float
    y1: float


class Table(BaseModel):
    rows: list[list[str]] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """Plain text version: one line per row, cells separated by ' | '."""
        return "\n".join(" | ".join(row) for row in self.rows)


class Block(BaseModel):
    type: BlockType
    text: str = ""
    level: int | None = None  # heading level (1-6)
    table: Table | None = None
    bbox: BBox | None = None
    confidence: float | None = None  # engine's own confidence (OCR), 0..1
    engine: str | None = None

    @property
    def plain_text(self) -> str:
        """The text of this block. For tables, the cells joined as plain text."""
        if self.type == BlockType.TABLE and self.table is not None:
            return self.table.text
        return self.text


class Page(BaseModel):
    number: int  # 1-based
    kind: PageKind = PageKind.NOT_PAGED
    engine: str | None = None  # which engine produced the blocks we kept
    blocks: list[Block] = Field(default_factory=list)
    quality: float | None = None  # text_quality of the kept output, 0..1
    # degrees (clockwise) the picture was turned before OCR; block boxes are on the turned picture
    turned: int = 0
    warnings: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(t for t in (b.plain_text for b in self.blocks) if t)


class Decision(BaseModel):
    """One line of the router's log: what it tried, and what happened."""

    scope: str  # "file" or "page 3" or "pages 1-4"
    engine: str
    outcome: str  # accepted | rejected | skipped | failed | timeout
    reason: str = ""
    quality: float | None = None
    seconds: float = 0.0


class Document(BaseModel):
    source: str
    file_type: str = "unknown"
    mime: str = "application/octet-stream"
    pages: list[Page] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    children: list[Document] = Field(default_factory=list)  # files inside a zip / email
    decisions: list[Decision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    @property
    def text(self) -> str:
        """Plain text of this document's own pages (not its children)."""
        return "\n\n".join(t for t in (p.text for p in self.pages) if t)

    def iter_all(self) -> Iterator[Document]:
        """This document, then every nested child, depth first."""
        yield self
        for child in self.children:
            yield from child.iter_all()

    def to_markdown(self) -> str:
        from doctordoc.render import to_markdown

        return to_markdown(self)

    def to_json(self, indent: int | None = 2) -> str:
        from doctordoc.render import to_json

        return to_json(self, indent=indent)


class EngineRun(BaseModel):
    """One engine run ALONE on a whole file (used to compare engines side by side)."""

    engine: str
    status: str  # ok | empty | failed | timeout | skipped
    reason: str = ""
    pages: list[Page] = Field(default_factory=list)
    quality: float | None = None  # text_quality of all the text it produced, 0..1
    chars: int = 0  # non-space characters produced
    seconds: float = 0.0
