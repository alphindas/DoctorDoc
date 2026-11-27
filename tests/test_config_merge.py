"""The tables of settings: a partial override changes only the entries it names."""

import json
from pathlib import Path

import pytest
import yaml

import samples
from doctordoc import parse
from doctordoc.config import DEFAULT_FILE_CHAINS, DEFAULT_PAGE_CHAINS, DEFAULT_TIMEOUTS, Config
from doctordoc.engines.ocr_rapid import RapidOcrEngine
from doctordoc.engines.ocr_tesseract import TesseractEngine
from doctordoc.service import config_from_json, config_to_json

SCANNED = Path(__file__).resolve().parent.parent / "examples" / "invoice_scanned.pdf"


@pytest.fixture(autouse=True)
def no_outside_settings(monkeypatch):
    names = ["PAGE_CHAINS", "PAGE_CHAINS__DIGITAL", "PAGE_CHAINS__SCANNED", "TIMEOUTS"]
    for name in [*names, "FILE_CHAINS__CSV", "FILE_CHAINS__DOCX"]:
        monkeypatch.delenv(f"DOCTORDOC_{name}", raising=False)


def from_file(tmp_path, data):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(data))
    return Config.from_yaml(path)


def with_variable(monkeypatch, name, value):
    monkeypatch.setenv(f"DOCTORDOC_{name}", value if isinstance(value, str) else json.dumps(value))


def check_scanned_example_still_parses(config):
    if RapidOcrEngine().missing_reason() and TesseractEngine().missing_reason():
        pytest.skip("no OCR engine is installed")
    config.gpu = "off"
    assert config.page_chains["scanned"] == DEFAULT_PAGE_CHAINS["scanned"]
    doc = parse(SCANNED, config)
    assert doc.error is None
    assert "RI-2025-1047" in doc.text
    assert doc.pages[0].engine in ("rapidocr", "tesseract")


def test_one_chain_from_a_variable_leaves_the_scanned_example_working(monkeypatch):
    with_variable(monkeypatch, "PAGE_CHAINS__DIGITAL", ["pdf_text"])
    config = Config()
    assert config.page_chains == {**DEFAULT_PAGE_CHAINS, "digital": ["pdf_text"]}
    check_scanned_example_still_parses(config)


def test_one_chain_from_a_file_leaves_the_scanned_example_working(tmp_path):
    config = from_file(tmp_path, {"page_chains": {"digital": ["pdf_text"]}})
    assert config.page_chains == {**DEFAULT_PAGE_CHAINS, "digital": ["pdf_text"]}
    check_scanned_example_still_parses(config)


@pytest.mark.parametrize("source", ["file", "variable", "whole table in a variable"])
def test_an_empty_list_still_means_no_engines(tmp_path, monkeypatch, source):
    if source == "file":
        config = from_file(tmp_path, {"page_chains": {"scanned": []}})
    else:
        if source == "variable":
            with_variable(monkeypatch, "PAGE_CHAINS__SCANNED", [])
        else:
            with_variable(monkeypatch, "PAGE_CHAINS", {"scanned": []})
        config = Config()
    assert config.page_chains == {**DEFAULT_PAGE_CHAINS, "scanned": []}
    config.gpu = "off"
    doc = parse(SCANNED, config)
    assert doc.text == "" and [d.engine for d in doc.decisions] == ["triage"]  # none tried


@pytest.mark.parametrize("table", ["page_chains", "file_chains", "timeouts"])
def test_an_empty_table_changes_nothing(tmp_path, table):
    assert getattr(from_file(tmp_path, {table: {}}), table) == getattr(Config(), table)


def test_one_timeout_leaves_the_others_at_their_defaults(tmp_path, monkeypatch):
    assert from_file(tmp_path, {"timeouts": {"docling": 11}}).timeouts == {
        **DEFAULT_TIMEOUTS,
        "docling": 11,
    }
    with_variable(monkeypatch, "TIMEOUTS__RAPIDOCR", "22")
    assert Config().timeouts == {**DEFAULT_TIMEOUTS, "rapidocr": 22}


def test_file_and_variable_add_up_and_the_file_wins_on_the_same_entry(tmp_path, monkeypatch):
    with_variable(monkeypatch, "TIMEOUTS__DOCLING", "5")
    with_variable(monkeypatch, "TIMEOUTS__RAPIDOCR", "22")
    config = from_file(tmp_path, {"timeouts": {"docling": 11}})
    assert config.timeouts == {**DEFAULT_TIMEOUTS, "docling": 11, "rapidocr": 22}


def test_settings_passed_in_code_are_merged_too():
    config = Config(page_chains={"digital": ["pdf_text"]})
    assert config.page_chains == {**DEFAULT_PAGE_CHAINS, "digital": ["pdf_text"]}


def test_changing_a_merged_config_does_not_change_the_defaults():
    before = {kind: list(chain) for kind, chain in DEFAULT_PAGE_CHAINS.items()}
    config = Config(page_chains={"digital": ["pdf_text"]}, timeouts={"docling": 1})
    config.page_chains["scanned"].append("extra")
    config.timeouts["rapidocr"] = 1
    assert DEFAULT_PAGE_CHAINS == before and DEFAULT_TIMEOUTS["rapidocr"] == 90


def test_the_round_trip_the_ui_uses_keeps_the_tables():
    config = Config(page_chains={"scanned": []}, timeouts={"docling": 1})
    again = config_from_json(config_to_json(config))
    assert (again.page_chains, again.timeouts) == (config.page_chains, config.timeouts)


# ---------------------------------------------------------------------------- file_chains


def test_one_file_chain_leaves_the_other_file_types_working(tmp_path, monkeypatch):
    from_a_file = from_file(tmp_path, {"file_chains": {"csv": ["native"]}})
    with_variable(monkeypatch, "FILE_CHAINS__CSV", ["native"])
    for config in (from_a_file, Config()):
        assert config.file_chains == {**DEFAULT_FILE_CHAINS, "csv": ["native"]}
        config.gpu = "off"
        doc = parse(samples.docx_bytes(), config, filename="report.docx")
        assert "Daily Report" in doc.text and doc.warnings == []


@pytest.mark.parametrize("source", ["file", "variable"])
def test_an_empty_file_chain_still_means_no_engines(tmp_path, monkeypatch, source):
    if source == "file":
        config = from_file(tmp_path, {"file_chains": {"docx": []}})
    else:
        with_variable(monkeypatch, "FILE_CHAINS__DOCX", [])
        config = Config()
    assert config.file_chains == {**DEFAULT_FILE_CHAINS, "docx": []}
    doc = parse(samples.docx_bytes(), config, filename="report.docx")
    assert doc.text == "" and doc.warnings == ["No engine chain is configured for 'docx'."]


def test_changing_one_merged_file_chain_changes_no_other_and_no_default():
    before = {name: list(chain) for name, chain in DEFAULT_FILE_CHAINS.items()}
    config = Config(file_chains={"csv": ["native"]})
    config.file_chains["docx"].append("extra")  # docx and pptx share one default list
    assert config.file_chains["pptx"] == before["pptx"]
    assert DEFAULT_FILE_CHAINS == before
