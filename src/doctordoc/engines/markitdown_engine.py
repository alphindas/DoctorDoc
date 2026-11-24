"""MarkItDown (Microsoft, MIT): a broad fallback that converts Office/misc formats to Markdown."""

from __future__ import annotations

import io
import re

from doctordoc.engines._markup import markdown_to_blocks
from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import Page, PageKind

SUPPORTED = {"docx", "xlsx", "pptx", "html", "csv", "json", "xml", "txt", "md"}
_SLIDE_MARKER = re.compile(r"<!--\s*Slide number:\s*(\d+)\s*-->")
_SHEET_HEADING = re.compile(r"^## (.+)$", re.MULTILINE)


class MarkItDownEngine(Engine):
    name = "markitdown"
    license = "MIT (markitdown)"
    extra = "markitdown"

    def missing_reason(self) -> str | None:
        return self.module_missing("markitdown")

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        return file_type in SUPPORTED and page_kind in (None, PageKind.NOT_PAGED)

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        from markitdown import MarkItDown

        try:
            result = MarkItDown(enable_plugins=False).convert_stream(
                io.BytesIO(data), file_extension=f".{file_type}"
            )
        except Exception as err:  # MarkItDown raises many different exception types
            raise EngineError(f"MarkItDown failed: {err}") from err
        markdown = result.text_content or ""
        if file_type == "pptx":
            return self._split(markdown, _SLIDE_MARKER)
        if file_type == "xlsx":
            return self._split(markdown, _SHEET_HEADING, keep_marker=True)
        return [Page(number=1, engine=self.name, blocks=markdown_to_blocks(markdown, self.name))]

    def _split(
        self, markdown: str, marker: re.Pattern[str], keep_marker: bool = False
    ) -> list[Page]:
        """Cut the Markdown into one page per slide / sheet."""
        starts = [m.start() for m in marker.finditer(markdown)]
        if not starts:
            return [
                Page(number=1, engine=self.name, blocks=markdown_to_blocks(markdown, self.name))
            ]
        pieces = [markdown[a:b] for a, b in zip(starts, starts[1:] + [len(markdown)], strict=True)]
        return [
            Page(number=i, engine=self.name, blocks=markdown_to_blocks(piece, self.name))
            for i, piece in enumerate(pieces, start=1)
        ]
