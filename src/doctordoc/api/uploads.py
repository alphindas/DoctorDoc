"""Uploads: stop one that is too large while it is still arriving, and delete each temporary
copy when its request ends."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from doctordoc.intake import safe_name
from doctordoc.service import safe_file_name

# Room in the request for what surrounds the file: multipart boundaries, headers, form fields.
FORM_OVERHEAD_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024


def too_large(max_file_mb: float) -> str:
    return f"The upload is over the {max_file_mb:g} MB limit (max_file_mb)."


class BodyLimit:
    """Counts the request body as it arrives and answers 413 as soon as it is too big.

    It has to sit in front of the app: FastAPI reads the whole upload before an endpoint
    runs, so a size check inside the endpoint would come too late.
    """

    def __init__(self, app: ASGIApp, max_file_mb: float) -> None:
        self.app = app
        self.max_bytes = int(max_file_mb * 1024 * 1024) + FORM_OVERHEAD_BYTES
        self.message = too_large(max_file_mb)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdecimal() and int(declared) > self.max_bytes:  # refuse before reading
            response = JSONResponse({"detail": self.message}, status_code=413)
            await response(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:  # for uploads that do not say their size, or lie
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413, self.message)  # FastAPI passes this on
            return message

        await self.app(scope, counted, send)


@asynccontextmanager
async def saved_upload(upload: UploadFile, max_file_mb: float) -> AsyncIterator[tuple[Path, str]]:
    """The upload as a file in its own temporary folder: (path, cleaned file name).

    The folder is deleted when the block ends, also after an error.
    """
    name = safe_name(upload.filename or "")  # no folders, no odd characters
    with tempfile.TemporaryDirectory(prefix="doctordoc-api-") as folder:
        # The prefix means that a name like ".." can never point outside the folder.
        path = Path(folder) / f"upload-{safe_file_name(name)}"
        await run_in_threadpool(_copy, upload.file, path, max_file_mb)
        await upload.close()  # drop the framework's own copy while we wait and work
        yield path, name


def _copy(source: BinaryIO, path: Path, max_file_mb: float) -> None:
    """Copy in chunks. The exact limit on the file itself is checked here."""
    limit, size = max_file_mb * 1024 * 1024, 0
    with path.open("wb") as target:
        while chunk := source.read(CHUNK_BYTES):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, too_large(max_file_mb))
            target.write(chunk)
