"""M11 DiplomacySystem: formation, mutual defense, propagation, strain, determinism."""

from __future__ import annotations

import meddler.engine.tickloop  # noqa: F401 -- populates EVENT_REGISTRY at import
from meddler.engine.model import Bloc, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.space import Position
from meddler.engine.systems import diplomacy
from meddler.engine.worldgen import generate_world


def make_world(country_count: int = 4, seed: int = 1):
    return generate_world(seed, WorldSettings(starting_country_count=country_count))


def _set_relation(world, a, b, v):
    world.relations[tuple(sorted((a, b)))] = v


def test_high_relation_shared_rival_near_pair_can_form_a_bloc_over_time():
    world = make_world(country_count=4)
    a, b, c = (world.countries[0].code, world.countries[1].code, world.countries[2].code)
    _set_relation(world, a, b, 90.0)
    _set_relation(world, a, c, -50.0)
    _set_relation(world, b, c, -50.0)
    world.country(b).position = world.country(a).position  # co-located -> passes angle gate
    rng = Rng(7)
    formed = False
    for _ in range(400):
        diplomacy.run(world, rng)
        if world.bloc_of(a) is not None:
            formed = True
            break
    assert formed, "a fully-eligible near pair never formed a bloc in 400 ticks"


def test_distant_pair_never_forms_despite_high_relation():
    world = make_world(country_count=4)
    a, b, c = (world.countries[0].code, world.countries[1].code, world.countries[2].code)
    _set_relation(world, a, b, 99.0)
    _set_relation(world, a, c, -50.0)
    _set_relation(world, b, c, -50.0)
    world.country(a).position = Position(lat=90.0, lon=0.0)
    world.country(b).position = Position(lat=-90.0, lon=0.0)  # antipodal, angle ~ pi
    rng = Rng(1)
    for _ in range(500):
        diplomacy.run(world, rng)
    assert world.bloc_of(a) is None


def test_mutual_defense_can_pull_an_ally_into_a_coalition_war():
    world = make_world(country_count=4)
    a, b, x = (world.countries[0].code, world.countries[1].code, world.countries[2].code)
    world.blocs.append(Bloc(id="BLOC1", members=sorted([a, b]), formed_at_tick=0))
    _set_relation(world, a, b, 80.0)
    world.country(a).at_war_with.append(x)
    world.country(x).at_war_with.append(a)
    world.country(b).position = world.country(a).position  # b next to a -> near x too
    rng = Rng(3)
    joined = False
    for _ in range(300):
        diplomacy.run(world, rng)
        if x in world.country(b).at_war_with:
            joined = True
            break
    assert joined, "an adjacent ally never joined the defender's war in 300 ticks"


def test_propagation_warms_intra_bloc_and_cools_toward_enemies():
    world = make_world(country_count=4)
    a, b, x = (world.countries[0].code, world.countries[1].code, world.countries[2].code)
    world.blocs.append(Bloc(id="BLOC1", members=sorted([a, b]), formed_at_tick=0))
    _set_relation(world, a, b, 30.0)
    _set_relation(world, a, x, 0.0)
    _set_relation(world, b, x, 0.0)
    world.country(a).at_war_with.append(x)  # a's enemy -> b should cool toward x
    rng = Rng(1)
    diplomacy.run(world, rng)
    assert world.relations[tuple(sorted((a, b)))] > 30.0  # warmed
    assert world.relations[tuple(sorted((b, x)))] < 0.0   # b cooled toward a's enemy


def test_strain_can_break_a_soured_bloc():
    # Isolate _strain (full run() would warm the bloc back up via propagation): a bloc whose
    # intra relation has soured well below the reference must dissolve under accumulated strain.
    world = make_world(country_count=4)
    a, b = sorted([world.countries[0].code, world.countries[1].code])
    world.blocs.append(Bloc(id="BLOC1", members=[a, b], formed_at_tick=0))
    _set_relation(world, a, b, 2.0)  # deeply soured -> factor ~20x, p ~0.01/tick
    rng = Rng(2)
    broke = False
    for _ in range(800):
        diplomacy._strain(world, rng)
        if not world.blocs:
            broke = True
            break
    assert broke, "a deeply-soured two-member bloc never dissolved under strain"


def test_healthy_bloc_rarely_breaks_under_strain():
    # A warm bloc (relation ~100, factor==1.0) should almost never break: at base 0.0005/tick
    # over 200 ticks the break probability is ~10%, so a fixed benign seed keeps it intact.
    world = make_world(country_count=4)
    a, b = sorted([world.countries[0].code, world.countries[1].code])
    world.blocs.append(Bloc(id="BLOC1", members=[a, b], formed_at_tick=0))
    _set_relation(world, a, b, 100.0)
    rng = Rng(1)
    for _ in range(200):
        diplomacy._strain(world, rng)
    assert world.blocs, "a healthy bloc broke under baseline strain (base rate too high?)"


def test_run_is_deterministic_for_a_fixed_seed():
    def trace():
        world = make_world(country_count=6)
        rng = Rng(42)
        out = []
        for _ in range(50):
            evs = diplomacy.run(world, rng)
            out.append(tuple((e.kind, e.country, e.country2) for e in evs))
        return tuple(out)

    assert trace() == trace()


def test_diplomacy_runs_inside_the_tick_loop():
    from meddler.engine import tickloop
    assert tickloop.diplomacy.run in tickloop.SYSTEMS
    names = [s.__module__ for s in tickloop.SYSTEMS]
    assert names.index("meddler.engine.systems.diplomacy") > \
        names.index("meddler.engine.systems.politics")
    assert names.index("meddler.engine.systems.diplomacy") < \
        names.index("meddler.engine.systems.exogenous")
