"""Small converters from Markdown and HTML text into Blocks. Shared by several engines."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from doctordoc.models import Block, BlockType, Table

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"^!\[(.*?)\]\(.*?\)\s*$")
_HTML_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")


def markdown_to_blocks(text: str, engine: str | None = None) -> list[Block]:
    """Turn Markdown into blocks: headings, lists, tables, code, images, paragraphs."""
    blocks: list[Block] = []
    paragraph: list[str] = []
    lines = text.splitlines()

    def flush() -> None:
        if paragraph:
            blocks.append(
                Block(type=BlockType.PARAGRAPH, text=" ".join(paragraph).strip(), engine=engine)
            )
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith(("```", "~~~")):
            flush()
            fence = line.strip()[:3]
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            blocks.append(Block(type=BlockType.CODE, text="\n".join(code), engine=engine))
        elif (m := _HEADING.match(line)) and m.group(2):
            flush()
            blocks.append(
                Block(type=BlockType.HEADING, text=m.group(2), level=len(m.group(1)), engine=engine)
            )
        elif "|" in line and i + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[i + 1]):
            flush()
            rows = [_split_row(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(Block(type=BlockType.TABLE, table=Table(rows=rows), engine=engine))
            continue
        elif m := _LIST_ITEM.match(line):
            flush()
            blocks.append(Block(type=BlockType.LIST_ITEM, text=m.group(1).strip(), engine=engine))
        elif m := _IMAGE.match(line.strip()):
            flush()
            blocks.append(Block(type=BlockType.IMAGE, text=m.group(1), engine=engine))
        elif _HTML_COMMENT.match(line):
            flush()
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        i += 1
    flush()
    return blocks


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip().replace("<br>", "\n") for c in cells]


# ---------------------------------------------------------------------------- HTML

_SKIP_TAGS = {"script", "style", "noscript", "template", "head", "svg"}
_BREAK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "nav",
    "aside",
    "main",
    "blockquote",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "figure",
    "figcaption",
    "form",
    "fieldset",
    "hr",
}


class _HtmlToBlocks(HTMLParser):
    """Walks the HTML once and builds blocks. Nested tables are flattened into cell text."""

    def __init__(self, engine: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.engine = engine
        self.blocks: list[Block] = []
        self._buf: list[str] = []
        self._skip = 0
        self._heading: int | None = None
        self._in_li = 0
        self._in_pre = 0
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    # -- helpers
    def _emit(self, block_type: BlockType, text: str, level: int | None = None) -> None:
        if text:
            self.blocks.append(Block(type=block_type, text=text, level=level, engine=self.engine))

    def _flush(self) -> None:
        raw = "".join(self._buf)
        self._buf = []
        if self._in_pre:
            self._emit(BlockType.CODE, raw.strip("\n"))
            return
        text = " ".join(raw.split())
        if self._heading:
            self._emit(BlockType.HEADING, text, self._heading)
        elif self._in_li:
            self._emit(BlockType.LIST_ITEM, text)
        else:
            self._emit(BlockType.PARAGRAPH, text)

    # -- parser callbacks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if self._skip:
            return
        if self._table_depth and tag in ("td", "th") and self._table_depth == 1:
            self._cell = []
        elif tag == "table":
            if self._table_depth == 0:
                self._flush()
                self._rows = []
            self._table_depth += 1
        elif tag == "tr" and self._table_depth == 1:
            self._row = []
        elif self._table_depth:
            return
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self._heading = int(tag[1])
        elif tag == "li":
            self._flush()
            self._in_li += 1
        elif tag == "pre":
            self._flush()
            self._in_pre += 1
        elif tag in _BREAK_TAGS:
            self._flush()
        elif tag == "br":
            self._buf.append("\n" if self._in_pre else " ")
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            self._flush()
            self._emit(BlockType.IMAGE, alt)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ("td", "th") and self._table_depth == 1 and self._cell is not None:
            if self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._table_depth == 1 and self._row is not None:
            if any(self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0 and self._rows:
                self.blocks.append(
                    Block(type=BlockType.TABLE, table=Table(rows=self._rows), engine=self.engine)
                )
                self._rows = []
        elif self._table_depth:
            return
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self._heading = None
        elif tag == "li":
            self._flush()
            self._in_li = max(0, self._in_li - 1)
        elif tag == "pre":
            self._flush()
            self._in_pre = max(0, self._in_pre - 1)
        elif tag in _BREAK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._table_depth == 0:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def html_to_blocks(html: str, engine: str | None = None) -> list[Block]:
    parser = _HtmlToBlocks(engine)
    parser.feed(html)
    parser.close()
    return parser.blocks
