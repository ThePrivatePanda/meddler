import math

import pytest

from meddler.engine import tickloop
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import relations
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def test_grudge_decays_toward_zero_at_specced_half_life():
    """M2.6's own accept criterion. relation_decay_rate=0.005/tick -> half-life
    ln(2)/0.005 ~= 138.6 ticks, matching PROPOSAL §5.2's "~140 ticks" claim."""
    world = _two_country_world()
    pair = (world.countries[0].code, world.countries[1].code)
    key = tuple(sorted(pair))
    world.relations = {key: -80.0}
    rng = Rng(1)

    half_life = math.log(2) / world.settings.relation_decay_rate
    for _ in range(round(half_life)):
        relations.run(world, rng)

    assert world.relations[key] == pytest.approx(-40.0, rel=0.02)


def test_decay_asymptotically_approaches_zero_without_crossing():
    world = _two_country_world()
    key = tuple(sorted((world.countries[0].code, world.countries[1].code)))
    world.relations = {key: -0.001}
    relations.run(world, Rng(1))
    assert -0.001 < world.relations[key] < 0.0


def test_zero_relation_produces_no_event():
    world = _two_country_world()
    key = tuple(sorted((world.countries[0].code, world.countries[1].code)))
    world.relations = {key: 0.0}
    events = relations.run(world, Rng(1))
    assert events == []


def test_relation_shift_emitted_on_threshold_crossing():
    world = _two_country_world()
    key = tuple(sorted((world.countries[0].code, world.countries[1].code)))
    # decay of |-62|*0.005=0.31/tick will cross -60 within a couple ticks
    world.relations = {key: -60.5}
    rng = Rng(1)

    shift_events = []
    for _ in range(5):
        events = relations.run(world, rng)
        shift_events += [e for e in events if e.kind == "RELATION_SHIFT"]

    assert len(shift_events) == 1
    assert shift_events[0].payload["threshold"] == -60.0
    assert shift_events[0].country2 == key[1]


def test_relation_decay_events_are_root_ambient():
    world = _two_country_world()
    key = tuple(sorted((world.countries[0].code, world.countries[1].code)))
    world.relations = {key: -10.0}
    events = relations.run(world, Rng(1))
    for event in events:
        assert event.parent_id is None
        assert event.depth == 0


def test_world_at_reconstructs_relations_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [relations.run])
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10, rival_pairs=4)
    world = generate_world(21, settings)
    tl = Timeline(seed=21, world=world, snapshots={}, rng=Rng(21))
    for _ in range(37):
        tl.advance()

    reconstructed = tl.world_at(37)
    for key, value in tl.world.relations.items():
        assert reconstructed.relations[key] == pytest.approx(value)
