"""The warning given at startup when images of pages could be sent to the cloud server.

No test here sends anything: the cloud call is replaced by a recorder, and the server address
points at a closed port.
"""

import io
import logging

import pytest
import yaml
from PIL import Image
from typer.testing import CliRunner

import samples
from doctordoc import parse
from doctordoc.cli import app as cli_app
from doctordoc.cloud_warning import cloud_exposure, cloud_warning
from doctordoc.config import DEFAULT_FILE_CHAINS, CloudConfig, Config
from doctordoc.engines.cloud_vlm import CloudVlmEngine

PAGE_KINDS = ("digital", "mixed", "scanned", "garbled")
IMAGES = ["png", "jpg", "tiff", "bmp", "gif", "webp"]
CLOUD_ON = {"enabled": True, "base_url": "http://user:secret@127.0.0.1:9/v1"}


def config(page=None, files=None, cloud=CLOUD_ON):
    """Cloud settings, with every chain empty except the ones given."""
    return Config(
        gpu="off",
        cloud=CloudConfig(**cloud),
        page_chains={**{k: [] for k in PAGE_KINDS}, **(page or {})},
        file_chains={**{t: [] for t in DEFAULT_FILE_CHAINS}, **(files or {})},
    )


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 80), "white").save(buffer, "PNG")
    return buffer.getvalue()


DOCUMENTS = {
    "digital": (samples.digital_pdf, "d.pdf"),
    "mixed": (samples.mixed_pdf, "m.pdf"),
    "scanned": (samples.scanned_pdf, "s.pdf"),
    "garbled": (samples.garbled_pdf, "g.pdf"),
    "png": (png, "i.png"),
    "docx": (samples.docx_bytes, "a.docx"),
}


def really_sent(monkeypatch, settings):
    """Which of the test documents the router actually hands to the cloud engine."""
    calls = []

    def record(self, image_b64):
        calls.append(1)
        return "cloud text"

    monkeypatch.setattr(CloudVlmEngine, "_ask", record)
    sent = set()
    for label, (make, name) in DOCUMENTS.items():
        calls.clear()
        parse(make(), settings, filename=name)
        if calls:
            sent.add(label)
    return sent


CLOUD = ["cloud_vlm"]


@pytest.mark.parametrize(
    "page, files",
    [
        ({}, {}),
        ({"digital": CLOUD}, {}),
        ({"mixed": CLOUD}, {}),
        ({"scanned": CLOUD}, {}),  # also reaches mixed pages
        ({"garbled": CLOUD}, {}),
        ({}, {"png": CLOUD}),
        ({}, {"docx": CLOUD}),
        ({k: CLOUD for k in PAGE_KINDS}, {"png": CLOUD, "docx": CLOUD}),
    ],
    ids=["none", "digital", "mixed", "scanned", "garbled", "image", "docx", "all"],
)
def test_the_warning_lists_exactly_what_the_router_really_sends(monkeypatch, page, files):
    settings = config(page, files)
    kinds, file_types = cloud_exposure(settings)
    assert set(kinds) | set(file_types) == really_sent(monkeypatch, settings)


def test_defaults_with_cloud_on_list_page_kinds_and_image_files():
    assert cloud_exposure(Config(cloud=CloudConfig(**CLOUD_ON))) == (
        ["mixed", "scanned", "garbled"],
        IMAGES,
    )


def test_a_partial_override_still_warns_about_the_merged_defaults():
    settings = Config(cloud=CloudConfig(**CLOUD_ON), page_chains={"digital": ["pdf_text"]})
    message = cloud_warning(settings)
    assert "page kinds mixed, scanned, garbled" in message
    assert "image files png, jpg, tiff, bmp, gif, webp" in message


def test_the_message_says_how_to_prevent_it_and_hides_the_address():
    message = cloud_warning(Config(cloud=CloudConfig(**CLOUD_ON)))
    for words in ("could be sent to the cloud server", "cloud.enabled: false", "`kind: []`"):
        assert words in message, words
    assert (
        "Mixed pages also use the scanned chain, so `mixed: []` alone does not stop them" in message
    )
    assert "page_chains" in message and "file_chains" in message
    assert "secret" not in message and "127.0.0.1" not in message  # an address can hold a key
    assert "\n" not in message


def test_following_the_advice_stops_the_warning_and_the_sending(monkeypatch):
    default = Config(gpu="off", cloud=CloudConfig(**CLOUD_ON))
    kinds, file_types = cloud_exposure(default)
    advised = Config(
        gpu="off",
        cloud=CloudConfig(**CLOUD_ON),
        page_chains={kind: [] for kind in kinds},
        file_chains={name: [] for name in file_types},
    )
    assert cloud_warning(advised) is None
    assert really_sent(monkeypatch, advised) == set()


