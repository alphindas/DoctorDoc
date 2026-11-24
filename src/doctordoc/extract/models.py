"""Result types for field extraction."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from doctordoc.models import BBox

MatchKind = Literal["exact", "normalized", "fuzzy", "inferred", "none"]


class Source(BaseModel):
    """Proof of where a value came from: the page and the exact text span."""

    page: int
    start: int  # character offsets inside Page.text
    end: int
    text: str  # the exact text found there
    match: MatchKind
    block_index: int | None = None  # which block of the page holds it
    bbox: BBox | None = None  # that block's box on the page, if the engine gave one


class Issue(BaseModel):
    """A problem found by a validation rule."""

    path: str  # e.g. "total" or "line_items[2].amount"
    rule: str
    message: str
    severity: Literal["error", "warning"] = "error"
    related: list[str] = Field(default_factory=list)  # other fields involved in the problem


class FieldResult(BaseModel):
    path: str
    value: Any
    grounded: bool  # was the value found in the source text?
    source: Source | None = None
    grounding_score: float = 0.0
    ocr_confidence: float = 1.0  # 1.0 for digital text
    validation_score: float = 1.0
    confidence: float = 0.0
    issues: list[Issue] = Field(default_factory=list)
    needs_review: bool = False


class ExtractionResult(BaseModel):
    schema_name: str
    data: dict[str, Any] = Field(default_factory=dict)  # the validated values
    fields: list[FieldResult] = Field(default_factory=list)
    needs_review: list[str] = Field(default_factory=list)  # paths a human should check
    issues: list[Issue] = Field(default_factory=list)  # every validation problem
    model: str = ""
    attempts: int = 0  # LLM calls made (retries included)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    def field(self, path: str) -> FieldResult | None:
        return next((f for f in self.fields if f.path == path), None)

    def to_json(self, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)
