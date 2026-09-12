"""Fork / scrub / focus state machine over a real WebSocket. docs/frontend-contract.md §4.2.

These drive `meddler serve`'s runtime through the god-mode loop the product is built
around -- fork a counterfactual, watch both chronicles, adopt it, scrub back, start a new
world -- and pin the session state each command leaves behind, so no flag can get stuck.

The server starts paused so every tick is an explicit `step`; that keeps the scenarios
deterministic and lets them compare against an independent prime-only reference run.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import serve

from meddler.bridge.runtime import LocalRuntime
from meddler.bridge.server import ServerRuntime
from meddler.engine import annals as engine_annals

SEED = 42
_END = "__end__"
_ids = itertools.count()


class Client:
    def __init__(self, ws: ClientConnection, runtime: ServerRuntime) -> None:
        self.ws = ws
        self.runtime = runtime
        self.initial: list[dict[str, Any]] = []

    async def recv(self, timeout: float = 30.0) -> dict[str, Any]:
        return json.loads(await asyncio.wait_for(self.ws.recv(), timeout))

    async def request(self, **cmd: Any) -> list[dict[str, Any]]:
        """Every direct reply to `cmd`, in order. Replies to one command are sent
        back-to-back, so a trailing sentinel command (an invalid setSpeed, answered with a
        toast) marks where they end. Unsolicited ticker frames are skipped."""
        request_id = f"r{next(_ids)}"
        await self.ws.send(json.dumps({**cmd, "requestId": request_id}))
        await self.ws.send(json.dumps({"cmd": "setSpeed", "tps": -1, "requestId": _END}))
        replies: list[dict[str, Any]] = []
        while True:
            message = await self.recv()
            if message.get("requestId") == _END:
                return replies
            if message.get("requestId") == request_id:
                replies.append(message)

    async def one(self, expected_type: str, **cmd: Any) -> dict[str, Any]:
        replies = await self.request(**cmd)
        matching = [m for m in replies if m["type"] == expected_type]
        assert len(matching) == 1, (expected_type, [m["type"] for m in replies])
        return matching[0]

    async def next_unsolicited(self, expected_type: str, timeout: float = 10.0) -> dict[str, Any]:
        while True:
            message = await self.recv(timeout)
            if message["type"] == expected_type and "requestId" not in message:
                return message

    @property
    def session(self) -> Any:
        return self.runtime.session


def _run(
    scenario: Callable[[Client], Awaitable[Any]],
    *,
    seed: int = SEED,
    start_running: bool = False,
) -> Any:
    async def wrapper() -> Any:
        runtime = ServerRuntime(seed, start_running=start_running)
        await runtime.start()
        server = await serve(runtime.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                client = Client(ws, runtime)
                client.initial = [await client.recv() for _ in range(3)]
                return await scenario(client)
        finally:
            server.close()
            await server.wait_closed()
            await runtime.close()

    return asyncio.run(wrapper())


async def _reconnect_view(client: Client) -> list[dict[str, Any]]:
    """What a second tab (or a reload) would be shown right now."""
    port = client.ws.remote_address[1]
    async with connect(f"ws://127.0.0.1:{port}/ws") as second:
        return [json.loads(await asyncio.wait_for(second.recv(), 30)) for _ in range(3)]


def _steps(client: Client, count: int) -> Awaitable[list[dict[str, Any]]]:
    async def go() -> list[dict[str, Any]]:
        return [await client.one("frame", cmd="step") for _ in range(count)]

    return go()


def _reference_prime_events(seed: int, through_tick: int) -> dict[int, list[dict[str, Any]]]:
    """Prime's per-tick chronicle from an independent, never-forked run."""
    local = LocalRuntime(seed, start_running=False)
    try:
        by_tick: dict[int, list[dict[str, Any]]] = {}
        for _ in range(through_tick):
            raw = local.handle_json(json.dumps({"cmd": "step"}))
            frame = [json.loads(line) for line in raw.split("\n")][-1]
            by_tick[frame["tick"]] = frame["timelines"]["A"]["events"]
        return by_tick
    finally:
        local.close()


