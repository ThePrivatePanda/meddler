"""M15 war and conquest economics (v2 spec §8)."""

from __future__ import annotations

import pytest

from meddler.engine import assets, cascade, commodities, config, structural, tickloop
from meddler.engine.model import Bloc, CountryStatus, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.space import Position
from meddler.engine.systems import commodity_production, war
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


class _AlwaysHit:
    def roll(self, probability: float) -> bool:
        return probability > 0.0


class _FirstHitOnly:
    def __init__(self) -> None:
        self.calls = 0

    def roll(self, probability: float) -> bool:
        self.calls += 1
        return self.calls == 1 and probability > 0.0


def _world(count: int = 3):
    return generate_world(17, WorldSettings(starting_country_count=count))


def _set_relation(world, a: str, b: str, value: float) -> None:
    world.relations[tuple(sorted((a, b)))] = value


def _strike_fingerprint(country) -> tuple[float, float, tuple[tuple[str, float], ...]]:
    conditions = tuple(
        (name, assets.get_asset(country.infrastructure, name).condition)
        for name in assets.all_asset_classes()
    )
    return country.stability, country.population, conditions


def _annexation_fingerprint(world, loser: str, winner: str) -> tuple:
    a = world.country(loser)
    b = world.country(winner)
    return (
        a.status,
        a.population,
        b.population,
        tuple(sorted(a.endowments.items())),
        tuple(sorted(b.endowments.items())),
        tuple((t.region, t.position.lat, t.position.lon) for t in a.territories),
        tuple((t.region, t.position.lat, t.position.lon) for t in b.territories),
        tuple(sorted(a.commodity_output.items())),
        tuple(sorted(b.commodity_output.items())),
        tuple(sorted(a.commodity_need.items())),
        tuple(sorted(b.commodity_need.items())),
        tuple(sorted(a.commodity_stock.items())),
        tuple(sorted(b.commodity_stock.items())),
        tuple(
            (
                name,
                assets.get_asset(a.infrastructure, name).count,
                assets.get_asset(a.infrastructure, name).condition,
                assets.get_asset(b.infrastructure, name).count,
                assets.get_asset(b.infrastructure, name).condition,
            )
            for name in assets.all_asset_classes()
        ),
        b.innovation_mult,
    )


def test_distance_reduces_both_strike_rate_and_damage() -> None:
    world = _world(2)
    near, far = world.countries
    near.position = Position(lat=0.0, lon=0.0)
    far.position = Position(lat=0.0, lon=180.0)

    near_factor = war._distance_factor(near, near, config.STRIKE_DISTANCE_K)
    far_factor = war._distance_factor(near, far, config.STRIKE_DISTANCE_K)

    assert near_factor == pytest.approx(1.0)
    assert 0.0 < far_factor < near_factor
    assert config.STRIKE_BASE_P * far_factor < config.STRIKE_BASE_P * near_factor
    assert config.STRIKE_POPULATION_DAMAGE * far_factor < (
        config.STRIKE_POPULATION_DAMAGE * near_factor
    )


def test_strike_records_exact_attrition_and_world_at_replays_it() -> None:
    world = _world(2)
    target, attacker = world.countries
    timeline = Timeline(seed=17, world=world, snapshots={}, rng=Rng(17))
    world.tick = 1

    event = cascade.emit_event(
        world,
        Rng(1),
        kind="STRIKE",
        primary=target.code,
        secondary=attacker.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"strike_power": 0.6, "distance_km": 1000.0},
    )

    assert event.payload["population_delta"] == pytest.approx(
        -config.STRIKE_POPULATION_DAMAGE * 0.6
    )
    assert event.payload["stability_delta"] == pytest.approx(
        -config.STRIKE_STABILITY_DAMAGE * 0.6
    )
    for name in assets.all_asset_classes():
        assert event.payload[f"infra_delta_{name}"] == pytest.approx(
            -config.STRIKE_INFRA_DAMAGE * 0.6
        )

    replayed = timeline.world_at(1)
    assert _strike_fingerprint(replayed.country(target.code)) == _strike_fingerprint(target)


def test_every_coalition_front_can_contribute_strikes() -> None:
    world = _world(3)
    ally_a, ally_b, enemy = world.countries
    world.blocs.append(
        Bloc(id="BLOC1", members=sorted([ally_a.code, ally_b.code]), formed_at_tick=0)
    )
    for country in world.countries:
        country.position = Position(lat=0.0, lon=0.0)
    ally_a.at_war_with = [enemy.code]
    ally_b.at_war_with = [enemy.code]
    enemy.at_war_with = sorted([ally_a.code, ally_b.code])

    events = war._strikes(world, _AlwaysHit())  # type: ignore[arg-type]
    directed_fronts = {(event.country2, event.country) for event in events}

    assert directed_fronts == {
        (ally_a.code, enemy.code),
        (ally_b.code, enemy.code),
        (enemy.code, ally_a.code),
        (enemy.code, ally_b.code),
    }


