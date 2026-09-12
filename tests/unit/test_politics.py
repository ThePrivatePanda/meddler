from meddler.engine import config, tickloop  # noqa: F401  -- import populates EVENT_REGISTRY
from meddler.engine.model import CountryStatus, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import politics
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def test_occupation_begin_fires_on_scripted_stability_zero_active_war_world():
    """M3.2's own accept criterion (implementation-spec.md): a scripted stability-0 +
    active-war world triggers OCCUPATION_BEGIN."""
    world = _two_country_world()
    occupied, occupier = world.countries[0], world.countries[1]
    occupied.stability = 0.0
    occupied.at_war_with = [occupier.code]
    key = tuple(sorted((occupied.code, occupier.code)))
    world.relations[key] = -95.0  # below OCCUPATION_RELATION_THRESHOLD (-80)

    rng = Rng(1)
    occupation_events = []
    for _ in range(200):  # OCCUPATION_BASE_P=0.05/tick -- 200 ticks makes a miss vanishingly unlikely
        events = politics.run(world, rng)
        occupation_events += [e for e in events if e.kind == "OCCUPATION_BEGIN"]
        if occupation_events:
            break

    assert len(occupation_events) == 1
    event = occupation_events[0]
    assert event.country == occupied.code
    assert event.country2 == occupier.code
    assert occupied.status == CountryStatus.OCCUPIED
    assert occupied.occupied_by == occupier.code


def test_occupation_not_rolled_when_relation_above_threshold():
    world = _two_country_world()
    occupied, occupier = world.countries[0], world.countries[1]
    occupied.stability = 0.0
    occupied.at_war_with = [occupier.code]
    key = tuple(sorted((occupied.code, occupier.code)))
    world.relations[key] = -50.0  # not below -80

    rng = Rng(1)
    for _ in range(200):
        events = politics.run(world, rng)
        assert not [e for e in events if e.kind == "OCCUPATION_BEGIN"]
    assert occupied.status == CountryStatus.ACTIVE


def test_occupation_not_rolled_when_not_at_war():
    world = _two_country_world()
    occupied, occupier = world.countries[0], world.countries[1]
    occupied.stability = 0.0
    key = tuple(sorted((occupied.code, occupier.code)))
    world.relations[key] = -95.0  # relation is hostile enough, but no war

    rng = Rng(1)
    for _ in range(200):
        events = politics.run(world, rng)
        assert not [e for e in events if e.kind == "OCCUPATION_BEGIN"]


def test_election_fires_on_due_tick_and_reschedules():
    world = _two_country_world()
    country = world.countries[0]
    world.tick = country.election_due_tick

    events = politics.run(world, Rng(1))
    elections = [e for e in events if e.kind == "ELECTION" and e.country == country.code]
    assert len(elections) == 1
    assert country.election_due_tick == world.tick + config.ELECTION_INTERVAL_TICKS


def test_election_does_not_fire_before_due_tick():
    world = _two_country_world()
    country = world.countries[0]
    world.tick = country.election_due_tick - 1

    events = politics.run(world, Rng(1))
    assert not [e for e in events if e.kind == "ELECTION" and e.country == country.code]


def test_leader_change_replaces_name_and_traits():
    world = _two_country_world()
    country = world.countries[0]
    original_name = country.leader.name
    original_traits = list(country.leader.traits)
    world.tick = country.election_due_tick

    # Force the incumbent-changes branch deterministically by driving stability far
    # below the low-stability threshold and marking the leader corrupt -- pushes
    # _election_change_probability to 1.0 so Rng(1)'s roll must hit.
    country.stability = 0.0
    country.leader.traits = ["corrupt", "warhawk"]

    found_change = False
    for seed in range(20):
        # copy fresh state each attempt so failed seeds don't compound
        c = world.countries[0]
        c.leader.name = original_name
        c.leader.traits = ["corrupt", "warhawk"]
        c.election_due_tick = world.tick
        events = politics.run(world, Rng(seed))
        leader_change = [e for e in events if e.kind == "LEADER_CHANGE" and e.country == c.code]
        if leader_change:
            found_change = True
            assert c.leader.name != original_name or c.leader.traits != original_traits
            break

    assert found_change, "expected at least one seed to roll an incumbent change"


def test_coup_never_rolled_above_stability_threshold():
    world = _two_country_world()
    country = world.countries[0]
    country.stability = config.COUP_STABILITY_THRESHOLD  # at threshold: not eligible
    country.election_due_tick = None  # isolate the coup roll from the election branch

    rng = Rng(1)
    for _ in range(500):
        events = politics.run(world, rng)
        assert not [e for e in events if e.kind == "COUP" and e.country == country.code]
