"""world_at reconstructs the WHOLE world, not just what events record.

Events rebuild money, stats, and structural state. The consequence queue, threshold
hysteresis arms, and election dates are end-of-tick state no event carries; the timeline
records them per tick. These tests compare every World field (the log aside) between the
live run and reconstructions taken mid snapshot-interval, and check that a no-intervention
fork taken mid-interval replays prime's future event for event.
"""

from __future__ import annotations

import copy
from dataclasses import fields

from meddler.engine import tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _timeline(seed: int, snapshot_interval: int = 50) -> Timeline:
    settings = WorldSettings(starting_country_count=6, snapshot_interval=snapshot_interval)
    return Timeline(seed=seed, world=generate_world(seed, settings), snapshots={}, rng=Rng(seed))


def _state(world: World) -> dict[str, object]:
    return copy.deepcopy({f.name: getattr(world, f.name) for f in fields(world) if f.name != "log"})


def _assert_close(path: str, a: object, b: object) -> None:
    if isinstance(a, float) and isinstance(b, float):
        assert abs(a - b) <= 1e-9 * max(1.0, abs(a)), path
    elif isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), path
        for key in a:
            _assert_close(f"{path}[{key}]", a[key], b[key])
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _assert_close(f"{path}[{i}]", x, y)
    elif hasattr(a, "__dataclass_fields__") and type(a) is type(b):
        for f in fields(a):  # type: ignore[arg-type]
            _assert_close(f"{path}.{f.name}", getattr(a, f.name), getattr(b, f.name))
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def test_world_at_mid_interval_matches_every_live_field() -> None:
    tl = _timeline(1337)
    live: dict[int, dict[str, object]] = {}
    for _ in range(320):
        tl.advance()
        if tl.world.tick % 23 == 7:
            live[tl.world.tick] = _state(tl.world)
    for tick, expected in live.items():
        _assert_close(f"t{tick}", expected, _state(tl.world_at(tick)))


def test_fork_taken_mid_interval_tracks_prime_event_for_event() -> None:
    tl = _timeline(1337)
    for _ in range(200):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork = mv.forks[mv.fork(at_tick=137, intervention=None)]
    # Prime has already moved on; the fork must still mirror prime's history from t137.
    replay = _timeline(1337)
    for _ in range(137):
        replay.advance()
    for _ in range(120):
        expected = [(e.kind, e.country, e.country2, e.payload) for e in replay.advance()]
        got = [(e.kind, e.country, e.country2, e.payload) for e in fork.advance()]
        assert got == expected, f"fork diverged at t{fork.world.tick}"
