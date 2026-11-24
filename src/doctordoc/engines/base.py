"""The interface every engine follows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from doctordoc.config import Config
from doctordoc.models import Page, PageKind


class EngineError(Exception):
    """An engine ran but could not process the input. The router logs it and moves on."""


class Engine(ABC):
    """One way of turning a file (or some of its pages) into Pages.

    The router never crashes because of an engine: a missing library, an error or a
    timeout all become a line in the decision log, then the next engine is tried.
    """

    name: ClassVar[str]
    license: ClassVar[str]
    extra: ClassVar[str] = ""  # the pip extra that installs it, e.g. "ocr"
    heavy: ClassVar[bool] = False  # True if it loads a big model (only one heavy model at a time)

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()

    @abstractmethod
    def missing_reason(self) -> str | None:
        """None if the engine is ready to use, else a short reason why it is not."""

    def is_available(self) -> bool:
        return self.missing_reason() is None

    @abstractmethod
    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        """Can this engine handle this file type (and this kind of PDF page)?"""

    @abstractmethod
    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        """Turn the file into Pages.

        For PDFs, `pages` is the list of 1-based page numbers to process, and the returned
        pages keep those numbers. For other files `pages` is None.
        """

    def release(self) -> None:  # noqa: B027  (intentionally not abstract)
        """Free any loaded model (RAM / VRAM). Default: nothing to free."""

    @property
    def install_hint(self) -> str:
        return f'pip install "doctordoc[{self.extra}]"' if self.extra else ""

    @staticmethod
    def module_missing(*modules: str) -> str | None:
        """Helper: return a reason string if any module cannot be found, without importing it."""
        import importlib.util

        for module in modules:
            try:
                found = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                found = False
            if not found:
                return f"python package '{module}' is not installed"
        return None
