"""Shared helpers for front ends (the Streamlit UI now, the HTTP API later).

Everything a front end needs that is NOT drawing: turn user settings into a Config, parse
uploaded files from a temporary folder, run engines side by side, render a page picture,
explain the router's choice in plain English. The UI only calls these and draws the result.
"""

from __future__ import annotations

import io
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from doctordoc.config import Config
from doctordoc.detect import IMAGE_TYPES, FileInfo, detect
from doctordoc.intake import Budget, extract_members, safe_name
from doctordoc.models import Document, EngineRun, PageKind
from doctordoc.pipeline import parse
from doctordoc.router import Router

# Engines that read pictures of text. "Force one OCR engine" chooses among these.
OCR_ENGINES = ("paddle_vl", "rapidocr", "tesseract", "cloud_vlm")
CLOUD_ENGINES = ("cloud_vlm",)

# ---------------------------------------------------------------------------- settings


@dataclass
class UiSettings:
    """What the user picked in the sidebar."""

    disabled_engines: set[str] = field(default_factory=set)
    forced_ocr: str | None = None  # None = Auto: the router decides
    local_only: bool = True  # True: no page ever leaves the machine (cloud fallback off)
    merge_mixed_pages: bool = True
    llm_model: str | None = None


def apply_settings(base: Config, settings: UiSettings) -> Config:
    """A copy of `base` with the sidebar choices applied. `base` is not changed."""
    config = base.model_copy(deep=True)
    config.merge_mixed_pages = settings.merge_mixed_pages
    if settings.llm_model:
        config.llm.model = settings.llm_model

    disabled = set(settings.disabled_engines)
    config.cloud.enabled = not settings.local_only
    if settings.local_only:
        disabled.update(CLOUD_ENGINES)  # the promise of "local only", even if the config says more

    if settings.forced_ocr:
        forced = settings.forced_ocr
        config.page_chains["scanned"] = [forced]
        config.page_chains["garbled"] = [forced, "pdf_text"]
        for file_type in IMAGE_TYPES:
            config.file_chains[file_type] = [forced]
        disabled.discard(forced)

    for chains in (config.page_chains, config.file_chains):
        for kind, chain in chains.items():
            chains[kind] = [name for name in chain if name not in disabled]
    return config


def config_to_json(config: Config) -> str:
    """A string that identifies a config (used as a cache key by front ends)."""
    return config.model_dump_json()


def config_from_json(text: str) -> Config:
    return Config.model_validate_json(text)


def file_too_big(size_bytes: int, config: Config) -> str | None:
    """A message if the file is over the size limit, else None."""
    limit = config.max_file_mb
    if size_bytes > limit * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB is over the {limit:g} MB limit."
    return None


# ---------------------------------------------------------------------------- parsing


def parse_uploads(
    files: Sequence[tuple[str, bytes]],
    config: Config,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[Document]:
    """Parse uploaded files. Each is written to a temporary folder, parsed, and deleted.

    Nothing is kept on disk afterwards. A bad file gives a Document with `.error` set; it
    never raises, and it never stops the other files.
    """
    router = Router(config)
    documents: list[Document] = []
    try:
        with tempfile.TemporaryDirectory(prefix="doctordoc-") as folder:
            for index, (name, data) in enumerate(files):
                if on_progress:
                    on_progress(index, len(files), name)
                too_big = file_too_big(len(data), config)
                if too_big:
                    documents.append(Document(source=name, error=f"File is too big: {too_big}"))
                    continue
                path = Path(folder) / f"{index}-{_safe_file_name(name)}"
                path.write_bytes(data)
                documents.append(parse(path, config, filename=name, router=router))
                path.unlink(missing_ok=True)
            if on_progress:
                on_progress(len(files), len(files), "")
    finally:
        router.release_all()
    return documents


def member_files(data: bytes, file_type: str, config: Config) -> dict[str, bytes]:
    """The bytes of the files directly inside a ZIP or email, by display name (kept in memory
    only). Lets a front end draw page pictures and run engines on a nested file."""
    members: dict[str, bytes] = {}
    for member in extract_members(data, file_type, Budget(config.intake)):
        members.setdefault(safe_name(member.name), member.data)
    return members


def _safe_file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)[:80] or "file"


# ---------------------------------------------------------------------------- compare engines


def engines_enabled(names: Sequence[str], settings: UiSettings) -> list[str]:
    """Drop the engines the user switched off (and the cloud engine when 'local only' is on)."""
    off = set(settings.disabled_engines) | (set(CLOUD_ENGINES) if settings.local_only else set())
    return [name for name in names if name not in off]


