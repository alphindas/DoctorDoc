"""The HTTP API. A thin layer: it uses the same parse and extract code as the command line.

There is no login. The API keeps no copy of uploads: each one lives in a temporary folder until
its request ends (a killed process can leave it behind), and the parse cache is always off.

/parse and /extract take turns (one lock for both), because only one heavy model should be in
memory at a time. At most `api.max_waiting` others wait for their turn, with no time limit; the
next one gets a 503. The lock and the count belong to one server process, so run a single worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

import httpx
from fastapi import APIRouter, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from doctordoc import __version__
from doctordoc.api.guards import LOCAL_HOSTS, SameSiteOnly, WaitingLine
from doctordoc.api.uploads import BodyLimit, saved_upload
from doctordoc.cloud_warning import warn_about_cloud
from doctordoc.config import Config, LLMConfig
from doctordoc.extract import BUILTIN_SCHEMAS, LLMError, extract, list_models
from doctordoc.models import Document
from doctordoc.pipeline import parse
from doctordoc.router import Router

log = logging.getLogger("doctordoc.api")
T = TypeVar("T")
JSON = "application/json"
MAX_NAMES_IN_WARNING = 10


def create_app(config: Config | None = None, *, local_only: bool = True) -> FastAPI:
    """Build the API. The settings are the server's: a request cannot change them.

    `local_only=False` says that the server listens on an address other computers can reach.
    Then any Host header is accepted, unless `api.allowed_hosts` lists the accepted ones.
    """
    config = (config or Config()).model_copy(deep=True)
    config.cache_dir = None  # the parse cache would keep documents' text on disk: always off here
    warn_about_cloud(config)
    docs = config.api.docs
    app = FastAPI(
        title="DoctorDoc",
        version=__version__,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    allowed = LOCAL_HOSTS | set(config.api.allowed_hosts)
    any_host = not local_only and not config.api.allowed_hosts
    app.add_middleware(BodyLimit, max_file_mb=config.max_file_mb)
    # Added last, so it runs first: a refused request is not looked at any further.
    app.add_middleware(SameSiteOnly, allowed_hosts=None if any_host else allowed)
    heavy_lock = asyncio.Lock()
    # the routes that wait for the lock: only a limited number of requests may be inside them
    heavy = APIRouter(route_class=WaitingLine(config.api.max_waiting).route_class())

    async def take_turns(work: Callable[[], T]) -> T:
        """Run heavy work in a thread, one request at a time. Waiting uses no thread."""
        async with heavy_lock:
            return await run_in_threadpool(work)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        log.error("unexpected %s on %s", type(error).__name__, request.url.path)
        return JSONResponse({"detail": "Internal error. See the server log."}, status_code=500)

    @app.get("/health")
    def health() -> dict[str, Any]:
        reachable = None  # null: the server is set up for another kind of model server
        if config.llm.provider == "ollama":
            reachable = not _model_server_down(config.llm)
        return {"status": "ok", "version": __version__, "ollama_reachable": reachable}

    @app.get("/engines")
    def engines() -> dict[str, Any]:
        return {"engines": Router(config).engine_report()}  # what `doctordoc engines` shows

    @heavy.post("/parse")
    async def parse_file(
        file: UploadFile,
        format: Literal["json", "markdown"] = "json",
        explain: bool = False,
    ) -> Response:
        async with saved_upload(file, config.max_file_mb) as (path, name):
            doc = await take_turns(lambda: parse(path, config, filename=name))
        _refuse_unreadable(doc)
        if format == "json":  # the Document, which already holds each file's decision log
            return Response(doc.to_json(), media_type=JSON)
        if not explain:
            return Response(doc.to_markdown(), media_type="text/markdown; charset=utf-8")
        decisions = [
            {"source": item.source, **d.model_dump()}
            for item in doc.iter_all()
            for d in item.decisions
        ]
        return JSONResponse({"markdown": doc.to_markdown(), "decisions": decisions})

    @heavy.post("/extract")
    async def extract_fields(
        file: UploadFile,
        schema_name: Annotated[str, Form(alias="schema")],
    ) -> Response:
        # Built-in names only. The command line also takes "module:Class", but over HTTP
        # that would let any caller make the server import a Python module.
        if schema_name not in BUILTIN_SCHEMAS:
            raise HTTPException(422, f"Unknown schema. Use one of: {', '.join(BUILTIN_SCHEMAS)}.")
        if await run_in_threadpool(_model_server_down, config.llm):
            raise HTTPException(503, _down_message(config.llm))
        async with saved_upload(file, config.max_file_mb) as (path, name):

            def work() -> tuple[Document, Any]:
                doc = parse(path, config, filename=name)
                return doc, None if doc.error else extract(doc, schema_name, config)

            doc, result = await take_turns(work)
        _refuse_unreadable(doc)
        status = 200
        if result.error:
            if not any(page.text.strip() for page in doc.pages):
                status = 422  # a readable file with nothing to extract from
            elif await run_in_threadpool(_model_server_down, config.llm):
                raise HTTPException(503, _down_message(config.llm))
            else:
                status = 502  # the model step failed; the report says why
        return Response(_report_json(doc, result), status_code=status, media_type=JSON)

    app.include_router(heavy)
    return app


def _report_json(doc: Document, result: Any) -> str:
    """What `doctordoc extract --format json` prints, plus a "warnings" list.

    The list is added here, not in the report itself, so the command line's JSON is unchanged
    (the command line prints its warnings separately).
    """
    report = result.to_report()
    report["warnings"] = [*result.warnings, *_not_read(doc)]
    return json.dumps(report, indent=2, ensure_ascii=False)


def _not_read(doc: Document) -> list[str]:
    """A warning about the files inside this one: extraction uses only its own pages."""
    if not doc.children:
        return []
    count = len(doc.children)
    names = ", ".join(child.source for child in doc.children[:MAX_NAMES_IN_WARNING])
    if count > MAX_NAMES_IN_WARNING:
        names += f" and {count - MAX_NAMES_IN_WARNING} more"
    if doc.file_type == "zip":
        return [
            f"Ignored ZIP contents: {count} file(s) were not read ({names}). /extract does not "
            "look inside a ZIP: send the files one at a time."
        ]
    note = " The email's own text, which lists their names, was used." if doc.pages else ""
    return [f"Ignored email attachments: {count} file(s) were not read ({names}).{note}"]


def _refuse_unreadable(doc: Document) -> None:
    """415 for a file that is unsupported or broken.

    A broken image or Office file often has no `error`: every engine just fails on it. A
    readable file with no text (a blank page) is not refused.
    """
    if doc.error:
        raise HTTPException(415, doc.error)
    if doc.children or any(page.blocks for page in doc.pages):
        return
    tried = [d for d in doc.decisions if d.engine != "triage"]
    if tried and all(d.outcome in ("failed", "skipped", "timeout") for d in tried):
        reasons = "; ".join(f"{d.engine}: {d.reason}" for d in tried)
        raise HTTPException(415, f"No engine could read this file. {reasons}")


def _model_server_down(llm: LLMConfig) -> bool:
    """Is the model server unusable? Never raises.

    For Ollama, any failure to list the models counts. An OpenAI-compatible server may not
    offer a model list, so for it only a failed connection counts.
    """
    try:
        list_models(llm)
    except LLMError as err:
        return llm.provider == "ollama" or isinstance(err.__cause__, httpx.TransportError)
    except Exception:  # for example a base_url that is not a URL at all
        return True
    return False


def _down_message(llm: LLMConfig) -> str:
    server = "Ollama" if llm.provider == "ollama" else "the model server"
    return (
        f"Cannot reach {server}, so /extract cannot run. Start it, or check llm.base_url "
        "in the server's settings. /parse works without it."
    )
