"""Scripted engines for testing the router without any real library or model."""

from __future__ import annotations

import time

from doctordoc.engines.base import Engine
from doctordoc.models import Block, BlockType, Page, PageKind

GOOD = "The drilling crew tripped out of the hole and changed the bit at noon."
JUNK = "(cid:12)(cid:45)(cid:78) ��� xq#$k%z^&"


class FakeEngine(Engine):
    license = "test"
    extra = ""

    def __init__(
        self,
        name: str,
        text: str | dict[int, str] = GOOD,
        *,
        available: bool = True,
        error: Exception | None = None,
        delay: float = 0.0,
        heavy: bool = False,
        kinds=None,
        uses_text_layer: bool = False,
    ) -> None:
        super().__init__()
        self.name = name  # type: ignore[misc]
        self.heavy = heavy  # type: ignore[misc]
        # like the real engines: pdf_text and docling read the PDF's text layer
        self.uses_text_layer = uses_text_layer or name in ("pdf_text", "docling")  # type: ignore[misc]
        self.text, self.available, self.error, self.delay = text, available, error, delay
        self.kinds = kinds
        self.calls: list[list[int] | None] = []
        self.released = 0

    def missing_reason(self) -> str | None:
        return None if self.available else "library not installed"

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        return self.kinds is None or page_kind in self.kinds

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        self.calls.append(pages)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        numbers = pages or [1]
        return [
            Page(number=n, blocks=[Block(type=BlockType.PARAGRAPH, text=self._text_for(n))])
            for n in numbers
        ]

    def _text_for(self, number: int) -> str:
        return self.text.get(number, "") if isinstance(self.text, dict) else self.text

    def release(self) -> None:
        self.released += 1
