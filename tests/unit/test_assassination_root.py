"""ASSASSINATION is reachable organically, and only in an unstable country.

Its only organic parent used to be RESISTANCE_MOVEMENT, itself a 0.5 child of
OCCUPATION_BEGIN. At depth 1 the child roll is 0.2 * cascade_decay**1 = 0.14, and occupation
happens a few times per seed before conquest consolidates the map, so the kind fired well
under once per 5000-tick seed and the catalog coverage run never saw it. It now also rolls as
a small-p exogenous root gated on low stability, like SECESSION's own gate.
"""

from __future__ import annotations

import pytest

from meddler.engine import cascade, tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import consequence, exogenous
from meddler.engine.worldgen import generate_world


def _world(stability: float) -> World:
    world = generate_world(1337, WorldSettings(starting_country_count=4))
    for country in world.countries:
        country.stability = stability
    return world


def test_assassination_is_rolled_as_a_root_in_an_unstable_country(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(stability=60.0)
    target = world.countries[2].code
    world.country(target).stability = 5.0
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)  # force every exogenous root

    hits = [e for e in exogenous.run(world, Rng(1)) if e.kind == "ASSASSINATION"]

    assert [(e.country, e.parent_id, e.depth) for e in hits] == [(target, None, 0)]


def test_a_stable_world_rolls_no_assassination(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(stability=60.0)
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)

    assert [e for e in exogenous.run(world, Rng(1)) if e.kind == "ASSASSINATION"] == []


def test_a_resistance_movement_can_still_end_in_an_assassination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(stability=5.0)
    code = world.countries[0].code
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)
    cascade.emit_event(
        world,
        Rng(1),
        kind="RESISTANCE_MOVEMENT",
        primary=code,
        secondary=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
    )
    world.tick = max(entry.fire_tick for entry in world.schedule)

    fired = [e for e in consequence.run(world, Rng(1)) if e.kind == "ASSASSINATION"]

    assert [(e.country, e.depth) for e in fired] == [(code, 1)]
