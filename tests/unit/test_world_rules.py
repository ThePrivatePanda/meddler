"""The world-rule toggles (§6.9) actually rule.

`allow_secession`, `allow_conquest` and `allow_extinction` were shipped controls that
nothing in the engine read: flipping one changed nothing at all. These tests pin what each
surviving rule does when it is off, and -- the point the toggles exist for -- that turning
one off does not wedge the world. `allow_extinction` is gone rather than wired: no code
anywhere ever set CountryStatus.DISSOLVED, so there was no behaviour to gate.
"""

from __future__ import annotations

import pytest

from meddler.engine import config, god, tickloop  # noqa: F401 -- populates EVENT_REGISTRY
from meddler.engine.events import ScheduleEntry
from meddler.engine.model import CountryStatus, World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import consequence, politics
from meddler.engine.worldgen import generate_world


def _world(**rules: bool) -> World:
    return generate_world(1337, WorldSettings(starting_country_count=4, **rules))


def _at_war(world: World) -> tuple[str, str]:
    a, b = sorted(c.code for c in world.countries)[:2]
    world.country(a).at_war_with = [b]
    world.country(b).at_war_with = [a]
    world.relations[(a, b)] = -95.0
    return a, b


# --- allow_secession -------------------------------------------------------------------


def test_god_mode_secession_is_refused_when_the_rule_is_off() -> None:
    world = _world(allow_secession=False)
    code = world.countries[0].code
    with pytest.raises(god.InterventionError, match="allow_secession"):
        god.intervene(world, Rng(1), kind="INTERVENE_SECEDE", country=code)
    assert len(world.countries) == 4


def test_god_mode_secession_works_by_default() -> None:
    world = _world()
    event = god.intervene(world, Rng(1), kind="INTERVENE_SECEDE", country=world.countries[0].code)
    assert event.payload["seceded"] == 1
    assert len(world.countries) == 5


def test_an_organic_secession_is_dropped_when_the_rule_is_off() -> None:
    world = _world(allow_secession=False)
    code = world.countries[0].code
    world.country(code).stability = 5.0  # satisfies SECESSION's own fire-time condition
    world.schedule.append(
        ScheduleEntry(
            fire_tick=world.tick,
            schedule_seq=1,
            child_kind="SECESSION",
            parent_id=0,
            parent_depth=0,
            primary=code,
            secondary=None,
            payload={},
        )
    )
    assert [e.kind for e in consequence.run(world, Rng(1))] == []
    assert not world.schedule  # the entry is consumed, not left to retry forever


# --- allow_conquest --------------------------------------------------------------------


def _collapsed_at_war(world: World) -> tuple[str, str]:
    a, b = _at_war(world)
    world.country(a).stability = 0.0
    return a, b


def test_occupation_fires_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world()
    a, _ = _collapsed_at_war(world)
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)
    kinds = [e.kind for e in politics.run(world, Rng(1))]
    assert "OCCUPATION_BEGIN" in kinds
    assert world.country(a).status == CountryStatus.OCCUPIED


def test_no_country_is_ever_taken_when_conquest_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(allow_conquest=False)
    a, _ = _collapsed_at_war(world)
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)
    kinds = [e.kind for e in politics.run(world, Rng(1))]
    assert "OCCUPATION_BEGIN" not in kinds
    assert "ANNEXATION" not in kinds
    assert world.country(a).status == CountryStatus.ACTIVE


def test_a_war_still_ends_when_conquest_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hang risk, closed empirically. Occupation is how a war structurally ends, so
    switching conquest off has to leave the other exit open -- otherwise a country at
    stability 0 would sit at war forever and the toggle would quietly wedge the world.
    Turning it off in fact makes peace MORE reachable: the organic peace roll requires an
    ACTIVE country, and an unoccupied one stays ACTIVE."""
    world = _world(allow_conquest=False)
    a, b = _collapsed_at_war(world)
    monkeypatch.setattr(Rng, "roll", lambda self, p: True)

    kinds = [e.kind for e in politics.run(world, Rng(1))]

    assert "PEACE" in kinds
    assert world.country(a).at_war_with == []
    assert world.country(b).at_war_with == []
