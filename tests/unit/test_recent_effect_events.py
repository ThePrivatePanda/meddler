"""`EventLog.recent_effect_events` returns exactly what a full scan of the log would.

It is the lookup behind the contributing-cause links a threshold or tariff headline carries,
so its results are part of the causal graph and must not depend on how SQLite chooses to
find them. Left to the planner, the query walked the whole events table newest-first and
probed each event for a matching effect: one tariff lookup at t362 of seed 1337 took three
seconds against 364 matching rows. The first test pins the results to a brute-force scan;
the second pins the cost to the matches rather than to the length of history.
"""

from __future__ import annotations

from collections import Counter

from meddler.engine import tickloop
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world


def _world(ticks: int) -> World:
    world = generate_world(1337, WorldSettings(starting_country_count=4))
    rng = Rng(1337)
    for _ in range(ticks):
        tickloop.tick(world, rng)
    return world


def _brute_force(
    world: World,
    *,
    target: str,
    metrics: tuple[str, ...],
    start_tick: int | None,
    direction: int | None,
    limit: int,
    exclude_ids: frozenset[int],
) -> tuple[int, ...]:
    def matches(delta: float | int | None) -> bool:
        if direction is None:
            return True
        if delta is None:
            return False
        return delta > 0 if direction > 0 else delta < 0

    ids = {
        event.id
        for event in world.log
        if (start_tick is None or event.tick >= start_tick) and event.id not in exclude_ids
        for effect in event.effects
        if effect.target == target and effect.metric in metrics and matches(effect.delta)
    }
    return tuple(sorted(ids, reverse=True)[:limit])


def test_results_match_a_brute_force_scan_of_the_log() -> None:
    world = _world(80)
    counts = Counter((e.target, e.metric) for event in world.log for e in event.effects)
    by_frequency = sorted(counts, key=lambda key: (-counts[key], key))
    singles = by_frequency[:4] + by_frequency[-4:]
    metrics_of: dict[str, list[str]] = {}
    for target, metric in sorted(counts):
        metrics_of.setdefault(target, []).append(metric)
    pairs = [(t, tuple(m[:2])) for t, m in sorted(metrics_of.items()) if len(m) >= 2][:4]
    signatures = [(t, (m,)) for t, m in singles] + pairs

    checked = 0
    for target, metrics in signatures:
        for start_tick in (None, world.tick - 10):
            for direction in (None, 1, -1):
                query = {
                    "target": target,
                    "metrics": metrics,
                    "start_tick": start_tick,
                    "direction": direction,
                    "limit": 3,
                }
                expected = _brute_force(world, exclude_ids=frozenset(), **query)
                got = world.log.recent_effect_events(**query)
                assert tuple(e.id for e in got) == expected, query
                if expected:
                    excluded = frozenset({expected[0]})
                    expected = _brute_force(world, exclude_ids=excluded, **query)
                    got = world.log.recent_effect_events(exclude_ids=excluded, **query)
                    assert tuple(e.id for e in got) == expected, (query, excluded)
                checked += 1
    assert checked == len(signatures) * 6 and len(signatures) >= 10


def test_a_full_history_lookup_costs_its_matches_not_the_whole_log() -> None:
    world = _world(150)
    counts = Counter((e.target, e.metric) for event in world.log for e in event.effects)
    target, metric = min(counts, key=lambda key: (counts[key], key))  # the rarest signature
    world.log.flush()
    connection = world.log._store.connection
    steps = 0

    def count_steps() -> int:
        nonlocal steps
        steps += 1
        return 0

    connection.set_progress_handler(count_steps, 1000)
    try:
        world.log.recent_effect_events(
            target=target, metrics=(metric,), start_tick=None, direction=None, limit=3
        )
    finally:
        connection.set_progress_handler(None, 1000)

    # A walk of the events table costs several VM steps per event; one that reads only the
    # matching effects finishes in a handful of thousands however long the history is.
    assert steps * 1000 < len(world.log), (steps, len(world.log))
