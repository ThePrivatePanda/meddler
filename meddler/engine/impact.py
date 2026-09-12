"""Pure causal-DAG impact analysis over the append-only event log.

No function here mutates state or consumes RNG. Descendants reached by multiple causal
paths are represented once by event id, so cumulative effects cannot double-count a
shared consequence.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from meddler.engine.events import Effect, Event, EventLog


@dataclass(frozen=True)
class EventEffect:
    event_id: int
    event_kind: str
    effect: Effect


@dataclass(frozen=True)
class EffectTotal:
    effect_type: str
    target: str
    metric: str
    unit: str
    delta: float | int
    event_ids: tuple[int, ...]


@dataclass(frozen=True)
class EventImpact:
    event: Event
    ancestors: tuple[Event, ...]
    descendants: tuple[Event, ...]
    immediate_effects: tuple[Effect, ...]
    downstream_effects: tuple[EventEffect, ...]
    cumulative_totals: tuple[EffectTotal, ...]
    horizon: int | None


def parent_index(log: EventLog) -> dict[int, tuple[int, ...]]:
    return {event.id: event.parent_ids for event in log}


def child_index(log: EventLog) -> dict[int, tuple[int, ...]]:
    children: dict[int, list[int]] = {}
    for event in log:
        for parent_id in event.parent_ids:
            children.setdefault(parent_id, []).append(event.id)
    return {event_id: tuple(sorted(ids)) for event_id, ids in children.items()}


def ancestor_ids(log: EventLog, event_id: int) -> tuple[int, ...]:
    _validate_event_id(log, event_id)
    found: set[int] = set()
    pending = list(reversed(log[event_id].parent_ids))
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(reversed(log[current].parent_ids))
    return tuple(sorted(found))


def descendant_ids(log: EventLog, event_id: int, horizon: int | None = None) -> tuple[int, ...]:
    _validate_event_id(log, event_id)
    if horizon is not None and horizon < 0:
        raise ValueError("horizon must be non-negative")
    cutoff = None if horizon is None else log[event_id].tick + horizon
    found: set[int] = set()
    pending = list(reversed(log.children_of(event_id)))
    while pending:
        current = pending.pop()
        if current in found:
            continue
        event = log[current]
        if cutoff is not None and event.tick > cutoff:
            continue
        found.add(current)
        pending.extend(reversed(log.children_of(current)))
    return tuple(sorted(found))


def component_ids(log: EventLog, event_id: int) -> tuple[int, ...]:
    """Return the selected event's roots and all descendants of those roots."""
    lineage = set(ancestor_ids(log, event_id)) | {event_id}
    roots = sorted(
        current
        for current in lineage
        if not any(parent_id in lineage for parent_id in log[current].parent_ids)
    )
    component: set[int] = set()
    pending = list(reversed(roots))
    while pending:
        current = pending.pop()
        if current in component:
            continue
        component.add(current)
        pending.extend(reversed(log.children_of(current)))
    return tuple(sorted(component))


def analyze(log: EventLog, event_id: int, horizon: int | None = None) -> EventImpact:
    _validate_event_id(log, event_id)
    ancestor_events = tuple(log[current] for current in ancestor_ids(log, event_id))
    descendant_events = tuple(log[current] for current in descendant_ids(log, event_id, horizon))
    immediate = tuple(log[event_id].effects)
    downstream = tuple(
        EventEffect(event.id, event.kind, effect)
        for event in descendant_events
        for effect in event.effects
    )
    cumulative = _aggregate(
        (EventEffect(event_id, log[event_id].kind, effect) for effect in immediate),
        downstream,
    )
    return EventImpact(
        event=log[event_id],
        ancestors=ancestor_events,
        descendants=descendant_events,
        immediate_effects=immediate,
        downstream_effects=downstream,
        cumulative_totals=cumulative,
        horizon=horizon,
    )


def _aggregate(
    immediate: Iterable[EventEffect],
    downstream: tuple[EventEffect, ...],
) -> tuple[EffectTotal, ...]:
    records = list(immediate)
    records.extend(downstream)
    totals: dict[tuple[str, str, str, str], float | int] = {}
    event_ids: dict[tuple[str, str, str, str], set[int]] = {}
    for record in records:
        effect = record.effect
        if effect.delta is None:
            continue
        key = (effect.effect_type, effect.target, effect.metric, effect.unit)
        totals[key] = totals.get(key, 0) + effect.delta
        event_ids.setdefault(key, set()).add(record.event_id)
    return tuple(
        EffectTotal(*key, totals[key], tuple(sorted(event_ids[key]))) for key in sorted(totals)
    )


def _validate_event_id(log: EventLog, event_id: int) -> None:
    if event_id < 0 or event_id >= len(log):
        raise ValueError(f"no event with id {event_id}")
