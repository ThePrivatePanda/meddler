"""M15 war and conquest economics (v2 spec §8).

Runs after DiplomacySystem and before ExogenousSystem. Diplomacy's existing M11 mutual-
defense path therefore gets first chance to add coalition fronts; every active front then
contributes conventional strikes in this system. Newly resource-motivated wars begin after
this tick's strike pass and become strike-eligible on the next tick.

Determinism surface, in order:
1. attackers sorted by code, then foes sorted by code: one strike roll per directed front;
2. prospective resource aggressors sorted by code: deterministic best target/commodity,
   then exactly one resource-war roll when an opportunity exists.

STRIKE mutations are structural because their distance-scaled values are dynamic rather
than static EventSpec deltas. The live handler records the already-clamped actual changes
on payload; the replay handler applies only those recorded outcomes and consumes no RNG.
"""

from __future__ import annotations

from meddler.engine import assets, cascade, commodities, config, space, structural
from meddler.engine.events import Event, record_structural_effect
from meddler.engine.model import Country, CountryStatus, World
from meddler.engine.rng import Rng
from meddler.engine.stats import attrition_delta


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _relation(world: World, a: str, b: str) -> float:
    x, y = sorted((a, b))
    return world.relations.get((x, y), 0.0)


def _distance_factor(a: Country, b: Country, coefficient: float) -> float:
    angle = space.central_angle(a.position, b.position)
    return 1.0 / (1.0 + coefficient * angle)


def _active_war_pairs(world: World) -> int:
    pairs: set[tuple[str, str]] = set()
    for country in world.living_countries():
        if country.status != CountryStatus.ACTIVE:
            continue
        for foe in country.at_war_with:
            other = world.country(foe)
            if other.status == CountryStatus.ACTIVE:
                a, b = sorted((country.code, foe))
                pairs.add((a, b))
    return len(pairs)


def _apply_strike(world: World, rng: Rng, event: Event) -> None:
    """Apply one conventional strike to primary; secondary is the attacker.

    `strike_power` is the already-determined distance factor. Each actual delta is recorded
    after clamping so replay never has to recalculate geometry or know clamp rules.
    """
    if event.country is None or event.country2 is None:
        return
    power_raw = event.payload.get("strike_power")
    if not isinstance(power_raw, (int, float)):
        return
    power = _clamp01(float(power_raw))
    target = world.country(event.country)

    stability_before = target.stability
    stability_after = max(0.0, stability_before - config.STRIKE_STABILITY_DAMAGE * power)
    stability_delta = stability_after - stability_before
    target.stability = stability_after
    event.payload["stability_delta"] = stability_delta
    record_structural_effect(
        event,
        target=target.code,
        metric="stability",
        before=stability_before,
        after=stability_after,
        delta=stability_delta,
    )

    population_before = target.population
    # Proportional, not subtractive: a strike takes a share of who remains, so repeated
    # strikes grind a nation down without ever erasing it (see config's note). The max() is
    # kept as a backstop and is now unreachable.
    requested = config.STRIKE_POPULATION_DAMAGE * power
    population_after = max(0.0, population_before + attrition_delta(population_before, requested))
    population_delta = population_after - population_before
    target.population = population_after
    event.payload["population_delta"] = population_delta
    record_structural_effect(
        event,
        target=target.code,
        metric="population",
        before=population_before,
        after=population_after,
        delta=population_delta,
    )

    for asset_class in assets.all_asset_classes():
        asset = assets.get_asset(target.infrastructure, asset_class)
        condition_before = asset.condition
        condition_after = max(0.0, condition_before - config.STRIKE_INFRA_DAMAGE * power)
        condition_delta = condition_after - condition_before
        asset.condition = condition_after
        event.payload[f"infra_delta_{asset_class}"] = condition_delta
        record_structural_effect(
            event,
            target=target.code,
            metric=f"infra:{asset_class}",
            before=condition_before,
            after=condition_after,
            delta=condition_delta,
        )


def _replay_strike(world: World, event: Event) -> None:
    """RNG-free replay of the exact, payload-recorded strike outcome."""
    if event.country is None:
        return
    target = world.country(event.country)
    stability_delta = event.payload.get("stability_delta")
    population_delta = event.payload.get("population_delta")
    if isinstance(stability_delta, (int, float)):
        target.stability += float(stability_delta)
    if isinstance(population_delta, (int, float)):
        target.population += float(population_delta)
    for asset_class in assets.all_asset_classes():
        delta = event.payload.get(f"infra_delta_{asset_class}")
        if isinstance(delta, (int, float)):
            assets.get_asset(target.infrastructure, asset_class).condition += float(delta)


