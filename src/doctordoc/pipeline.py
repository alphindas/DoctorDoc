"""The front door: parse(file) -> Document. It never raises on bad input.

Steps for every file:  read bytes -> detect the real type -> (unpack a container) ->
route to engines -> assemble one Document. Any failure ends up in Document.error.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from doctordoc.config import Config
from doctordoc.detect import FileInfo, detect
from doctordoc.intake import Budget, extract_members, safe_name
from doctordoc.models import Document, PageKind
from doctordoc.router import Router

log = logging.getLogger("doctordoc.pipeline")

Source = str | Path | bytes | BinaryIO
CONTAINER_TYPES = {"zip", "eml", "msg"}


class _Cache:
    """Parsed documents keyed by file hash. In memory, and on disk if config.cache_dir is set."""

    def __init__(self, config: Config) -> None:
        self.memory: dict[str, Document] = {}
        self.directory = config.cache_dir
        self.fingerprint = config.fingerprint()

    def key(self, data: bytes, extension: str) -> str:
        return f"{hashlib.sha256(data).hexdigest()}-{extension.lstrip('.')}-{self.fingerprint}"

    def get(self, key: str) -> Document | None:
        if key in self.memory:
            return self.memory[key].model_copy(deep=True)
        if self.directory:
            path = self.directory / f"{key}.json"
            if path.exists():
                try:
                    doc = Document.model_validate_json(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
                self.memory[key] = doc
                return doc.model_copy(deep=True)
        return None

    def put(self, key: str, doc: Document) -> None:
        self.memory[key] = doc.model_copy(deep=True)
        if self.directory:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                (self.directory / f"{key}.json").write_text(
                    doc.to_json(indent=None), encoding="utf-8"
                )
            except OSError as err:
                log.warning("could not write cache file: %s", err)


# ---------------------------------------------------------------------------- public API


def parse(
    source: Source,
    config: Config | None = None,
    *,
    filename: str | None = None,
    router: Router | None = None,
) -> Document:
    """Parse one file (path, bytes or file object) into a Document. Never raises."""
    config = config or Config()
    own_router = router is None
    router = router or Router(config)
    try:
        return _parse_source(source, filename, config, router, _Cache(config))
    finally:
        if own_router and config.free_models_after_use:
            router.release_all()


def parse_many(sources: Iterable[Source], config: Config | None = None) -> list[Document]:
    """Parse a batch. Models stay loaded between files, and identical files are parsed once."""
    config = config or Config()
    router = Router(config)
    cache = _Cache(config)
    try:
        return [_parse_source(s, None, config, router, cache) for s in sources]
    finally:
        if config.free_models_after_use:
            router.release_all()


# ---------------------------------------------------------------------------- internals


def _parse_source(
    source: Source, filename: str | None, config: Config, router: Router, cache: _Cache
) -> Document:
    try:
        data, name = _read_source(source, filename, config)
    except (OSError, ValueError) as err:
        return Document(source=filename or str(source), error=f"Could not read the file: {err}")
    return _parse_bytes(data, name, config, router, cache, Budget(config.intake), depth=0)


def _read_source(source: Source, filename: str | None, config: Config) -> tuple[bytes, str]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.stat().st_size > config.max_file_mb * 1024 * 1024:
            raise ValueError(f"file is larger than {config.max_file_mb} MB")
        return path.read_bytes(), filename or path.name
    if isinstance(source, bytes):
        data, name = source, filename or "<bytes>"
    else:
        data, name = source.read(), filename or getattr(source, "name", None) or "<stream>"
    if len(data) > config.max_file_mb * 1024 * 1024:
        raise ValueError(f"file is larger than {config.max_file_mb} MB")
    return data, Path(str(name)).name


def _parse_bytes(
    data: bytes,
    name: str,
    config: Config,
    router: Router,
    cache: _Cache,
    budget: Budget,
    depth: int,
) -> Document:
    """Parse one file (top level or nested). Any exception becomes Document.error."""
    started = time.perf_counter()
    doc = Document(source=name)
    try:
        info = detect(data, name)
        doc.file_type, doc.mime = info.file_type, info.mime
        doc.warnings.extend(info.warnings)
        doc.metadata.update(size_bytes=len(data), extension=info.extension)
        if _cannot_parse(info, doc):
            return doc

        key = cache.key(data, info.extension)
        cached = cache.get(key)
        if cached is not None:
            cached.source = name
            cached.metadata["cache_hit"] = True
            return cached

        if info.file_type in CONTAINER_TYPES:
            _unpack(data, info, doc, config, router, cache, budget, depth)
        if info.file_type != "zip" and info.file_type != "msg":
            _route(data, info, doc, router)
        doc.metadata["seconds"] = round(time.perf_counter() - started, 3)
        doc.warnings.extend(budget.warnings if depth == 0 else [])
        cache.put(key, doc)
    except Exception as err:  # the library promise: bad input never raises
        log.debug("parse failed for %s", name, exc_info=True)
        doc.error = f"{type(err).__name__}: {err}"
    return doc


def _cannot_parse(info: FileInfo, doc: Document) -> bool:
    """Set doc.error and return True for files we must not or cannot parse."""
    if info.empty or info.encrypted or info.corrupt:
        doc.error = info.problem
        return True
    if info.file_type in ("unknown", "ole2"):
        doc.error = info.problem or "Unrecognised file type: cannot parse it."
        return True
    return False


def _route(data: bytes, info: FileInfo, doc: Document, router: Router) -> None:
    result = router.route(data, info)
    doc.pages = result.pages
    doc.decisions = result.decisions
    doc.warnings.extend(result.warnings)
    if info.file_type == "pdf":
        kinds: dict[str, int] = {}
        for page in doc.pages:
            kinds[page.kind.value] = kinds.get(page.kind.value, 0) + 1
        doc.metadata["page_count"] = len(doc.pages)
        doc.metadata["page_kinds"] = kinds
    elif doc.pages and all(p.kind == PageKind.NOT_PAGED for p in doc.pages):
        doc.metadata["page_count"] = len(doc.pages)


def _unpack(
    data: bytes,
    info: FileInfo,
    doc: Document,
    config: Config,
    router: Router,
    cache: _Cache,
    budget: Budget,
    depth: int,
) -> None:
    """Parse every file inside a container and attach them as children."""
    if depth >= config.intake.max_depth:
        doc.warnings.append(
            f"Not unpacked: nesting is deeper than {config.intake.max_depth} levels."
        )
        return
    if info.file_type == "msg":
        doc.warnings.append("Outlook .msg: only attachments are read, not the message text.")
    for member in extract_members(data, info.file_type, budget):
        child = _parse_bytes(
            member.data, safe_name(member.name), config, router, cache, budget, depth + 1
        )
        doc.children.append(child)
