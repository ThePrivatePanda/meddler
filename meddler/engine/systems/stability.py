"""StabilitySystem: pull stability toward a country's own equilibrium, and push it away.
PROPOSAL §6.1 slot 7, §6.2.

"-5 one-shot on scandal" is the SCANDAL EventSpec's own stat_delta (M3.1+), applied by
ExogenousSystem/ConsequenceSystem when that event fires -- not a continuous rule this
system evaluates every tick. This system covers only the ambient, always-on conditions.
Runs after InflationSystem (§6.1 slot 6), so it reads this tick's already-updated
inflation, not last tick's.

MEAN REVERSION, replacing the flat recovery bonus. The old rule added a constant +0.22
whenever a country was not actively penalised and clamped the result at 100. That is a
one-sided ratchet against a hard ceiling: the clamp was the only attractor in the model,
so every country climbed to 100 and stayed, at a rate set purely by its genesis draw --
(100 - s0)/0.22 ticks, i.e. 68 ticks from 85 and 273 from 40. Lowering the bonus only
delayed the arrival; it could not change the destination, which is why this was a
mechanism problem and not a tuning one. With every country pinned at 100, UNREST_THRESHOLD
(35) became unreachable and an entire class of news could no longer fire.

Stability now reverts toward a TARGET the country carries, closing
STABILITY_REVERSION_RATE of the gap each tick -- the same idiom, and the same rate, as
InflationSystem's pull toward its baseline. The target is the country's genesis
temperament (`base_stability`, drawn from settings.starting_stability_range), shifted by
how well it treats its people. Nations therefore settle at DIFFERENT levels, the ceiling
is approached asymptotically instead of being hit, and a shock heals in proportion to its
size: a scratch is shrugged off, a catastrophe has a long tail.

WAR is the one condition with no equilibrium: its target is zero, so an at-war country's
delta is negative at every stability above zero, and monotonic-war-drain is structural
rather than the arithmetic accident of 0.22 < 0.3. The additive STABILITY_WAR_PENALTY
stays on top of that, because reversion alone is geometric and geometric decay never
ARRIVES: without it stability would pass under UNREST quickly and then approach zero
forever, and OCCUPATION_BEGIN's `stability <= 0.0` gate would become unreachable. The
additive term makes the last stretch linear, so a war still ends in a decision.

INFLATION and SHORTAGE stay additive, so they act as DISPLACEMENTS from the target rather
than slides to zero: a country under permanent shortage parks at target - penalty/rate
instead of dying, which is both more legible and more useful drama.
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.events import Event, StatDelta
from meddler.engine.model import Country, World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_country_stat


def social_index(country: Country) -> float:
    """How well a country treats its people, as one 0-100 number. Public because the
    target is a product claim ("richer-in-rights nations are calmer") that tests assert."""
    return (
        country.civil_rights + country.press_freedom + country.education + country.health
    ) / 4.0


def target_stability(country: Country) -> float:
    """The level this country's stability is pulled toward.

    A country at war has no equilibrium -- the target is zero and it drains until the war
    ends. Otherwise it is the genesis temperament, shifted by the social index around its
    genesis value, and capped below 100 so the ceiling is never an attractor.
    """
    if country.at_war_with:
        return 0.0
    shift = config.STABILITY_TARGET_SOCIAL_WEIGHT * (
        social_index(country) - config.STABILITY_TARGET_SOCIAL_REFERENCE
    )
    return max(0.0, min(config.STABILITY_TARGET_CEILING, country.base_stability + shift))


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in world.living_countries():
        # A food shortage is a STOCK condition (actually running low on food), NOT a flow
        # one. The old `grain_output < grain_need` flagged every structural food IMPORTER --
        # a country perfectly well fed through trade -- as permanently in shortage (fixed in
        # the 2026-07-22 pacing pass); it fires on the FAMINE_WARNING line, same as the
        # warning itself.
        shortage = country.grain_stock < country.grain_need * config.FAMINE_WARNING_DAYS

        target = target_stability(country)
        delta = config.STABILITY_REVERSION_RATE * (target - country.stability)
        if country.at_war_with:
            delta -= config.STABILITY_WAR_PENALTY
        if country.inflation > config.STABILITY_HIGH_INFLATION_THRESHOLD:
            delta -= config.STABILITY_HIGH_INFLATION_PENALTY
        if shortage:
            delta -= config.STABILITY_SHORTAGE_PENALTY

        new_stability = max(0.0, min(100.0, country.stability + delta))

        stat_deltas: list[StatDelta] = []
        apply_country_stat(
            world, stat_deltas, country.code, "stability", new_stability - country.stability
        )

        event = world.log.append(
            tick=world.tick,
            kind="STABILITY_UPDATE",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={"stability": new_stability, "target": target},
            stat_deltas=tuple(stat_deltas),
            severity=0,
        )
        events.append(event)
    return events
