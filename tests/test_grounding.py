from datetime import date

from doctordoc.extract.grounding import PageText, date_candidates, ground_value, parse_number
from doctordoc.models import Block, BlockType, Page

TEXT = (
    "Invoice number: INV-2024-0042\n\nInvoice date: 15 March 2024\n\n"
    "Total: 4,620.00 USD\n\nBill to: Nothern Star Energy"
)
PAGES = [PageText.from_text(TEXT)]


def test_exact_text_has_a_span_that_points_at_the_text():
    g = ground_value("INV-2024-0042", PAGES)
    assert g.kind == "exact" and g.score == 1.0
    assert TEXT[g.source.start : g.source.end] == "INV-2024-0042"


def test_case_and_whitespace_differences_are_normalized_matches():
    g = ground_value("acme   drilling", [PageText.from_text("ACME\nDrilling Ltd")])
    assert g.kind == "normalized" and g.source.text == "ACME\nDrilling"


def test_numbers_match_by_value():
    for value in (4620, 4620.0, 4620.00):
        g = ground_value(value, PAGES)
        assert g.grounded and g.source.text == "4,620.00"


def test_dates_match_by_value_in_many_formats():
    assert ground_value(date(2024, 3, 15), PAGES).source.text == "15 March 2024"
    for text in ["2024-03-15", "15/03/2024", "03/15/2024", "Mar 15, 2024", "15-Mar-2024"]:
        assert date(2024, 3, 15) in [d for _, _, d in date_candidates(text)], text


def test_ocr_typo_is_a_fuzzy_match_with_lower_score():
    g = ground_value("Northern Star Energy", PAGES)
    assert g.kind == "fuzzy" and 0.7 < g.score < 1.0 and g.source.text == "Nothern Star Energy"


def test_invented_values_are_not_grounded():
    for value in ("Totally Absent Corp", 99999.0, date(1999, 1, 1)):
        g = ground_value(value, PAGES)
        assert not g.grounded and g.source is None and g.score == 0.0


def test_number_is_not_found_inside_an_identifier_or_a_longer_number():
    assert not ground_value(42, PAGES).grounded  # 0042 is part of INV-2024-0042
    assert not ground_value(620.0, PAGES).grounded  # part of 4,620.00


def test_booleans_are_inferred_not_grounded():
    g = ground_value(True, PAGES)
    assert g.kind == "inferred" and not g.grounded


def test_short_text_must_match_whole_words():
    assert not ground_value("US", PAGES).grounded  # only inside "USD"


def test_first_page_that_matches_wins_and_page_number_is_kept():
    pages = [PageText.from_text("nothing here", 1), PageText.from_text("Total: 12.50", 2)]
    assert ground_value(12.5, pages).source.page == 2


def test_parse_number_formats():
    assert parse_number("4,620.00") == parse_number("4620.00")
    assert str(parse_number("1.234,56")) == "1234.56"
    assert str(parse_number("12,5")) == "12.5"
    assert str(parse_number("1,234")) == "1234"


def test_ocr_confidence_and_bbox_come_from_the_blocks():
    page = Page(
        number=3,
        blocks=[
            Block(type=BlockType.PARAGRAPH, text="first line", confidence=0.99),
            Block(type=BlockType.PARAGRAPH, text="Total: 4,620.00", confidence=0.60),
        ],
    )
    g = ground_value(4620.0, [PageText.from_page(page)])
    assert g.source.page == 3 and g.source.block_index == 1
    assert abs(g.ocr_confidence - 0.60) < 1e-9
