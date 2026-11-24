"""Run the REAL engines on synthetic files. Each test skips itself if the engine is missing."""

import pytest

import samples
from doctordoc.engines.docling_engine import DoclingEngine
from doctordoc.engines.markitdown_engine import MarkItDownEngine
from doctordoc.engines.ocr_rapid import RapidOcrEngine
from doctordoc.engines.ocr_tesseract import TesseractEngine
from doctordoc.models import BlockType

pytestmark = pytest.mark.integration


def need(engine_class):
    engine = engine_class()
    if not engine.is_available():
        pytest.skip(f"{engine.name}: {engine.missing_reason()}")
    return engine


@pytest.mark.parametrize("engine_class", [RapidOcrEngine, TesseractEngine])
def test_ocr_engines_read_a_noisy_tilted_scan(engine_class):
    engine = need(engine_class)
    [page] = engine.parse(samples.scanned_pdf(), "pdf")
    for expected in ("INV-2024-0042", "4620.00", "Northern Star Energy"):
        assert expected in page.text, f"{engine.name} missed {expected!r}:\n{page.text}"
    assert all(b.confidence and b.confidence > 0.5 for b in page.blocks)
    assert page.blocks[0].bbox is not None


def test_rapidocr_keeps_table_columns_apart():
    engine = need(RapidOcrEngine)
    [page] = engine.parse(samples.scanned_pdf(), "pdf")
    assert "Drill bit 8.5 inch 2 1500.00 3000.00" in page.text


def test_rapidocr_reads_an_image_file():
    import io

    import pypdfium2 as pdfium

    engine = need(RapidOcrEngine)
    image = pdfium.PdfDocument(samples.digital_pdf()).__getitem__(0).render(scale=2).to_pil()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    [page] = engine.parse(buf.getvalue(), "png")
    assert "INV-2024-0042" in page.text


def test_docling_reads_digital_pdf_one_page_of_many():
    engine = need(DoclingEngine)
    data = samples.digital_pdf([samples.INVOICE_LINES, [samples.PARAGRAPH]])
    [page] = engine.parse(data, "pdf", [2])
    assert page.number == 2 and "tripped out of the hole" in page.text
    assert "INV-2024-0042" not in page.text


def test_docling_finds_tables_in_docx():
    engine = need(DoclingEngine)
    [page] = engine.parse(samples.docx_bytes(), "docx")
    table = next(b for b in page.blocks if b.type == BlockType.TABLE)
    assert table.table.rows[1] == ["1200", "35.2"]


def test_markitdown_converts_office_files():
    engine = need(MarkItDownEngine)
    pages = engine.parse(samples.pptx_bytes(), "pptx")
    assert len(pages) == 2 and "Well Summary" in pages[0].text
    [page] = engine.parse(samples.docx_bytes(), "docx")
    assert any(b.type == BlockType.TABLE for b in page.blocks)


def test_full_pipeline_with_default_config_on_every_kind_of_page():
    from doctordoc import parse
    from doctordoc.config import Config

    cfg = Config(gpu="off")
    if not (RapidOcrEngine().is_available() or TesseractEngine().is_available()):
        pytest.skip("no OCR engine installed")
    for name, data in {"scanned": samples.scanned_pdf(), "garbled": samples.garbled_pdf()}.items():
        doc = parse(data, cfg, filename=f"{name}.pdf")
        assert "INV-2024-0042" in doc.text, name
        assert doc.error is None
    mixed = parse(samples.mixed_pdf(), cfg)
    assert (
        "Daily summary" in mixed.text and "APPROVED" in mixed.text
    )  # text layer + stamp in picture
