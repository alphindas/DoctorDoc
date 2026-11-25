"""Field extraction: document -> LLM -> validated data, with proof of where each value came from.

document text --(split by page if long)--> LLM (JSON schema) --> Pydantic validation
    --> grounding (find each value in the text) --> rules (totals, dates ...)
    --> confidence per field --> needs_review list
"""

from __future__ import annotations

import importlib
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from doctordoc.config import Config
from doctordoc.extract.grounding import Grounding, PageText, ground_value
from doctordoc.extract.llm import LLMClient, LLMError, LLMPromptTooLong
from doctordoc.extract.models import ExtractionResult, FieldResult, Issue
from doctordoc.extract.schemas import BUILTIN_SCHEMAS
from doctordoc.extract.validation import Rule, run_rules
from doctordoc.models import Document

SYSTEM_PROMPT = """You are a precise data-extraction engine. \
Read the document text and fill a JSON object that follows the schema.
Rules:
- Copy values exactly as they are written in the document (same spelling and numbers).
- Numbers are plain numbers: no currency symbols, no thousands separators.
- Dates are written as YYYY-MM-DD.
- If a value is not in the document, use null. Never guess and never invent values.
- For lists, include every item that appears in the document.
- Answer with the JSON object only.

JSON schema:
{schema}"""

USER_PROMPT = "Document text:\n<<<\n{text}\n>>>\n\nReturn the JSON object."
RETRY_PROMPT = "That answer was not valid: {problem}\nReturn a corrected JSON object only."
MAX_SALVAGE_PASSES = 6
_SCALARS = (str, int, float, bool, date, datetime, Decimal)


# ---------------------------------------------------------------------------- public


def extract(
    source: Document | Path | bytes | str,
    schema: type[BaseModel] | str,
    config: Config | None = None,
    *,
    rules: list[Rule] | None = None,
    llm: LLMClient | None = None,
) -> ExtractionResult:
    """Extract the fields of `schema` from a document.

    `source` can be a parsed Document, a file path (Path) or file bytes (both are parsed
    first), or a str, which is treated as the document text itself.
    `schema` is a Pydantic model class, a built-in name ("invoice", "receipt", "drilling",
    "form"), or "package.module:ClassName".
    Never raises for bad documents or LLM trouble: the problem is put in result.error.
    """
    config = config or Config()
    started = time.perf_counter()
    result = ExtractionResult(schema_name="?", model=config.llm.model)
    try:
        model_class = resolve_schema(schema)
        result.schema_name = model_class.__name__
        pages = _pages_of(source, config, result)
        if not any(p.text.strip() for p in pages):
            result.error = result.error or "The document has no text to extract from."
            return result
        client = llm or LLMClient(config.llm)
        first_call = len(client.calls)  # a shared client may hold earlier documents' calls
        try:
            _run(model_class, pages, config, client, rules or [], result)
        finally:
            _record_calls(result, client.calls[first_call:])  # also when extraction failed
            if llm is None:
                client.close()
    except LLMError as err:
        result.error = str(err)
    except Exception as err:  # the promise: extraction never raises
        result.error = f"{type(err).__name__}: {err}"
    result.seconds = round(time.perf_counter() - started, 2)
    return result


def resolve_schema(schema: type[BaseModel] | str) -> type[BaseModel]:
    """A class stays a class; a name is looked up in the built-ins or imported."""
    if isinstance(schema, str):
        if schema in BUILTIN_SCHEMAS:
            return BUILTIN_SCHEMAS[schema]
        if ":" in schema:
            module_name, class_name = schema.split(":", 1)
            return getattr(importlib.import_module(module_name), class_name)  # type: ignore[no-any-return]
        raise ValueError(f"Unknown schema '{schema}'. Built-ins: {', '.join(BUILTIN_SCHEMAS)}")
    return schema


# ---------------------------------------------------------------------------- steps


def _pages_of(
    source: Document | Path | bytes | str, config: Config, result: ExtractionResult
) -> list[PageText]:
    if isinstance(source, str):
        return [PageText.from_text(source)]
    if isinstance(source, Document):
        document = source
    else:
        from doctordoc.pipeline import parse

        document = parse(source, config)
    if document.error:
        result.error = f"The document could not be parsed: {document.error}"
    result.warnings.extend(document.warnings)
    return [PageText.from_page(p) for p in document.pages if p.text.strip()]


def _record_calls(result: ExtractionResult, calls: list[dict[str, Any]]) -> None:
    """Keep the token trace on the result, and show any prompt size problem as a warning."""
    result.llm_calls = calls
    for problem in dict.fromkeys(p for call in calls for p in call["problems"]):
        result.warnings.append(f"Prompt size: {problem}")


