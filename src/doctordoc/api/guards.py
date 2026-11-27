"""Checks made before an upload is read: where the request comes from, and is there room to wait.

Neither is a login. The Host and Origin checks only stop web browsers from being used against
the server: any other program can send whatever headers it likes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# A fixed hint, not an estimate: how long a document takes is not known in advance.
RETRY_AFTER_SECONDS = 30

_DEFAULT_PORTS = {"http": 80, "https": 443}
# "name", "name:port", "[ipv6]" or "[ipv6]:port", and nothing else
_AUTHORITY = re.compile(r"(\[[0-9A-Fa-f:.]+\]|[^\[\]:/\\@?#\s]+)(?::([0-9]{1,5}))?")

BAD_HOST = (
    "Refused: the server does not accept this Host header. Use localhost, 127.0.0.1 or ::1, "
    "or add the name to api.allowed_hosts in the server's settings."
)
BAD_ORIGIN = (
    "Refused: the Origin header does not match the Host header, so the request may come from "
    "another web site."
)


def split_host(value: str) -> tuple[str, int | None] | None:
    """("name", port or None) from "name:port", or None if the text is not of that form."""
    match = _AUTHORITY.fullmatch(value)
    if not match:
        return None
    name, port = match.groups()
    return name.strip("[]").lower(), None if port is None else int(port)


def _same_site(origin: str, host: tuple[str, int | None]) -> bool:
    """Do the Origin and Host headers name the same host and port?

    A missing port means the usual one for the Origin's scheme (80 or 443). The scheme itself is
    not compared, because a proxy in front of the server may handle HTTPS.
    """
    scheme, _, authority = origin.partition("://")
    usual = _DEFAULT_PORTS.get(scheme.lower())
    parsed = split_host(authority)
    if usual is None or parsed is None:  # also "null", which sandboxed pages send
        return False
    (name, port), (host_name, host_port) = parsed, host
    return name == host_name and (usual if port is None else port) == (
        usual if host_port is None else host_port
    )


class SameSiteOnly:
    """Answers 403 to a wrong Host header, or an Origin that does not match it.

    `allowed_hosts` is None when any Host is accepted. The Origin check always applies.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: frozenset[str] | None) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            reason = self._why_not(Headers(scope=scope))
            if reason:
                await JSONResponse({"detail": reason}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _why_not(self, headers: Headers) -> str | None:
        hosts, origins = headers.getlist("host"), headers.getlist("origin")
        host = split_host(hosts[0]) if len(hosts) == 1 else None
        if self.allowed_hosts is not None and (host is None or host[0] not in self.allowed_hosts):
            return BAD_HOST
        if origins and (len(origins) > 1 or host is None or not _same_site(origins[0], host)):
            return BAD_ORIGIN
        return None


class WaitingLine:
    """Room for one working request plus `max_waiting` others.

    A request takes its place as soon as it arrives, before its upload is read, and keeps it
    until its answer is ready. Only the server's event loop uses the count, so it needs no lock.
    """

    def __init__(self, max_waiting: int) -> None:
        self.max_waiting = max_waiting
        self.taken = 0

    @contextmanager
    def place(self) -> Iterator[None]:
        if self.taken > self.max_waiting:
            raise HTTPException(
                503,
                f"The server is busy: {self.taken} /parse or /extract requests are already in "
                f"progress or waiting (api.max_waiting is {self.max_waiting}). Try again later.",
                headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        self.taken += 1
        try:
            yield
        finally:  # also after an error or a client that went away
            self.taken -= 1

    def route_class(self) -> type[APIRoute]:
        """A route that takes a place first. FastAPI reads the upload inside the route's
        handler, so this runs before a single byte of it is read or written to disk."""
        line = self

        class RouteWithPlace(APIRoute):
            def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
                handle = super().get_route_handler()

                async def handle_with_place(request: Request) -> Response:
                    with line.place():
                        return await handle(request)

                return handle_with_place

        return RouteWithPlace
