"""UI tests with Streamlit's AppTest: they run the app in-process, no browser needed.

The app is pointed at a small config (native + pdf_text + tesseract/rapidocr only) so the
tests never load Docling or any other big model.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import streamlit as st
import yaml
from streamlit.testing.v1 import AppTest

from doctordoc import service
from doctordoc.extract.models import ExtractionResult, FieldResult, Issue, Source

APP = Path(__file__).resolve().parent.parent / "src" / "doctordoc" / "ui" / "app.py"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def app(tmp_path, monkeypatch) -> AppTest:
    from doctordoc.config import DEFAULT_FILE_CHAINS, DEFAULT_PAGE_CHAINS

    page = {
        k: [e for e in v if e not in ("docling", "paddle_vl", "cloud_vlm")]
        for k, v in DEFAULT_PAGE_CHAINS.items()
    }
    files = {
        k: [e for e in v if e not in ("docling", "paddle_vl", "cloud_vlm")]
        for k, v in DEFAULT_FILE_CHAINS.items()
    }
    path = tmp_path / "ui.yaml"
    path.write_text(yaml.safe_dump({"page_chains": page, "file_chains": files, "gpu": "off"}))
    monkeypatch.setenv("DOCTORDOC_UI_CONFIG", str(path))
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    return at


def parse_examples(at: AppTest, *names: str) -> AppTest:
    for name in names:
        at.sidebar.multiselect(key="examples").select(name)
    at.sidebar.button(key="parse").click()
    return at.run()


def open_tab(at: AppTest, label: str) -> AppTest:
    at.session_state["main_tabs"] = label
    return at.run()


def html_of(at: AppTest) -> str:
    """Everything the page drew with st.html (chips, tables, highlighted text)."""
    return "\n".join(h.value for h in at.get("html"))


def text_of(at: AppTest) -> str:
    """All visible text: markdown, errors and st.html."""
    return "\n".join(
        [*(str(m.value) for m in at.markdown), *(str(e.value) for e in at.error), html_of(at)]
    )


# ---------------------------------------------------------------------------- start screen


def test_the_empty_page_shows_the_four_tabs_and_a_hint(app):
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Parse", "Router log", "Extract", "Compare engines"]
    assert "Start here" in text_of(app)


def test_the_sidebar_has_the_controls_from_the_design(app):
    labels = [w.label for w in app.sidebar.selectbox] + [w.label for w in app.sidebar.toggle]
    assert "OCR engine" in labels and "Local only (no cloud OCR)" in labels
    assert "Merge mixed pages" in labels
    assert "Model (for Extract)" in labels
    assert [b.label for b in app.sidebar.button] == ["Parse document"]


def test_local_only_is_on_by_default_and_the_cloud_warning_appears_when_it_is_turned_off(app):
    assert app.sidebar.toggle(key="local_only").value is True
    assert not app.sidebar.warning
    app.sidebar.toggle(key="local_only").set_value(False).run()
    assert [w.value for w in app.sidebar.warning] == [service.CLOUD_PRIVACY_NOTE]
    app.sidebar.toggle(key="local_only").set_value(True).run()
    assert not app.sidebar.warning


def sidebar_words(at: AppTest) -> str:
    toggle = at.sidebar.toggle(key="local_only")
    return " ".join(
        [
            toggle.help,
            *(w.value for w in at.sidebar.warning),
            *(c.value for c in at.sidebar.caption),
        ]
    )


@pytest.mark.parametrize("api_key", ["", "synthetic-key"], ids=["no key", "with a key"])
def test_the_sidebar_never_says_a_missing_key_stops_the_cloud(tmp_path, monkeypatch, api_key):
    at = cloud_app(tmp_path, monkeypatch, {"cloud": {"api_key": api_key}})
    assert at.sidebar.toggle(key="local_only").help == service.CLOUD_PRIVACY_NOTE
    at.sidebar.toggle(key="local_only").set_value(False).run()
    assert [w.value for w in at.sidebar.warning] == [service.CLOUD_PRIVACY_NOTE]
    assert "will be skipped" not in sidebar_words(at)
    assert "No cloud API key" not in sidebar_words(at)


def cloud_app(tmp_path, monkeypatch, settings) -> AppTest:
    """The UI with the settings given. The cloud address points at a closed port."""
    data = {"gpu": "off", **settings}
    data["cloud"] = {"base_url": "http://127.0.0.1:9/v1", **data.get("cloud", {})}
    path = tmp_path / "cloud-ui.yaml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("DOCTORDOC_UI_CONFIG", str(path))
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    return at


@pytest.mark.parametrize("local_only", [False, True], ids=["Local only off", "Local only on"])
def test_the_words_match_what_the_ui_sends(tmp_path, monkeypatch, local_only):
    """With no API key and cloud.enabled false: the switch decides what is sent."""
    sent = []

    def record(url, **kwargs):
        sent.append((url, kwargs["headers"]))
        raise httpx.ConnectError("recorded, not sent")

    monkeypatch.setattr(httpx, "post", record)
    at = cloud_app(tmp_path, monkeypatch, {"cloud": {"enabled": False, "api_key": ""}})
    at.sidebar.toggle(key="local_only").set_value(False).run()
    at.sidebar.toggle(key="engine_cloud_vlm").set_value(True).run()  # in the Engines list
    if local_only:
        at.sidebar.toggle(key="local_only").set_value(True).run()
    parse_examples(at, "invoice_digital.pdf")
    open_tab(at, "Compare engines")
    at.button(key="run_compare").click()
    at.run()
    assert not at.exception
    if local_only:
        assert sent == []
    else:
        assert sent == [("http://127.0.0.1:9/v1/chat/completions", {})]  # no key, and still sent


@pytest.mark.parametrize(
    "base_url, warns",
    [("http://localhost:11434", False), ("http://models.example.com:11434", True)],
    ids=["local model server", "another computer"],
)
def test_a_model_server_elsewhere_is_warned_about_with_local_only_on(
    tmp_path, monkeypatch, base_url, warns
):
    def no_server(url, **kwargs):
        raise httpx.ConnectError("no server in tests")

    monkeypatch.setattr(httpx, "get", no_server)  # the model list: nothing is contacted
    at = cloud_app(tmp_path, monkeypatch, {"llm": {"base_url": base_url}})
    assert at.sidebar.toggle(key="local_only").value is True
    expected = [service.REMOTE_LLM_NOTE] if warns else []
    assert [w.value for w in at.sidebar.warning] == expected
    # the label claims nothing about where the model is, and neither does the text under it
    assert at.sidebar.selectbox(key="model").label == "Model (for Extract)"
    assert not [c.value for c in at.sidebar.caption if "local model" in c.value.lower()]


def test_parse_without_a_file_asks_for_one_and_does_not_crash(app):
    app.sidebar.button(key="parse").click().run()
    assert not app.exception
    assert any("Add a file" in str(w.value) for w in app.sidebar.warning)


# ---------------------------------------------------------------------------- Parse tab


def test_parsing_an_example_fills_the_parse_tab(app):
    parse_examples(app, "invoice_digital.pdf")
    assert not app.exception
    text = text_of(app)
    assert "Extracted text" in text and "Output" in text
    assert any("Digital" in str(m.value) for m in app.markdown)  # the page chip


def test_several_files_show_a_results_table_and_a_download_all_button(app):
    parse_examples(app, "invoice_digital.pdf", "line_items.xlsx")
    assert not app.exception
    assert len(app.dataframe) == 1 and len(app.dataframe[0].value) == 2
    assert [s.label for s in app.selectbox if s.label == "File"]  # the file picker


def serve_files(monkeypatch, tmp_path, files: dict[str, bytes]) -> None:
    """Make the 'try the examples' picker offer these files (so a test can feed in bad ones)."""
    from doctordoc.ui import state

    paths = {}
    for name, data in files.items():
        paths[name] = tmp_path / name
        paths[name].write_bytes(data)
    monkeypatch.setattr(state, "example_files", lambda: paths)


def test_a_wrong_extension_is_flagged_in_the_parse_tab(tmp_path, monkeypatch, app):
    serve_files(monkeypatch, tmp_path, {"report.pdf": (EXAMPLES / "quotation.docx").read_bytes()})
    app.run()
    parse_examples(app, "report.pdf")
    assert not app.exception
    warnings = " ".join(str(w.value) for w in app.warning)
    assert "Wrong file extension" in warnings and "content is docx" in warnings


def test_the_output_panel_switches_between_its_four_views(app):
    parse_examples(app, "line_items.xlsx")
    for view in ("Markdown (raw)", "JSON", "Tables", "Rendered Markdown"):
        app.session_state["output_view"] = view
        app.run()
        assert not app.exception, view
    app.session_state["output_view"] = "JSON"
    app.run()
    assert any('"file_type"' in c.value for c in app.code)  # the JSON view shows the document


# ---------------------------------------------------------------------------- Router log tab


def test_router_log_shows_why_and_a_decision_table(app):
    parse_examples(app, "invoice_digital.pdf")
    open_tab(app, "Router log")
    assert not app.exception
    text = text_of(app)
    assert "Why this engine was chosen" in text and "Decision log" in text
    assert "dd-table" in text and "Used" in text and "Checked" in text


# ---------------------------------------------------------------------------- Extract tab


def fake_result() -> ExtractionResult:
    source = Source(page=1, start=0, end=5, text="Total", match="exact")
    return ExtractionResult(
        schema_name="Invoice",
        model="llama3.2:3b",
        fields=[
            FieldResult(path="total", value=40000.0, grounded=True, source=source, confidence=0.96),
            FieldResult(
                path="currency", value="USD", grounded=False, confidence=0.25, needs_review=True
            ),
        ],
        needs_review=["currency"],
        issues=[
            Issue(path="subtotal", rule="sum_equals", message="does not add up", severity="warning")
        ],
    )


def test_extract_tab_shows_fields_confidence_review_list_and_issues(app, monkeypatch):
    monkeypatch.setattr(service, "run_extraction", lambda doc, schema, config: fake_result())
    parse_examples(app, "invoice_digital.pdf")
    open_tab(app, "Extract")
    app.button(key="run_extract").click().run()
    assert not app.exception
    page = text_of(app)
    assert "Needs review (1)" in page and "currency" in page
    assert "Not found" in page and "96%" in page and "Validation issues (1)" in page
    assert "does not add up" in page


def test_clicking_a_field_highlights_its_source_text(app, monkeypatch):
    monkeypatch.setattr(service, "run_extraction", lambda doc, schema, config: fake_result())
    parse_examples(app, "invoice_digital.pdf")
    open_tab(app, "Extract")
    app.button(key="run_extract").click().run()
    app.button(key="src_0_0").click().run()
    assert not app.exception
    marked = re.findall(r"<mark>([^<]*)</mark>", html_of(app))
    assert len(marked) == 1 and len(marked[0]) == 5  # exactly the span start=0..end=5


def test_extraction_failure_is_shown_nicely(app, monkeypatch):
    broken = ExtractionResult(
        schema_name="Invoice", error="Cannot reach the LLM server at http://localhost:11434."
    )
    monkeypatch.setattr(service, "run_extraction", lambda doc, schema, config: broken)
    parse_examples(app, "invoice_digital.pdf")
    open_tab(app, "Extract")
    app.button(key="run_extract").click().run()
    assert not app.exception
    assert any("Cannot reach" in str(e.value) for e in app.error)


# ---------------------------------------------------------------------------- Compare tab


def test_compare_tab_runs_each_engine_and_marks_the_routers_choice(app, monkeypatch):
    monkeypatch.setattr(
        service, "comparable_engines", lambda data, name, config: ["pdf_text", "tesseract"]
    )
    parse_examples(app, "invoice_digital.pdf")
    open_tab(app, "Compare engines")
    app.button(key="run_compare").click().run()
    assert not app.exception
    page = html_of(app)
    assert "pdf_text" in page and "tesseract" in page and "★" in page


# ---------------------------------------------------------------------------- bad files


def test_a_password_protected_pdf_shows_a_clear_error(tmp_path, monkeypatch, app):
    import samples

    serve_files(monkeypatch, tmp_path, {"locked.pdf": samples.digital_pdf(encrypt="pw")})
    app.run()
    parse_examples(app, "locked.pdf")
    assert not app.exception
    assert any("password-protected" in str(e.value) for e in app.error)


def test_garbage_and_empty_files_do_not_crash_any_tab(tmp_path, monkeypatch, app):
    serve_files(monkeypatch, tmp_path, {"junk.pdf": b"%PDF-1.4 not a pdf", "empty.txt": b""})
    app.run()
    parse_examples(app, "junk.pdf", "empty.txt")
    for tab in ("Parse", "Router log", "Extract", "Compare engines"):
        open_tab(app, tab)
        assert not app.exception, tab


def test_an_example_that_fails_is_reported_in_the_results_not_as_a_crash(app, monkeypatch):
    from doctordoc.models import Document

    monkeypatch.setattr(
        service,
        "parse_uploads",
        lambda files, config, on_progress=None: [
            Document(source=name, error="boom: unreadable") for name, _ in files
        ],
    )
    parse_examples(app, "invoice_digital.pdf")
    assert not app.exception
    assert any("could not be parsed" in str(e.value) and "boom" in str(e.value) for e in app.error)