def _run(
    schema: type[BaseModel],
    pages: list[PageText],
    config: Config,
    client: LLMClient,
    extra_rules: list[Rule],
    result: ExtractionResult,
) -> None:
    chunks = _split_into_chunks(pages, config.llm.max_chars_per_call)
    # one time budget for the whole document, however many calls and retries it takes
    deadline = time.monotonic() + config.llm.max_seconds_per_document
    first_call = len(client.calls)
    merged: dict[str, Any] = {}
    issues: list[Issue] = []
    for chunk in chunks:
        data, attempts, dropped = _ask_model(schema, chunk, config, client, deadline)
        result.attempts += attempts
        issues.extend(dropped)
        _merge_into(merged, data)
    instance = schema.model_validate(merged)
    data = instance.model_dump()

    all_rules = list(getattr(schema, "doctordoc_rules", [])) + extra_rules
    issues.extend(run_rules(all_rules, data))
    result.data = data
    result.fields = _score_fields(data, pages, issues, config.review_threshold)
    # If a sent prompt may have been cut, no field can be trusted: send all of them to review.
    cut = [p for call in client.calls[first_call:] if call["sent"] for p in call["problems"]]
    if cut:
        issues.append(
            Issue(
                path="document",
                rule="prompt_length",
                severity="error",  # "error" puts "document" on the needs_review list
                message="Check every field. " + " ".join(dict.fromkeys(cut)),
            )
        )
        for item in result.fields:
            item.needs_review = True
    result.issues = issues
    result.needs_review = _review_list(result.fields, issues)
    if len(chunks) > 1:
        result.warnings.append(
            f"Long document: asked the model {len(chunks)} times (split by page)."
        )


def _split_into_chunks(pages: list[PageText], max_chars: int) -> list[str]:
    """Text with [Page N] markers, cut into pieces of about max_chars. Pages stay whole when
    possible; a long page is cut at blank lines. A single block longer than max_chars is cut at
    line breaks, and a longer line by characters. (An ordinary piece can still be a few
    characters over: the page marker and joining blank lines are not counted.)"""
    pieces: list[str] = []
    for page in pages:
        marker = f"[Page {page.number}]\n"
        current = ""
        cut_a_block = False
        for paragraph in page.text.split("\n\n"):
            if len(paragraph) > max_chars:
                if current:
                    pieces.append(f"{marker}{current}")
                parts = _split_block(paragraph, max_chars - len(marker))
                pieces.extend(marker + part for part in parts)
                current, cut_a_block = "", True
                continue
            if current and len(current) + len(paragraph) > max_chars:
                pieces.append(f"{marker}{current}")
                current = ""
            current = f"{current}\n\n{paragraph}" if current else paragraph
        if current or not cut_a_block:
            pieces.append(f"{marker}{current}")
    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + len(piece) < max_chars:
            chunks[-1] += "\n\n" + piece
        else:
            chunks.append(piece)
    return chunks


def _split_block(block: str, size: int) -> list[str]:
    """Cut one long block into parts of at most `size` characters: between lines where it can,
    inside a line only if that line alone is too long."""
    size = max(size, 1)
    parts: list[str] = []
    current: str | None = None
    for line in block.split("\n"):
        while len(line) > size:
            if current is not None:
                parts.append(current)
                current = None
            parts.append(line[:size])
            line = line[size:]
        if current is not None and len(current) + 1 + len(line) > size:
            parts.append(current)
            current = None
        current = line if current is None else f"{current}\n{line}"
    if current:
        parts.append(current)
    return parts


def _ask_model(
    schema: type[BaseModel], text: str, config: Config, client: LLMClient, deadline: float
) -> tuple[dict[str, Any], int, list[Issue]]:
    """Ask the LLM; on invalid JSON or invalid values ask again with the error message.
    After the last try, keep whatever is valid and drop the rest (recorded as issues)."""
    json_schema = schema.model_json_schema()
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(schema=json.dumps(json_schema, separators=(",", ":"))),
        },
        {"role": "user", "content": USER_PROMPT.format(text=text)},
    ]
    attempts, last_object, last_error = 0, None, None
    for _ in range(1 + config.llm.max_retries):
        left = deadline - time.monotonic()
        if left <= 0:
            raise LLMError(
                f"Gave up on this document after {config.llm.max_seconds_per_document:.0f} s "
                "of model time (llm.max_seconds_per_document)."
            )
        try:
            reply = client.chat(messages, json_schema, timeout=min(config.llm.timeout, left))
        except LLMPromptTooLong:
            if last_error is None:
                raise
            break  # the retry would not fit: keep what is valid in the earlier answer
        attempts += 1
        try:
            obj = _parse_json_object(reply)
            last_object = obj
            return schema.model_validate(obj).model_dump(mode="json"), attempts, []
        except (ValueError, ValidationError) as err:
            last_error = err
            problem = _describe(err)
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": RETRY_PROMPT.format(problem=problem)},
            ]
    if isinstance(last_error, ValidationError) and isinstance(last_object, dict):
        data, dropped = _drop_invalid(schema, last_object)
        return data, attempts, dropped
    raise ValueError(f"The model never returned usable JSON ({_describe(last_error)})")


def _parse_json_object(reply: str) -> dict[str, Any]:
    """Parse the reply as a JSON object. Tolerates ```json fences around it."""
    text = reply.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    obj = json.loads(fenced.group(1) if fenced else text)
    if not isinstance(obj, dict):
        raise ValueError("the answer must be a JSON object, not a list or a value")
    return obj


