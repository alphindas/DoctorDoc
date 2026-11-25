"""Tests for the benchmark itself: the scores, the sample generator, and the report maths."""

import argparse
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest
from benchmarks import run as bench
from benchmarks.metrics import (
    field_accuracy,
    mean,
    stitch,
    table_accuracy,
    text_accuracy,
    values_match,
    word_recall,
)

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ metrics


def test_text_accuracy_ignores_case_spacing_and_punctuation():
    assert text_accuracy("Total:  4,620.00 USD", "total 4620.00 usd") == 1.0


def test_text_accuracy_punishes_wrong_characters_and_wrong_order():
    truth = "invoice number 12345 total 99"
    assert text_accuracy("invoice number 12845 total 99", truth) < 1.0
    assert text_accuracy("total 99 invoice number 12345", truth) < text_accuracy(
        "invoice number 12845 total 99", truth
    )


def test_text_accuracy_edge_cases():
    assert text_accuracy("", "") == 1.0
    assert text_accuracy("something", "") == 0.0
    assert text_accuracy("", "something") == 0.0


def test_word_recall_ignores_order_but_counts_repeats():
    assert word_recall("b a", "a b") == 1.0
    assert word_recall("a", "a a") == 0.5
    assert word_recall("", "a b") == 0.0


TRUTH = [["Item", "Qty"], ["Bit", "2"], ["Mud", "10"]]


def test_table_accuracy():
    assert table_accuracy([TRUTH], [TRUTH]) == 1.0
    assert table_accuracy([], [TRUTH]) == 0.0
    wrong_cell = [["Item", "Qty"], ["Bit", "3"], ["Mud", "10"]]
    assert table_accuracy([wrong_cell], [TRUTH]) == pytest.approx(5 / 6, abs=1e-3)
    assert table_accuracy([[["Item"]]], [TRUTH]) == pytest.approx(1 / 6, abs=1e-3)
    assert table_accuracy([], []) == 1.0  # no table expected, nothing to lose


def test_table_continued_on_next_page_is_stitched_and_repeated_header_dropped():
    page1 = [["Item", "Qty"], ["Bit", "2"]]
    page2 = [["Item", "Qty"], ["Mud", "10"]]
    assert stitch([page1, page2]) == TRUTH
    assert table_accuracy([page1, page2], [TRUTH]) == 1.0


def test_values_match_compares_by_value_not_format():
    assert values_match(4620, 4620.0) and values_match("4620.00", 4620.0)
    assert not values_match(4621, 4620.0)
    assert values_match(date(2025, 3, 1), "2025-03-01")
    assert values_match("Falcon-7", "falcon 7")
    assert values_match(None, None) and not values_match(None, "x") and not values_match("x", None)
    assert not values_match("abc", 5.0)


def test_field_accuracy():
    truth = {"a": "X", "b": 2.0, "c": "2025-01-01"}
    assert field_accuracy({"a": "x", "b": 2, "c": date(2025, 1, 1)}, truth) == 1.0
    assert field_accuracy({"a": "x"}, truth) == pytest.approx(1 / 3, abs=1e-3)
    assert field_accuracy({}, {}) == 1.0


def test_mean_skips_none():
    assert mean([1.0, None, 0.0]) == 0.5 and mean([None]) is None


# ------------------------------------------------------------------ report maths


def row(system, category, status="ok", text=1.0, seconds=1.0, pages=1, table=None, field=None):
    return {
        "system": system, "doc": f"{category}-1", "category": category, "status": status,
        "text_acc": text, "word_recall": text, "table_acc": table, "field_acc": field,
        "seconds": seconds, "pages": pages, "engines_used": "", "reason": "",
    }  # fmt: skip


ROWS = [
    row("a", "docx", text=0.9), row("b", "docx", text=0.6),
    row("a", "digital_pdf", "skipped", text=0.0), row("b", "digital_pdf", text=0.8),
    row("router", "docx", text=0.9), row("router", "digital_pdf", text=0.95),
]  # fmt: skip


def test_cell_shows_na_only_when_every_file_of_the_type_was_skipped():
    assert bench.cell(ROWS, "a", "digital_pdf", "text_acc") == "n/a"
    assert bench.cell(ROWS, "a", "docx", "text_acc") == "0.90"
    assert bench.cell(ROWS, "a", None, "text_acc") == "0.45"  # skipped file counts as 0


def test_speed_is_pages_per_second_over_files_that_were_read():
    rows = [row("a", "docx", seconds=2.0, pages=4), row("a", "docx", "failed", seconds=99, pages=9)]
    assert bench.speed_cell(rows, "a", "docx") == "2.0"
    assert bench.speed_cell(rows, "a", None) == "2.0"


