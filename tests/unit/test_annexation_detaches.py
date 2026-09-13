"""Annexation removes a country from every standing arrangement that names it.

Blocs, tariffs, embargoes and occupations record a country by code, and the systems that
read them do not ask whether the code still names a country. Before this, an annexed bloc
member kept taking relation shifts and could break its alliance, a tariff against it could
be repealed or retaliated against, and a country it occupied could be annexed into a state
that no longer existed.
"""

from __future__ import annotations

import copy

from meddler.engine import cascade, structural, tariffs, tickloop  # noqa: F401 -- registry
from meddler.engine.events import Event
from meddler.engine.model import Bloc, CountryStatus, TariffPolicy, World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world


def _world() -> tuple[World, list[str]]:
    world = generate_world(1337, WorldSettings(starting_country_count=5))
    return world, sorted(c.code for c in world.countries)


def _occupy(world: World, occupied: str, occupier: str) -> None:
    world.country(occupied).status = CountryStatus.OCCUPIED
    world.country(occupied).occupied_by = occupier


def _annex(world: World, annexed: str, annexer: str) -> Event:
    _occupy(world, annexed, annexer)
    return cascade.emit_event(
        world,
        Rng(1),
        kind="ANNEXATION",
        primary=annexed,
        secondary=annexer,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
    )


def test_a_two_member_bloc_dissolves_when_one_member_is_annexed() -> None:
    world, (a, b, c, _d, _e) = _world()
    world.blocs = [Bloc(id="BLOC1", members=[a, c], formed_at_tick=0)]
    _annex(world, a, b)
    assert world.blocs == []


def test_a_larger_bloc_loses_only_the_annexed_member() -> None:
    world, (a, b, c, d, _e) = _world()
    world.blocs = [Bloc(id="BLOC1", members=[a, c, d], formed_at_tick=0)]
    _annex(world, a, b)
    assert [(bl.id, bl.members) for bl in world.blocs] == [("BLOC1", [c, d])]
    assert world.bloc_of(a) is None


def test_tariffs_naming_the_annexed_country_are_dropped() -> None:
    world, (a, b, c, d, _e) = _world()
    kept = TariffPolicy(c, d, "*", 0.3, 0, 0)
    world.tariffs = sorted(
        [TariffPolicy(a, c, "*", 0.2, 0, 0), TariffPolicy(c, a, "food", 0.1, 0, 0), kept],
        key=tariffs.policy_key,
    )
    _annex(world, a, b)
    assert world.tariffs == [kept]


def test_embargoes_naming_the_annexed_country_are_lifted() -> None:
    world, (a, b, c, d, _e) = _world()
    world.embargoes = [(a, c), (c, d)]
    _annex(world, a, b)
    assert world.embargoes == [(c, d)]


def test_a_country_the_annexed_one_occupied_passes_to_the_annexer() -> None:
    world, (a, b, _c, d, _e) = _world()
    _occupy(world, d, a)
    _annex(world, a, b)
    assert world.country(d).status == CountryStatus.OCCUPIED
    assert world.country(d).occupied_by == b


def test_an_annexer_occupied_by_the_country_it_absorbs_is_freed() -> None:
    world, (a, b, _c, _d, _e) = _world()
    _occupy(world, b, a)
    _annex(world, a, b)
    assert world.country(b).status == CountryStatus.ACTIVE
    assert world.country(b).occupied_by is None


def test_an_ordinary_annexation_records_no_detachment() -> None:
    world, (a, b, _c, _d, _e) = _world()
    event = _annex(world, a, b)
    assert not any(
        key.startswith(("detached_", "tariffs_", "embargoes_", "occupations_"))
        for key in event.payload
    )


def test_replay_reproduces_every_detachment() -> None:
    world, (a, b, c, d, e) = _world()
    world.blocs = [Bloc(id="BLOC1", members=[a, c, d], formed_at_tick=0)]
    world.tariffs = sorted(
        [TariffPolicy(a, c, "*", 0.2, 0, 0), TariffPolicy(c, d, "*", 0.3, 0, 0)],
        key=tariffs.policy_key,
    )
    world.embargoes = [(a, e)]
    _occupy(world, d, a)
    _occupy(world, a, b)
    replayed = copy.deepcopy(world)

    event = _annex(world, a, b)
    structural.REPLAY_EFFECTS["ANNEXATION"](replayed, event)

    def standing(w: World) -> tuple[object, ...]:
        return (
            [(bl.id, bl.members) for bl in w.blocs],
            w.tariffs,
            w.embargoes,
            {c.code: (c.status, c.occupied_by) for c in w.countries},
        )

    assert standing(world)[0] == [("BLOC1", [c, d])], "the scenario must detach something"
    assert standing(replayed) == standing(world)