def comparable_engines(data: bytes, name: str, config: Config) -> list[str]:
    """The installed engines that could read this file (the ones the compare tab will run)."""
    return Router(config).runnable_engines(detect(data, name))


def iter_engine_runs(
    data: bytes, name: str, config: Config, engines: Sequence[str] | None = None
) -> Iterator[EngineRun]:
    """Run each engine alone on the file, one after the other (so a UI can show progress)."""
    router = Router(config)
    info = detect(data, name)
    try:
        for engine_name in engines if engines is not None else router.runnable_engines(info):
            yield router.run_engine(engine_name, data, info)
            router.engines[engine_name].release()  # one heavy model at a time
    finally:
        router.release_all()


def run_one_engine(data: bytes, name: str, config: Config, engine: str) -> EngineRun:
    """Run a single engine alone (cacheable building block for the compare tab)."""
    return next(iter_engine_runs(data, name, config, [engine]))


def chosen_engines(doc: Document) -> set[str]:
    """The engine names the router actually used for the pages of this document."""
    names: set[str] = set()
    for page in doc.pages:
        if page.engine:
            names.update(page.engine.split("+"))
    return names


# ---------------------------------------------------------------------------- page pictures


def render_page_png(data: bytes, file_type: str, number: int, dpi: int = 110) -> bytes | None:
    """A PNG of one page (a PDF page, or an image file as it is). None if it cannot be drawn."""
    if file_type != "pdf" and file_type not in IMAGE_TYPES:
        return None
    from doctordoc.engines._images import iter_page_images

    try:
        for _number, image, _scale in iter_page_images(data, file_type, [number], dpi):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # a page we cannot draw must not break the screen
        return None
    return None


# ---------------------------------------------------------------------------- explanations


def explain_routing(doc: Document, config: Config | None = None) -> str:
    """A few plain-English lines: why each page ended up with the engine it got."""
    threshold = (config or Config()).quality_threshold
    if doc.error:
        return f"This file could not be parsed, so no engine was chosen. {doc.error}"
    if not doc.pages:
        return "No pages were produced, so no engine was chosen."
    lines: list[str] = []
    shown = doc.pages[:12]
    for page in shown:
        scope_decisions = [d for d in doc.decisions if _scope_covers(d.scope, page.number)]
        if page.kind == PageKind.BLANK:
            lines.append(f"- **Page {page.number}** is blank, so no engine was needed.")
            continue
        kind = "" if page.kind == PageKind.NOT_PAGED else f" is **{page.kind.value}** and"
        head = f"- **Page {page.number}**{kind}" if kind else "- **This file**"
        if not page.engine:
            lines.append(f"{head} produced no text from any engine.")
            continue
        quality = f"{page.quality:.2f}" if page.quality is not None else "n/a"
        engine = page.engine.replace("+", " + ")
        line = f"{head} was read by **{engine}** (text quality {quality}, needs {threshold:.2f})."
        others = [
            f"{d.engine} {_past(d.outcome)} ({d.reason})"
            for d in scope_decisions
            if d.engine not in page.engine.split("+") and d.engine != "triage"
        ]
        if others:
            line += " Before that: " + "; ".join(others) + "."
        lines.append(line)
    if len(doc.pages) > len(shown):
        lines.append(f"- … and {len(doc.pages) - len(shown)} more pages (see the table below).")
    return "\n".join(lines)


def _past(outcome: str) -> str:
    return {"skipped": "was skipped", "failed": "failed", "timeout": "timed out"}.get(
        outcome, f"was {outcome}"
    )


def _scope_covers(scope: str, number: int) -> bool:
    """Does a decision scope ('file', 'page 3', 'pages 1-4', 'pages 1,3,7') include this page?"""
    if scope == "file":
        return True
    match = re.fullmatch(r"pages? ([\d,\- ]+)", scope)
    if not match:
        return False
    body = match.group(1)
    if "-" in body:
        low, _, high = body.partition("-")
        return int(low) <= number <= int(high)
    return number in {int(n) for n in body.split(",") if n.strip()}


def run_extraction(doc: Document, schema: str, config: Config):  # type: ignore[no-untyped-def]
    """Extract fields from a parsed document (a thin wrapper, so front ends and tests have one
    place to call). Never raises: problems end up in result.error."""
    from doctordoc.extract import extract

    return extract(doc, schema, config)


def split_warnings(doc: Document) -> tuple[list[str], list[str]]:
    """(wrong-extension warnings, all other warnings) of a document."""
    wrong = [w for w in doc.warnings if w.startswith("Extension ")]
    return wrong, [w for w in doc.warnings if w not in wrong]


def info_for(data: bytes, name: str) -> FileInfo:
    """What `detect` says about a file (type, and any warning such as a wrong extension)."""
    return detect(data, name)