def _first_country(client: Client) -> str:
    return str(client.initial[0]["countries"][0]["code"])


# --- intervene -------------------------------------------------------------------------


def test_intervene_without_at_tick_forks_at_prime_present() -> None:
    async def scenario(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        await _steps(client, 3)
        replies = await client.request(
            cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client)
        )
        started = next(m for m in replies if m["type"] == "forkStarted")
        status = next(m for m in replies if m["type"] == "status")
        return started, status

    started, status = _run(scenario)
    assert started["tick"] == 3
    assert status["focus"] == started["id"]


def test_intervene_without_at_tick_while_scrubbed_forks_at_the_viewed_tick() -> None:
    async def scenario(client: Client) -> dict[str, Any]:
        await _steps(client, 6)
        await client.one("snapshot", cmd="worldAt", tick=2)
        return await client.one(
            "forkStarted", cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client)
        )

    assert _run(scenario)["tick"] == 2


def test_fork_from_scrubbed_past_leaves_scrub_and_restores_the_running_clock() -> None:
    async def scenario(client: Client) -> tuple[dict[str, Any], dict[str, Any], bool]:
        await _steps(client, 6)
        await client.one("status", cmd="setSpeed", tps=4)
        await client.one("status", cmd="resume")
        # Prime is now ticking; scrubbing holds the clock, forking must release it again.
        scrub = await client.one("snapshot", cmd="worldAt", tick=3)
        replies = await client.request(
            cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client), atTick=3
        )
        started = next(m for m in replies if m["type"] == "forkStarted")
        status = next(m for m in replies if m["type"] == "status")
        frame = await client.next_unsolicited("frame")
        assert frame["focus"] == started["id"]
        assert frame["tick"] > started["tick"]
        assert "B" in frame["timelines"]
        return scrub, status, client.session.scrub_tick is None

    scrub, status, scrub_cleared = _run(scenario)
    assert scrub["scrubbed"] is True
    assert status["running"] is True
    assert scrub_cleared


def test_fork_from_scrub_while_paused_stays_paused_but_is_no_longer_scrubbed() -> None:
    async def scenario(client: Client) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await _steps(client, 5)
        await client.one("snapshot", cmd="worldAt", tick=2)
        replies = await client.request(
            cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client), atTick=2
        )
        status = next(m for m in replies if m["type"] == "status")
        return status, await _reconnect_view(client)

    status, reconnect = _run(scenario)
    assert status["running"] is False
    # A reload lands in the fork's two-column view, not in a stale scrub snapshot.
    assert reconnect[2]["type"] == "timelineFocus"


# --- fork frames: both chronicles ------------------------------------------------------


def test_fork_at_present_frames_carry_prime_and_fork_chronicles() -> None:
    fork_tick, steps = 4, 25

    async def scenario(client: Client) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        await _steps(client, fork_tick)
        started = await client.one(
            "forkStarted", cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client)
        )
        frames = await _steps(client, steps)
        fork = client.session.multiverse.forks[started["id"]]
        # Every templated event the fork recorded after its intervention tick.
        from meddler.bridge import adapter

        expected_b = adapter.renderable_events(
            list(fork.world.log.events_between(fork_tick, fork_tick + steps)), fork.world
        )
        return frames, expected_b, started["id"]

    frames, expected_b, fork_id = _run(scenario)
    reference = _reference_prime_events(SEED, fork_tick + steps)
    for frame in frames:
        assert frame["focus"] == fork_id
        tick = frame["tick"]
        assert frame["liveTick"] == tick  # forked at the present: prime keeps pace
        assert frame["timelines"]["A"]["events"] == reference[tick]
        assert all(event["tick"] == tick for event in frame["timelines"]["B"]["events"])
    delivered_b = [event for frame in frames for event in frame["timelines"]["B"]["events"]]
    assert [event["id"] for event in delivered_b] == [event["id"] for event in expected_b]
    assert delivered_b, "a drought fork should produce a chronicle of its own"


