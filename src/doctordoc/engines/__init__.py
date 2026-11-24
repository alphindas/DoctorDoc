"""All engines, plus a helper to build them."""

from __future__ import annotations

from doctordoc.config import Config
from doctordoc.engines.base import Engine, EngineError

__all__ = ["Engine", "EngineError", "build_engines", "engine_classes"]


def engine_classes() -> list[type[Engine]]:
    """Every engine class we know. Importing them is cheap: heavy libraries load lazily."""
    from doctordoc.engines.cloud_vlm import CloudVlmEngine
    from doctordoc.engines.docling_engine import DoclingEngine
    from doctordoc.engines.markitdown_engine import MarkItDownEngine
    from doctordoc.engines.native import NativeEngine
    from doctordoc.engines.ocr_paddle import PaddleVlEngine
    from doctordoc.engines.ocr_rapid import RapidOcrEngine
    from doctordoc.engines.ocr_tesseract import TesseractEngine
    from doctordoc.engines.pdf_text import PdfTextEngine

    return [
        NativeEngine,
        PdfTextEngine,
        DoclingEngine,
        MarkItDownEngine,
        PaddleVlEngine,
        RapidOcrEngine,
        TesseractEngine,
        CloudVlmEngine,
    ]


def build_engines(config: Config) -> dict[str, Engine]:
    """Create one instance of each engine, keyed by name."""
    return {cls.name: cls(config) for cls in engine_classes()}