def _make_resource_opportunity(world) -> tuple[str, str, str]:
    aggressor, rich, lesser = world.countries
    for country in world.countries:
        country.position = Position(lat=0.0, lon=0.0)
    _set_relation(world, aggressor.code, rich.code, -70.0)
    _set_relation(world, aggressor.code, lesser.code, -70.0)
    _set_relation(world, rich.code, lesser.code, 20.0)
    for country in world.countries:
        for name in commodities.extractive_names():
            country.commodity_output[name] = country.commodity_need[name]
    aggressor.commodity_output["energy"] = 0.0
    aggressor.endowments["energy"] = 0.1
    rich.endowments["energy"] = 0.9
    lesser.endowments["energy"] = 0.5
    return aggressor.code, rich.code, lesser.code


def test_resource_target_is_the_richest_hostile_near_non_ally() -> None:
    world = _world(3)
    aggressor_code, rich_code, lesser_code = _make_resource_opportunity(world)
    aggressor = world.country(aggressor_code)

    opportunity = war._resource_opportunity(world, aggressor)
    assert opportunity is not None
    _score, target, commodity, _angle = opportunity
    assert target.code == rich_code
    assert commodity == "energy"

    world.blocs.append(
        Bloc(id="BLOC1", members=sorted([aggressor_code, rich_code]), formed_at_tick=0)
    )
    allied_excluded = war._resource_opportunity(world, aggressor)
    assert allied_excluded is not None
    assert allied_excluded[1].code == lesser_code


def test_resource_war_reuses_war_declared_and_honours_max_wars() -> None:
    world = _world(3)
    aggressor_code, rich_code, _ = _make_resource_opportunity(world)

    events = war._resource_wars(world, _FirstHitOnly())  # type: ignore[arg-type]

    assert len(events) == 1
    event = events[0]
    assert event.kind == "WAR_DECLARED"
    assert event.country == aggressor_code
    assert event.country2 == rich_code
    assert event.payload["cause"] == "resource"
    assert event.payload["commodity"] == "energy"
    assert rich_code in world.country(aggressor_code).at_war_with

    capped = _world(3)
    a, b, _c = capped.countries
    capped.settings.max_wars_concurrent = 1
    a.at_war_with = [b.code]
    b.at_war_with = [a.code]
    assert war._resource_wars(capped, _AlwaysHit()) == []  # type: ignore[arg-type]


def test_occupation_redirects_extractive_output_to_the_occupier() -> None:
    world = _world(2)
    occupied, occupier = world.countries
    occupied.status = CountryStatus.OCCUPIED
    occupied.occupied_by = occupier.code
    name = "raw_materials"  # static extractive: no grid recomputation obscures the flow
    occupied.commodity_output[name] = occupied.commodity_need[name] + 8.0
    occupier.commodity_output[name] = occupier.commodity_need[name]
    occupied.commodity_stock[name] = 0.0
    occupier.commodity_stock[name] = 0.0
    tribute = occupied.commodity_output[name] * config.OCCUPATION_EXTRACTIVE_TRIBUTE_SHARE

    commodity_production.run(world, Rng(1))

    assert occupied.commodity_stock[name] == pytest.approx(8.0 - tribute)
    assert occupier.commodity_stock[name] == pytest.approx(tribute)


def test_population_change_recomputes_every_commodity_need() -> None:
    world = _world(2)
    country = world.countries[0]
    country.population *= 0.4

    commodity_production.run(world, Rng(1))

    for name in commodities.ORDER:
        assert country.commodity_need[name] == pytest.approx(
            country.population * config.COMMODITY_NEED_PER_CAPITA[name]
        )