def test_fork_in_the_past_shows_prime_history_at_the_aligned_tick() -> None:
    async def scenario(client: Client) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        await _steps(client, 10)
        await client.one("snapshot", cmd="worldAt", tick=4)
        await client.one(
            "forkStarted",
            cmd="intervene",
            kind="INTERVENE_DROUGHT",
            country=_first_country(client),
            atTick=4,
        )
        behind = await _steps(client, 3)  # fork t5..t7, prime still at t10
        catching_up = await _steps(client, 5)  # fork t8..t12: prime resumes at t11
        return behind, catching_up

    behind, catching_up = _run(scenario)
    reference = _reference_prime_events(SEED, 12)
    for frame in behind + catching_up:
        assert frame["timelines"]["A"]["events"] == reference[frame["tick"]]
    assert [f["tick"] for f in behind] == [5, 6, 7]
    assert [f["liveTick"] for f in behind] == [10, 10, 10]
    assert [f["liveTick"] for f in catching_up] == [10, 10, 10, 11, 12]


def test_snapshot_reports_where_live_is() -> None:
    async def scenario(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        await _steps(client, 5)
        scrub = await client.one("snapshot", cmd="worldAt", tick=2)
        live = await client.one("snapshot", cmd="resumeLive")
        return scrub, live

    scrub, live = _run(scenario)
    assert (scrub["tick"], scrub["liveTick"], scrub["live"], scrub["scrubbed"]) == (2, 5, False, True)
    assert (live["tick"], live["liveTick"], live["live"], live["scrubbed"]) == (5, 5, True, False)


# --- focus / scrub / step interactions -------------------------------------------------


def test_world_at_while_fork_focused_then_resume_live_returns_to_the_fork_view() -> None:
    async def scenario(client: Client) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await _steps(client, 6)
        started = await client.one(
            "forkStarted", cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client)
        )
        await _steps(client, 2)
        scrub = await client.one("snapshot", cmd="worldAt", tick=3)
        back = await client.request(cmd="resumeLive")
        assert client.session.focused_fork_id == started["id"]
        return scrub, back

    scrub, back = _run(scenario)
    assert scrub["tick"] == 3 and scrub["scrubbed"] is True
    assert [m["type"] for m in back] == ["timelineFocus", "status"]
    assert back[0]["tick"] == 8


def test_focus_and_step_while_scrubbed_leave_the_scrub_view() -> None:
    async def scenario(client: Client) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
        await _steps(client, 4)
        await client.one("snapshot", cmd="worldAt", tick=1)
        focus = await client.request(cmd="focusTimeline", id="A")
        scrub_after_focus = client.session.scrub_tick
        await client.one("snapshot", cmd="worldAt", tick=2)
        step = await client.request(cmd="step")
        assert scrub_after_focus is None
        return focus, step, client.session.scrub_tick

    focus, step, scrub_tick = _run(scenario)
    assert focus[0]["type"] == "snapshot" and focus[0]["scrubbed"] is False
    assert [m["type"] for m in step] == ["snapshot", "frame"]
    assert step[0]["scrubbed"] is False and step[1]["tick"] == 5
    assert scrub_tick is None


def test_drop_fork_names_the_fork_and_returns_to_the_prime_view() -> None:
    async def scenario(client: Client) -> list[dict[str, Any]]:
        await _steps(client, 2)
        started = await client.one(
            "forkStarted", cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client)
        )
        return await client.request(cmd="dropFork", id=started["id"])

    replies = _run(scenario)
    assert [m["type"] for m in replies] == ["forkDropped", "snapshot", "status"]
    assert replies[0]["id"] == "B" and replies[0]["kept"] == "A"
    assert replies[1]["live"] is True and replies[2]["forks"] == []