def _describe(error: Exception | None) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(f"{_path(e['loc'])}: {e['msg']}" for e in error.errors()[:8])
    return str(error)


def _path(loc: tuple[Any, ...]) -> str:
    """('line_items', 2, 'amount') -> 'line_items[2].amount'"""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _drop_invalid(
    schema: type[BaseModel], obj: dict[str, Any]
) -> tuple[dict[str, Any], list[Issue]]:
    """Remove the values Pydantic rejects (one pass at a time) until the rest validates."""
    issues: list[Issue] = []
    for _ in range(MAX_SALVAGE_PASSES):
        try:
            return schema.model_validate(obj).model_dump(mode="json"), issues
        except ValidationError as err:
            for e in err.errors():
                if e["type"] == "missing":
                    raise ValueError(f"required field missing: {_path(e['loc'])}") from err
                issues.append(
                    Issue(
                        path=_path(e["loc"]),
                        rule="type",
                        severity="error",
                        message=f"model returned {e.get('input')!r}: {e['msg']}",
                    )
                )
                _remove_at(obj, e["loc"])
    raise ValueError("could not repair the model's answer")


def _remove_at(obj: Any, loc: tuple[Any, ...]) -> None:
    """Delete the value at `loc`. A list item is removed, a field falls back to its default."""
    for step in loc[:-1]:
        try:
            obj = obj[step]
        except (KeyError, IndexError, TypeError):
            return
    last = loc[-1]
    if isinstance(obj, dict):
        obj.pop(last, None)
    elif isinstance(obj, list) and isinstance(last, int) and last < len(obj):
        obj[last] = None  # keep list positions stable; None items are dropped below
        obj[:] = [item for item in obj if item is not None]


def _merge_into(target: dict[str, Any], new: dict[str, Any]) -> None:
    """Combine the answers for two chunks: fill gaps, extend lists, never overwrite."""
    for key, value in new.items():
        old = target.get(key)
        if old in (None, "", [], {}):
            target[key] = value
        elif isinstance(old, list) and isinstance(value, list):
            old.extend(item for item in value if item not in old)
        elif isinstance(old, dict) and isinstance(value, dict):
            _merge_into(old, value)


# ---------------------------------------------------------------------------- scoring

# Confidence = grounding x OCR confidence x validation factor.
# Each part can only LOWER the result, so a high score needs all three to be good:
#   grounding   1.0 exact copy, 0.95 same value written differently, ~0.8 fuzzy, 0.25 not found
#   OCR         the engine's own confidence for the text the value was found in (1.0 for digital)
#   validation  1.0 no problem, 0.8 a warning, 0.5 a failed rule
UNGROUNDED_SCORE = 0.25
VALIDATION_FACTOR = {"ok": 1.0, "warning": 0.8, "error": 0.5}


def _score_fields(
    data: dict[str, Any], pages: list[PageText], issues: list[Issue], threshold: float
) -> list[FieldResult]:
    """One FieldResult per extracted value, with grounding and a confidence."""
    results = []
    for path, value in _leaves(data):
        grounding = ground_value(value, pages)
        related = [i for i in issues if i.path == path or path in i.related]
        results.append(_field_result(path, value, grounding, related, threshold))
    return results


def _field_result(
    path: str, value: Any, grounding: Grounding, issues: list[Issue], threshold: float
) -> FieldResult:
    has_error = any(i.severity == "error" for i in issues)
    state = "error" if has_error else "warning" if issues else "ok"
    evidence = grounding.score if grounding.kind != "none" else UNGROUNDED_SCORE
    confidence = evidence * grounding.ocr_confidence * VALIDATION_FACTOR[state]
    return FieldResult(
        path=path,
        value=value,
        grounded=grounding.grounded,
        source=grounding.source,
        grounding_score=grounding.score,
        ocr_confidence=round(grounding.ocr_confidence, 3),
        validation_score=VALIDATION_FACTOR[state],
        confidence=round(confidence, 3),
        issues=issues,
        needs_review=has_error or confidence < threshold,
    )


def _leaves(node: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every single value in the data (not null, not empty), with its path."""
    if isinstance(node, dict):
        found = []
        for key, value in node.items():
            found += _leaves(value, f"{path}.{key}" if path else key)
        return found
    if isinstance(node, list):
        found = []
        for index, value in enumerate(node):
            found += _leaves(value, f"{path}[{index}]")
        return found
    if isinstance(node, _SCALARS) and node != "":
        return [(path, node)]
    return []


def _review_list(fields: list[FieldResult], issues: list[Issue]) -> list[str]:
    """Fields to check: low confidence, or involved in a validation error (even if empty)."""
    paths = [f.path for f in fields if f.needs_review]
    for issue in issues:
        if issue.severity == "error":
            paths.extend(p for p in [issue.path, *issue.related] if p)
    return list(dict.fromkeys(paths))  # unique, keeps order
