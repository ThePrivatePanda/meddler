"""M5.2: Multiverse fork + diff, real registry/tickloop. PROPOSAL §3.5, §4.4.

tests/property/test_determinism.py already exercises fork/adopt/drop mechanics with
fixture systems (isolating RNG/snapshot correctness from real simulation noise). This
file complements it with the REAL tickloop (all systems, the real EVENT_REGISTRY) driving
engine/diff.py, matching M5.2's own acceptance bar verbatim: fork without intervention
diffs to zero at every tick; fork with intervention diffs nonzero; adopt promotes B and
drops C/D.
"""

from __future__ import annotations

from meddler.engine import god, tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.diff import diff
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _timeline(seed: int, snapshot_interval: int = 10) -> Timeline:
    settings = WorldSettings(starting_country_count=4, snapshot_interval=snapshot_interval)
    world = generate_world(seed, settings)
    return Timeline(seed=seed, world=world, snapshots={}, rng=Rng(seed))


def test_fork_without_intervention_diffs_to_zero_at_every_tick():
    tl = _timeline(1337)
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=20, intervention=None)
    fork_tl = mv.forks[fork_id]

    for _ in range(30):
        tl.advance()
        fork_tl.advance()
        assert diff(tl.world, fork_tl.world) == {}


def test_fork_with_intervention_diffs_nonzero_and_persists():
    tl = _timeline(1337)
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=20, intervention=None)
    fork_tl = mv.forks[fork_id]

    code = fork_tl.world.countries[0].code
    god.god_edit(fork_tl.world, code, "stability", 5.0)

    result = diff(tl.world, fork_tl.world)
    assert result != {}
    assert result[code]["stability"][1] == 5.0

    for _ in range(10):
        tl.advance()
        fork_tl.advance()
    # Divergence must persist -- the whole point of the demo (§3.5): every difference
    # traces back to the intervention, not to different dice (the fork mirrors prime's
    # exact RNG state, docs/design-decisions.md "Fork RNG semantics resolved").
    assert diff(tl.world, fork_tl.world) != {}


def test_adopt_promotes_b_and_drops_c_and_d():
    tl = _timeline(1337)
    for _ in range(10):
        tl.advance()
    mv = Multiverse(prime=tl)
    b = mv.fork(at_tick=10, intervention=None)
    c = mv.fork(at_tick=10, intervention=None)
    d = mv.fork(at_tick=10, intervention=None)
    promoted_world = mv.forks[b].world

    mv.adopt_fork(b)

    assert mv.prime.world is promoted_world
    assert b not in mv.forks
    assert c not in mv.forks
    assert d not in mv.forks
    assert mv.forks == {}


def test_diff_only_lists_countries_and_stats_that_actually_differ():
    tl = _timeline(7)
    code = tl.world.countries[0].code
    other_code = tl.world.countries[1].code

    # Same seed + same worldgen-relevant settings -> byte-identical genesis, so any
    # difference below is attributable only to the one manual edit.
    fork_world = generate_world(7, WorldSettings(starting_country_count=4))
    original_stability = tl.world.country(code).stability
    fork_world.country(code).stability = original_stability + 1.0

    result = diff(tl.world, fork_world)
    assert set(result.keys()) == {code}
    assert result[code] == {"stability": [original_stability, original_stability + 1.0]}
    assert other_code not in result
