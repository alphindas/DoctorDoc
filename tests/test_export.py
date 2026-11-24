"""Tests for doctordoc.export: tables, the ZIP of outputs, the summary and the file tree."""

from __future__ import annotations

import io
import zipfile

from openpyxl import load_workbook

from doctordoc import export
from doctordoc.models import Block, BlockType, Document, Page, Table


def _table_doc(name: str = "inv.pdf", rows=None) -> Document:
    rows = rows or [["Item", "Qty"], ["Bit", "2"], ["Mud", "10"]]
    block = Block(type=BlockType.TABLE, table=Table(rows=rows))
    return Document(
        source=name,
        file_type="pdf",
        pages=[Page(number=1, engine="docling", quality=0.9, blocks=[block])],
    )


def test_document_tables_lists_every_table_with_its_page():
    refs = export.document_tables(_table_doc())
    assert len(refs) == 1 and refs[0].page == 1 and refs[0].label == "page 1, table 1"


def test_nested_tables_only_appear_when_asked_for():
    parent = Document(source="b.zip", file_type="zip", children=[_table_doc()])
    assert export.document_tables(parent) == []
    assert len(export.document_tables(parent, include_children=True)) == 1


def test_table_to_csv_quotes_cells_that_contain_commas():
    csv_text = export.table_to_csv(Table(rows=[["a", "b,c"], ["1", "2"]]))
    assert csv_text.splitlines()[0] == 'a,"b,c"'


def test_tables_to_xlsx_has_one_sheet_per_table_and_never_runs_formulas():
    doc = Document(
        source="x",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(type=BlockType.TABLE, table=Table(rows=[["=1+1", "ok"]])),
                    Block(type=BlockType.TABLE, table=Table(rows=[["b"]])),
                ],
            )
        ],
    )
    data = export.tables_to_xlsx(export.document_tables(doc))
    book = load_workbook(io.BytesIO(data))
    assert book.sheetnames == ["p1_t1", "p1_t2"]
    cell = book["p1_t1"]["A1"]
    assert cell.value == "=1+1" and cell.data_type == "s"  # text, not a formula
    assert export.tables_to_xlsx([]) is None


def test_outputs_zip_has_md_json_xlsx_and_a_summary():
    docs = [_table_doc("a.pdf"), _table_doc("a.pdf"), Document(source="bad.pdf", error="broken")]
    archive = zipfile.ZipFile(io.BytesIO(export.outputs_zip(docs)))
    names = set(archive.namelist())
    assert {
        "a.pdf.md",
        "a.pdf.json",
        "a.pdf.tables.xlsx",
        "a.pdf-2.md",
        "bad.pdf.md",
        "summary.csv",
    } <= names
    assert "broken" in archive.read("bad.pdf.md").decode()  # the error is written, not hidden
    assert "file,type,pages" in archive.read("summary.csv").decode()


def test_zip_member_names_cannot_escape_the_archive():
    archive = zipfile.ZipFile(io.BytesIO(export.outputs_zip([Document(source="../../etc/passwd")])))
    assert all(".." not in n and not n.startswith("/") for n in archive.namelist())


def test_summary_rows_show_nested_files_status_and_quality():
    parent = Document(
        source="b.zip",
        file_type="zip",
        children=[_table_doc("a.pdf"), Document(source="x.pdf", error="bad")],
    )
    rows = export.summary_rows([parent])
    assert [r["file"] for r in rows] == ["b.zip", "↳ a.pdf", "↳ x.pdf"]
    assert rows[1]["engine used"] == "docling" and rows[1]["quality"] == 0.9
    assert rows[2]["status"] == "error" and rows[2]["quality"] is None


def test_doc_tree_marks_failures_and_indents_children():
    parent = Document(
        source="b.zip",
        file_type="zip",
        children=[
            Document(source="ok.txt", file_type="txt"),
            Document(source="bad.pdf", error="broken"),
        ],
    )
    tree = export.doc_tree(parent)
    assert tree.splitlines()[0].startswith("✓ b.zip")
    assert "├── ✓ ok.txt" in tree and "└── ✗ bad.pdf" in tree and "broken" in tree
