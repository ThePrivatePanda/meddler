"""Regime change moves a nation's temperament, and REVOLUTION is reachable without famine.

Two linked gaps in the stability model:

- base_stability (the level stability reverts toward) was fixed at the genesis draw for a
  country's whole life unless someone used god mode, so a nation nothing happened to sat
  still at its own constant. A change of leader and a revolution now move it. The move is
  Country state no StatDelta carries, so these tests pin that world_at rebuilds it exactly
  and that a fork taken off a reconstruction tracks prime.
- REVOLUTION's only parent was FAMINE (grain_stock == 0), so a world whose trade relief
  works could not produce one however badly it was governed. CIVIL_WAR_RISK now leads to it.
"""

from __future__ import annotations

from meddler.engine import cascade, config, god, tickloop  # noqa: F401 -- populates EVENT_REGISTRY
from meddler.engine.events import ScheduleEntry
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import consequence, thresholds
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _world():
    return generate_world(1, WorldSettings(starting_country_count=2))


def test_political_collapse_reaches_revolution_without_any_famine():
    """Hold one country below the civil-war line with full granaries and run the real
    threshold -> schedule -> fire path. Before the CIVIL_WAR_RISK edge existed this loop
    could never produce a REVOLUTION, because the only parent was FAMINE."""
    world = _world()
    country = world.countries[0]
    rng = Rng(1)
    revolutions = []
    for _ in range(300):
        world.tick += 1
        country.stability = config.CIVIL_WAR_RISK_THRESHOLD - 5.0
        country.grain_stock = country.grain_need * config.FAMINE_WARNING_RECOVERY_DAYS * 2
        country.armed.pop("civil_war_risk", None)  # re-arm so every tick is a fresh crossing
        thresholds.run(world, rng)
        fired = consequence.run(world, rng)
        assert country.grain_stock > 0, "the test must stay famine-free to prove the new path"
        assert not any(e.kind == "FAMINE" for e in fired)
        revolutions += [e for e in fired if e.kind == "REVOLUTION" and e.country == country.code]
        if revolutions:
            break
    assert revolutions, "300 ticks below the civil-war line never produced a REVOLUTION"
    assert revolutions[0].parent_id is not None
    parent = world.log[revolutions[0].parent_id]
    assert parent.kind == "CIVIL_WAR_RISK"


def test_revolution_does_not_land_on_a_country_that_recovered_during_the_delay():
    world = _world()
    country = world.countries[0]
    country.stability = 60.0
    world.schedule.append(
        ScheduleEntry(
            fire_tick=world.tick,
            schedule_seq=world.schedule_seq,
            child_kind="REVOLUTION",
            parent_id=None,
            parent_depth=0,
            primary=country.code,
            secondary=None,
            payload={},
        )
    )
    world.schedule_seq += 1
    assert not [e for e in consequence.run(world, Rng(1)) if e.kind == "REVOLUTION"]


def test_a_new_leaders_traits_shift_where_the_country_settles():
    shifts = set()
    for seed in range(40):
        world = _world()
        country = world.countries[0]
        country.base_stability = 50.0
        event = cascade.emit_event(
            world, Rng(seed), kind="LEADER_CHANGE", primary=country.code, secondary=None,
            parent_id=None, depth=0, is_intervention=False, payload={},
        )
        good = sum(t in config.LEADER_GOOD_TRAITS for t in country.leader.traits)
        bad = sum(t in config.LEADER_BAD_TRAITS for t in country.leader.traits)
        expected = (good - bad) * config.LEADER_TEMPERAMENT_TRAIT_SHIFT
        assert country.base_stability == 50.0 + expected
        assert event.payload["base_stability_shift"] == expected
        recorded = [e for e in event.effects if e.metric == "base_stability"]
        assert len(recorded) == (0 if expected == 0.0 else 1)
        shifts.add(expected)
    # Both directions occur, so handovers wander rather than ratchet.
    assert any(s > 0 for s in shifts) and any(s < 0 for s in shifts), shifts


def test_revolution_moves_the_temperament_toward_the_middle_of_the_genesis_range():
    world = _world()
    country = world.countries[0]
    country.base_stability = 20.0
    country.stability = 10.0
    cascade.emit_event(
        world, Rng(1), kind="REVOLUTION", primary=country.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    low, high = world.settings.starting_stability_range
    midpoint = (low + high) / 2.0
    assert country.base_stability == 20.0 + config.REVOLUTION_TEMPERAMENT_RESET_SHARE * (
        midpoint - 20.0
    )


def test_world_at_and_a_fork_rebuild_a_temperament_moved_by_revolution_and_leader_change():
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    tl = Timeline(seed=1337, world=generate_world(1337, settings), snapshots={}, rng=Rng(1337))
    for _ in range(17):
        tl.advance()
    code = tl.world.countries[0].code
    god.god_edit(tl.world, code, "stability", 5.0)  # recorded, so replay sees it too
    genesis_like = tl.world.country(code).base_stability
    revolution = cascade.emit_event(
        tl.world, tl.rng, kind="REVOLUTION", primary=code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    assert revolution.payload["base_stability_shift"] != 0.0
    at = tl.world.tick
    live = {at: tl.world.country(code).base_stability}
    for _ in range(30):  # crosses snapshot boundaries; the LEADER_CHANGE child fires in here
        tl.advance()
        live[tl.world.tick] = tl.world.country(code).base_stability
    changes = [
        e for e in tl.world.log.events_between(at, tl.world.tick)
        if e.kind == "LEADER_CHANGE" and e.country == code
    ]
    assert changes, "the revolution's LEADER_CHANGE child should have fired"
    assert live[tl.world.tick] != genesis_like

    for tick, expected in live.items():
        assert tl.world_at(tick).country(code).base_stability == expected, tick

    # A fork whose reconstruction missed the shift would revert toward the old target and
    # drift from prime a little more every tick, so check a trajectory, not a point.
    mv = Multiverse(prime=tl)
    fork = mv.forks[mv.fork(at_tick=at + 3, intervention=None)]
    for _ in range(20):
        fork.advance()
        c = fork.world.country(code)
        p = tl.world_at(fork.world.tick).country(code)
        assert (c.stability, c.base_stability) == (p.stability, p.base_stability), (
            f"prime and fork diverged at t{fork.world.tick}"
        )
