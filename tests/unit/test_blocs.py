"""M11 structural bloc/embargo state + ALLIANCE/ALLIANCE_BROKEN/EMBARGO handlers."""

from __future__ import annotations

import meddler.engine.tickloop  # noqa: F401 -- populates EVENT_REGISTRY at import
from meddler.engine import cascade, structural
from meddler.engine.model import Bloc, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world


def make_world(country_count: int = 4, seed: int = 1):
    return generate_world(seed, WorldSettings(starting_country_count=country_count))


def _emit_alliance(world, rng, a, b):
    return cascade.emit_event(
        world, rng, kind="ALLIANCE", primary=a, secondary=b,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )


def _emit_alliance_broken(world, rng, a, b):
    return cascade.emit_event(
        world, rng, kind="ALLIANCE_BROKEN", primary=a, secondary=b,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )


def _emit_embargo(world, rng, a, b):
    return cascade.emit_event(
        world, rng, kind="EMBARGO", primary=a, secondary=b,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )


# --- Task 1: model + world state -------------------------------------------------------

def test_bloc_of_returns_the_containing_bloc_or_none():
    world = make_world(country_count=4)
    a, b = world.countries[0].code, world.countries[1].code
    assert world.bloc_of(a) is None
    world.blocs.append(Bloc(id="BLOC1", members=sorted([a, b]), formed_at_tick=world.tick))
    assert world.bloc_of(a).id == "BLOC1"
    assert world.bloc_of(b).id == "BLOC1"
    assert world.bloc_of(world.countries[2].code) is None


def test_world_bloc_state_defaults_are_empty():
    world = make_world(country_count=2)
    assert world.blocs == []
    assert world.bloc_seq == 0
    assert world.embargoes == []


# --- Task 3: ALLIANCE structural + replay ----------------------------------------------

def test_alliance_between_two_blocless_countries_creates_a_bloc():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b = sorted([world.countries[0].code, world.countries[1].code])
    ev = _emit_alliance(world, rng, a, b)
    bloc = world.bloc_of(a)
    assert bloc is not None
    assert bloc.members == sorted([a, b])
    assert world.bloc_of(b) is bloc
    assert world.relations[(a, b)] == 70.0
    assert ev.payload["bloc_members"] == ",".join(sorted([a, b]))
    assert ev.payload["relation_after"] == 70.0


def test_alliance_adds_a_blocless_country_to_an_existing_bloc():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b, c = (world.countries[0].code, world.countries[1].code, world.countries[2].code)
    _emit_alliance(world, rng, *sorted([a, b]))
    _emit_alliance(world, rng, *sorted([a, c]))
    bloc = world.bloc_of(a)
    assert set(bloc.members) == {a, b, c}
    assert len(world.blocs) == 1


def test_alliance_replay_reconstructs_identical_bloc_state():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b = sorted([world.countries[0].code, world.countries[1].code])
    ev = _emit_alliance(world, rng, a, b)
    world2 = make_world(country_count=4)
    structural.replay(world2, ev)
    assert world2.bloc_of(a).members == world.bloc_of(a).members
    assert world2.relations[(a, b)] == world.relations[(a, b)]


# --- Task 4: ALLIANCE_BROKEN structural + replay ---------------------------------------

def test_alliance_broken_removes_defector_from_a_three_member_bloc():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b, c = (world.countries[0].code, world.countries[1].code, world.countries[2].code)
    _emit_alliance(world, rng, *sorted([a, b]))
    _emit_alliance(world, rng, *sorted([a, c]))
    ev = _emit_alliance_broken(world, rng, a, b)
    bloc = world.bloc_of(b)
    assert bloc is not None and set(bloc.members) == {b, c}
    assert world.bloc_of(a) is None
    assert ev.payload["departed"] == a
    assert ev.payload["dissolved"] == "0"


def test_alliance_broken_dissolves_a_two_member_bloc():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b = sorted([world.countries[0].code, world.countries[1].code])
    _emit_alliance(world, rng, a, b)
    ev = _emit_alliance_broken(world, rng, a, b)
    assert world.blocs == []
    assert ev.payload["dissolved"] == "1"


def test_alliance_broken_replay_matches_live():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b, c = (world.countries[0].code, world.countries[1].code, world.countries[2].code)
    _emit_alliance(world, rng, *sorted([a, b]))
    _emit_alliance(world, rng, *sorted([a, c]))
    _emit_alliance_broken(world, rng, a, b)
    world2 = make_world(country_count=4)
    for e in world.log:
        if e.kind in ("ALLIANCE", "ALLIANCE_BROKEN"):
            structural.replay(world2, e)
    assert (world2.bloc_of(a) is None) == (world.bloc_of(a) is None)
    assert sorted(m for bl in world2.blocs for m in bl.members) == \
        sorted(m for bl in world.blocs for m in bl.members)


# --- Task 5: EMBARGO structural + replay -----------------------------------------------

def test_embargo_records_a_structural_lane_once():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b = sorted([world.countries[0].code, world.countries[1].code])
    _emit_embargo(world, rng, a, b)
    assert tuple(sorted((a, b))) in world.embargoes
    _emit_embargo(world, rng, a, b)
    assert world.embargoes.count(tuple(sorted((a, b)))) == 1


def test_embargo_replay_records_the_lane():
    world = make_world(country_count=4)
    rng = Rng(1)
    a, b = sorted([world.countries[0].code, world.countries[1].code])
    ev = _emit_embargo(world, rng, a, b)
    world2 = make_world(country_count=4)
    structural.replay(world2, ev)
    assert tuple(sorted((a, b))) in world2.embargoes
