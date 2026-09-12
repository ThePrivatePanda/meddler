"""M7.3: conquest/absorption completion. PROPOSAL §6.8.

WAR_DECLARED/PEACE structural effects (at_war_with, relations) and the ANNEXATION/
LIBERATION_WAR rolls + their structural effects, on top of M3.2's OCCUPATION_BEGIN
(already covered by tests/unit/test_politics.py).
"""

from __future__ import annotations

from meddler.engine import cascade, commodities, config, tickloop  # noqa: F401
from meddler.engine.model import CountryStatus, WorldSettings
from meddler.engine import structural
from meddler.engine.rng import Rng
from meddler.engine.systems import politics
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def _two_country_timeline(seed: int = 1) -> Timeline:
    world = generate_world(seed, WorldSettings(starting_country_count=2))
    return Timeline(seed=seed, world=world, snapshots={}, rng=Rng(seed))


# ---- WAR_DECLARED / PEACE structural effects ------------------------------------


def test_war_declared_populates_at_war_with_both_directions_and_sets_relation():
    world = _two_country_world()
    a, b = world.countries[0], world.countries[1]
    key = tuple(sorted((a.code, b.code)))
    world.relations[key] = -10.0

    event = cascade.emit_event(
        world, Rng(1), kind="WAR_DECLARED", primary=a.code, secondary=b.code,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )

    assert b.code in a.at_war_with
    assert a.code in b.at_war_with
    assert world.relations[key] == -90.0
    assert event.payload["relation_before"] == -10.0
    assert event.payload["relation_after"] == -90.0


def test_war_declared_does_not_duplicate_existing_war_entry():
    world = _two_country_world()
    a, b = world.countries[0], world.countries[1]
    a.at_war_with = [b.code]
    b.at_war_with = [a.code]

    cascade.emit_event(
        world, Rng(1), kind="WAR_DECLARED", primary=a.code, secondary=b.code,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )

    assert a.at_war_with == [b.code]
    assert b.at_war_with == [a.code]


def test_peace_clears_at_war_with_both_directions():
    world = _two_country_world()
    a, b = world.countries[0], world.countries[1]
    a.at_war_with = [b.code]
    b.at_war_with = [a.code]

    event = cascade.emit_event(
        world, Rng(1), kind="PEACE", primary=a.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )

    assert a.at_war_with == []
    assert b.at_war_with == []
    assert event.payload["ended_wars_with"] == b.code


def test_war_declared_no_ops_at_war_with_when_a_party_is_not_active():
    """M7.4: _declare_war no-ops the at_war_with/relation mutation if either side isn't
    ACTIVE (occupied countries shouldn't be draggable into fresh wars -- see
    docs/progress.md's M7.4 section). The event's own declarative stat_deltas
    (stability -3.0) and consequences still apply regardless -- only the structural
    war-state mutation is guarded."""
    world = _two_country_world()
    a, b = world.countries[0], world.countries[1]
    b.status = CountryStatus.OCCUPIED
    key = tuple(sorted((a.code, b.code)))
    world.relations[key] = -10.0

    event = cascade.emit_event(
        world, Rng(1), kind="WAR_DECLARED", primary=a.code, secondary=b.code,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )

    assert a.at_war_with == []
    assert b.at_war_with == []
    assert world.relations[key] == -10.0
    assert "relation_after" not in event.payload