def test_adopt_fork_announces_the_adoption_then_the_new_prime() -> None:
    async def scenario(client: Client) -> tuple[list[dict[str, Any]], int]:
        await _steps(client, 3)
        started = await client.one(
            "forkStarted", cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client)
        )
        await _steps(client, 2)
        replies = await client.request(cmd="adoptFork", id=started["id"])
        return replies, client.session.multiverse.prime.world.tick

    replies, prime_tick = _run(scenario)
    assert [m["type"] for m in replies] == ["forkAdopted", "snapshot", "status"]
    assert replies[0]["id"] == "B" and replies[0]["tick"] == prime_tick == 5
    assert replies[1]["tick"] == 5 and replies[1]["liveTick"] == 5
    assert replies[2]["forks"] == [] and replies[2]["focus"] is None


# --- god edits -------------------------------------------------------------------------


def test_god_edit_targets_the_focused_fork_and_keeps_the_two_column_view() -> None:
    async def scenario(client: Client) -> tuple[list[dict[str, Any]], str, float, float]:
        code = _first_country(client)
        await _steps(client, 2)
        started = await client.one(
            "forkStarted", cmd="intervene", kind="INTERVENE_DROUGHT", country=code
        )
        replies = await client.request(cmd="godEdit", code=code, field="stability", value=12.5)
        prime = client.session.multiverse.prime.world.country(code).stability
        fork = client.session.multiverse.forks[started["id"]].world.country(code).stability
        return replies, code, prime, fork

    replies, code, prime_stability, fork_stability = _run(scenario)
    assert [m["type"] for m in replies] == ["timelineFocus"]
    assert replies[0]["statsB"][code]["stability"] == 12.5
    assert replies[0]["statsA"][code]["stability"] != 12.5
    assert fork_stability == 12.5 and prime_stability != 12.5


def test_god_edit_while_scrubbed_is_refused_rather_than_mislabelled() -> None:
    async def scenario(client: Client) -> tuple[list[dict[str, Any]], float, float]:
        code = _first_country(client)
        await _steps(client, 3)
        before = client.session.multiverse.prime.world.country(code).stability
        await client.one("snapshot", cmd="worldAt", tick=1)
        replies = await client.request(cmd="godEdit", code=code, field="stability", value=3.0)
        return replies, before, client.session.multiverse.prime.world.country(code).stability

    replies, before, after = _run(scenario)
    assert [m["type"] for m in replies] == ["toast"]
    assert replies[0]["tone"] == "warn"
    assert after == before


# --- new world -------------------------------------------------------------------------


def test_new_world_regenerates_with_restart_required_settings() -> None:
    async def scenario(client: Client) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
        await _steps(client, 4)
        await client.one(
            "forkStarted", cmd="intervene", kind="INTERVENE_DROUGHT", country=_first_country(client)
        )
        ack = await client.one(
            "settingsAck", cmd="updateSettings", settings={"starting_country_count": 5}
        )
        replies = await client.request(cmd="newWorld")
        stale = await client.request(cmd="worldAt", tick=3)
        return ack, replies, stale

    ack, replies, stale = _run(scenario)
    assert "starting_country_count" in ack["restartRequired"]
    assert [m["type"] for m in replies] == ["hello", "status", "snapshot"]
    hello, status, snapshot = replies
    assert hello["seed"] == SEED
    assert len(hello["countries"]) == 5
    assert hello["settings"]["starting_country_count"] == 5
    assert status["forks"] == [] and status["focus"] is None
    assert snapshot["tick"] == 0 and snapshot["liveTick"] == 0 and snapshot["live"] is True
    assert set(snapshot["stats"]) == {c["code"] for c in hello["countries"]}
    # The old history is gone, honestly: there is no tick 3 in the new world yet.
    assert [m["type"] for m in stale] == ["toast"]


def test_new_world_with_a_seed_is_that_seeds_genesis() -> None:
    async def scenario(client: Client) -> dict[str, Any]:
        await _steps(client, 2)
        return await client.one("hello", cmd="newWorld", seed=7)

    hello = _run(scenario)
    fresh = LocalRuntime(7, start_running=False)
    try:
        expected = json.loads(fresh.connect_json().split("\n")[0])
    finally:
        fresh.close()
    assert hello["seed"] == 7
    assert hello["countries"] == expected["countries"]
    assert hello["worldObjects"] == expected["worldObjects"]


