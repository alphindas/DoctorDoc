"""Glue between Streamlit and the library: caches, the list of parsed files, the sidebar values.

Caches are `st.cache_resource` dictionaries (so they survive reruns and are shared by every
browser tab). They hold parsed Documents and results, never the uploaded file bytes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import streamlit as st

from doctordoc import service
from doctordoc.config import Config
from doctordoc.models import Document, EngineRun
from doctordoc.service import UiSettings

MAX_CACHE_ITEMS = 40


# ---------------------------------------------------------------------------- caches


@st.cache_resource
def _store(name: str) -> dict[Any, Any]:
    return {}


def _remember(store_name: str, key: Any, value: Any) -> None:
    store = _store(store_name)
    store[key] = value
    while len(store) > MAX_CACHE_ITEMS:
        store.pop(next(iter(store)))  # forget the oldest


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_files(
    files: list[tuple[str, bytes]],
    config: Config,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[Document]:
    """Parse files, re-using earlier results for the same file + settings."""
    cfg = service.config_to_json(config)
    keys = [(_digest(data), name, cfg) for name, data in files]
    store = _store("parse")
    missing = [i for i, key in enumerate(keys) if key not in store]
    if missing:
        fresh = service.parse_uploads([files[i] for i in missing], config, on_progress)
        for i, doc in zip(missing, fresh, strict=True):
            _remember("parse", keys[i], doc)
    return [store[key].model_copy(deep=True) for key in keys]


def engine_run(data: bytes, name: str, config: Config, engine: str) -> EngineRun:
    key = (_digest(data), name, service.config_to_json(config), engine)
    store = _store("compare")
    if key not in store:
        _remember("compare", key, service.run_one_engine(data, name, config, engine))
    return store[key].model_copy(deep=True)  # type: ignore[no-any-return]


def extraction(doc: Document, schema: str, config: Config):  # type: ignore[no-untyped-def]
    key = (
        _digest(doc.to_json(indent=None).encode()),
        schema,
        config.llm.model,
        config.llm.base_url,
    )
    store = _store("extract")
    if key not in store:
        result = service.run_extraction(doc, schema, config)
        if not result.error:  # do not remember a failure: the server may be up next time
            _remember("extract", key, result)
        return result
    return store[key].model_copy(deep=True)


@st.cache_data(ttl=20, show_spinner=False)
def local_models(base_url: str, provider: str, api_key: str) -> tuple[list[str], str | None]:
    """(model names, error). Looked up again every 20 seconds, so starting Ollama is noticed."""
    from doctordoc.config import LLMConfig
    from doctordoc.extract import LLMError, list_models

    try:
        config = LLMConfig(base_url=base_url, provider=provider, api_key=api_key)  # type: ignore[arg-type]
        return list_models(config), None
    except LLMError as err:
        return [], str(err)


# ---------------------------------------------------------------------------- parsed files


@dataclass
class ParsedFile:
    name: str
    data: bytes
    doc: Document
    members: dict[str, bytes] = field(default_factory=dict)  # bytes of files inside a zip/email


@dataclass
class Entry:
    """One file you can look at: an upload, or a file inside a ZIP or email."""

    key: str
    label: str
    doc: Document
    data: bytes | None  # None for deeply nested files (no picture, no engine comparison)
    top_level: bool


def make_parsed(
    files: list[tuple[str, bytes]], docs: list[Document], config: Config
) -> list[ParsedFile]:
    parsed = []
    for (name, data), doc in zip(files, docs, strict=True):
        members = {}
        if doc.children and doc.file_type in ("zip", "eml", "msg"):
            members = service.member_files(data, doc.file_type, config)
        parsed.append(ParsedFile(name=name, data=data, doc=doc, members=members))
    return parsed


def entries_of(parsed: list[ParsedFile]) -> list[Entry]:
    """Flatten uploads and the files inside them into one list for the file picker."""
    entries: list[Entry] = []
    for index, item in enumerate(parsed):
        entries.append(Entry(str(index), item.name, item.doc, item.data, True))
        _add_children(entries, item.doc, str(index), item.name, item.members)
    return entries


def _add_children(
    entries: list[Entry], doc: Document, key: str, path: str, members: dict[str, bytes]
) -> None:
    for number, child in enumerate(doc.children):
        child_path = f"{path} ▸ {child.source}"
        child_key = f"{key}-{number}"
        entries.append(Entry(child_key, child_path, child, members.get(child.source), False))
        _add_children(entries, child, child_key, child_path, {})


@st.cache_data(ttl=60, show_spinner=False)
def engine_report(config_json: str) -> list[dict[str, str]]:
    """Which engines are installed (looked at again every minute)."""
    from doctordoc.router import Router

    return Router(service.config_from_json(config_json)).engine_report()


@st.cache_data(max_entries=64, show_spinner=False)
def page_png(data: bytes, file_type: str, number: int) -> bytes | None:
    return service.render_page_png(data, file_type, number)


def example_files() -> dict[str, Path]:
    """The sample files in examples/ (next to the package in a checkout, or in the folder
    you started from)."""
    for folder in (Path(__file__).resolve().parents[3] / "examples", Path.cwd() / "examples"):
        if folder.is_dir():
            return {p.name: p for p in sorted(folder.iterdir()) if p.is_file()}
    return {}


# ---------------------------------------------------------------------------- sidebar values


@dataclass
class SidebarState:
    files: list[tuple[str, bytes]]
    settings: UiSettings
    parse_clicked: bool
