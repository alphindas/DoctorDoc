"""Docling (IBM, MIT): layout analysis + table structure for digital PDFs, DOCX, PPTX and HTML.

Docling's own OCR is switched OFF: scanned pages are the router's job (it picks the OCR
engine). The layout and table models (~500 MB) are downloaded from HuggingFace on first use.
"""

from __future__ import annotations

import gc
import io
from collections import defaultdict

from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import BBox, Block, BlockType, Page, PageKind, Table

DOCLING_TYPES = {"pdf", "docx", "pptx", "html"}


class DoclingEngine(Engine):
    name = "docling"
    uses_text_layer = True
    license = "MIT (Docling)"
    extra = "docling"
    heavy = True

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._converter = None

    def missing_reason(self) -> str | None:
        return self.module_missing("docling")

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        if file_type == "pdf":
            return page_kind in (PageKind.DIGITAL, PageKind.MIXED)
        return file_type in DOCLING_TYPES and page_kind in (None, PageKind.NOT_PAGED)

    # ------------------------------------------------------------------ loading

    def _get_converter(self):
        """Create the converter once. Models load on the first conversion."""
        if self._converter is None:
            _quiet_model_loading()
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                AcceleratorDevice,
                AcceleratorOptions,
                PdfPipelineOptions,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(
                do_ocr=False,  # OCR is chosen by our router, not by Docling
                do_table_structure=True,
                accelerator_options=AcceleratorOptions(
                    device=AcceleratorDevice.AUTO if self.config.use_gpu else AcceleratorDevice.CPU
                ),
            )
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter

    def release(self) -> None:
        self._converter = None
        gc.collect()
        try:  # give GPU memory back if torch is around
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    # ------------------------------------------------------------------ parsing

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        from docling.datamodel.base_models import DocumentStream

        converter = self._get_converter()
        stream = DocumentStream(name=f"input.{file_type}", stream=io.BytesIO(data))
        try:
            if file_type == "pdf" and pages:
                # Docling takes one range, so we convert min..max and keep the pages we need.
                result = converter.convert(stream, page_range=(min(pages), max(pages)))
            else:
                result = converter.convert(stream)
        except Exception as err:  # Docling raises many kinds of errors
            raise EngineError(f"Docling could not convert the file: {err}") from err

        grouped = _blocks_by_page(result.document, self.name)
        wanted = pages if pages is not None else sorted(grouped) or [1]
        return [Page(number=n, engine=self.name, blocks=grouped.get(n, [])) for n in wanted]


# ---------------------------------------------------------------------------- mapping


def _blocks_by_page(doc, engine: str) -> dict[int, list[Block]]:
    """Walk the DoclingDocument in reading order and sort each item into its page."""
    from docling_core.types.doc import DocItemLabel
    from docling_core.types.doc.document import (
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TextItem,
    )

    grouped: dict[int, list[Block]] = defaultdict(list)
    for item, _depth in doc.iterate_items():
        page_no = item.prov[0].page_no if getattr(item, "prov", None) else 1
        bbox = _bbox(doc, item)
        block: Block | None = None
        if isinstance(item, TableItem):
            rows = [[cell.text.strip() for cell in row] for row in item.data.grid]
            block = Block(type=BlockType.TABLE, table=Table(rows=rows))
        elif isinstance(item, PictureItem):
            caption = item.caption_text(doc) if hasattr(item, "caption_text") else ""
            block = Block(type=BlockType.IMAGE, text=caption or "")
        elif isinstance(item, TextItem):
            text = item.text.strip()
            if not text:
                continue
            label = item.label
            if label == DocItemLabel.TITLE:
                block = Block(type=BlockType.HEADING, text=text, level=1)
            elif isinstance(item, SectionHeaderItem):
                block = Block(type=BlockType.HEADING, text=text, level=min(item.level + 1, 6))
            elif label == DocItemLabel.LIST_ITEM:
                block = Block(type=BlockType.LIST_ITEM, text=text)
            elif label == DocItemLabel.CODE:
                block = Block(type=BlockType.CODE, text=text)
            else:
                block = Block(type=BlockType.PARAGRAPH, text=text)
        if block is not None:
            block.bbox = bbox
            block.engine = engine
            grouped[page_no].append(block)
    return grouped


def _bbox(doc, item) -> BBox | None:
    """Docling boxes come with the origin at the bottom-left. We store top-left, in points."""
    if not getattr(item, "prov", None):
        return None
    prov = item.prov[0]
    page = doc.pages.get(prov.page_no)
    if page is None:
        return None
    box = prov.bbox.to_top_left_origin(page_height=page.size.height)
    return BBox(x0=box.l, y0=box.t, x1=box.r, y1=box.b)


def _quiet_model_loading() -> None:
    """Hide the 'Loading weights' progress bars: they would pollute the CLI output."""
    try:
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()
    except ImportError:
        pass
