"""The demo config (small CPU host) must parse every example without any heavy engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from doctordoc import parse
from doctordoc.config import Config
from doctordoc.router import Router

ROOT = Path(__file__).resolve().parent.parent
HEAVY = {"docling", "paddle_vl", "cloud_vlm"}


@pytest.fixture(scope="module")
def demo() -> Config:
    return Config.from_yaml(ROOT / "doctordoc.demo.yaml")


def test_the_demo_config_uses_no_heavy_engine_and_no_cloud(demo):
    used = {e for chain in [*demo.page_chains.values(), *demo.file_chains.values()] for e in chain}
    assert not used & HEAVY
    assert demo.cloud.enabled is False and demo.max_file_mb <= 10


def test_the_demo_config_covers_every_file_type_the_default_config_does(demo):
    assert set(demo.file_chains) == set(Config().file_chains)
    assert set(demo.page_chains) == set(Config().page_chains)


@pytest.mark.parametrize(
    "name", ["invoice_digital.pdf", "invoice_scanned.pdf", "quotation.docx", "line_items.xlsx"]
)
def test_every_example_parses_with_the_demo_config(demo, name):
    if name == "invoice_scanned.pdf" and not Router(demo).engines["rapidocr"].is_available():
        pytest.skip("rapidocr is not installed")
    doc = parse(ROOT / "examples" / name, demo)
    assert doc.error is None
    assert "Riverview" in doc.text
    assert {e for p in doc.pages if p.engine for e in p.engine.split("+")} - HEAVY
