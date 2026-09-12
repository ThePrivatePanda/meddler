"""M3.1 acceptance + targeted cascade-mechanics tests. PROPOSAL §6.4, §4.7.

The statistical test (2000 ticks x 10 seeds, §6.6 build order) is the milestone's stated
acceptance bar: wars and crises occur, the depth cap holds, and the clip counter stays
within budget. The bar is an AGGREGATE across all 10 seeds combined ("2000 ticks x 10
seeds -- >=1 war and >=3 crises occur") -- deliberately not a per-seed bar, because
per-seed the WAR_DECLARED count is Poisson-ish with mean ~4 (p=0.002/tick x 2000 ticks),
so P(zero wars in a given seed) ~= e^-4 ~= 1.8%; asserting ">=1 war" independently on
each of 10 seeds would make the suite flaky (~17% chance some seed rolls zero). Keeping
the aggregate semantics means the seed loop can't be split into 10 independent pytest
test items with their own pass/fail -- pytest-xdist distributes whole test ITEMS across
worker processes and has no built-in cross-process aggregation.

So the speedup instead runs the 10 seeds in parallel via ProcessPoolExecutor, INSIDE one
test's setup (each seed's 2000-tick loop is CPU-bound pure-Python simulation with no
shared state, an ideal multiprocessing candidate). This is orthogonal to `pytest -n auto`
-- it parallelizes across a machine's cores regardless of whether the outer pytest run
itself is single- or multi-process, and avoids xdist's inter-worker test-ordering
uncertainty entirely (an aggregate test would otherwise need to run only after every
per-seed test completes, which xdist's load-balancing does not guarantee).

Each seed's tickloop.tick calls are driven directly rather than Timeline.advance, so the
growing event log is never deep-copied into per-interval snapshots -- that keeps each
worker's memory flat (the M2 systems already emit ~30 bookkeeping events per tick, so a
2000-tick log is ~60k events per seed).

Through M3.1-M6 the registered catalog was deliberately acyclic (max chain depth 7 <
max_depth 8), so clip_count was 0 by construction and the depth-cap LOGIC needed separate
hand-built unit tests driving schedule_children at exactly the cap. M7.1 registered the
full §6.6 catalog and restored the one edge M3.1 had trimmed to keep things acyclic
(CAPITAL_FLIGHT -> CURRENCY_SLIDE), so clip_count can now be genuinely nonzero -- the
budget assertion below is a real tuning signal as of M7.1/M7.4, not a formality.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

import pytest

from meddler.engine import cascade, tickloop
from meddler.engine.config import CLIP_BUDGET_PER_1000_TICKS
from meddler.engine.model import WorldSettings
from meddler.engine.registry import EVENT_REGISTRY, validate_all
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world

# Heavy multi-seed run; excluded from the quick local subset via -m "not slow".
pytestmark = pytest.mark.slow

TICKS = 2000
SEEDS = 10
COUNTRIES = 4


@dataclass
class _Stats:
    total_wars: int = 0
    total_sev2: int = 0
    max_depth_seen: int = 0
    max_clip: int = 0
    depth_cap: int = 0
    saw_child: bool = False


@dataclass
class _SeedResult:
    wars: int
    sev2: int
    max_depth_seen: int
    depth_cap: int
    clip_count: int
    saw_child: bool


def _run_seed(seed: int) -> _SeedResult:
    """The unit of work handed to each worker process. Module-level (not a closure/
    lambda) so it is picklable by ProcessPoolExecutor's default spawn/fork start method."""
    world = generate_world(seed, WorldSettings(starting_country_count=COUNTRIES))
    rng = Rng(seed)
    for _ in range(TICKS):
        tickloop.tick(world, rng)

    wars = 0
    sev2 = 0
    max_depth_seen = 0
    saw_child = False
    for event in world.log:
        if event.kind == "WAR_DECLARED":
            wars += 1
        if event.severity == 2:
            sev2 += 1
        if event.depth > max_depth_seen:
            max_depth_seen = event.depth
        if event.parent_id is not None and event.depth >= 1:
            saw_child = True
    return _SeedResult(
        wars=wars,
        sev2=sev2,
        max_depth_seen=max_depth_seen,
        depth_cap=world.settings.max_depth,
        clip_count=world.clip_count,
        saw_child=saw_child,
    )


_CACHE: _Stats | None = None


