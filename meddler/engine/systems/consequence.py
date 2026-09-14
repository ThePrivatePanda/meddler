"""ConsequenceSystem: fire due entries from the schedule queue. PROPOSAL §6.1 slot 11, §6.4.

The queue (world.schedule) holds ScheduleEntry rows ordered by (fire_tick, schedule_seq)
per §4.3.7. Each tick this system drains every entry whose fire_tick has arrived, always
popping the minimum (fire_tick, schedule_seq) first, and re-scanning after each fire so
that a child scheduled with delay 0 (fire_tick == this tick) is interleaved in correct
schedule_seq order. The drain is bounded by the depth cap (a cascade cannot exceed
max_depth levels in one tick), so it always terminates.

For each due entry: look up the EventSpec, re-check its conditions AND the rule-carried
conditions at fire time (silent drop, no event, no RNG, if any fail -- §4.7), then emit
via the shared cascade path (applies deltas, appends the Event, schedules children). The
pinned RNG draw order for scheduling lives in cascade.py's module docstring.
"""

from __future__ import annotations

from meddler.engine import cascade
from meddler.engine.events import Event, ScheduleEntry
from meddler.engine.model import World
from meddler.engine.registry import EVENT_REGISTRY, world_rule_allows
from meddler.engine.rng import Rng


def _due(world: World) -> list[ScheduleEntry]:
    return [e for e in world.schedule if e.fire_tick <= world.tick]


def _edge_allows(world: World, entry: ScheduleEntry) -> bool:
    """Evaluate the fire_conditions of the edge that scheduled this entry.

    ScheduleEntry cannot carry Condition objects (events.py cannot import registry), so the
    edge is found again from the parent's kind. The parent is read only when some edge into
    this child kind is gated at all, so ungated consequences never touch the log. A read here
    is safe: every event emitted earlier in this drain has finished its structural handlers.
    If a parent had two gated edges into the same kind, both would have to pass."""
    gated = any(
        rule.fire_conditions
        for spec in EVENT_REGISTRY.values()
        for rule in spec.consequences
        if rule.child_kind == entry.child_kind
    )
    if not gated or entry.primary is None or entry.parent_id is None:
        return True
    parent_spec = EVENT_REGISTRY.get(world.log[entry.parent_id].kind)
    if parent_spec is None:
        return True
    return all(
        cascade.eval_conditions(world, rule.fire_conditions, entry.primary, entry.secondary)
        for rule in parent_spec.consequences
        if rule.child_kind == entry.child_kind
    )


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    while True:
        due = _due(world)
        if not due:
            break
        entry = min(due, key=lambda e: (e.fire_tick, e.schedule_seq))
        world.schedule.remove(entry)

        spec = EVENT_REGISTRY.get(entry.child_kind)
        if spec is None:
            continue  # unregistered kind: validate_all() forbids this, but never fire blind
        if entry.primary is None:
            continue
        # A consequence aimed at a country that has since left the world has nothing left
        # to happen to: an OCCUPATION_END queued before an annexation would otherwise
        # change no state and still report a celebration. Same silent drop as below.
        if not world.country(entry.primary).in_world or (
            entry.secondary is not None and not world.country(entry.secondary).in_world
        ):
            continue
        # Fire-time conditions: the spec's own preconditions plus the rule's extra ones.
        # Both are re-evaluated now because world state may have moved during the delay.
        if not world_rule_allows(entry.child_kind, world.settings):
            continue  # a world rule forbids this kind outright (§6.9); same silent drop
        if not cascade.eval_conditions(world, spec.conditions, entry.primary, entry.secondary):
            continue  # silently dropped; consumed its queue slot but emits nothing (§4.7)
        if not _edge_allows(world, entry):
            continue  # the scheduling edge's own fire-time gate; same silent drop

        events.append(
            cascade.emit_event(
                world,
                rng,
                kind=entry.child_kind,
                primary=entry.primary,
                secondary=entry.secondary,
                parent_id=entry.parent_id,
                depth=entry.parent_depth + 1,
                is_intervention=False,
                payload=dict(entry.payload),
            )
        )
    return events
