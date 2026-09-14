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
from meddler.engine.systems import consequence, secession, thresholds
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


def _revolutions_due_from(parent_kind: str, stability_when_due: float) -> list:
    """Fire a real parent event, then queue a REVOLUTION child of it for a country whose
    stability has moved to `stability_when_due` by the time the child comes due."""
    world = _world()
    country = world.countries[0]
    country.stability = 5.0
    parent = cascade.emit_event(
        world, Rng(1), kind=parent_kind, primary=country.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    world.schedule.clear()  # only the hand-queued child below may fire
    country.stability = stability_when_due
    world.schedule.append(
        ScheduleEntry(
            fire_tick=world.tick,
            schedule_seq=world.schedule_seq,
            child_kind="REVOLUTION",
            parent_id=parent.id,
            parent_depth=parent.depth,
            primary=country.code,
            secondary=None,
            payload={},
        )
    )
    world.schedule_seq += 1
    return [e for e in consequence.run(world, Rng(1)) if e.kind == "REVOLUTION"]


def test_revolution_does_not_land_on_a_country_that_recovered_during_the_delay():
    assert not _revolutions_due_from("CIVIL_WAR_RISK", 60.0)
    assert _revolutions_due_from("CIVIL_WAR_RISK", 30.0), "still in unrest: it must land"


def test_the_unrest_gate_is_on_the_civil_war_edge_only_so_famine_still_topples_the_calm():
    """PROPOSAL §6.6.3's FAMINE -> REVOLUTION edge was ungated before the civil-war edge
    existed; gating the REVOLUTION kind itself would have silently changed it."""
    assert _revolutions_due_from("FAMINE", 60.0)


def test_a_new_leaders_traits_shift_where_the_country_settles():
    shifts = set()
    for seed in range(40):
        world = _world()
        country = world.countries[0]
        country.base_stability = 50.0
        country.genesis_stability = 50.0  # at its identity, so only the traits move it
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


def test_a_handover_pulls_a_drifted_temperament_back_toward_its_genesis_draw():
    """The walk has a memory: without the pull, handover shifts are a running sum and a
    country that changes rulers often forgets what it was."""
    world = _world()
    country = world.countries[0]
    country.genesis_stability = 40.0
    country.base_stability = 70.0
    event = cascade.emit_event(
        world, Rng(3), kind="LEADER_CHANGE", primary=country.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    good = sum(t in config.LEADER_GOOD_TRAITS for t in country.leader.traits)
    bad = sum(t in config.LEADER_BAD_TRAITS for t in country.leader.traits)
    expected = (good - bad) * config.LEADER_TEMPERAMENT_TRAIT_SHIFT + (
        config.LEADER_TEMPERAMENT_REVERSION * (40.0 - 70.0)
    )
    assert event.payload["base_stability_shift"] == expected
    assert country.genesis_stability == 40.0

    # Over many handovers the gap stays bounded near the stationary spread instead of
    # growing with the square root of the count (sd ~20 after 50 at no reversion).
    gaps = []
    for seed in range(200):
        world = _world()
        country = world.countries[0]
        for step in range(50):
            cascade.emit_event(
                world, Rng(seed * 1000 + step), kind="LEADER_CHANGE", primary=country.code,
                secondary=None, parent_id=None, depth=0, is_intervention=False, payload={},
            )
        gaps.append(country.base_stability - country.genesis_stability)
    spread = (sum(g * g for g in gaps) / len(gaps)) ** 0.5
    assert spread < 8.0, spread


def test_revolution_moves_the_temperament_back_toward_the_countrys_own_genesis_draw():
    world = _world()
    country = world.countries[0]
    country.genesis_stability = 30.0  # below the middle of the range, to tell them apart
    country.base_stability = 10.0
    country.stability = 10.0
    cascade.emit_event(
        world, Rng(1), kind="REVOLUTION", primary=country.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    assert country.base_stability == 10.0 + config.REVOLUTION_TEMPERAMENT_RESET_SHARE * (
        30.0 - 10.0
    )


def test_a_seceded_country_replays_with_its_genesis_draw():
    world = _world()
    country = world.countries[0]
    country.genesis_stability = 41.5
    country.base_stability = 63.0
    rebuilt = secession._country_from_json(secession._country_to_json(country))
    assert (rebuilt.genesis_stability, rebuilt.base_stability) == (41.5, 63.0)


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

    # Force every read below through the store. Replay returns a resident event when one is
    # cached, so without this a shift written to the payload after the event was persisted
    # (and never re-persisted) would still be seen and the check could not catch it.
    tl.world.log.flush()
    tl.world.log._cache.clear()

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
