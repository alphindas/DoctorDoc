import fakes
import samples
from doctordoc import router as router_module
from doctordoc.detect import detect
from doctordoc.models import PageKind
from doctordoc.router import Router


def run(config, engines, data, name="x.pdf"):
    return Router(config, {e.name: e for e in engines}).route(data, detect(data, name))


def outcomes(result):
    return [(d.scope, d.engine, d.outcome) for d in result.decisions if d.engine != "triage"]


def test_good_first_engine_is_accepted_and_next_is_not_called(config):
    first, second = fakes.FakeEngine("pdf_text"), fakes.FakeEngine("rapidocr")
    result = run(config, [first, second], samples.digital_pdf())
    assert outcomes(result) == [("page 1", "pdf_text", "accepted")]
    assert second.calls == []
    assert result.pages[0].engine == "pdf_text" and result.pages[0].kind == PageKind.DIGITAL


def test_bad_output_falls_back_to_next_engine(config):
    config.page_chains["digital"] = ["bad", "good"]
    result = run(
        config,
        [fakes.FakeEngine("bad", fakes.JUNK), fakes.FakeEngine("good")],
        samples.digital_pdf(),
    )
    assert outcomes(result) == [("page 1", "bad", "rejected"), ("page 1", "good", "accepted")]
    assert result.pages[0].text == fakes.GOOD and not result.warnings


def test_missing_engine_is_skipped_with_reason_and_never_crashes(config):
    config.page_chains["digital"] = ["gone", "good"]
    result = run(
        config,
        [fakes.FakeEngine("gone", available=False), fakes.FakeEngine("good")],
        samples.digital_pdf(),
    )
    skipped = [d for d in result.decisions if d.outcome == "skipped"]
    assert "library not installed" in skipped[0].reason
    assert result.pages[0].engine == "good"


def test_unknown_engine_name_in_chain_is_skipped(config):
    config.page_chains["digital"] = ["typo", "good"]
    result = run(config, [fakes.FakeEngine("good")], samples.digital_pdf())
    assert any(d.engine == "typo" and d.outcome == "skipped" for d in result.decisions)


def test_engine_exception_becomes_a_decision(config):
    config.page_chains["digital"] = ["boom", "good"]
    result = run(
        config,
        [fakes.FakeEngine("boom", error=RuntimeError("kaput")), fakes.FakeEngine("good")],
        samples.digital_pdf(),
    )
    failed = [d for d in result.decisions if d.outcome == "failed"]
    assert "kaput" in failed[0].reason and result.pages[0].engine == "good"


def test_timeout(config, monkeypatch):
    monkeypatch.setattr(router_module, "MODEL_LOAD_ALLOWANCE", 0.0)
    config.timeouts["slow"] = 0.2
    config.page_chains["digital"] = ["slow", "good"]
    result = run(
        config, [fakes.FakeEngine("slow", delay=3), fakes.FakeEngine("good")], samples.digital_pdf()
    )
    assert any(d.engine == "slow" and d.outcome == "timeout" for d in result.decisions)
    assert result.pages[0].engine == "good"


def test_nothing_good_keeps_best_attempt_and_warns(config):
    config.page_chains["digital"] = ["a", "b"]
    mediocre = "Wkh txlfn eurzq ira mxpsv ryhu wkh odcb grj"
    result = run(
        config,
        [fakes.FakeEngine("a", fakes.JUNK), fakes.FakeEngine("b", mediocre)],
        samples.digital_pdf(),
    )
    assert result.pages[0].engine == "b"  # b scored higher than a
    assert any(d.outcome == "fallback" for d in result.decisions)
    assert result.warnings and result.pages[0].warnings


def test_no_engine_at_all_gives_empty_page_with_warning(config):
    config.page_chains["scanned"] = ["rapidocr"]
    result = run(config, [fakes.FakeEngine("rapidocr", available=False)], samples.scanned_pdf())
    assert result.pages[0].blocks == [] and "No text could be extracted" in result.warnings[0]


def test_pages_are_routed_by_kind_in_one_pdf(config):
    # page 1 digital, page 2 scanned: build one PDF with both using pypdfium2 import
    import pypdfium2 as pdfium

    merged = pdfium.PdfDocument(samples.digital_pdf())
    merged.import_pages(pdfium.PdfDocument(samples.scanned_pdf()))
    import io

    buf = io.BytesIO()
    merged.save(buf)
    text_engine = fakes.FakeEngine("pdf_text", "Digital text from the text layer of page one.")
    ocr_engine = fakes.FakeEngine("rapidocr", "Scanned text found by optical character reading.")
    result = run(config, [text_engine, ocr_engine], buf.getvalue())
    assert [p.kind for p in result.pages] == [PageKind.DIGITAL, PageKind.SCANNED]
    assert [p.engine for p in result.pages] == ["pdf_text", "rapidocr"]
    assert text_engine.calls == [[1]] and ocr_engine.calls == [[2]]


