"""Turn parsed Documents into files people download: tables as CSV/Excel, one ZIP of everything,
a summary table, and a text tree of the files inside a ZIP or email."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from doctordoc.models import BlockType, Document, PageKind, Table

# ---------------------------------------------------------------------------- tables


@dataclass
class TableRef:
    file: str
    page: int
    number: int  # 1-based, counted inside the page
    table: Table

    @property
    def label(self) -> str:
        return f"page {self.page}, table {self.number}"


def document_tables(doc: Document, include_children: bool = False) -> list[TableRef]:
    """Every real table block of the document, in reading order."""
    found: list[TableRef] = []
    for item in doc.iter_all() if include_children else [doc]:
        for page in item.pages:
            count = 0
            for block in page.blocks:
                if block.type == BlockType.TABLE and block.table and block.table.rows:
                    count += 1
                    found.append(TableRef(item.source, page.number, count, block.table))
    return found


def table_to_csv(table: Table) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(table.rows)
    return buffer.getvalue()


def tables_to_xlsx(refs: list[TableRef]) -> bytes | None:
    """One Excel file, one sheet per table. None if there are no tables."""
    if not refs:
        return None
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)  # type: ignore[arg-type]
    used: set[str] = set()
    for ref in refs:
        title = f"p{ref.page}_t{ref.number}"
        while title in used:  # same page number in different nested files
            title += "_"
        used.add(title)
        sheet = book.create_sheet(title[:31])
        for r, row in enumerate(ref.table.rows, start=1):
            for c, text in enumerate(row, start=1):
                cell = sheet.cell(row=r, column=c, value=text)
                if text.startswith("="):  # never let a scanned cell run as a formula
                    cell.data_type = "s"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------- summary


def summary_rows(docs: list[Document]) -> list[dict[str, Any]]:
    """One row per file (nested files are indented with an arrow): for the results table."""
    rows: list[dict[str, Any]] = []
    for top in docs:
        for depth, item in _walk(top, 0):
            qualities = [p.quality for p in item.pages if p.quality is not None]
            engines = sorted({e for p in item.pages if p.engine for e in p.engine.split("+")})
            rows.append(
                {
                    "file": ("↳ " * min(depth, 1)) + item.source,
                    "type": item.file_type,
                    "pages": len(item.pages),
                    "engine used": ", ".join(engines) or "-",
                    "quality": round(sum(qualities) / len(qualities), 2) if qualities else None,
                    "status": status_of(item),
                }
            )
    return rows


def status_of(doc: Document) -> str:
    if doc.error:
        return "error"
    return f"{len(doc.warnings)} warning(s)" if doc.warnings else "ok"


def _walk(doc: Document, depth: int):
    yield depth, doc
    for child in doc.children:
        yield from _walk(child, depth + 1)


def doc_tree(doc: Document) -> str:
    """A text tree of the file and the files inside it (zip members, email attachments)."""
    lines: list[str] = []

    def add(item: Document, prefix: str, last: bool, top: bool) -> None:
        mark = {"ok": "✓", "error": "✗"}.get(status_of(item), "!")
        detail = f"{item.file_type}, {len(item.pages)} page(s)" if not item.error else item.error
        label = f"{mark} {item.source}  [{detail}]"
        if top:
            lines.append(label)
            child_prefix = ""
        else:
            lines.append(f"{prefix}{'└── ' if last else '├── '}{label}")
            child_prefix = prefix + ("    " if last else "│   ")
        for index, child in enumerate(item.children):
            add(child, child_prefix, index == len(item.children) - 1, False)

    add(doc, "", True, True)
    return "\n".join(lines)


def kind_counts(doc: Document) -> dict[str, int]:
    counts: dict[str, int] = {}
    for page in doc.pages:
        if page.kind != PageKind.NOT_PAGED:
            counts[page.kind.value] = counts.get(page.kind.value, 0) + 1
    return counts


# ---------------------------------------------------------------------------- ZIP of everything


def outputs_zip(docs: list[Document]) -> bytes:
    """One ZIP with, for every file: name.md, name.json and (if it has tables) name.tables.xlsx,
    plus summary.csv for the whole batch."""
    buffer = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for doc in docs:
            stem = _unique(_safe_stem(doc.source), used)
            archive.writestr(f"{stem}.md", doc.to_markdown())
            archive.writestr(f"{stem}.json", doc.to_json())
            workbook = tables_to_xlsx(document_tables(doc, include_children=True))
            if workbook:
                archive.writestr(f"{stem}.tables.xlsx", workbook)
        rows = summary_rows(docs)
        if rows:
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr("summary.csv", out.getvalue())
    return buffer.getvalue()


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", PurePath(name).name).strip("._") or "file"
    return stem[:80]


def _unique(stem: str, used: set[str]) -> str:
    candidate, number = stem, 2
    while candidate in used:
        candidate, number = f"{stem}-{number}", number + 1
    used.add(candidate)
    return candidate
