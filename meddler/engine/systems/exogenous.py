"""ExogenousSystem: roll root events that fire with no parent. PROPOSAL §6.1 slot 10, §6.4.

Each tick, every is_exogenous spec (iterated SORTED BY KIND for determinism) rolls
exogenous_base_p * world.settings.drama_multiplier once. On a hit, a target country
(or ordered pair, for targets == 2) is chosen from the sorted eligible set and a root
event (parent_id=None, depth=0) is emitted via the shared cascade path -- which applies
its stat_deltas/pool_transfers and schedules its consequences exactly as any event.

RNG order and the reasoning behind rolling before the eligibility choice are pinned in
cascade.py's module docstring. Settings tag filtering (§6.9 enabled/disabled_event_tags)
is honored here, as is the §6.6.4 `allow_nukes` gate (M7.1): NUCLEAR_TEST/NUCLEAR_STRIKE
are tagged "nuclear" and that tag is treated as unconditionally disabled while
world.settings.allow_nukes is False (default), regardless of enabled/disabled_event_tags
-- a disabled kind's roll is skipped entirely (no RNG consumed), which is already the
existing behavior for any tag-disabled kind, so this introduces no new RNG-order case.
"""

from __future__ import annotations

from meddler.engine import cascade
from meddler.engine.events import Event
from meddler.engine.model import World, WorldSettings
from meddler.engine.registry import EVENT_REGISTRY, EventSpec, world_rule_allows
from meddler.engine.rng import Rng

NUCLEAR_TAG = "nuclear"


def _tag_enabled(spec: EventSpec, settings: WorldSettings) -> bool:
    """§6.9: a kind is enabled if its tags intersect enabled_event_tags (or that list is
    the wildcard ["*"]) and do not intersect disabled_event_tags. Untagged kinds pass the
    enable gate only under the wildcard. §6.6.4: NUCLEAR_TEST/NUCLEAR_STRIKE additionally
    require settings.allow_nukes."""
    if NUCLEAR_TAG in spec.tags and not settings.allow_nukes:
        return False
    if not world_rule_allows(spec.kind, settings):
        return False
    if spec.tags & set(settings.disabled_event_tags):
        return False
    if "*" in settings.enabled_event_tags:
        return True
    return bool(spec.tags & set(settings.enabled_event_tags))


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    drama = world.settings.drama_multiplier
    # A country that has been annexed or dissolved is not a place a disaster can strike.
    codes = [c.code for c in world.living_countries()]
    for kind in sorted(EVENT_REGISTRY):
        spec = EVENT_REGISTRY[kind]
        if not spec.is_exogenous:
            continue
        if not _tag_enabled(spec, world.settings):
            continue
        if not rng.roll(spec.exogenous_base_p * drama):  # (1) roll first, always
            continue
        eligible = [c for c in codes if cascade.eval_conditions(world, spec.conditions, c, None)]
        if not eligible:
            continue  # fizzle: no country can host it; no target draw consumed
        primary = rng.choice(eligible)  # (2) primary
        secondary: str | None = None
        if spec.targets == 2:
            others = [c for c in codes if c != primary]
            if not others:
                continue
            secondary = rng.choice(others)  # (3) secondary
        events.append(
            cascade.emit_event(
                world,
                rng,
                kind=kind,
                primary=primary,
                secondary=secondary,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={},
            )
        )
    return events
