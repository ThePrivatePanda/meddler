"""M6: bridge handshake + core commands. docs/frontend-contract.md.

No pytest-asyncio dependency (kept out of dev-deps deliberately) -- each test wraps a
single `asyncio.run(...)` driving the whole scenario as one coroutine: start the server
on an OS-assigned ephemeral port (safe under parallel test workers, unlike the fixed
production port 7677), connect a real `websockets` client, drive the protocol, tear down.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, serve

from meddler.bridge.server import ServerRuntime

SEED = 42


async def _start_server(seed: int = SEED) -> tuple[Server, int, ServerRuntime]:
    runtime = ServerRuntime(seed)
    await runtime.start()
    server = await serve(runtime.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port, runtime


async def _recv(ws: ClientConnection) -> dict[str, Any]:
    return json.loads(await ws.recv())


async def _send(ws: ClientConnection, **cmd: Any) -> None:
    await ws.send(json.dumps(cmd))


def _run(scenario: Any) -> Any:
    async def wrapper() -> Any:
        server, port, runtime = await _start_server()
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                return await scenario(ws)
        finally:
            server.close()
            await server.wait_closed()
            await runtime.close()

    return asyncio.run(wrapper())


def test_hello_status_snapshot_handshake_and_full_command_tour():
    """One connection, driven through the full command set in sequence -- cheaper than
    spinning up a server per behavior, and exercises command ordering the way a real
    client would."""

    async def scenario(ws: ClientConnection) -> dict[str, Any]:
        results: dict[str, Any] = {}

        hello = await _recv(ws)
        status = await _recv(ws)
        snapshot = await _recv(ws)
        results["hello"] = hello
        results["status_initial"] = status
        results["snapshot_initial"] = snapshot

        code = hello["countries"][0]["code"]
        other_code = hello["countries"][1]["code"]

        # pause / resume
        await _send(ws, cmd="pause")
        results["status_paused"] = await _recv(ws)
        await _send(ws, cmd="resume")
        results["status_resumed"] = await _recv(ws)
        await _send(ws, cmd="pause")
        await _recv(ws)

        # setSpeed: valid then invalid
        await _send(ws, cmd="setSpeed", tps=4)
        results["status_speed"] = await _recv(ws)
        await _send(ws, cmd="setSpeed", tps=3)
        results["toast_bad_speed"] = await _recv(ws)

        # step
        await _send(ws, cmd="step")
        results["frame_step"] = await _recv(ws)

        # worldAt / resumeLive
        await _send(ws, cmd="worldAt", tick=0)
        results["snapshot_scrubbed"] = await _recv(ws)
        results["status_scrubbed"] = await _recv(ws)
        await _send(ws, cmd="resumeLive")
        results["snapshot_resumed"] = await _recv(ws)
        results["status_after_resume_live"] = await _recv(ws)

        # intervene -> fork
        await _send(ws, cmd="intervene", kind="INTERVENE_DROUGHT", country=code, atTick=1)
        results["fork_started"] = await _recv(ws)
        results["status_after_intervene"] = await _recv(ws)

        fork_id = results["fork_started"]["id"]
        await _send(ws, cmd="focusTimeline", id=fork_id)
        results["timeline_focus"] = await _recv(ws)
        results["status_after_focus"] = await _recv(ws)
        await _send(ws, cmd="step")
        results["fork_frame"] = await _recv(ws)

        # dropFork
        await _send(ws, cmd="dropFork", id=fork_id)
        results["fork_dropped"] = await _recv(ws)
        results["snapshot_after_drop"] = await _recv(ws)
        results["status_after_drop"] = await _recv(ws)

        # intervene again, then adopt
        await _send(ws, cmd="intervene", kind="INTERVENE_MINT", country=code, atTick=1)
        results["fork_started_2"] = await _recv(ws)
        results["status_after_intervene_2"] = await _recv(ws)
        fork_id_2 = results["fork_started_2"]["id"]
        await _send(ws, cmd="adoptFork", id=fork_id_2)
        results["fork_adopted"] = await _recv(ws)
        results["snapshot_after_adopt"] = await _recv(ws)
        results["status_after_adopt"] = await _recv(ws)

        # godEdit / godRelation / godPeace
        await _send(ws, cmd="godEdit", code=code, field="stability", value=17.5)
        results["snapshot_god_edit"] = await _recv(ws)

        await _send(ws, cmd="godRelation", a=code, b=other_code, delta=25.0)
        results["snapshot_god_relation"] = await _recv(ws)

        await _send(ws, cmd="godPeace", code=code)
        results["snapshot_god_peace"] = await _recv(ws)

        # countryDetail
        await _send(ws, cmd="countryDetail", code=code)
        results["country_detail"] = await _recv(ws)
        detail_ticks = results["country_detail"]["ticks"]
        chart_end = detail_ticks[-1]
        chart_start = detail_ticks[-2] if len(detail_ticks) > 1 else chart_end - 1
        await _send(
            ws,
            cmd="countryChartEvents",
            code=code,
            tl="A",
            startTick=chart_start,
            endTick=chart_end,
        )
        results["country_chart_events"] = await _recv(ws)

        # updateSettings
        await _send(
            ws,
            cmd="updateSettings",
            settings={"drama_multiplier": 2.5, "starting_country_count": 6},
        )
        results["settings_ack"] = await _recv(ws)

        # restart -- tick=1, not 0: after adoptFork above, prime IS the promoted fork.
        # `world_at` before its branch point works (forks inherit prime's snapshot
        # lineage), but `restart` to such a tick does not: EventLog.truncate_after
        # refuses to cut into the shared history prefix. tick=1 is exactly the branch
        # point, which is always safe.
        await _send(ws, cmd="restart", tick=1)
        results["snapshot_after_restart"] = await _recv(ws)
        results["status_after_restart"] = await _recv(ws)

        results["code"] = code
        results["other_code"] = other_code
        return results

    r = _run(scenario)

    # hello
    assert r["hello"]["type"] == "hello"
    assert r["hello"]["protocol"] == 2
    assert r["hello"]["worldObjects"]["tick"] == 0
    assert set(r["hello"]["worldObjects"]["countries"]) == {
        country["code"] for country in r["hello"]["countries"]
    }
    assert r["hello"]["seed"] == SEED
    assert len(r["hello"]["countries"]) == 8  # WorldSettings() default starting_country_count
    assert len(r["hello"]["interventions"]) == 20  # 15 base + 5 §6.7.4 infra interventions (M7.1)
    assert all({"kind", "label", "icon", "desc"} <= set(iv) for iv in r["hello"]["interventions"])
    # settings in hello (M8.2b: so the settings overlay can render current values on load)
    assert isinstance(r["hello"]["settings"], dict)
    assert (
        "drama_multiplier" in r["hello"]["settings"] and "allow_conquest" in r["hello"]["settings"]
    )

    # status / snapshot
    assert r["status_initial"]["type"] == "status"
    assert r["status_initial"]["running"] is True
    assert r["snapshot_initial"]["type"] == "snapshot"
    assert r["snapshot_initial"]["live"] is True
    assert set(r["snapshot_initial"]["stats"]) == {c["code"] for c in r["hello"]["countries"]}
    assert r["snapshot_initial"]["worldObjects"] == r["hello"]["worldObjects"]

    # spark (M8.2a: sparkline history buffer) -- per country, three stat arrays
    spark = r["snapshot_initial"]["spark"]
    assert set(spark) == {c["code"] for c in r["hello"]["countries"]}
    sample = spark[r["hello"]["countries"][0]["code"]]
    assert set(sample) == {"stability", "inflation", "fx"}
    assert all(isinstance(sample[k], list) and sample[k] for k in sample)

    # pause/resume
    assert r["status_paused"]["running"] is False
    assert r["status_resumed"]["running"] is True

    # setSpeed
    assert r["status_speed"]["tps"] == 4
    assert r["toast_bad_speed"]["type"] == "toast"
    assert r["toast_bad_speed"]["tone"] == "warn"

    # step
    assert r["frame_step"]["type"] == "frame"
    assert r["frame_step"]["tick"] == 1
    assert r["frame_step"]["timelines"]["A"]["worldObjects"]["tick"] == 1

    # worldAt/resumeLive
    assert r["snapshot_scrubbed"]["scrubbed"] is True
    assert r["snapshot_scrubbed"]["live"] is False
    assert r["snapshot_scrubbed"]["tick"] == 0
    assert r["snapshot_scrubbed"]["worldObjects"]["tick"] == 0
    assert r["snapshot_resumed"]["live"] is True
    assert r["snapshot_resumed"]["worldObjects"]["tick"] == 1
    assert r["snapshot_resumed"]["scrubbed"] is False

    # intervene / forkStarted
    fork_started = r["fork_started"]
    assert fork_started["type"] == "forkStarted"
    assert fork_started["tick"] == 1
    assert fork_started["worldObjects"]["tick"] == 1
    assert r["status_after_intervene"]["forks"]
    assert r["status_after_intervene"]["focus"] == fork_started["id"]

    # focusTimeline on a fork -> timelineFocus
    assert r["timeline_focus"]["type"] == "timelineFocus"
    assert r["timeline_focus"]["id"] == fork_started["id"]
    assert "statsA" in r["timeline_focus"] and "statsB" in r["timeline_focus"]
    assert r["timeline_focus"]["worldObjectsA"]["tick"] == 1
    assert r["timeline_focus"]["worldObjectsB"]["tick"] == 1
    fork_frame = r["fork_frame"]
    assert fork_frame["timelines"]["A"]["worldObjects"]["tick"] == fork_frame["tick"]
    assert fork_frame["timelines"]["B"]["worldObjects"]["tick"] == fork_frame["tick"]

    # dropFork -- names the discarded fork, then re-states the view that is left
    assert r["fork_dropped"]["type"] == "forkDropped"
    assert r["fork_dropped"]["id"] == fork_started["id"]
    assert r["fork_dropped"]["kept"] == "A"
    assert r["snapshot_after_drop"]["type"] == "snapshot"
    assert r["status_after_drop"]["forks"] == []
    assert r["status_after_drop"]["focus"] is None

    # second intervene + adopt
    assert r["fork_started_2"]["type"] == "forkStarted"
    assert r["status_after_intervene_2"]["forks"]
    assert r["fork_adopted"]["type"] == "forkAdopted"
    assert r["fork_adopted"]["id"] == r["fork_started_2"]["id"]
    assert r["snapshot_after_adopt"]["type"] == "snapshot"
    assert r["status_after_adopt"]["forks"] == []
    assert r["status_after_adopt"]["focus"] is None

    # god edits
    code = r["code"]
    assert r["snapshot_god_edit"]["stats"][code]["stability"] == 17.5
    # +25 relation delta was applied on top of whatever the seeded value was; just
    # confirm the snapshot round-trips without error and stats are present.
    assert code in r["snapshot_god_relation"]["stats"]
    assert r["snapshot_god_peace"]["stats"][code]["war"] is False

    # countryDetail
    detail = r["country_detail"]
    assert detail["type"] == "countryDetail"
    assert detail["code"] == code
    assert "stats" in detail and "leader" in detail
    assert detail["worldObject"]["code"] == code
    assert "territories" in detail["worldObject"] and "assets" in detail["worldObject"]
    # commodities (M10, v2 §3): all six buckets, each numeric output/need/stock, dossier-only
    assert set(detail["commodities"]) == {
        "food",
        "energy",
        "raw_materials",
        "manufactured",
        "consumer",
        "high_tech",
    }
    for bucket in detail["commodities"].values():
        assert set(bucket) == {"output", "need", "stock"}
        assert all(isinstance(bucket[k], (int, float)) for k in bucket)
    # bloc (M11): null when unaligned, else {id, members, formedAt}
    assert "bloc" in detail
    if detail["bloc"] is not None:
        assert set(detail["bloc"]) == {"id", "members", "formedAt"}
    # series/ticks (M8.2a: full-history charts) -- all five charted stats, downsampled
    assert set(detail["series"]) == {"stability", "inflation", "gdp", "fx", "treasury"}
    assert len(detail["ticks"]) <= 240
    assert all(len(detail["series"][k]) == len(detail["ticks"]) for k in detail["series"])
    assert detail["ticks"]
    assert detail["tl"] == "A"
    assert detail["chartEventMode"] == "interval"
    assert detail["chartEvents"] == []

    chart_events = r["country_chart_events"]
    assert chart_events["type"] == "countryChartEvents"
    assert chart_events["tl"] == "A"
    assert chart_events["code"] == code
    assert len(chart_events["events"]) <= 6
    assert chart_events["total"] >= len(chart_events["events"])
    assert all(
        chart_events["startTick"] < event["tick"] <= chart_events["endTick"]
        for event in chart_events["events"]
    )
    assert all(
        event["country"] == code
        or event["country2"] == code
        or any(
            effect["target"] == code or effect["target"].startswith(f"{code}.")
            for effect in event["effects"]
        )
        for event in chart_events["events"]
    )

    # updateSettings
    ack = r["settings_ack"]
    assert ack["type"] == "settingsAck"
    assert ack["settings"]["drama_multiplier"] == 2.5
    assert ack["settings"]["starting_country_count"] == 6
    assert "starting_country_count" in ack["restartRequired"]
    assert "drama_multiplier" not in ack["restartRequired"]

    # restart
    assert r["snapshot_after_restart"]["tick"] == 1
    assert r["snapshot_after_restart"]["live"] is True
    assert r["status_after_restart"]["type"] == "status"


def test_unknown_command_is_silently_ignored_not_fatal():
    async def scenario(ws: ClientConnection) -> dict[str, Any]:
        await _recv(ws)  # hello
        await _recv(ws)  # status
        await _recv(ws)  # snapshot
        await _send(ws, cmd="notARealCommand")
        # Connection must still be alive and respond to a real command afterward.
        await _send(ws, cmd="pause")
        return await _recv(ws)

    status = _run(scenario)
    assert status["type"] == "status"
    assert status["running"] is False


def test_dropping_nonexistent_fork_returns_toast_not_crash():
    async def scenario(ws: ClientConnection) -> dict[str, Any]:
        await _recv(ws)
        await _recv(ws)
        await _recv(ws)
        await _send(ws, cmd="dropFork", id="B")
        return await _recv(ws)

    toast = _run(scenario)
    assert toast["type"] == "toast"
    assert toast["tone"] == "warn"


def test_trace_a_fresh_intervention_event():
    """The intervention itself is always traceable as a root event -- trace it by id
    without depending on whether its declarative consequences happened to fire."""

    async def scenario(ws: ClientConnection) -> dict[str, Any]:
        hello = await _recv(ws)
        await _recv(ws)
        await _recv(ws)
        code = hello["countries"][0]["code"]
        await _send(ws, cmd="intervene", kind="INTERVENE_DROUGHT", country=code, atTick=0)
        fork_started = await _recv(ws)
        await _recv(ws)  # status
        fork_id = fork_started["id"]
        # The intervention is the last event in the fork's shared-recent-or-own log at
        # tick 0; ask the fork's own recent history via countryDetail-free route: trace
        # id 0 on timeline "A" won't exist yet at tick 0 (no organic events guaranteed),
        # so trace the fork itself starting from its last known event id.
        await _send(ws, cmd="countryDetail", code=code, tl=fork_id)
        detail = await _recv(ws)
        assert detail["recentEvents"], (
            "expected the intervention to appear in the fork's recent events"
        )
        event_id = detail["recentEvents"][-1]["id"]
        await _send(ws, cmd="trace", eventId=event_id, tl=fork_id)
        return await _recv(ws)

    trace = _run(scenario)
    assert trace["type"] == "trace"
    assert trace["nodes"]
    assert trace["nodes"][0]["intervention"] is True


def test_tick_cadence_accounts_for_engine_and_frame_work() -> None:
    from meddler.bridge.server import _next_tick_delay

    assert abs(_next_tick_delay(4.0, 0.08) - 0.17) < 1e-12
    assert _next_tick_delay(4.0, 0.30) == 0.0


def test_request_id_echoes_on_success_error_and_every_multi_reply() -> None:
    async def scenario(ws: ClientConnection) -> dict[str, Any]:
        hello = await _recv(ws)
        await _recv(ws)
        await _recv(ws)
        await _send(ws, cmd="pause", requestId="pause-1")
        paused = await _recv(ws)
        await _send(ws, cmd="dropFork", id="missing")
        uncorrelated = await _recv(ws)
        await _send(
            ws,
            cmd="intervene",
            kind="INTERVENE_DROUGHT",
            country=hello["countries"][0]["code"],
            atTick=0,
            requestId="fork-1",
        )
        fork_started = await _recv(ws)
        fork_status = await _recv(ws)
        return {
            "paused": paused,
            "uncorrelated": uncorrelated,
            "fork_started": fork_started,
            "fork_status": fork_status,
        }

    result = _run(scenario)
    assert result["paused"]["requestId"] == "pause-1"
    assert "requestId" not in result["uncorrelated"]
    assert result["fork_started"]["requestId"] == "fork-1"
    assert result["fork_status"]["requestId"] == "fork-1"


def test_reload_reconnects_to_same_world_settings_and_database_then_shutdown_cleans_up() -> None:
    async def scenario() -> tuple[int, int, float, str, bool]:
        server, port, runtime = await _start_server(seed=77)
        database_path = runtime.session.multiverse.prime.world.log.database_path
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as first:
                await _recv(first)
                await _recv(first)
                initial = await _recv(first)
                await _send(first, cmd="pause")
                await _recv(first)
                await _send(first, cmd="step")
                stepped = await _recv(first)
                await _send(
                    first,
                    cmd="updateSettings",
                    settings={"drama_multiplier": 2.5},
                )
                await _recv(first)
            async with connect(f"ws://127.0.0.1:{port}/ws") as second:
                hello = await _recv(second)
                status = await _recv(second)
                snapshot = await _recv(second)
                assert status["running"] is False
                assert snapshot["tick"] == stepped["tick"]
                assert snapshot["tick"] > initial["tick"]
                persisted_tick = snapshot["tick"]
                persisted_setting = hello["settings"]["drama_multiplier"]
            same_database = (
                runtime.session.multiverse.prime.world.log.database_path == database_path
            )
        finally:
            server.close()
            await server.wait_closed()
            await runtime.close()
        from pathlib import Path

        return initial["tick"], persisted_tick, persisted_setting, database_path, (
            same_database and not Path(database_path).exists()
        )

    initial_tick, persisted_tick, setting, _path, cleaned = asyncio.run(scenario())
    assert persisted_tick > initial_tick
    assert setting == 2.5
    assert cleaned is True


def test_paused_focused_fork_reconnect_restores_prime_live_tick() -> None:
    async def scenario() -> tuple[dict[str, Any], int]:
        server, port, runtime = await _start_server(seed=78)
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as first:
                hello = await _recv(first)
                await _recv(first)
                await _recv(first)
                await _send(first, cmd="pause")
                await _recv(first)
                await _send(
                    first,
                    cmd="intervene",
                    kind="INTERVENE_DROUGHT",
                    country=hello["countries"][0]["code"],
                    atTick=0,
                )
                fork_started = await _recv(first)
                await _recv(first)
            async with connect(f"ws://127.0.0.1:{port}/ws") as second:
                await _recv(second)
                status = await _recv(second)
                focused = await _recv(second)
                assert status["running"] is False
                assert status["focus"] == fork_started["id"]
                return focused, runtime.session.multiverse.prime.world.tick
        finally:
            server.close()
            await server.wait_closed()
            await runtime.close()

    focused, prime_tick = asyncio.run(scenario())
    assert focused["type"] == "timelineFocus"
    assert focused["live"] == prime_tick
