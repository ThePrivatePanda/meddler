"""HTTP/WebSocket transport and the server-owned simulation runtime.

``meddler serve`` owns one Session, one SQLite history, and one wall-clock ticker for its
entire process lifetime. Browser sockets are replaceable views onto that runtime: reloads
and transient reconnects never create a new world or delete history. The runtime closes its
history only when the server process shuts down.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from meddler.bridge import commands
from meddler.bridge.runtime import advance_live, decode_command, initial_messages, new_session
from meddler.bridge.runtime import next_tick_delay as _next_tick_delay

HOST = "127.0.0.1"
PORT = 7677
WS_PATH = "/ws"

WEB_DIR_ENV = "MEDDLER_WEB_DIR"


class WebDirNotFound(RuntimeError):
    """No usable copy of the browser frontend could be located."""


def _web_dir_candidates() -> list[tuple[str, Path]]:
    """Where the frontend may live, in priority order (after the env override).

    - packaged: a wheel install ships web/ as ``meddler/web`` (see pyproject.toml);
    - checkout: running from a source tree (editable install or PYTHONPATH) serves the
      repository's top-level ``web/`` directly, so edits show up on reload."""
    package_root = Path(__file__).resolve().parents[1]
    return [
        ("packaged", package_root / "web"),
        ("checkout", package_root.parent / "web"),
    ]


def resolve_web_dir() -> Path:
    """Locate the frontend: ``$MEDDLER_WEB_DIR`` → packaged copy → repository checkout.

    An explicitly set ``MEDDLER_WEB_DIR`` that is not a directory with an ``index.html`` is
    an error rather than a silent fallback. Raises WebDirNotFound listing every location
    tried."""
    override = os.environ.get(WEB_DIR_ENV)
    if override:
        path = Path(override).expanduser().resolve()
        if (path / "index.html").is_file():
            return path
        raise WebDirNotFound(
            f"{WEB_DIR_ENV}={override!r} does not point to a directory containing index.html"
        )
    tried: list[str] = []
    for label, path in _web_dir_candidates():
        if (path / "index.html").is_file():
            return path.resolve()
        tried.append(f"  {label}: {path}")
    raise WebDirNotFound(
        "cannot find the Meddler web frontend (index.html). Looked in:\n"
        + "\n".join(tried)
        + f"\nReinstall the package, or set {WEB_DIR_ENV} to the repository's web/ directory."
    )


def _resolve_web_dir_or_none() -> Path | None:
    try:
        return resolve_web_dir()
    except WebDirNotFound:
        return None


# Resolved once at import; `serve` calls resolve_web_dir() up front to fail loudly instead.
WEB_DIR: Path | None = _resolve_web_dir_or_none()
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}

logger = logging.getLogger(__name__)


def _static_response(path: str) -> Response:
    """Serve a path-traversal-safe file from the bundled frontend."""
    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    not_found = Response(404, "Not Found", Headers(**{"Content-Type": "text/plain"}), b"404 Not Found")
    web_dir = WEB_DIR
    if web_dir is None or not web_dir.is_dir():
        return not_found
    target = (web_dir / rel).resolve()
    try:
        target.relative_to(web_dir)
    except ValueError:
        return Response(403, "Forbidden", Headers(**{"Content-Type": "text/plain"}), b"403 Forbidden")
    if not target.is_file():
        return not_found
    body = target.read_bytes()
    ctype = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
    headers = Headers(**{"Content-Type": ctype, "Content-Length": str(len(body))})
    return Response(200, "OK", headers, body)


def _process_request(connection: ServerConnection, request: Request) -> Response | None:
    path = request.path.split("?", 1)[0]
    if path == WS_PATH:
        return None
    return _static_response(path)


async def _send(connection: ServerConnection, message: dict[str, Any]) -> None:
    await connection.send(json.dumps(message))


class ServerRuntime:
    """One process-owned simulation shared by replaceable browser connections."""

    def __init__(self, seed: int, *, start_running: bool = True) -> None:
        self.session = new_session(seed)
        self.session.running = start_running
        self.connections: set[ServerConnection] = set()
        self._ticker: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._ticker is None:
            self._ticker = asyncio.create_task(self._tick_loop())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        self.session.close()

    async def _broadcast(self, message: dict[str, Any]) -> None:
        dead: list[ServerConnection] = []
        encoded = json.dumps(message)
        for connection in tuple(self.connections):
            try:
                await connection.send(encoded)
            except ConnectionClosed:
                dead.append(connection)
        for connection in dead:
            self.connections.discard(connection)

    async def _tick_loop(self) -> None:
        """Advance continuously, projecting frames only while a client is attached."""
        loop = asyncio.get_running_loop()
        delay = 1.0 / self.session.tps
        while True:
            await asyncio.sleep(delay)
            started = loop.time()
            messages = advance_live(self.session)
            if messages is None:
                delay = 1.0 / self.session.tps
                continue
            if self.connections:
                for message in messages:
                    await self._broadcast(message)
            elapsed = loop.time() - started
            delay = _next_tick_delay(self.session.tps, elapsed)

    async def _send_initial_state(self, connection: ServerConnection) -> None:
        for message in initial_messages(self.session):
            await _send(connection, message)

    async def handle(self, connection: ServerConnection) -> None:
        await self.start()
        self.connections.add(connection)
        try:
            await self._send_initial_state(connection)
            async for raw in connection:
                cmd = decode_command(raw)
                if cmd is None:
                    continue
                for message in commands.handle(self.session, cmd):
                    await _send(connection, message)
        except ConnectionClosed:
            pass
        finally:
            self.connections.discard(connection)


def make_handler(seed: int) -> Callable[[ServerConnection], Awaitable[None]]:
    """Compatibility factory; the returned bound method retains its shared runtime."""
    return ServerRuntime(seed).handle


async def serve_forever(seed: int, host: str = HOST, port: int = PORT) -> None:
    runtime = ServerRuntime(seed)
    await runtime.start()
    # Ctrl-C already unwinds through the `finally` below. SIGTERM (`kill`, `timeout`, a
    # service manager) would otherwise end the process without closing the history file.
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        async with serve(runtime.handle, host, port, process_request=_process_request):
            url = f"http://{host}:{port}/"
            print(f"Meddler is running — open {url}  (seed={seed}, Ctrl-C to stop)", flush=True)
            logger.info(
                "meddler serving %s and ws://%s:%d%s (seed=%d)",
                url,
                host,
                port,
                WS_PATH,
                seed,
            )
            await stopped.wait()
    finally:
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(signal.SIGTERM)
        await runtime.close()


def run(seed: int, host: str = HOST, port: int = PORT) -> None:
    try:
        asyncio.run(serve_forever(seed, host, port))
    except KeyboardInterrupt:
        print("\nMeddler stopped.", flush=True)
