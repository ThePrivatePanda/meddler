"""Transport-free simulation runtime shared by every way of hosting a Session.

``meddler serve`` wraps this in a WebSocket server (``server.py``); the static browser
demo runs the same code under Pyodide inside a Web Worker. Nothing here imports
``websockets`` or touches asyncio, so the second path needs only the standard library.

The pieces are deliberately small so each host can keep its own clock:

* :func:`new_session` (from ``session.py``) builds the world a fresh runtime starts from.
* :func:`initial_messages` is what a newly attached client receives, in order.
* :func:`advance_live` is one wall-clock tick: it advances the focused timeline and
  returns the messages that tick produced, or nothing while paused or scrubbing.
* :func:`next_tick_delay` keeps a start-to-start cadence at the session's speed.

:class:`LocalRuntime` strings them together behind a JSON-in/JSON-out surface for hosts
that are not Python (the Pyodide worker drives it from JavaScript).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from meddler.bridge import adapter, commands
from meddler.bridge.session import Session, new_session


def next_tick_delay(tps: float, elapsed: float) -> float:
    """Delay needed to maintain a start-to-start tick cadence."""
    return max(0.0, 1.0 / tps - elapsed)


def initial_messages(session: Session) -> Iterator[dict[str, Any]]:
    """hello -> status -> the view the client should open on (scrub, fork, or live).

    A generator on purpose: each message is built only when the host is ready to send
    it, so a tick that lands while an earlier message is still draining can never leave
    the client holding a snapshot older than a frame it has already received.
    """
    yield adapter.hello_message(session)
    yield adapter.status_message(session)
    if session.scrub_tick is not None:
        world = session.multiverse.prime.world_at(session.scrub_tick)
        yield adapter.snapshot_message(session, world, live=False, scrubbed=True)
    elif session.focused_fork_id is not None:
        yield adapter.timeline_focus_message(session, session.focused_fork_id)
    else:
        yield adapter.snapshot_message(
            session, session.multiverse.prime.world, live=True, scrubbed=False
        )


def advance_live(session: Session) -> list[dict[str, Any]] | None:
    """One wall-clock tick. Returns the messages to push (a `frame`, preceded by a
    `countryAdded` for any nation born on this tick), or None if the clock is held."""
    if not session.running or session.scrub_tick is not None:
        return None
    return commands.advance_one_tick(session)


def decode_command(raw: str | bytes) -> dict[str, Any] | None:
    """Parse one client command; malformed or non-object payloads are silently dropped."""
    try:
        cmd = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return cmd if isinstance(cmd, dict) else None


def _lines(messages: Iterable[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(message) for message in messages)


class LocalRuntime:
    """A Session plus the JSON surface a non-Python host needs to drive it.

    Messages come back already encoded, exactly as ``meddler serve`` would put them on
    the socket. Methods that can yield several messages return them newline-separated
    (``json.dumps`` never emits a raw newline), so the host splits on ``"\\n"`` and gets
    one wire payload per line. The host owns the clock: call :meth:`tick_json` on its own
    timer and :meth:`delay` for the next wait.
    """

    def __init__(self, seed: int, *, start_running: bool = True) -> None:
        self.session = new_session(seed)
        self.session.running = start_running

    def connect_json(self) -> str:
        return _lines(initial_messages(self.session))

    def handle_json(self, raw: str) -> str:
        cmd = decode_command(raw)
        if cmd is None:
            return ""
        return _lines(commands.handle(self.session, cmd))

    def tick_json(self) -> str | None:
        """One tick as wire payloads, or None while the clock is held (paused/scrubbing).

        Usually one `frame`; a tick that creates a country returns its `countryAdded`
        first, newline-separated like the other methods here."""
        messages = advance_live(self.session)
        if messages is None:
            return None
        return _lines(messages)

    def delay(self, elapsed: float) -> float:
        return next_tick_delay(self.session.tps, elapsed)

    def idle_delay(self) -> float:
        return 1.0 / self.session.tps

    def close(self) -> None:
        self.session.close()


__all__ = [
    "LocalRuntime",
    "advance_live",
    "decode_command",
    "initial_messages",
    "new_session",
    "next_tick_delay",
]