def test_blank_page_runs_no_engine(config):
    engine = fakes.FakeEngine("pdf_text")
    result = run(config, [engine], samples.blank_pdf())
    assert engine.calls == [] and result.pages[0].kind == PageKind.BLANK


def test_mixed_page_keeps_ocr_when_it_finds_much_more_text(config):
    short = "Short text layer on the page."
    long_ocr = (
        short + " Plus a lot more text that was only inside the big picture on this page. " * 3
    )
    result = run(
        config,
        [
            fakes.FakeEngine("pdf_text", short),
            fakes.FakeEngine("rapidocr", long_ocr),
            fakes.FakeEngine("tesseract"),
        ],
        samples.mixed_pdf(),
    )
    assert result.pages[0].engine == "rapidocr"
    rejected = [d for d in result.decisions if d.engine == "pdf_text"]
    assert rejected[0].outcome == "rejected" and "rapidocr was better" in rejected[0].reason


def test_mixed_page_keeps_text_layer_on_a_tie(config):
    same = fakes.GOOD
    result = run(
        config,
        [fakes.FakeEngine("pdf_text", same), fakes.FakeEngine("rapidocr", same)],
        samples.mixed_pdf(),
    )
    assert result.pages[0].engine == "pdf_text"


def test_only_one_heavy_engine_is_loaded_at_a_time(config):
    config.page_chains["digital"] = ["heavy_a", "heavy_b"]
    a = fakes.FakeEngine("heavy_a", fakes.JUNK, heavy=True)
    b = fakes.FakeEngine("heavy_b", heavy=True)
    run(config, [a, b], samples.digital_pdf())
    assert a.released == 1  # freed before heavy_b was used


def test_whole_file_chain_for_office_formats(config):
    config.file_chains["docx"] = ["bad", "good"]
    result = run(
        config,
        [fakes.FakeEngine("bad", fakes.JUNK), fakes.FakeEngine("good")],
        samples.docx_bytes(),
        "x.docx",
    )
    assert [(d.scope, d.engine, d.outcome) for d in result.decisions] == [
        ("file", "bad", "rejected"),
        ("file", "good", "accepted"),
    ]
    assert result.pages[0].kind == PageKind.NOT_PAGED


def test_unsupported_page_kind_is_skipped(config):
    config.page_chains["digital"] = ["ocr_only", "good"]
    ocr_only = fakes.FakeEngine("ocr_only", kinds={PageKind.SCANNED})
    result = run(config, [ocr_only, fakes.FakeEngine("good")], samples.digital_pdf())
    assert ocr_only.calls == []
    assert any("does not handle" in d.reason for d in result.decisions)


def test_mixed_page_adds_content_only_the_other_engine_found(config):
    text_layer = (
        "Daily summary: the drilling crew tripped out of the hole at noon "
        "and changed the bit. Mud weight was 10.2 ppg."
    )
    ocr = text_layer + "\n\nStamp: APPROVED"  # short, so the text layer still wins on length
    pdf_text = fakes.FakeEngine("pdf_text", text_layer)

    # the fake OCR returns two blocks: one repeating the text layer, one new
    class TwoBlocks(fakes.FakeEngine):
        def parse(self, data, file_type, pages=None):
            from doctordoc.models import Block, BlockType, Page

            return [
                Page(
                    number=n,
                    blocks=[Block(type=BlockType.PARAGRAPH, text=t) for t in ocr.split("\n\n")],
                )
                for n in (pages or [1])
            ]

    result = run(
        config,
        [pdf_text, TwoBlocks("rapidocr"), fakes.FakeEngine("tesseract")],
        samples.mixed_pdf(),
    )
    page = result.pages[0]
    assert "Stamp: APPROVED" in page.text and page.text.count("Daily summary") == 1
    assert page.engine == "pdf_text+rapidocr"
    merged = [d for d in result.decisions if d.outcome == "merged"]
    assert merged and "1 extra block" in merged[0].reason
    assert "was better" in merged[0].reason and "pdf_text+" not in merged[0].reason


def test_merge_can_be_switched_off(config):
    config.merge_mixed_pages = False
    long_ocr = (
        fakes.GOOD
        + " Scanned stamp that only OCR can read inside the picture area of the page. " * 3
    )
    result = run(
        config,
        [
            fakes.FakeEngine("pdf_text", fakes.GOOD),
            fakes.FakeEngine("rapidocr", long_ocr),
            fakes.FakeEngine("tesseract"),
        ],
        samples.mixed_pdf(),
    )
    assert not any(d.outcome == "merged" for d in result.decisions)
