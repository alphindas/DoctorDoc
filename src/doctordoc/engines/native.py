"""Native engine: formats that already hold their text in a readable structure.

No models, no GPU. Plain text, Markdown, CSV/TSV, JSON, XML, HTML, DOCX, XLSX, PPTX, EML.
"""

from __future__ import annotations

import csv
import io
import json
import re
from email import policy
from email.parser import BytesParser
from typing import Any

from doctordoc.detect import decode_text
from doctordoc.engines._markup import html_to_blocks, markdown_to_blocks
from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import Block, BlockType, Page, PageKind, Table

MAX_SHEET_ROWS = 200_000  # protect memory on gigantic spreadsheets
NATIVE_TYPES = {"txt", "md", "csv", "tsv", "json", "xml", "html", "docx", "xlsx", "pptx", "eml"}


class NativeEngine(Engine):
    name = "native"
    license = "MIT (python-docx, openpyxl, python-pptx are MIT)"
    extra = ""

    def missing_reason(self) -> str | None:
        return self.module_missing("docx", "openpyxl", "pptx")

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        return file_type in NATIVE_TYPES and page_kind in (None, PageKind.NOT_PAGED)

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        handler = getattr(self, f"_parse_{file_type}", None)
        if handler is None:
            raise EngineError(f"native engine cannot read '{file_type}'")
        result: list[Page] = handler(data)
        return result

    # ------------------------------------------------------------ text-like formats

    def _page(self, blocks: list[Block], number: int = 1) -> Page:
        for block in blocks:
            block.engine = self.name
        return Page(number=number, kind=PageKind.NOT_PAGED, engine=self.name, blocks=blocks)

    @staticmethod
    def _text(data: bytes) -> str:
        return decode_text(data) or data.decode("latin-1")

    def _parse_txt(self, data: bytes) -> list[Page]:
        text = self._text(data).replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = [p.strip("\n") for p in re.split(r"\n\s*\n", text) if p.strip()]
        blocks = [Block(type=BlockType.PARAGRAPH, text=p) for p in paragraphs]
        return [self._page(blocks)]

    def _parse_md(self, data: bytes) -> list[Page]:
        return [self._page(markdown_to_blocks(self._text(data)))]

    def _parse_csv(self, data: bytes, delimiter: str | None = None) -> list[Page]:
        text = self._text(data)
        if delimiter is None:
            try:
                delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
        rows = [list(r) for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
        rows = [r for r in rows if any(cell.strip() for cell in r)]
        return [self._page([Block(type=BlockType.TABLE, table=Table(rows=rows))])]

    def _parse_tsv(self, data: bytes) -> list[Page]:
        return self._parse_csv(data, delimiter="\t")

    def _parse_json(self, data: bytes) -> list[Page]:
        text = self._text(data)
        try:
            value = json.loads(text)
        except ValueError:
            page = self._page([Block(type=BlockType.CODE, text=text)])
            page.warnings.append("Not valid JSON: showing the raw text.")
            return [page]
        table = _json_records_to_table(value)
        if table is not None:
            return [self._page([Block(type=BlockType.TABLE, table=table)])]
        pretty = json.dumps(value, indent=2, ensure_ascii=False)
        return [self._page([Block(type=BlockType.CODE, text=pretty)])]

    def _parse_xml(self, data: bytes) -> list[Page]:
        # We deliberately do NOT parse the XML (entity expansion attacks): show the raw text.
        return [self._page([Block(type=BlockType.CODE, text=self._text(data).strip())])]

    def _parse_html(self, data: bytes) -> list[Page]:
        return [self._page(html_to_blocks(self._text(data), engine=self.name))]

    # ------------------------------------------------------------ Office formats

    def _parse_docx(self, data: bytes) -> list[Page]:
        import docx
        from docx.table import Table as DocxTable
        from docx.text.paragraph import Paragraph

        try:
            document = docx.Document(io.BytesIO(data))
        except Exception as err:  # python-docx raises many kinds of errors on bad files
            raise EngineError(f"python-docx could not open the file: {err}") from err

        blocks: list[Block] = []
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                block = _docx_paragraph(Paragraph(child, document))
                if block:
                    blocks.append(block)
            elif child.tag.endswith("}tbl"):
                rows = [
                    [cell.text.strip() for cell in row.cells]
                    for row in DocxTable(child, document).rows
                ]
                if rows:
                    blocks.append(Block(type=BlockType.TABLE, table=Table(rows=rows)))
        return [self._page(blocks)]

    def _parse_xlsx(self, data: bytes) -> list[Page]:
        import openpyxl

        try:
            workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as err:
            raise EngineError(f"openpyxl could not open the file: {err}") from err
        pages: list[Page] = []
        try:
            for number, sheet in enumerate(workbook.worksheets, start=1):
                rows, truncated = _sheet_rows(sheet)
                blocks = [Block(type=BlockType.HEADING, text=sheet.title, level=2)]
                blocks.append(Block(type=BlockType.TABLE, table=Table(rows=rows)))
                page = self._page(blocks, number)
                if truncated:
                    page.warnings.append(f"Sheet '{sheet.title}' cut at {MAX_SHEET_ROWS} rows.")
                pages.append(page)
        finally:
            workbook.close()
        return pages

    def _parse_pptx(self, data: bytes) -> list[Page]:
        from pptx import Presentation

        try:
            presentation = Presentation(io.BytesIO(data))
        except Exception as err:
            raise EngineError(f"python-pptx could not open the file: {err}") from err
        pages = []
        for number, slide in enumerate(presentation.slides, start=1):
            blocks = _pptx_shapes(list(slide.shapes))
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    blocks.append(Block(type=BlockType.PARAGRAPH, text=f"Notes: {notes}"))
            pages.append(self._page(blocks, number))
        return pages

    # ------------------------------------------------------------ email

    def _parse_eml(self, data: bytes) -> list[Page]:
        message = BytesParser(policy=policy.default).parsebytes(data)
        blocks: list[Block] = []
        for header in ("From", "To", "Cc", "Date", "Subject"):
            value = message.get(header)
            if value:
                blocks.append(Block(type=BlockType.KEY_VALUE, text=f"{header}: {value}"))
        body = message.get_body(preferencelist=("plain", "html"))
        if body is not None:
            content = body.get_content()
            if body.get_content_subtype() == "html":
                blocks.extend(html_to_blocks(content, engine=self.name))
            else:
                blocks.extend(
                    Block(type=BlockType.PARAGRAPH, text=p.strip())
                    for p in re.split(r"\n\s*\n", content.replace("\r\n", "\n"))
                    if p.strip()
                )
        names = [part.get_filename() or "(unnamed)" for part in message.iter_attachments()]
        if names:
            blocks.append(Block(type=BlockType.KEY_VALUE, text="Attachments: " + ", ".join(names)))
        return [self._page(blocks)]


# ---------------------------------------------------------------------------- helpers


def _json_records_to_table(value: Any) -> Table | None:
    """A JSON list of flat objects becomes a table. Anything else returns None."""
    if not (isinstance(value, list) and value and all(isinstance(v, dict) for v in value)):
        return None
    columns: list[str] = []
    for record in value:
        columns.extend(k for k in record if k not in columns)
    if len(columns) > 60:
        return None
    rows = [columns]
    for record in value:
        rows.append([_json_cell(record.get(col)) for col in columns])
    return Table(rows=rows)


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _docx_paragraph(paragraph) -> Block | None:
    text = paragraph.text.strip()
    if not text:
        return None
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    if style == "Title":
        return Block(type=BlockType.HEADING, text=text, level=1)
    match = re.match(r"Heading (\d)", style)
    if match:
        return Block(type=BlockType.HEADING, text=text, level=min(int(match.group(1)), 6))
    if style.startswith("List"):
        return Block(type=BlockType.LIST_ITEM, text=text)
    return Block(type=BlockType.PARAGRAPH, text=text)


def _sheet_rows(sheet) -> tuple[list[list[str]], bool]:
    """All non-empty rows of a sheet as strings, with empty trailing columns removed."""
    rows: list[list[str]] = []
    truncated = False
    for index, row in enumerate(sheet.iter_rows(values_only=True)):
        if index >= MAX_SHEET_ROWS:
            truncated = True
            break
        cells = ["" if v is None else _format_cell(v) for v in row]
        if any(cells):
            rows.append(cells)
    width = max((len(r) for r in rows), default=0)
    while width and all(not (r[width - 1] if len(r) >= width else "") for r in rows):
        width -= 1
    return [r[:width] + [""] * (width - len(r)) for r in rows], truncated


def _format_cell(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _pptx_shapes(shapes) -> list[Block]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

    blocks: list[Block] = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            blocks.extend(_pptx_shapes(list(shape.shapes)))
        elif getattr(shape, "has_table", False) and shape.has_table:
            rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
            blocks.append(Block(type=BlockType.TABLE, table=Table(rows=rows)))
        elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            alt = shape._element.xpath("./p:nvPicPr/p:cNvPr/@descr")
            blocks.append(Block(type=BlockType.IMAGE, text=alt[0] if alt else ""))
        elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            is_title = shape.is_placeholder and shape.placeholder_format.type in (
                PP_PLACEHOLDER.TITLE,
                PP_PLACEHOLDER.CENTER_TITLE,
            )
            for paragraph in shape.text_frame.paragraphs:
                text = "".join(run.text for run in paragraph.runs).strip() or paragraph.text.strip()
                if not text:
                    continue
                if is_title:
                    blocks.append(Block(type=BlockType.HEADING, text=text, level=1))
                elif (
                    shape.is_placeholder
                    and paragraph.level == 0
                    and len(shape.text_frame.paragraphs) > 1
                ):
                    blocks.append(Block(type=BlockType.LIST_ITEM, text=text))
                else:
                    blocks.append(Block(type=BlockType.PARAGRAPH, text=text))
    return blocks
