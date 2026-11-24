from __future__ import annotations

import pytest

import samples
from doctordoc.config import Config


@pytest.fixture
def config() -> Config:
    """A config that never loads heavy models, so unit tests stay fast and offline."""
    cfg = Config(gpu="off")
    cfg.page_chains = {
        "digital": ["pdf_text"],
        "mixed": ["pdf_text"],
        "scanned": ["rapidocr", "tesseract"],
        "garbled": ["rapidocr", "tesseract", "pdf_text"],
    }
    cfg.file_chains = {
        **cfg.file_chains,
        "docx": ["native"],
        "pptx": ["native"],
        "html": ["native"],
    }
    return cfg


@pytest.fixture
def invoice_pdf() -> bytes:
    return samples.digital_pdf()
