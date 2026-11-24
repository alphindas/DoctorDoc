"""Field extraction (phase 2): fill a Pydantic schema from a document, with proof and checks."""

from doctordoc.extract.extractor import extract, resolve_schema
from doctordoc.extract.llm import LLMClient, LLMError
from doctordoc.extract.models import ExtractionResult, FieldResult, Issue, Source
from doctordoc.extract.schemas import (
    BUILTIN_SCHEMAS,
    DailyDrillingReport,
    GenericForm,
    Invoice,
    Receipt,
)
from doctordoc.extract.validation import (
    Rule,
    date_order,
    in_range,
    matches,
    required,
    row_product,
    rule,
    sum_equals,
    sum_of_fields,
)

__all__ = [
    "BUILTIN_SCHEMAS",
    "DailyDrillingReport",
    "ExtractionResult",
    "FieldResult",
    "GenericForm",
    "Invoice",
    "Issue",
    "LLMClient",
    "LLMError",
    "Receipt",
    "Rule",
    "Source",
    "date_order",
    "extract",
    "in_range",
    "matches",
    "required",
    "resolve_schema",
    "row_product",
    "rule",
    "sum_equals",
    "sum_of_fields",
]
