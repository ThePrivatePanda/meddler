"""Queryable event archive: eras, wars, records, majorEvents. PROPOSAL §3.7.

`annals(world, country=None)` derives everything from the real event log -- no
client-side set dressing (contrast the frontend prototype's fabricated Annals, which
this replaces per §3.7: "the real engine supplies a queryable event archive... so eras
and records derive from actual history").

Engine/text layering (§4.1): engine/ never imports text/, so this module returns
STRUCTURED summaries only, no prose. "Eras" here are structural windows (a tick range
plus the window's dominant event kind), not the named-epigraph eras of the PROPOSAL §3.2
mock UI -- giving them poetic names/epigraphs is a text/bridge-layer concern, mirroring
how engine/trace.py returns nodes without a `headline` field (left to the M6 bridge
adapter, which already owns turning engine data into prose).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from meddler.engine.events import Event
from meddler.engine.model import World

MAJOR_SEVERITY = 1  # majorEvents = severity >= this
ERA_BOUNDARY_SEVERITY = 2  # a severity-2 event closes the current era and opens the next


def _involves(event: Event, country: str | None) -> bool:
    return country is None or event.country == country or event.country2 == country


def _event_summary(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "tick": event.tick,
        "kind": event.kind,
        "severity": event.severity,
        "country": event.country,
        "country2": event.country2,
        "is_intervention": event.is_intervention,
    }


def _major_events(world: World, country: str | None) -> list[dict[str, Any]]:
    events = [e for e in world.log if e.severity >= MAJOR_SEVERITY and _involves(e, country)]
    events.sort(key=lambda e: e.tick)
    return [_event_summary(e) for e in events]


@dataclass
class _War:
    aggressor: str
    defender: str
    start_tick: int
    end_tick: int | None  # None == still ongoing as of world.tick
    toll: int | None  # ticks the war lasted, or None if ongoing


def _derive_wars(world: World, country: str | None) -> list[dict[str, Any]]:
    """PEACE (§6.6.4) is registered targets=1 (a single country), not a pair -- so a
    war's end is heuristically the first PEACE fired by EITHER belligerent after the
    matching WAR_DECLARED. This is a best-effort derivation from what M3.1/M3.2 actually
    record, not a general multi-war ledger (no at_war_with-driven bookkeeping yet --
    M7.1/M7.3 territory)."""
    wars: list[_War] = []
    for event in world.log:
        if event.kind != "WAR_DECLARED" or event.country is None or event.country2 is None:
            continue
        if not _involves(event, country):
            continue
        belligerents = {event.country, event.country2}
        end_tick = next(
            (
                later.tick
                for later in world.log
                if later.tick > event.tick
                and later.kind == "PEACE"
                and later.country in belligerents
            ),
            None,
        )
        wars.append(
            _War(
                aggressor=event.country,
                defender=event.country2,
                start_tick=event.tick,
                end_tick=end_tick,
                toll=(end_tick - event.tick) if end_tick is not None else None,
            )
        )
    return [
        {
            "aggressor": w.aggressor,
            "defender": w.defender,
            "start_tick": w.start_tick,
            "end_tick": w.end_tick,
            "toll": w.toll,
            "outcome": "peace" if w.end_tick is not None else "ongoing",
        }
        for w in wars
    ]


def _derive_records(world: World, country: str | None) -> dict[str, Any]:
    """A handful of legible superlatives (§3.7's "records & superlatives"), computed
    only from events that actually carry a comparable number or from simple event
    counts. Deliberately not exhaustive -- a small, correct set beats a large,
    speculative one; extend as new payload-bearing kinds are added (M7.1)."""
    records: dict[str, Any] = {}

    worst_inflation: tuple[float, Event] | None = None
    for event in world.log:
        if event.kind != "INFLATION_CRISIS" or not _involves(event, country):
            continue
        value = event.payload.get("inflation")
        if isinstance(value, (int, float)) and (
            worst_inflation is None or value > worst_inflation[0]
        ):
            worst_inflation = (float(value), event)
    if worst_inflation is not None:
        value, event = worst_inflation
        records["worst_inflation"] = {
            "country": event.country,
            "tick": event.tick,
            "value": value,
        }

    finished_wars = [w for w in _derive_wars(world, country) if w["toll"] is not None]
    if finished_wars:
        records["longest_war"] = max(finished_wars, key=lambda w: w["toll"])

    coup_counts = Counter(
        e.country for e in world.log if e.kind == "COUP" and _involves(e, country) and e.country
    )
    if coup_counts:
        code, count = coup_counts.most_common(1)[0]
        records["most_coups"] = {"country": code, "count": count}

    return records


def _window_summary(window: list[dict[str, Any]], start_tick: int) -> dict[str, Any]:
    kinds = Counter(e["kind"] for e in window)
    return {
        "start_tick": start_tick,
        "end_tick": window[-1]["tick"],
        "event_count": len(window),
        "dominant_kind": kinds.most_common(1)[0][0],
    }


def _derive_eras(major_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Partition the timeline into windows that each end on a severity-2 event (§3.7's
    "named eras" -- structural boundaries here; naming/epigraphs are text/bridge work,
    see module docstring)."""
    if not major_events:
        return []
    eras: list[dict[str, Any]] = []
    window: list[dict[str, Any]] = []
    start_tick = major_events[0]["tick"]
    for event in major_events:
        window.append(event)
        if event["severity"] >= ERA_BOUNDARY_SEVERITY:
            eras.append(_window_summary(window, start_tick))
            window = []
            start_tick = event["tick"]
    if window:
        eras.append(_window_summary(window, start_tick))
    return eras


def annals(world: World, country: str | None = None) -> dict[str, Any]:
    """PROPOSAL §3.7: `annals {country?}` -> `{eras, wars, records, majorEvents}`, all
    derived from the real event log."""
    major_events = _major_events(world, country)
    return {
        "eras": _derive_eras(major_events),
        "wars": _derive_wars(world, country),
        "records": _derive_records(world, country),
        "majorEvents": major_events,
    }
