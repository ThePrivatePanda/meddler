"""An annexed country is not simulated by any per-country system.

The bridge has always told the client this: `bridge/adapter.py` excludes ANNEXED and
DISSOLVED countries from the roster and `bridge/world_objects.py` from the globe. Six
engine systems were never told, so a viewer could watch a nation vanish from the world list
and then read its election results in the feed, with no country left to click through to.
On seed 1337 Numoania was absorbed at t770 and went on holding elections at t1000.

ONE TEST PER SYSTEM, deliberately. Six independent omissions produced this, and a single
aggregate test would pass with five of the six fixed -- which is exactly the failure mode
that let it happen the first time.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from meddler.engine import cascade, config, tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.events import ScheduleEntry
from meddler.engine.model import CountryStatus, World, WorldSettings
from meddler.engine.registry import Condition, ConsequenceRule
from meddler.engine.rng import Rng
from meddler.engine.systems import (
    consequence,
    exogenous,
    infrastructure,
    inflation,
    politics,
    relations,
    stability,
    thresholds,
)
from meddler.engine.worldgen import generate_world


def _world_with_an_annexed_country() -> tuple[World, str]:
    world = generate_world(1337, WorldSettings(starting_country_count=4))
    gone = world.countries[0]
    gone.status = CountryStatus.ANNEXED
    gone.population = 0.0
    for name in list(gone.commodity_need):
        gone.commodity_need[name] = 0.0
        gone.commodity_output[name] = 0.0
        gone.commodity_stock[name] = 0.0
    return world, gone.code


def _emitted_for(events: list, code: str) -> list:
    return [e for e in events if e.country == code or e.country2 == code]


def test_the_predicate_is_the_single_definition() -> None:
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    assert gone.in_world is False
    gone.status = CountryStatus.DISSOLVED
    assert gone.in_world is False
    gone.status = CountryStatus.OCCUPIED
    assert gone.in_world is True, "an occupied country is still a country"
    gone.status = CountryStatus.ACTIVE
    assert gone.in_world is True


def test_exogenous_does_not_strike_an_annexed_country(monkeypatch: pytest.MonkeyPatch) -> None:
    world, code = _world_with_an_annexed_country()
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)  # force every exogenous root
    assert _emitted_for(exogenous.run(world, Rng(1)), code) == []


def test_thresholds_does_not_fire_for_an_annexed_country() -> None:
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    gone.stability = 1.0  # far below UNREST and CIVIL_WAR_RISK
    gone.inflation = config.INFLATION_CRISIS_THRESHOLD + 50
    assert _emitted_for(thresholds.run(world, Rng(1)), code) == []


def test_stability_does_not_update_an_annexed_country() -> None:
    world, code = _world_with_an_annexed_country()
    before = world.country(code).stability
    assert _emitted_for(stability.run(world, Rng(1)), code) == []
    assert world.country(code).stability == before


def test_inflation_does_not_update_an_annexed_country() -> None:
    world, code = _world_with_an_annexed_country()
    before = world.country(code).inflation
    assert _emitted_for(inflation.run(world, Rng(1)), code) == []
    assert world.country(code).inflation == before


def test_infrastructure_does_not_maintain_an_annexed_country() -> None:
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    gone.infrastructure.power_grid.condition = 0.01  # deep in failure territory
    before = gone.infrastructure.power_grid.condition
    assert _emitted_for(infrastructure.run(world, Rng(1)), code) == []
    assert gone.infrastructure.power_grid.condition == before


def test_relations_do_not_decay_toward_an_annexed_country() -> None:
    world, code = _world_with_an_annexed_country()
    other = world.countries[1].code
    pair = tuple(sorted((code, other)))
    world.relations[pair] = 60.0

    assert _emitted_for(relations.run(world, Rng(1)), code) == []
    assert world.relations[pair] == 60.0, "a relationship needs two countries that exist"


def test_an_annexed_country_holds_no_election() -> None:
    world, code = _world_with_an_annexed_country()
    world.country(code).election_due_tick = world.tick  # the vote is due this tick
    assert _emitted_for(politics.run(world, Rng(1)), code) == []


def test_an_annexed_country_suffers_no_coup(monkeypatch: pytest.MonkeyPatch) -> None:
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    gone.election_due_tick = None  # isolate the coup path from the election path
    gone.stability = 0.0
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)  # force every coup roll
    assert _emitted_for(politics.run(world, Rng(1)), code) == []


def _due_entry(world: World, kind: str, primary: str, secondary: str | None) -> ScheduleEntry:
    # A queued consequence always has a parent in the log; give it one, on a living
    # country, and discard whatever that parent scheduled so only this entry is due.
    parent = cascade.emit_event(
        world,
        Rng(1),
        kind="ELECTION",
        primary=world.countries[1].code,
        secondary=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"incumbent_changed": False},
    )
    world.schedule.clear()
    return ScheduleEntry(
        fire_tick=world.tick,
        schedule_seq=world.schedule_seq,
        child_kind=kind,
        parent_id=parent.id,
        parent_depth=0,
        primary=primary,
        secondary=secondary,
        payload={},
    )


def test_a_queued_consequence_does_not_fire_on_an_annexed_country() -> None:
    # Seed 1337: a LIBERATION_WAR queued OCCUPATION_END, Numoania was annexed during the
    # delay, and the feed still reported the occupation ending six ticks later.
    world, code = _world_with_an_annexed_country()
    world.schedule.append(_due_entry(world, "OCCUPATION_END", code, None))
    assert _emitted_for(consequence.run(world, Rng(1)), code) == []
    assert world.schedule == [], "the entry is consumed, not left to retry"


def test_a_queued_consequence_does_not_fire_against_an_annexed_secondary() -> None:
    world, code = _world_with_an_annexed_country()
    other = world.countries[1].code
    world.schedule.append(_due_entry(world, "RELATION_SHIFT", other, code))
    assert _emitted_for(consequence.run(world, Rng(1)), code) == []
    assert world.schedule == []


def _others_toward(world: World, primary: str, value: float) -> None:
    for c in world.countries:
        if c.code != primary:
            world.relations[(min(primary, c.code), max(primary, c.code))] = value


def _fired_by(code: str) -> Any:
    return SimpleNamespace(country=code, country2=None)


def test_an_annexed_country_is_nobodys_ally() -> None:
    world, code = _world_with_an_annexed_country()
    primary = world.countries[1].code
    _others_toward(world, primary, -10.0)
    world.relations[(min(primary, code), max(primary, code))] = 50.0
    rule = ConsequenceRule("RELATION_SHIFT", 1.0, 0, 0, target="ally")
    assert cascade._resolve_target(world, Rng(1), rule, _fired_by(primary)) is None


def test_an_annexed_country_is_never_a_random_target() -> None:
    world, code = _world_with_an_annexed_country()
    primary = world.countries[1].code
    rule = ConsequenceRule("RELATION_SHIFT", 1.0, 0, 0, target="random")
    picks = {
        cascade._resolve_target(world, Rng(seed), rule, _fired_by(primary)) for seed in range(64)
    }
    assert picks, "the pool of living countries is not empty"
    assert all(pick is not None and code not in pick for pick in picks)


def test_an_annexed_country_is_never_the_worst_relation() -> None:
    world, code = _world_with_an_annexed_country()
    primary = world.countries[1].code
    _others_toward(world, primary, 0.0)
    world.relations[(min(primary, code), max(primary, code))] = -100.0
    rule = ConsequenceRule("RELATION_SHIFT", 1.0, 0, 0, target="worst_relation")
    target = cascade._resolve_target(world, Rng(1), rule, _fired_by(primary))
    assert target is not None and code not in target


def test_world_wide_conditions_ignore_an_annexed_country() -> None:
    world, code = _world_with_an_annexed_country()
    for c in world.countries:
        c.stability = 50.0
    world.country(code).stability = 0.0
    primary = world.countries[1].code
    every = [Condition("stability", ">", 10.0, target="all")]
    some = [Condition("stability", "<", 5.0, target="any")]
    assert cascade.eval_conditions(world, every, primary, None) is True
    assert cascade.eval_conditions(world, some, primary, None) is False