@pytest.mark.parametrize(
    "cloud",
    [{"enabled": False}, {**CLOUD_ON, "base_url": ""}, {**CLOUD_ON, "model": ""}],
    ids=["off", "no address", "no model"],
)
def test_no_warning_when_nothing_can_be_sent(cloud):
    assert cloud_warning(Config(cloud=CloudConfig(**cloud))) is None


def test_a_missing_api_key_does_not_silence_the_warning():
    # the engine does not need a key to send a page, so the warning must not need one either
    assert cloud_warning(Config(cloud=CloudConfig(**CLOUD_ON, api_key="")))


def test_the_warning_changes_no_setting():
    settings = Config(cloud=CloudConfig(**CLOUD_ON))
    before = settings.model_dump()
    cloud_warning(settings)
    assert settings.model_dump() == before


# ---------------------------------------------------------------------------- at startup


@pytest.fixture
def cloud_file(tmp_path):
    path = tmp_path / "cloud.yaml"
    path.write_text(yaml.safe_dump({"gpu": "off", "cloud": CLOUD_ON}))
    return str(path)


def warnings_logged(caplog):
    return [r for r in caplog.records if "Cloud fallback is ON" in r.getMessage()]


def run_cli(caplog, *arguments):
    with caplog.at_level(logging.WARNING):
        result = CliRunner().invoke(cli_app, [str(a) for a in arguments])
    return result, warnings_logged(caplog)


def test_parse_warns_once_for_a_whole_folder(tmp_path, caplog, cloud_file):
    folder = tmp_path / "in"
    folder.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (folder / name).write_text("some plain words")
    result, warned = run_cli(caplog, "parse", folder, "-c", cloud_file)
    assert result.exit_code == 0, result.output
    assert len(warned) == 1 and warned[0].levelno == logging.WARNING


def test_extract_warns_once(tmp_path, caplog, cloud_file):
    path = tmp_path / "a.txt"
    path.write_text("some plain words")
    arguments = ("extract", path, "-f", "json", "--base-url", "http://127.0.0.1:9", "-c")
    _, warned = run_cli(caplog, *arguments, cloud_file)
    assert len(warned) == 1


@pytest.mark.parametrize("command", ["engines", "triage"])
def test_commands_that_send_nothing_do_not_warn(tmp_path, caplog, cloud_file, command):
    path = tmp_path / "a.pdf"
    path.write_bytes(samples.digital_pdf())
    extra = [path] if command == "triage" else []
    result, warned = run_cli(caplog, command, *extra, "-c", cloud_file)
    assert result.exit_code == 0 and warned == []


def test_the_ui_command_warns_and_says_when_it_applies(monkeypatch, caplog, cloud_file):
    pytest.importorskip("streamlit")
    import subprocess

    monkeypatch.setattr(subprocess, "call", lambda *a, **k: 0)
    result, warned = run_cli(caplog, "ui", "-c", cloud_file)
    assert result.exit_code == 0 and len(warned) == 1
    assert 'the "Local only" switch decides this, not cloud.enabled' in warned[0].getMessage()
    assert "this computer" not in warned[0].getMessage()  # the switch does not control Extract


def test_the_api_warns_once_when_it_is_built_not_per_request(caplog):
    pytest.importorskip("fastapi")
    pytest.importorskip("python_multipart")
    from fastapi.testclient import TestClient

    from doctordoc.api import create_app

    with caplog.at_level(logging.WARNING):
        app = create_app(Config(gpu="off", cloud=CloudConfig(**CLOUD_ON)))
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        for _ in range(3):
            reply = client.post("/parse", files={"file": ("a.txt", b"some plain words")})
            assert reply.status_code == 200
    assert len(warnings_logged(caplog)) == 1


def test_no_warning_at_startup_with_the_cloud_off(tmp_path, caplog):
    path = tmp_path / "a.txt"
    path.write_text("some plain words")
    result, warned = run_cli(caplog, "parse", path, "--no-gpu")
    assert result.exit_code == 0 and warned == []


def test_the_compare_tool_sends_pages_whatever_the_chains_say(monkeypatch):
    """Why the web UI's warning has a note about its Compare engines tab."""
    from doctordoc import service

    calls = []
    monkeypatch.setattr(CloudVlmEngine, "_ask", lambda self, image: calls.append(1) or "text")
    nothing = config()  # cloud on, every chain empty
    assert cloud_warning(nothing) is None
    data = samples.digital_pdf()
    assert "cloud_vlm" in service.comparable_engines(data, "d.pdf", nothing)
    service.run_one_engine(data, "d.pdf", nothing, "cloud_vlm")
    assert calls
