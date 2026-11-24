"""Tests for the pure HTML helpers of the UI (no Streamlit needed)."""

from __future__ import annotations

from doctordoc.models import Block, BlockType, Decision, Page, PageKind, Table
from doctordoc.ui import widgets


def test_outcome_colours_follow_the_rules():
    tone = {
        outcome: widgets.outcome_chip(Decision(scope="p", engine="e", outcome=outcome))
        for outcome in ("accepted", "rejected", "failed", "skipped", "timeout")
    }
    assert "dd-green" in tone["accepted"] and "Used" in tone["accepted"]
    assert "dd-amber" in tone["rejected"] and "Rejected" in tone["rejected"]  # never a green tick
    assert "dd-red" in tone["failed"] and "dd-red" in tone["timeout"]
    assert "dd-grey" in tone["skipped"]


def test_the_triage_line_is_not_called_used():
    chip = widgets.outcome_chip(Decision(scope="file", engine="triage", outcome="accepted"))
    assert "Used" not in chip and "Checked" in chip


def test_decision_table_escapes_text_from_documents():
    evil = Decision(
        scope="page 1",
        engine="<script>x</script>",
        outcome="failed",
        reason="<img src=x onerror=1>",
    )
    html = widgets.decisions_html([evil])
    assert "<script>" not in html and "<img" not in html and "&lt;script&gt;" in html


def test_every_page_kind_has_its_own_chip():
    labels = {widgets.page_kind_chip(kind) for kind in PageKind}
    assert len(labels) == len(PageKind)
    assert "Garbled" in widgets.page_kind_chip(PageKind.GARBLED)


def test_confidence_tone_uses_the_review_threshold():
    assert widgets.confidence_tone(0.95) == "green"
    assert widgets.confidence_tone(0.8) == "amber"
    assert widgets.confidence_tone(0.69, review_threshold=0.7) == "red"


def test_blocks_become_real_tables_and_are_escaped():
    table = Block(type=BlockType.TABLE, table=Table(rows=[["A<b>", "B"], ["1", "2"]]))
    page = Page(
        number=1,
        blocks=[
            Block(type=BlockType.HEADING, text="Title", level=1),
            table,
            Block(type=BlockType.PARAGRAPH, text="<b>hi</b>"),
        ],
    )
    html = widgets.page_html(page)
    assert "<table" in html and "<th>A&lt;b&gt;</th>" in html and "&lt;b&gt;hi" in html
    assert "<h3>Title</h3>" in html


def test_empty_and_blank_pages_say_so():
    assert "blank" in widgets.page_html(Page(number=1, kind=PageKind.BLANK))
    assert "No text" in widgets.page_html(Page(number=1))


def test_highlight_marks_exactly_the_source_span_and_escapes_the_rest():
    text = "Total: <4620.00> due"
    start = text.index("4620.00")
    html = widgets.highlight_html(text, start, start + 7)
    assert "<mark>4620.00</mark>" in html and "&lt;" in html and "<4620" not in html


def test_highlight_cuts_long_text_to_the_context_and_survives_bad_offsets():
    text = "x" * 1000 + "NEEDLE" + "y" * 1000
    html = widgets.highlight_html(text, 1000, 1006, context=20)
    assert len(html) < 200 and html.count("…") == 2
    assert "<mark>" in widgets.highlight_html("abc", 5, 99)  # offsets past the end do not crash


def test_markdown_for_screen_hides_comments_and_escapes_dollars():
    out = widgets.markdown_for_screen("<!-- page 2 -->\n\nPrice $5 <!-- warning: x -->")
    assert "page 2" in out and "<!--" not in out and "\\$5" in out