def _strikes(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    active = {
        country.code: country
        for country in world.living_countries()
        if country.status == CountryStatus.ACTIVE
    }
    for attacker_code in sorted(active):
        attacker = active[attacker_code]
        for target_code in sorted(attacker.at_war_with):
            target = active.get(target_code)
            if target is None:
                continue
            power = _distance_factor(attacker, target, config.STRIKE_DISTANCE_K)
            if not rng.roll(config.STRIKE_BASE_P * power):
                continue
            events.append(
                cascade.emit_event(
                    world,
                    rng,
                    kind="STRIKE",
                    primary=target.code,
                    secondary=attacker.code,
                    parent_id=None,
                    depth=0,
                    is_intervention=False,
                    payload={
                        "strike_power": power,
                        "distance_km": space.distance_km(attacker.position, target.position),
                    },
                )
            )
    return events


def _same_bloc(world: World, a: str, b: str) -> bool:
    bloc = world.bloc_of(a)
    return bloc is not None and b in bloc.members


def _scarcity(country: Country, name: str) -> float:
    """Structural import dependence in [0,1], based on domestic flow rather than stock.

    Stock is a temporary buffer and imports can refill it; output below need is the durable
    scarcity that acquiring richer territory can actually address.
    """
    need = country.commodity_need[name]
    if need <= 0.0:
        return 0.0
    return _clamp01((need - country.commodity_output[name]) / need)


def _resource_opportunity(
    world: World, aggressor: Country
) -> tuple[float, Country, str, float] | None:
    """Return the strongest (score, target, commodity, angle), deterministically.

    score = own scarcity × target endowment advantage × hostility × proximity. Only
    extractive commodities can motivate territorial capture, allies/current enemies are
    excluded, and the hard angle gate prevents far-side resource wars.
    """
    candidates: list[tuple[float, Country, str, float]] = []
    for target in world.living_countries():
        if target.code == aggressor.code or target.status != CountryStatus.ACTIVE:
            continue
        if target.code in aggressor.at_war_with or _same_bloc(world, aggressor.code, target.code):
            continue
        relation = _relation(world, aggressor.code, target.code)
        hostility = _clamp01(-relation / 100.0)
        if hostility <= 0.0:
            continue
        angle = space.central_angle(aggressor.position, target.position)
        if angle > config.RESOURCE_WAR_MAX_ANGLE:
            continue
        proximity = 1.0 / (1.0 + config.RESOURCE_WAR_DISTANCE_K * angle)
        for name in commodities.extractive_names():
            spec = commodities.commodity(name)
            assert spec.endowment_key is not None
            advantage = target.endowments[spec.endowment_key] - aggressor.endowments[
                spec.endowment_key
            ]
            if advantage <= 0.0:
                continue
            score = _scarcity(aggressor, name) * advantage * hostility * proximity
            if score > 0.0:
                candidates.append((score, target, name, angle))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1].code, item[2]))
    return candidates[0]


def _resource_wars(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    war_pairs = _active_war_pairs(world)
    for aggressor in world.living_countries():
        if aggressor.status != CountryStatus.ACTIVE:
            continue
        if war_pairs >= world.settings.max_wars_concurrent:
            break
        opportunity = _resource_opportunity(world, aggressor)
        if opportunity is None:
            continue
        score, target, commodity, angle = opportunity
        if not rng.roll(config.RESOURCE_WAR_BASE_P * score):
            continue
        events.append(
            cascade.emit_event(
                world,
                rng,
                kind="WAR_DECLARED",
                primary=aggressor.code,
                secondary=target.code,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={
                    "cause": "resource",
                    "commodity": commodity,
                    "motivation": score,
                    "distance_km": angle * config.WORLD_RADIUS_KM,
                },
            )
        )
        war_pairs += 1
    return events


def run(world: World, rng: Rng) -> list[Event]:
    events = _strikes(world, rng)
    events += _resource_wars(world, rng)
    return events


structural.register_structural("STRIKE", _apply_strike)
structural.register_replay("STRIKE", _replay_strike)