def test_annexation_captures_resource_capacity_territory_and_assets() -> None:
    world = _world(2)
    loser, winner = world.countries
    loser_pop = loser.population
    winner_pop = winner.population
    winner_energy = winner.endowments["energy"]
    loser_energy = loser.endowments["energy"]
    expected_energy = (
        winner_energy * winner_pop + loser_energy * loser_pop
    ) / (winner_pop + loser_pop)
    loser_stock = dict(loser.commodity_stock)
    winner_stock = dict(winner.commodity_stock)
    for name in assets.all_asset_classes():
        loser_asset = assets.get_asset(loser.infrastructure, name)
        winner_asset = assets.get_asset(winner.infrastructure, name)
        loser_asset.count = 5
        loser_asset.condition = 0.4
        winner_asset.count = 3
        winner_asset.condition = 0.8

    event = cascade.emit_event(
        world,
        Rng(1),
        kind="ANNEXATION",
        primary=loser.code,
        secondary=winner.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
    )

    assert loser.status == CountryStatus.ANNEXED
    assert loser.territories == []
    assert len(winner.territories) == 2
    assert winner.endowments["energy"] == pytest.approx(expected_energy)
    for name in commodities.ORDER:
        assert winner.commodity_stock[name] == pytest.approx(
            winner_stock[name] + loser_stock[name]
        )
        assert loser.commodity_stock[name] == 0.0
        assert loser.commodity_output[name] == 0.0
    for name in commodities.extractive_names():
        expected = commodities.genesis_output(
            name,
            population=winner.population,
            endowments=winner.endowments,
            gdp_tick=winner.gdp_tick,
            innovation_mult=winner.innovation_mult - config.ANNEXATION_GDP_BOOST,
            education=winner.education,
        )
        if name in config.GRID_FED_COMMODITIES:
            expected *= assets.production_multiplier(winner)
        assert winner.commodity_output[name] == pytest.approx(expected)
    for name in assets.all_asset_classes():
        loser_asset = assets.get_asset(loser.infrastructure, name)
        winner_asset = assets.get_asset(winner.infrastructure, name)
        assert loser_asset.count == 0
        assert loser_asset.condition == 0.0
        assert winner_asset.count == 5  # 3 existing + floor(5 * 0.5)
        assert winner_asset.condition == pytest.approx((3 * 0.8 + 2 * 0.4) / 5)
    assert event.payload["territory_count_captured"] == 1


def test_world_at_reconstructs_complete_m15_annexation_state() -> None:
    world = _world(2)
    loser, winner = world.countries
    for name in assets.all_asset_classes():
        assets.get_asset(loser.infrastructure, name).count = 7
        assets.get_asset(loser.infrastructure, name).condition = 0.33
    timeline = Timeline(seed=17, world=world, snapshots={}, rng=Rng(17))
    world.tick = 1

    cascade.emit_event(
        world,
        Rng(1),
        kind="ANNEXATION",
        primary=loser.code,
        secondary=winner.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
    )

    replayed = timeline.world_at(1)
    assert _annexation_fingerprint(replayed, loser.code, winner.code) == (
        _annexation_fingerprint(world, loser.code, winner.code)
    )


def test_war_system_is_in_the_m15_tick_slot_and_handlers_are_registered() -> None:
    systems = [system.__module__ for system in tickloop.SYSTEMS]
    war_index = systems.index("meddler.engine.systems.war")
    assert systems[war_index - 2] == "meddler.engine.systems.diplomacy"
    assert systems[war_index - 1] == "meddler.engine.systems.tariffs"
    assert systems[war_index + 1] == "meddler.engine.systems.exogenous"
    assert "STRIKE" in structural.STRUCTURAL_EFFECTS
    assert "STRIKE" in structural.REPLAY_EFFECTS


@pytest.mark.parametrize("seed", [1, 17, 1337])
def test_war_system_is_deterministic_across_repeated_seeded_runs(seed: int) -> None:
    def trace() -> tuple:
        world = generate_world(seed, WorldSettings(starting_country_count=4))
        a, b = world.countries[:2]
        a.position = Position(lat=0.0, lon=0.0)
        b.position = Position(lat=0.0, lon=0.0)
        a.at_war_with = [b.code]
        b.at_war_with = [a.code]
        rng = Rng(seed)
        for _ in range(250):
            world.tick += 1
            war.run(world, rng)
        strikes = tuple(
            (
                event.tick,
                event.country,
                event.country2,
                tuple(sorted(event.payload.items())),
            )
            for event in world.log
            if event.kind == "STRIKE"
        )
        return strikes, _strike_fingerprint(a), _strike_fingerprint(b)

    first = trace()
    second = trace()
    assert first == second
    assert first[0], "configured zero-distance war produced no STRIKE in 250 ticks"


def test_population_never_reaches_zero_under_sustained_maximum_damage():
    """A LIMIT, not a direction. Population loss used to be a flat subtraction floored at
    zero, so it ARRIVED there -- and a country with no people still gets simulated, its food
    need scaled to zero with its population, and FXSystem divided by that need. Guarding the
    division would have left the next divisor to find; scaling the damage removes the class.

    Asserting "population decreased" would pass under both rules. Only asserting that it
    stays strictly positive under damage that would long since have floored it can fail
    against the old one.
    """
    from meddler.engine.stats import attrition_delta

    population = 24.4  # NUM's genesis population on seed 1337, the country that hit zero
    worst = config.STRIKE_POPULATION_DAMAGE * 10.0  # far above any real strike's power
    for _ in range(500):
        population += attrition_delta(population, worst)
        assert population > 0.0, "population reached zero under sustained attrition"

    assert population < 1.0  # devastated, as it should be -- but still a country