def test_verdict_names_categories_where_a_single_engine_wins():
    text = bench.verdict(ROWS, ["a", "b", "router"], "text_acc")
    assert "none" in text  # router 0.9 and 0.95 are the best in both types
    rows = [*ROWS, row("b", "html", text=1.0), row("router", "html", text=0.5)]
    assert "html" in bench.verdict(rows, ["a", "b", "router"], "text_acc")


def test_verdict_copes_with_a_router_only_quick_run():
    assert "partial run" in bench.verdict([row("router", "docx")], ["router"], "text_acc")


def test_fooled_counts():
    outputs = [
        {"quality": 1.0, "text_acc": 0.2},  # bad and accepted: fooled
        {"quality": 0.3, "text_acc": 0.2},  # bad and rejected: caught
        {"quality": 1.0, "text_acc": 1.0},  # good and accepted
        {"quality": None, "text_acc": 0.0},  # produced nothing: not counted
    ]
    assert bench.fooled_counts(outputs, 0.9, 0.55) == {
        "outputs": 3, "bad": 2, "passed": 2, "fooled": 1
    }  # fmt: skip


def test_markdown_table_bolds_the_best_value():
    table = bench.markdown_table(ROWS, ["a", "b", "router"], "text_acc", "T")
    assert "| docx | **0.90** | 0.60 | **0.90** |" in table


# ------------------------------------------------------------------ quick mode


def test_types_accept_commas_or_spaces():
    assert bench.parse_types(["rotated,photo"]) == ["rotated", "photo"]
    assert bench.parse_types(["rotated", "photo,"]) == ["rotated", "photo"]
    assert bench.parse_types(None) == []


def test_select_types_filters_and_rejects_typos():
    manifest = [{"category": "docx"}, {"category": "photo"}]
    assert bench.select_types(manifest, ["photo"]) == [{"category": "photo"}]
    assert bench.select_types(manifest, []) == manifest
    with pytest.raises(SystemExit, match="phto"):
        bench.select_types(manifest, ["phto"])


@pytest.mark.parametrize(
    "options, folder",
    [
        ({}, "."),
        ({"types": ["rotated", "photo"]}, "runs/rotated_photo"),
        ({"types": ["rotated"], "label": "before"}, "runs/before"),
        ({"systems": ["router"]}, "runs/partial"),
        ({"limit": 3}, "runs/partial"),
    ],
)
def test_a_partial_run_never_overwrites_the_full_results(tmp_path, options, folder):
    args = argparse.Namespace(
        out=tmp_path, **{"types": [], "systems": None, "limit": None, "label": None, **options}
    )
    assert bench.output_dir(args)[0] == tmp_path / folder


# ------------------------------------------------------------------ sample generator


@pytest.fixture(scope="module")
def make_samples():
    spec = importlib.util.spec_from_file_location(
        "make_samples", ROOT / "scripts" / "make_samples.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_samples"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def corpus(make_samples):
    return make_samples.build_corpus(seed=1234)


def test_corpus_has_every_kind_of_file_the_spec_asks_for(corpus):
    categories = {e.category for e in corpus}
    assert categories == set(bench.CATEGORY_ORDER) | {"corrupt"}
    assert len(corpus) == 48


def test_same_seed_gives_identical_bytes_and_other_seed_does_not(make_samples, corpus):
    again = make_samples.build_corpus(seed=1234)
    assert [e.data for e in again] == [e.data for e in corpus]
    other = make_samples.build_corpus(seed=99)
    assert [e.data for e in other] != [e.data for e in corpus]


def test_ground_truth_is_present_and_consistent(corpus):
    for entry in corpus:
        if entry.expect_error:
            assert entry.category == "corrupt" and not entry.truth_text
        else:
            assert entry.truth_text.strip(), entry.filename
        if entry.schema:
            assert entry.fields, entry.filename
    assert {e.schema for e in corpus if e.schema} == {"invoice", "drilling"}


def test_two_page_tables_really_cross_a_page(corpus):
    for entry in corpus:
        if entry.category == "table_2page":
            assert entry.pages >= 2


def test_invoice_totals_in_the_truth_add_up(corpus):
    for entry in corpus:
        f = entry.fields
        if entry.schema == "invoice":
            assert f["subtotal"] + f["tax_amount"] == pytest.approx(f["total"], abs=0.011)


def test_digital_files_are_read_back_perfectly_by_the_text_layer(corpus, tmp_path):
    """The truth must be reachable: a clean digital PDF read by pdf_text scores 1.0."""
    from doctordoc.detect import detect
    from doctordoc.engines.pdf_text import PdfTextEngine

    entry = next(e for e in corpus if e.category == "digital_pdf")
    pages = PdfTextEngine().parse(entry.data, "pdf")
    assert detect(entry.data).file_type == "pdf"
    assert text_accuracy("\n".join(p.text for p in pages), entry.truth_text) == 1.0