def _stats() -> _Stats:
    """Run all SEEDS timelines once (in parallel, one process per seed, capped at the
    machine's core count), aggregating everything the assertions below need. Prints one
    progress line per completed seed to stderr -- this run is heavy enough on a
    contended host that a silent multi-minute wait is unhelpful."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    start = time.monotonic()
    s = _Stats()
    done = 0
    with ProcessPoolExecutor() as pool:
        futures = {pool.submit(_run_seed, seed): seed for seed in range(SEEDS)}
        for future in as_completed(futures):
            seed = futures[future]
            result = future.result()
            s.total_wars += result.wars
            s.total_sev2 += result.sev2
            s.max_depth_seen = max(s.max_depth_seen, result.max_depth_seen)
            s.depth_cap = result.depth_cap
            s.max_clip = max(s.max_clip, result.clip_count)
            s.saw_child = s.saw_child or result.saw_child
            done += 1
            elapsed = time.monotonic() - start
            print(
                f"[test_cascade] seed {seed} done ({done}/{SEEDS}), {elapsed:.0f}s elapsed",
                file=sys.stderr,
                flush=True,
            )
    _CACHE = s
    return s


def test_validate_all_passes_at_import() -> None:
    # engine.kinds ran validate_all() at import; re-running must still pass and the core
    # kinds must actually be present in the registry.
    validate_all()
    for kind in ("DROUGHT", "WAR_DECLARED", "INFLATION_CRISIS", "SECESSION", "INTERVENE_CHAOS"):
        assert kind in EVENT_REGISTRY


def test_at_least_one_war_occurs() -> None:
    assert _stats().total_wars >= 1, f"expected >=1 WAR_DECLARED, got {_stats().total_wars}"


def test_at_least_three_crises_occur() -> None:
    assert _stats().total_sev2 >= 3, f"expected >=3 severity-2 events, got {_stats().total_sev2}"


def test_depth_never_exceeds_cap() -> None:
    st = _stats()
    assert st.max_depth_seen <= st.depth_cap, (
        f"observed depth {st.max_depth_seen} > max_depth {st.depth_cap}"
    )


def test_clip_counter_within_budget() -> None:
    assert _stats().max_clip <= CLIP_BUDGET_PER_1000_TICKS, (
        f"per-timeline clip_count {_stats().max_clip} exceeds budget {CLIP_BUDGET_PER_1000_TICKS}"
    )


def test_cascades_actually_chain() -> None:
    # Guards against a silently-dead scheduling path: the statistical bars above could all
    # pass on depth-0 roots alone, so assert at least one scheduled consequence fired.
    assert _stats().saw_child, "no scheduled consequence ever fired across any seed"


def test_depth_cap_clips_and_counts_when_at_cap() -> None:
    """Targeted coverage of the depth-cap branch the acyclic catalog never reaches live:
    an event WITH consequences sitting at max_depth must clip its whole cascade -- mark the
    payload once, bump clip_count once, and schedule nothing."""
    world = generate_world(0, WorldSettings(starting_country_count=4))
    code = sorted(c.code for c in world.countries)[0]
    assert EVENT_REGISTRY["DROUGHT"].consequences  # precondition: DROUGHT can cascade

    event = world.log.append(
        tick=world.tick,
        kind="DROUGHT",
        country=code,
        country2=None,
        parent_id=None,
        depth=world.settings.max_depth,
        is_intervention=False,
        payload={},
        severity=2,
    )
    cascade.schedule_children(world, Rng(1), event)

    assert event.payload.get("cascade_clipped") is True
    assert world.clip_count == 1
    assert world.schedule == []


def test_leaf_at_cap_is_not_marked_clipped() -> None:
    """A childless spec landing at the cap has no cascade to clip: it must NOT be marked or
    counted (that would inflate the budget with meaningless clips)."""
    world = generate_world(0, WorldSettings(starting_country_count=4))
    code = sorted(c.code for c in world.countries)[0]
    assert not EVENT_REGISTRY["POPULATION_LOSS"].consequences  # precondition: leaf

    event = world.log.append(
        tick=world.tick,
        kind="POPULATION_LOSS",
        country=code,
        country2=None,
        parent_id=None,
        depth=world.settings.max_depth,
        is_intervention=False,
        payload={},
        severity=2,
    )
    cascade.schedule_children(world, Rng(1), event)

    assert "cascade_clipped" not in event.payload
    assert world.clip_count == 0
    assert world.schedule == []