def test_world_at_reconstructs_a_no_op_war_declared():
    """The replay counterpart to the no-op test above: world_at must NOT retroactively
    apply at_war_with/relation state for a WAR_DECLARED that never took effect live --
    _replay_declare_war relies on relation_after's absence from payload as that signal."""
    tl = _two_country_timeline()
    a, b = tl.world.countries[0].code, tl.world.countries[1].code
    tl.world.country(b).status = CountryStatus.OCCUPIED
    key = tuple(sorted((a, b)))
    for _ in range(5):
        tl.advance()

    war = cascade.emit_event(
        tl.world, Rng(1), kind="WAR_DECLARED", primary=a, secondary=b,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    for _ in range(5):
        tl.advance()

    at_war = tl.world_at(war.tick)
    assert at_war.country(a).at_war_with == []
    assert at_war.country(b).at_war_with == []
    assert key not in at_war.relations or at_war.relations[key] != -90.0


def test_world_at_reconstructs_war_declared_and_peace():
    tl = _two_country_timeline()
    a, b = tl.world.countries[0].code, tl.world.countries[1].code
    for _ in range(5):
        tl.advance()

    war = cascade.emit_event(
        tl.world, Rng(1), kind="WAR_DECLARED", primary=a, secondary=b,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    for _ in range(5):
        tl.advance()

    before_war = tl.world_at(war.tick - 1)
    at_war = tl.world_at(war.tick)
    assert before_war.country(a).at_war_with == []
    assert b in at_war.country(a).at_war_with
    assert at_war.relations[tuple(sorted((a, b)))] == -90.0

    peace = cascade.emit_event(
        tl.world, Rng(2), kind="PEACE", primary=a, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    for _ in range(5):
        tl.advance()

    at_peace = tl.world_at(peace.tick)
    assert at_peace.country(a).at_war_with == []
    assert at_peace.country(b).at_war_with == []


# ---- ANNEXATION -------------------------------------------------------------------


def _occupied_world():
    world = _two_country_world()
    occupied, occupier = world.countries[0], world.countries[1]
    occupied.status = CountryStatus.OCCUPIED
    occupied.occupied_by = occupier.code
    occupied.occupation_start_tick = 0
    occupied.stability = 30.0  # > ANNEXATION_LOW_RESISTANCE_STABILITY, "low resistance"
    world.log.append(
        tick=0, kind="OCCUPATION_BEGIN", country=occupied.code, country2=occupier.code,
        parent_id=None, depth=0, is_intervention=False, payload={}, severity=2,
    )
    return world, occupied, occupier


def test_annexation_rolls_after_min_occupation_ticks_and_merges_stats():
    world, occupied, occupier = _occupied_world()
    world.tick = config.ANNEXATION_MIN_OCCUPATION_TICKS
    occupied_pop_before = occupied.population
    occupied_grain_before = occupied.grain_stock
    occupier_pop_before = occupier.population
    occupier_grain_before = occupier.grain_stock

    rng = Rng(1)
    annexations = []
    for _ in range(300):  # ANNEXATION_BASE_P=0.05/tick -- 300 ticks makes a miss vanishing
        world.tick += 1
        events = politics.run(world, rng)
        annexations += [e for e in events if e.kind == "ANNEXATION"]
        if annexations or occupied.status == CountryStatus.ANNEXED:
            break

    assert annexations, "expected ANNEXATION to fire within 300 ticks of eligible occupation"
    event = annexations[0]
    assert event.country == occupied.code
    assert event.country2 == occupier.code
    assert occupied.status == CountryStatus.ANNEXED
    assert occupied.population == 0.0
    assert occupied.grain_stock == 0.0
    assert occupier.population == occupier_pop_before + occupied_pop_before
    assert occupier.grain_stock == occupier_grain_before + occupied_grain_before


def test_annexation_transfers_every_commodity_stock():
    """M10 gave countries six commodity buckets; annexation must move all of them, not
    just the food one that happens to still be called grain_stock."""
    world, occupied, occupier = _occupied_world()
    victim_before = dict(occupied.commodity_stock)
    annexer_before = dict(occupier.commodity_stock)

    cascade.emit_event(
        world, Rng(1), kind="ANNEXATION", primary=occupied.code, secondary=occupier.code,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )

    for name in config.COMMODITY_ORDER:
        assert occupier.commodity_stock[name] == annexer_before[name] + victim_before[name], (
            f"{name} stock was not transferred on annexation"
        )
        assert occupied.commodity_stock[name] == 0.0


def test_annexation_not_rolled_before_min_occupation_ticks():
    world, occupied, occupier = _occupied_world()
    world.tick = config.ANNEXATION_MIN_OCCUPATION_TICKS - 10

    rng = Rng(1)
    for _ in range(9):  # stays below the threshold the whole time
        world.tick += 1
        events = politics.run(world, rng)
        assert not [e for e in events if e.kind == "ANNEXATION"]
    assert occupied.status == CountryStatus.OCCUPIED


def test_annexation_not_rolled_when_resistance_too_high():
    world, occupied, occupier = _occupied_world()
    occupied.stability = 5.0  # below ANNEXATION_LOW_RESISTANCE_STABILITY: active resistance
    world.tick = config.ANNEXATION_MIN_OCCUPATION_TICKS

    rng = Rng(1)
    for _ in range(100):
        world.tick += 1
        events = politics.run(world, rng)
        assert not [e for e in events if e.kind == "ANNEXATION"]
    assert occupied.status == CountryStatus.OCCUPIED


def test_world_at_reconstructs_annexation():
    tl = _two_country_timeline()
    a, b = tl.world.countries[0], tl.world.countries[1]
    for _ in range(5):
        tl.advance()

    a_pop_before, a_grain_before = a.population, a.grain_stock
    b_pop_before, b_grain_before = b.population, b.grain_stock

    annexation = cascade.emit_event(
        tl.world, Rng(1), kind="ANNEXATION", primary=a.code, secondary=b.code,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    for _ in range(5):
        tl.advance()

    before = tl.world_at(annexation.tick - 1)
    after = tl.world_at(annexation.tick)
    assert before.country(a.code).status == CountryStatus.ACTIVE
    assert after.country(a.code).status == CountryStatus.ANNEXED
    assert after.country(a.code).population == 0.0
    assert after.country(b.code).population == b_pop_before + a_pop_before
    assert after.country(b.code).grain_stock == b_grain_before + a_grain_before


# ---- LIBERATION_WAR / OCCUPATION_END -----------------------------------------------


def test_liberation_war_rolls_when_occupied_stability_recovers():
    world, occupied, occupier = _occupied_world()
    occupied.stability = config.LIBERATION_STABILITY_THRESHOLD + 10.0
    world.tick = 10  # well before the annexation-eligibility window

    rng = Rng(1)
    liberations = []
    for _ in range(300):
        world.tick += 1
        events = politics.run(world, rng)
        liberations += [e for e in events if e.kind == "LIBERATION_WAR"]
        if liberations:
            break

    assert liberations, "expected LIBERATION_WAR to fire once stability recovered"
    event = liberations[0]
    assert event.country == occupied.code
    assert event.country2 == occupier.code


def test_occupation_end_structural_handler_restores_active_status():
    world, occupied, occupier = _occupied_world()

    event = cascade.emit_event(
        world, Rng(1), kind="OCCUPATION_END", primary=occupied.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )

    assert occupied.status == CountryStatus.ACTIVE
    assert occupied.occupied_by is None
    assert event.kind == "OCCUPATION_END"


def test_an_occupation_ending_does_not_resurrect_an_annexed_country():
    """A garrison withdrawing cannot undo a border being redrawn.

    OCCUPATION_END restored its country to ACTIVE unconditionally, but an occupation can
    end while the country is already ANNEXED -- annexation is what an occupation turns
    into. ANNEXATION transfers the whole population to the annexer and leaves the annexed
    country at zero, so what came back was a live nation with no people, no output and no
    needs, still holding elections and signing treaties in the feed. It also crashed the
    tick at t777 of seed 1337, because FXSystem divided by its zero food need.
    """
    world = generate_world(1337, WorldSettings(starting_country_count=3))
    dead, occupied = world.countries[0], world.countries[1]
    dead.status = CountryStatus.ANNEXED
    dead.population = 0.0
    occupied.status = CountryStatus.OCCUPIED
    occupied.occupied_by = world.countries[2].code

    for target in (dead, occupied):
        event = world.log.append(
            tick=world.tick,
            kind="OCCUPATION_END",
            country=target.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={},
        )
        structural.run(world, Rng(1), event)

    assert dead.status == CountryStatus.ANNEXED  # stays annexed; the border stands
    assert occupied.status == CountryStatus.ACTIVE  # a real occupation still ends
    assert occupied.occupied_by is None
