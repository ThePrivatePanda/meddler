"""M5.1: engine/god.py. PROPOSAL §3.5, §4.7, §12.3.4."""

from __future__ import annotations

import pytest

from meddler.engine import config, god, tickloop  # noqa: F401 -- populates EVENT_REGISTRY
from meddler.engine.systems import stability
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.trace import trace
from meddler.engine.worldgen import generate_world


def _two_country_timeline(seed: int = 1, snapshot_interval: int = 10) -> Timeline:
    settings = WorldSettings(starting_country_count=2, snapshot_interval=snapshot_interval)
    world = generate_world(seed, settings)
    return Timeline(seed=seed, world=world, snapshots={}, rng=Rng(seed))


# ---- intervene() ----------------------------------------------------------------


def test_intervene_fires_root_event_and_consequences_trace_back_to_it():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_DROUGHT", country=code)

    assert event.is_intervention is True
    assert event.parent_id is None
    assert event.depth == 0
    assert event.kind == "INTERVENE_DROUGHT"

    # INTERVENE_DROUGHT -> PRICE_SPIKE p=0.95: essentially guaranteed to have scheduled a
    # child. Drain the schedule queue via consequence.run to confirm it actually fires
    # and traces back to the intervention.
    from meddler.engine.systems import consequence

    fired: list = []
    for _ in range(20):
        tl.world.tick += 1
        fired.extend(consequence.run(tl.world, tl.rng))
        if fired:
            break

    assert fired, "expected INTERVENE_DROUGHT's PRICE_SPIKE consequence to fire"
    nodes = trace(tl.world.log, fired[0].id)
    assert nodes[0]["id"] == event.id
    assert nodes[0]["is_intervention"] is True


def test_intervene_two_target_kind_requires_second_country():
    tl = _two_country_timeline()
    a, b = tl.world.countries[0].code, tl.world.countries[1].code
    with pytest.raises(god.InterventionError):
        god.intervene(tl.world, tl.rng, kind="INTERVENE_WAR", country=a)
    # Providing it works.
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_WAR", country=a, country2=b)
    assert event.country == a
    assert event.country2 == b


def test_intervene_rejects_non_intervention_kind():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    with pytest.raises(god.InterventionError):
        god.intervene(tl.world, tl.rng, kind="DROUGHT", country=code)  # organic, not god-mode


def test_intervene_rejects_unknown_kind():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    with pytest.raises(god.InterventionError):
        god.intervene(tl.world, tl.rng, kind="NOT_A_REAL_KIND", country=code)


def test_intervene_raises_when_observer_only():
    tl = _two_country_timeline()
    tl.world.settings.observer_only = True
    code = tl.world.countries[0].code
    with pytest.raises(god.InterventionError):
        god.intervene(tl.world, tl.rng, kind="INTERVENE_DROUGHT", country=code)


def test_intervene_raises_when_target_protected():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    tl.world.settings.protected_countries = [code]
    with pytest.raises(god.InterventionError):
        god.intervene(tl.world, tl.rng, kind="INTERVENE_DROUGHT", country=code)


def test_intervene_chaos_resolves_to_a_registered_exogenous_kind():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    from meddler.engine.registry import EVENT_REGISTRY

    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_CHAOS", country=code)
    assert event.is_intervention is True
    assert EVENT_REGISTRY[event.kind].is_exogenous
    assert event.payload["chaos_resolved_to"] == event.kind


# ---- god_edit ---------------------------------------------------------------------


def test_god_edit_stability_records_before_after_and_mutates():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    before_value = tl.world.country(code).stability

    event = god.god_edit(tl.world, code, "stability", 12.5)

    assert tl.world.country(code).stability == 12.5
    assert event is not None
    assert event.kind == "GOD_EDIT"
    assert event.is_intervention is True
    assert event.payload["before"] == before_value
    assert event.payload["after"] == 12.5


def test_god_edit_treasury_uses_ledger_and_conserves_currency():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    before_treasury = tl.world.country(code).pools["treasury"]

    event = god.god_edit(tl.world, code, "treasury", before_treasury + 5000)

    assert tl.world.country(code).pools["treasury"] == before_treasury + 5000
    assert event is not None
    assert len(event.ledger) == 1
    assert event.ledger[0].kind == "mint"


def test_god_edit_rejects_non_editable_field():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    with pytest.raises(god.InterventionError):
        god.god_edit(tl.world, code, "leader", 1.0)


def test_god_edit_world_at_reconstructs_the_edited_value():
    """The M4.2 lesson applied proactively: a non-silent god_edit must actually replay
    correctly via world_at, not just mutate the live world."""
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    for _ in range(5):
        tl.advance()

    god.god_edit(tl.world, code, "stability", 3.0)
    edit_tick = tl.world.tick

    for _ in range(5):
        tl.advance()

    before = tl.world_at(edit_tick - 1)
    at_edit = tl.world_at(edit_tick)
    at_edit_again = tl.world_at(edit_tick)  # reconstruction must be idempotent
    assert before.country(code).stability != 3.0
    assert at_edit.country(code).stability == 3.0
    assert at_edit_again.country(code).stability == 3.0


def test_silent_god_edit_leaves_no_event():
    tl = _two_country_timeline()
    tl.world.settings.silent_god_edits = True
    code = tl.world.countries[0].code
    log_len_before = len(tl.world.log)

    event = god.god_edit(tl.world, code, "stability", 42.0)

    assert event is None
    assert len(tl.world.log) == log_len_before
    assert tl.world.country(code).stability == 42.0


