from doctordoc.models import Block, BlockType, Document, Page, PageKind, Table
from doctordoc.render import render_table, to_markdown


def test_table_is_github_markdown_and_escapes_pipes():
    md = render_table(Table(rows=[["a", "b|c"], ["1", "line1\nline2"]]))
    assert md.splitlines()[0] == "| a | b\\|c |"
    assert md.splitlines()[1] == "| --- | --- |"
    assert "line1<br>line2" in md


def test_markdown_has_headings_lists_and_page_markers():
    doc = Document(
        source="x",
        pages=[
            Page(
                number=1,
                kind=PageKind.DIGITAL,
                blocks=[
                    Block(type=BlockType.HEADING, text="Title", level=1),
                    Block(type=BlockType.LIST_ITEM, text="one"),
                    Block(type=BlockType.LIST_ITEM, text="two"),
                    Block(type=BlockType.PARAGRAPH, text="para"),
                ],
            )
        ],
    )
    md = to_markdown(doc)
    assert "<!-- page 1 -->" in md and "# Title" in md and "- one\n- two" in md


def test_children_and_errors_are_rendered():
    child = Document(
        source="inner.txt",
        pages=[Page(number=1, blocks=[Block(type=BlockType.PARAGRAPH, text="hi")])],
    )
    parent = Document(source="a.zip", children=[child])
    assert "## File: inner.txt" in parent.to_markdown()
    assert "**Error:** boom" in Document(source="x", error="boom").to_markdown()


def test_json_round_trip_and_iter_all():
    child = Document(source="c")
    doc = Document(source="p", children=[child])
    assert [d.source for d in doc.iter_all()] == ["p", "c"]
    assert Document.model_validate_json(doc.to_json()).children[0].source == "c"


def test_code_fence_is_longer_than_backticks_inside():
    doc = Document(
        source="x", pages=[Page(number=1, blocks=[Block(type=BlockType.CODE, text="a ``` b")])]
    )
    assert "````\na ``` b\n````" in doc.to_markdown()
