import samples
from doctordoc.config import Config
from doctordoc.models import PageKind
from doctordoc.triage import classify, triage_pdf


def kinds(data: bytes) -> list[PageKind]:
    return [t.kind for t in triage_pdf(data)]


def test_real_pdfs_are_classified():
    assert kinds(samples.digital_pdf()) == [PageKind.DIGITAL]
    assert kinds(samples.scanned_pdf()) == [PageKind.SCANNED]
    assert kinds(samples.garbled_pdf()) == [PageKind.GARBLED]
    assert kinds(samples.junk_text_pdf()) == [PageKind.GARBLED]
    assert kinds(samples.mixed_pdf()) == [PageKind.MIXED]
    assert kinds(samples.blank_pdf()) == [PageKind.BLANK]


def test_each_page_is_classified_separately():
    data = samples.digital_pdf([samples.INVOICE_LINES, [samples.PARAGRAPH]])
    assert kinds(data) == [PageKind.DIGITAL, PageKind.DIGITAL]


def test_triage_reports_numbers():
    page = triage_pdf(samples.scanned_pdf())[0]
    assert page.chars == 0 and page.image_coverage > 0.9 and page.quality is None


def test_rules_directly():
    cfg = Config()
    assert classify(0, None, 0.0, 0, cfg) == PageKind.BLANK
    assert classify(0, None, 0.9, 0, cfg) == PageKind.SCANNED
    assert classify(0, None, 0.0, 50, cfg) == PageKind.SCANNED  # vector drawing / outlined text
    assert classify(500, 0.2, 0.0, 0, cfg) == PageKind.GARBLED
    assert classify(500, 0.95, 0.6, 0, cfg) == PageKind.MIXED
    assert classify(500, 0.95, 0.05, 0, cfg) == PageKind.DIGITAL
