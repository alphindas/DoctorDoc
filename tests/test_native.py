import samples
from doctordoc.engines.native import NativeEngine
from doctordoc.models import BlockType


def parse(data: bytes, file_type: str):
    return NativeEngine().parse(data, file_type)


def types(page):
    return [b.type for b in page.blocks]


def test_docx_headings_lists_tables():
    page = parse(samples.docx_bytes(), "docx")[0]
    assert types(page) == [
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.LIST_ITEM,
        BlockType.LIST_ITEM,
        BlockType.TABLE,
    ]
    assert page.blocks[-1].table.rows[1] == ["1200", "35.2"]


def test_xlsx_one_page_one_table_per_sheet():
    pages = parse(samples.xlsx_bytes(), "xlsx")
    assert [p.blocks[0].text for p in pages] == ["Rates", "Crew"]
    assert pages[0].blocks[1].table.rows == [["Depth", "ROP"], ["1200", "35.2"], ["1300", "40.1"]]


def test_pptx_slides_are_pages():
    pages = parse(samples.pptx_bytes(), "pptx")
    assert len(pages) == 2
    assert pages[0].blocks[0].text == "Well Summary"
    assert pages[1].blocks[1].type == BlockType.TABLE


def test_csv_json_html():
    assert parse(samples.csv_bytes(), "csv")[0].blocks[0].table.rows[0] == ["depth", "rop", "wob"]
    assert parse(samples.json_bytes(), "json")[0].blocks[0].table.rows[1] == ["A-1", "3200"]
    html = parse(samples.html_bytes(), "html")[0]
    assert "alert" not in html.text and "Hello world, this is a paragraph." in html.text


def test_invalid_json_falls_back_to_raw_text_with_warning():
    page = parse(b'{"a": 1,', "json")[0]
    assert page.blocks[0].type == BlockType.CODE and page.warnings


def test_eml_body_headers_and_attachment_names():
    page = parse(samples.eml_bytes(), "eml")[0]
    assert "Subject: Daily report attached" in page.text
    assert "Attachments: invoice.pdf, rates.csv" in page.text


def test_xml_is_shown_raw_and_never_parsed():
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    assert "ENTITY" in parse(xml, "xml")[0].text