# ---- god_relation -------------------------------------------------------------------


def test_god_relation_sets_value_clamped_and_records_event():
    tl = _two_country_timeline()
    a, b = tl.world.countries[0].code, tl.world.countries[1].code

    event = god.god_relation(tl.world, a, b, 500.0)  # out of range, must clamp

    key = tuple(sorted((a, b)))
    assert tl.world.relations[key] == 100.0
    assert event is not None
    assert event.kind == "GOD_RELATION_SHIFT"
    assert event.payload["after"] == 100.0


def test_god_relation_auto_ends_war_when_crossing_above_threshold():
    tl = _two_country_timeline()
    a, b = tl.world.countries[0].code, tl.world.countries[1].code
    key = tuple(sorted((a, b)))
    tl.world.relations[key] = -50.0
    tl.world.country(a).at_war_with = [b]
    tl.world.country(b).at_war_with = [a]

    event = god.god_relation(tl.world, a, b, -10.0)  # crosses above -30

    assert tl.world.country(a).at_war_with == []
    assert tl.world.country(b).at_war_with == []
    assert event is not None
    assert event.payload["ended_war"] is True


def test_silent_god_relation_leaves_no_event_but_still_applies():
    tl = _two_country_timeline()
    tl.world.settings.silent_god_edits = True
    a, b = tl.world.countries[0].code, tl.world.countries[1].code
    log_len_before = len(tl.world.log)

    event = god.god_relation(tl.world, a, b, 50.0)

    assert event is None
    assert len(tl.world.log) == log_len_before
    key = tuple(sorted((a, b)))
    assert tl.world.relations[key] == 50.0


# ---- god_peace ----------------------------------------------------------------------


def test_god_peace_clears_wars_and_fires_real_peace_kind():
    tl = _two_country_timeline()
    a, b = tl.world.countries[0].code, tl.world.countries[1].code
    tl.world.country(a).at_war_with = [b]
    tl.world.country(b).at_war_with = [a]

    event = god.god_peace(tl.world, tl.rng, a)

    assert tl.world.country(a).at_war_with == []
    assert tl.world.country(b).at_war_with == []
    assert event is not None
    assert event.kind == "PEACE"
    assert event.is_intervention is True
    assert event.country == a
    assert event.payload["ended_wars_with"] == b


def test_god_peace_no_wars_still_fires_event_with_empty_record():
    tl = _two_country_timeline()
    a = tl.world.countries[0].code
    event = god.god_peace(tl.world, tl.rng, a)
    assert event is not None
    assert event.payload["ended_wars_with"] == ""


def test_silent_god_peace_leaves_no_event_but_still_clears_wars():
    tl = _two_country_timeline()
    tl.world.settings.silent_god_edits = True
    a, b = tl.world.countries[0].code, tl.world.countries[1].code
    tl.world.country(a).at_war_with = [b]
    tl.world.country(b).at_war_with = [a]
    log_len_before = len(tl.world.log)

    event = god.god_peace(tl.world, tl.rng, a)

    assert event is None
    assert len(tl.world.log) == log_len_before
    assert tl.world.country(a).at_war_with == []
    assert tl.world.country(b).at_war_with == []


def test_a_stability_edit_moves_where_the_country_settles() -> None:
    """A god edit to stability carries into base_stability, so the edit changes the
    country's equilibrium rather than draining back to its genesis temperament."""
    world = generate_world(1337, WorldSettings(starting_country_count=3))
    country = world.countries[0]
    country.base_stability = 45.0
    country.stability = 45.0

    god.god_edit(world, country.code, "stability", 95.0)

    assert country.stability == 95.0
    expected = 45.0 + 50.0 * config.GOD_EDIT_TEMPERAMENT_SHARE
    assert country.base_stability == pytest.approx(expected)
    assert stability.target_stability(country) > 45.0


def test_a_stability_edit_survives_replay_and_a_fork_taken_from_it() -> None:
    """base_stability is Country state no event would otherwise record. It rides a
    StatDelta, so world_at rebuilds it generically -- and a fork taken off a reconstructed
    past must agree with prime on BOTH stability and the target it reverts toward."""
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    tl = Timeline(seed=1337, world=generate_world(1337, settings), snapshots={}, rng=Rng(1337))
    for _ in range(17):
        tl.advance()
    code = tl.world.countries[0].code
    god.god_edit(tl.world, code, "stability", 95.0)
    at = tl.world.tick
    live = {at: (tl.world.country(code).stability, tl.world.country(code).base_stability)}
    for _ in range(20):
        tl.advance()
        c = tl.world.country(code)
        live[tl.world.tick] = (c.stability, c.base_stability)

    for tick in (at, at + 1, at + 9, at + 20):
        rebuilt = tl.world_at(tick).country(code)
        assert (rebuilt.stability, rebuilt.base_stability) == pytest.approx(live[tick]), tick

    # Agreement over a TRAJECTORY, not at a point. A fork whose reconstruction missed
    # base_stability would revert toward the genesis draw instead of the edited target, so
    # the two timelines would pull apart a little more every tick. Checking only the fork's
    # opening state would not catch that -- it has to run.
    mv = Multiverse(prime=tl)
    fork = mv.forks[mv.fork(at_tick=at + 5, intervention=None)]
    assert fork.world.country(code).base_stability == pytest.approx(live[at + 5][1])
    for _ in range(15):
        fork.advance()
        c = fork.world.country(code)
        assert (c.stability, c.base_stability) == pytest.approx(live[fork.world.tick]), (
            f"prime and fork diverged at t{fork.world.tick}"
        )
