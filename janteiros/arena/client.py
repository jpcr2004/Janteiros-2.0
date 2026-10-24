"""The arena over HTTP: the kit's ArenaClient plus a hard guard on paths the Live Final forbids,
timing of every call, and attachment text extraction.

The kit already sends a fresh X-Move-Id per POST, reuses it on retry (idempotent), honours a short
Retry-After on 429 and uses a 180 s timeout; we keep that and wrap it.
"""

import os
import time
from urllib.parse import urlsplit

import httpx
from arena_kit.client import ArenaClient, ArenaError

__all__ = ["Arena", "ArenaError", "ForbiddenPath", "is_forbidden", "is_local"]

# Reading any of these during the Live Final is a rule violation. We never need them, so they are
# refused always, before a request leaves the process.
FORBIDDEN = ("/events", "/screen", "/leaderboard", "/results")


class ForbiddenPath(RuntimeError):
    pass


def is_forbidden(url_or_path: str) -> bool:
    path = urlsplit(str(url_or_path)).path.rstrip("/").lower() or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if path.startswith("/v1/"):
        path = path[3:]
    elif path == "/v1":
        path = "/"
    return any(path == p or path.startswith(p + "/") for p in FORBIDDEN)


def _guard(request: httpx.Request):
    if is_forbidden(request.url.path):
        raise ForbiddenPath(f"refused: {request.url.path} is forbidden for the agent")


def is_local(url: str | None) -> bool:
    host = urlsplit(url or "").hostname or ""
    return host in ("localhost", "127.0.0.1", "::1", "testserver") or host.endswith(".localhost")


class Arena(ArenaClient):
    """ArenaClient with the forbidden-path guard on every request and a per-call timing hook."""

    def __init__(self, base_url=None, api_key=None, *, on_call=None, **kw):
        super().__init__(base_url, api_key, **kw)
        self.base_url = (base_url or os.environ.get("ARENA_URL", "")).rstrip("/")
        self.http.event_hooks["request"].append(_guard)
        self.on_call = on_call  # fn(method, path, status, seconds)

    def _call(self, method, path, body=None, params=None, raw=False):
        if is_forbidden(path):
            raise ForbiddenPath(f"refused: {path} is forbidden for the agent")
        t0 = time.monotonic()
        status = 0
        try:
            out = super()._call(method, path, body, params, raw)
            status = 200
            return out
        except ArenaError as e:
            status = e.status
            raise
        finally:
            if self.on_call:
                try:
                    self.on_call(method, path, status, time.monotonic() - t0)
                except Exception:  # noqa: BLE001 - telemetry must never break a move
                    pass

    @property
    def local(self) -> bool:
        return is_local(self.base_url)

    # the kit has these; make sure nobody calls them by accident
    def leaderboard(self, set_="practice"):
        raise ForbiddenPath("leaderboard is forbidden for the agent")
