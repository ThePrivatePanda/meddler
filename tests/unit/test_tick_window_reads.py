"""`EventLog.events_between` and `events_of_kinds_between` read short tick windows by index.

The bridge reads one tick of prime's chronicle per frame and the globe a dozen ticks of
shipment history; logistics reads a season. A segment's id bounds cover its whole branch, so
the unhinted plan walked every event in the branch for each of those reads, about 36 ms per
frame at t1000 of seed 1337. Short windows now go through the `events_tick` index. The first
test pins the results to a plain scan of the log on both sides of the 100-tick span threshold
and across a fork's two segments; the second pins a short read's cost to the window rather
than to the length of history.
"""

from __future__ import annotations

from meddler.engine import tickloop
from meddler.engine.events import Event
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world

# The span threshold in events.py; written out so this test also runs against a log that
# predates the hint and shows the two plans agree.
_THRESHOLD = 100


def _world(ticks: int) -> tuple[World, Rng]:
    world = generate_world(1337, WorldSettings(starting_country_count=4))
    rng = Rng(1337)
    for _ in range(ticks):
        tickloop.tick(world, rng)
    return world, rng


def _scan(log: list[Event], start: int, end: int, kinds: tuple[str, ...] | None) -> list[int]:
    return [
        e.id for e in log if start < e.tick <= end and (kinds is None or e.kind in kinds)
    ]


def test_windows_match_a_scan_of_the_log_either_side_of_the_threshold() -> None:
    world, rng = _world(130)
    # A fork: the prefix stays on the parent branch and later ticks land on a new one.
    world.log = world.log.branch()
    for _ in range(30):
        tickloop.tick(world, rng)
    assert len(world.log._all_segments()) == 2

    everything = list(world.log)
    frequency: dict[str, int] = {}
    for event in everything:
        frequency[event.kind] = frequency.get(event.kind, 0) + 1
    common = sorted(frequency, key=lambda k: (-frequency[k], k))
    kind_sets = [None, tuple(common[:1]), tuple(common[1:4]), ("NO_SUCH_KIND",)]
    end = world.tick
    spans = [0, 1, 13, _THRESHOLD - 1, _THRESHOLD, _THRESHOLD + 1, end + 1]
    checked = 0
    for span in spans:
        for at in (end, 128, 131, 5):
            start = at - span
            for kinds in kind_sets:
                expected = _scan(everything, start, at, kinds)
                if kinds is None:
                    got = [e.id for e in world.log.events_between(start, at)]
                else:
                    got = [e.id for e in world.log.events_of_kinds_between(kinds, start, at)]
                assert got == expected, (span, at, kinds)
                checked += 1
    assert checked == len(spans) * 4 * len(kind_sets)


def _short_read_steps(world: World) -> int:
    """SQLite VM steps for the bridge's one-tick read plus a ten-tick kind lookup."""
    world.log.flush()
    connection = world.log._store.connection
    steps = 0

    def count_steps() -> int:
        nonlocal steps
        steps += 1
        return 0

    connection.set_progress_handler(count_steps, 100)
    try:
        window = list(world.log.events_between(world.tick - 1, world.tick))
        world.log.events_of_kinds_between(("NO_SUCH_KIND",), world.tick - 10, world.tick)
    finally:
        connection.set_progress_handler(None, 100)
    assert window
    return steps


def test_a_short_read_costs_its_window_not_the_whole_log() -> None:
    world, rng = _world(150)
    early = _short_read_steps(world)
    length_early = len(world.log)
    for _ in range(150):
        tickloop.tick(world, rng)
    late = _short_read_steps(world)
    assert len(world.log) >= 2 * length_early - length_early // 10

    # Measured on this seed: walking the branch by primary key costs about four VM steps
    # per event in the log, so the same read doubled (523 -> 1027 hundred-steps) as the
    # history doubled. Through the tick index it stayed flat (65 -> 65).
    assert late <= early + early // 5, (early, late, length_early, len(world.log))