# --- annals ----------------------------------------------------------------------------


def test_annals_projects_the_engines_real_history() -> None:
    async def scenario(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        await _steps(client, 60)
        data = await client.one("annalsData", cmd="annals")
        world = client.session.multiverse.prime.world
        expected = engine_annals.annals(world)
        scrubbed = await client.one("annalsData", cmd="annals", tick=30)
        return data, expected, scrubbed

    data, expected, scrubbed = _run(scenario, seed=1337)
    assert data["tl"] == "A" and data["tick"] == 60 and data["country"] is None
    assert [era["startTick"] for era in data["eras"]] == [e["start_tick"] for e in expected["eras"]]
    assert [era["dominantKind"] for era in data["eras"]] == [
        e["dominant_kind"] for e in expected["eras"]
    ]
    assert [(w["aggressor"], w["defender"], w["startTick"], w["endTick"]) for w in data["wars"]] == [
        (w["aggressor"], w["defender"], w["start_tick"], w["end_tick"]) for w in expected["wars"]
    ]
    assert data["majorEventCount"] == len(expected["majorEvents"])
    assert [e["id"] for e in data["majorEvents"]] == [
        e["id"] for e in expected["majorEvents"]
    ][-len(data["majorEvents"]):]
    assert all(isinstance(e["headline"], str) and e["headline"] for e in data["majorEvents"])
    assert scrubbed["tick"] == 30
    assert all(e["tick"] <= 30 for e in scrubbed["majorEvents"])


def test_scrubbing_past_the_live_edge_is_refused_not_invented() -> None:
    async def scenario(client: Client) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        await _steps(client, 4)
        return (
            await client.request(cmd="worldAt", tick=40),
            await client.request(cmd="worldAt", tick=-3),
        )

    ahead, before_genesis = _run(scenario)
    assert [m["type"] for m in ahead] == ["toast"] and ahead[0]["tone"] == "warn"
    assert [m["type"] for m in before_genesis] == ["toast"]


def test_a_country_born_mid_run_is_announced_before_the_frame_that_carries_it() -> None:
    """The engine creates countries at runtime (INTERVENE_SECEDE today, organic secession
    later); the bridge must name one the moment it appears. The nation here is planted by
    hand so the detection is tested without depending on an engine path."""
    import copy

    local = LocalRuntime(SEED, start_running=True)
    try:
        json.loads(local.tick_json() or "{}")
        world = local.session.multiverse.prime.world
        newborn = copy.deepcopy(world.countries[0])
        newborn.code = "ZZZ"
        newborn.name = "Later Republic"
        newborn.parent_code = world.countries[0].code
        newborn.born_at_tick = world.tick + 1
        parent_code = newborn.parent_code
        world.countries.append(newborn)
        messages = [json.loads(line) for line in (local.tick_json() or "").split("\n")]
        # Announced exactly once: the tick after is an ordinary frame.
        later = [json.loads(line) for line in (local.tick_json() or "").split("\n")]
    finally:
        local.close()

    assert [m["type"] for m in messages] == ["countryAdded", "frame"]
    added, frame = messages
    assert added["tl"] == "A"
    assert added["tick"] == frame["tick"]
    assert added["parent"] == parent_code
    assert added["country"]["code"] == "ZZZ"
    assert added["country"]["name"] == "Later Republic"
    assert added["country"]["bornAt"] == frame["tick"]
    assert added["worldObject"]["code"] == "ZZZ"
    assert "ZZZ" in frame["timelines"]["A"]["stats"]
    assert [m["type"] for m in later] == ["frame"]


def test_restart_required_settings_cover_every_genesis_field() -> None:
    from meddler.bridge.commands import RESTART_REQUIRED_SETTINGS

    assert "friendly_pairs" in RESTART_REQUIRED_SETTINGS
