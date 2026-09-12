"""The transport-free runtime must say exactly what the WebSocket server says.

The static browser demo drives `LocalRuntime` from a Web Worker instead of talking to
`meddler serve`. These tests replay one scripted session through both hosts and require
the wire payloads to be identical, so the demo cannot drift from the real protocol.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from meddler.bridge.runtime import LocalRuntime
from meddler.bridge.server import ServerRuntime

SEED = 42


def _script(country: str) -> list[dict[str, Any]]:
    return [
        {"cmd": "step", "requestId": "s1"},
        {"cmd": "step"},
        {"cmd": "setSpeed", "tps": 4, "requestId": 7},
        {"cmd": "setSpeed", "tps": 3},
        {"cmd": "worldAt", "tick": 1},
        {"cmd": "resumeLive"},
        {"cmd": "intervene", "kind": "INTERVENE_DROUGHT", "country": country, "atTick": 1},
        {"cmd": "step"},
        {"cmd": "countryDetail", "code": country, "tl": "A"},
        {"cmd": "noSuchCommand"},
    ]


async def _server_payloads(reply_counts: list[int]) -> tuple[list[str], list[list[str]]]:
    runtime = ServerRuntime(SEED, start_running=False)
    await runtime.start()
    server = await serve(runtime.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    replies: list[list[str]] = []
    try:
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            initial = [str(await ws.recv()) for _ in range(3)]
            country = json.loads(initial[0])["countries"][0]["code"]
            for cmd, expected in zip(_script(country), reply_counts, strict=True):
                await ws.send(json.dumps(cmd))
                replies.append(
                    [str(await asyncio.wait_for(ws.recv(), 30)) for _ in range(expected)]
                )
            # Nothing may follow the last (ignored) command.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.5)
    finally:
        server.close()
        await server.wait_closed()
        await runtime.close()
    return initial, replies


def test_local_runtime_matches_websocket_server_byte_for_byte() -> None:
    local = LocalRuntime(SEED, start_running=False)
    try:
        initial = local.connect_json().split("\n")
        assert [json.loads(m)["type"] for m in initial] == ["hello", "status", "snapshot"]
        country = json.loads(initial[0])["countries"][0]["code"]
        local_replies: list[list[str]] = []
        for cmd in _script(country):
            raw = local.handle_json(json.dumps(cmd))
            local_replies.append(raw.split("\n") if raw else [])
    finally:
        local.close()

    # The unknown command is ignored by both hosts. It goes last, and the server side
    # then waits briefly to prove no stray reply follows it.
    assert local_replies[-1] == []
    assert all(local_replies[:-1])

    server_initial, server_replies = asyncio.run(
        _server_payloads([len(reply) for reply in local_replies])
    )
    assert server_initial == initial
    assert server_replies == local_replies


def test_local_runtime_clock_is_held_while_paused_or_scrubbed() -> None:
    local = LocalRuntime(SEED, start_running=False)
    try:
        assert local.tick_json() is None
        local.handle_json(json.dumps({"cmd": "resume"}))
        frame = json.loads(local.tick_json() or "{}")
        assert frame["type"] == "frame" and frame["tick"] == 1
        local.handle_json(json.dumps({"cmd": "worldAt", "tick": 0}))
        assert local.tick_json() is None
        local.handle_json(json.dumps({"cmd": "resumeLive"}))
        assert json.loads(local.tick_json() or "{}")["tick"] == 2
        assert local.idle_delay() == 1.0
        assert abs(local.delay(0.25) - 0.75) < 1e-12
    finally:
        local.close()


def test_local_runtime_drops_malformed_commands_like_the_server() -> None:
    local = LocalRuntime(SEED, start_running=False)
    try:
        assert local.handle_json("not json") == ""
        assert local.handle_json("[1, 2]") == ""
        assert local.handle_json('"pause"') == ""
    finally:
        local.close()
