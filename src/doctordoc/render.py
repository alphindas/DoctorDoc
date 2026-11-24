"""Turn a Document into Markdown or JSON."""

from __future__ import annotations

from doctordoc.models import Block, BlockType, Document, Page, PageKind, Table


def to_json(doc: Document, indent: int | None = 2) -> str:
    return doc.model_dump_json(indent=indent)


def to_markdown(doc: Document) -> str:
    """Markdown for the document, then each child file under its own heading."""
    parts: list[str] = []
    if doc.error:
        parts.append(f"> **Error:** {doc.error}")
    show_markers = len(doc.pages) > 1 or any(p.kind != PageKind.NOT_PAGED for p in doc.pages)
    for page in doc.pages:
        parts.append(_render_page(page, show_markers))
    for child in doc.children:
        parts.append(f"---\n\n## File: {child.source}\n\n{to_markdown(child)}".rstrip())
    return "\n\n".join(p for p in parts if p).strip() + "\n"


def _render_page(page: Page, show_marker: bool) -> str:
    lines: list[str] = []
    if show_marker:
        lines.append(f"<!-- page {page.number} -->")
    lines.extend(f"<!-- warning: {w} -->" for w in page.warnings)
    body = render_blocks(page.blocks)
    if body:
        lines.append(body)
    return "\n\n".join(lines)


def render_blocks(blocks: list[Block]) -> str:
    """Blocks joined by blank lines, except list items which stay together."""
    out: list[str] = []
    previous: BlockType | None = None
    for block in blocks:
        text = _render_block(block)
        if not text:
            continue
        separator = "\n" if previous == BlockType.LIST_ITEM == block.type else "\n\n"
        out.append(separator + text if out else text)
        previous = block.type
    return "".join(out)


def _render_block(block: Block) -> str:
    kind = block.type
    if kind == BlockType.HEADING:
        return f"{'#' * (block.level or 2)} {block.text}"
    if kind == BlockType.LIST_ITEM:
        return f"- {block.text}"
    if kind == BlockType.TABLE and block.table is not None:
        return render_table(block.table)
    if kind == BlockType.CODE:
        fence = "`" * max(3, _longest_backtick_run(block.text) + 1)
        return f"{fence}\n{block.text}\n{fence}"
    if kind == BlockType.IMAGE:
        return f"*[image: {block.text}]*" if block.text else "*[image]*"
    if kind == BlockType.KEY_VALUE and ": " in block.text:
        key, value = block.text.split(": ", 1)
        return f"**{key}:** {value}"
    return block.text


def render_table(table: Table) -> str:
    """A GitHub pipe table. The first row is used as the header."""
    rows = [r for r in table.rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [[_cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", "").replace("\n", "<br>").strip()


def _longest_backtick_run(text: str) -> int:
    longest = current = 0
    for char in text:
        current = current + 1 if char == "`" else 0
        longest = max(longest, current)
    return longest
