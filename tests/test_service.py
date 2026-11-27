"""Tests for doctordoc.service: settings, temp-folder parsing, engine comparison, explanations."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import samples
from doctordoc import service
from doctordoc.config import Config
from doctordoc.detect import detect
from doctordoc.models import Document, Page, PageKind
from doctordoc.router import Router
from doctordoc.service import UiSettings, apply_settings
from fakes import FakeEngine


def test_apply_settings_removes_switched_off_engines_and_keeps_the_base_untouched():
    base = Config()
    config = apply_settings(base, UiSettings(disabled_engines={"docling", "tesseract"}))
    for chain in [*config.page_chains.values(), *config.file_chains.values()]:
        assert "docling" not in chain and "tesseract" not in chain
    assert "docling" in base.page_chains["digital"]  # the original was not changed


def test_local_only_switches_the_cloud_off_everywhere():
    config = apply_settings(Config(), UiSettings(local_only=True))
    assert config.cloud.enabled is False
    assert all("cloud_vlm" not in chain for chain in config.page_chains.values())
    assert all("cloud_vlm" not in chain for chain in config.file_chains.values())


def test_cloud_is_allowed_only_when_local_only_is_off():
    config = apply_settings(Config(), UiSettings(local_only=False))
    assert config.cloud.enabled is True
    assert "cloud_vlm" in config.page_chains["scanned"]


def test_forcing_one_ocr_engine_changes_scanned_garbled_and_image_chains():
    config = apply_settings(Config(), UiSettings(forced_ocr="rapidocr"))
    assert config.page_chains["scanned"] == ["rapidocr"]
    assert config.page_chains["garbled"] == ["rapidocr", "pdf_text"]
    assert config.file_chains["png"] == ["rapidocr"]
    assert config.page_chains["digital"] == ["docling", "pdf_text"]  # digital pages unchanged


def test_settings_carry_the_model_and_merge_switch():
    config = apply_settings(Config(), UiSettings(llm_model="qwen2.5:7b", merge_mixed_pages=False))
    assert config.llm.model == "qwen2.5:7b" and config.merge_mixed_pages is False


def test_config_survives_a_json_round_trip_for_cache_keys():
    config = apply_settings(Config(), UiSettings(forced_ocr="tesseract"))
    again = service.config_from_json(service.config_to_json(config))
    assert again.page_chains == config.page_chains


def test_engines_enabled_drops_off_engines_and_cloud_when_local():
    names = ["pdf_text", "docling", "cloud_vlm"]
    assert service.engines_enabled(names, UiSettings(disabled_engines={"docling"})) == ["pdf_text"]
    assert service.engines_enabled(names, UiSettings(local_only=False)) == [
        "pdf_text",
        "docling",
        "cloud_vlm",
    ]


# ---------------------------------------------------------------------------- parse_uploads


def test_parse_uploads_uses_a_temp_folder_and_deletes_it(config, monkeypatch):
    seen: list[Path] = []
    real_parse = service.parse

    def spy(path, *args, **kwargs):
        seen.append(Path(path))
        assert Path(path).exists()  # the file is really on disk while parsing
        return real_parse(path, *args, **kwargs)

    monkeypatch.setattr(service, "parse", spy)
    docs = service.parse_uploads([("inv.pdf", samples.digital_pdf())], config)
    assert docs[0].error is None and docs[0].source == "inv.pdf"
    assert seen and not seen[0].exists() and not seen[0].parent.exists()  # nothing left behind


def test_a_bad_file_does_not_stop_the_others(config):
    files = [
        ("empty.pdf", b""),
        ("locked.pdf", samples.digital_pdf(encrypt="pw")),
        ("good.pdf", samples.digital_pdf()),
    ]
    docs = service.parse_uploads(files, config)
    assert [bool(d.error) for d in docs] == [True, True, False]
    assert "password" in (docs[1].error or "").lower()


def test_files_over_the_size_limit_become_an_error_document(config):
    config.max_file_mb = 0.001  # about 1 KB
    docs = service.parse_uploads([("big.pdf", samples.digital_pdf())], config)
    assert docs[0].error and "too big" in docs[0].error.lower()


def test_progress_is_reported_for_every_file(config):
    calls: list[tuple[int, int, str]] = []
    service.parse_uploads(
        [("a.txt", b"hello world, this is a note"), ("b.txt", b"another small note here")],
        config,
        on_progress=lambda done, total, name: calls.append((done, total, name)),
    )
    assert calls[0] == (0, 2, "a.txt") and calls[-1][:2] == (2, 2)


def test_member_files_returns_the_bytes_inside_a_zip(config, tmp_path):
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("folder/a.txt", "alpha note text")
    members = service.member_files(path.read_bytes(), "zip", config)
    assert members == {"a.txt": b"alpha note text"}


# ---------------------------------------------------------------------------- page pictures


def test_render_page_png_draws_a_pdf_page_and_skips_other_types():
    png = service.render_page_png(samples.digital_pdf(), "pdf", 1)
    assert png is not None and png.startswith(b"\x89PNG")
    assert service.render_page_png(b"x", "docx", 1) is None
    assert service.render_page_png(b"not a pdf", "pdf", 1) is None  # never raises


# ---------------------------------------------------------------------------- compare engines


def _router(**engines: FakeEngine) -> Router:
    return Router(Config(gpu="off"), engines=engines)


def test_run_engine_reports_quality_time_and_pages():
    data = samples.digital_pdf()
    router = _router(good=FakeEngine("good", delay=0.02))
    run = router.run_engine("good", data, detect(data, "x.pdf"))
    assert run.status == "ok" and run.quality and run.quality > 0.5
    assert run.seconds >= 0.02 and run.pages and run.chars > 0


def test_run_engine_names_the_problem_when_an_engine_fails_or_is_missing():
    data = samples.digital_pdf()
    info = detect(data, "x.pdf")
    router = _router(
        broken=FakeEngine("broken", error=RuntimeError("boom")),
        gone=FakeEngine("gone", available=False),
        empty=FakeEngine("empty", text=""),
    )
    assert router.run_engine("broken", data, info).status == "failed"
    assert "boom" in router.run_engine("broken", data, info).reason
    assert router.run_engine("gone", data, info).status == "skipped"
    assert router.run_engine("empty", data, info).status == "empty"
    assert router.run_engine("nope", data, info).status == "skipped"


def test_runnable_engines_lists_only_installed_engines_that_can_read_the_file():
    data = samples.digital_pdf()
    router = _router(
        ok=FakeEngine("ok"),
        gone=FakeEngine("gone", available=False),
        scanned_only=FakeEngine("scanned_only", kinds=[PageKind.SCANNED]),
        digital_only=FakeEngine("digital_only", kinds=[PageKind.DIGITAL]),
    )
    assert router.runnable_engines(detect(data, "x.pdf")) == ["ok", "scanned_only", "digital_only"]


def test_chosen_engines_splits_merged_engine_names():
    doc = Document(source="x", pages=[Page(number=1, engine="docling+rapidocr")])
    assert service.chosen_engines(doc) == {"docling", "rapidocr"}


# ---------------------------------------------------------------------------- explanations


def test_explain_routing_says_which_engine_won_and_what_was_skipped(config):
    doc = service.parse_uploads([("scan.pdf", samples.scanned_pdf())], config)[0]
    text = service.explain_routing(doc, config)
    assert "scanned" in text and "rapidocr" in text or "tesseract" in text


def test_explain_routing_handles_errors_and_empty_documents():
    assert "could not be parsed" in service.explain_routing(Document(source="x", error="bad"))
    assert "No pages" in service.explain_routing(Document(source="x"))


def test_split_warnings_picks_out_the_wrong_extension_warning():
    doc = Document(source="x", warnings=["Extension '.pdf' says pdf, but it is docx.", "other"])
    wrong, other = service.split_warnings(doc)
    assert len(wrong) == 1 and other == ["other"]


def test_file_too_big_message():
    assert service.file_too_big(10, Config()) is None
    assert "limit" in (service.file_too_big(300 * 1024 * 1024, Config()) or "")


# ---------------------------------------------------------------------------- privacy words


def test_the_cloud_note_says_a_missing_key_does_not_protect():
    assert service.CLOUD_PRIVACY_NOTE == (
        'Pages can be sent to the cloud server whenever "Local only" is off, even when no API key '
        'is set. Turning "Local only" on is what keeps pages on this computer.'
    )


def test_local_only_off_turns_the_cloud_engine_on_even_without_a_key_or_cloud_enabled():
    base = Config(cloud={"enabled": False, "api_key": ""})
    for local_only, enabled in [(True, False), (False, True)]:
        config = service.apply_settings(base, service.UiSettings(local_only=local_only))
        assert config.cloud.enabled is enabled
        engine = Router(config).engines["cloud_vlm"]
        assert (engine.missing_reason() is None) is enabled  # the key plays no part


def test_an_empty_cloud_address_keeps_the_cloud_off_whatever_the_switch_says():
    """The advice in docs/deploy-demo.md."""
    base = Config(cloud={"base_url": ""})
    config = service.apply_settings(base, service.UiSettings(local_only=False))
    assert Router(config).engines["cloud_vlm"].missing_reason() is not None


@pytest.mark.parametrize(
    "base_url, remote",
    [
        ("http://localhost:11434", False),
        ("http://127.0.0.1:11434", False),
        ("http://[::1]:8000/v1", False),
        ("https://api.openai.com/v1", True),
        ("http://192.168.1.20:11434", True),
        ("http://localhost.example.com", True),
        ("http://127.0.0.1@models.example.com", True),
        ("http://[broken", True),
        ("not an address", True),
    ],
)
def test_the_model_server_note_appears_unless_the_server_is_surely_local(base_url, remote):
    config = Config(llm={"base_url": base_url})
    assert (service.llm_privacy_note(config) == service.REMOTE_LLM_NOTE) is remote
