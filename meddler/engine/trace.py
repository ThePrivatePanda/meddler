"""Causal trace: an event's whole causal component, DFS order. PROPOSAL §3.3, §4.6.

`trace(log, event_id)` returns the selected event's whole causal DAG component. It walks every
legacy/direct and typed additional parent to find the component roots, then traverses every
root's descendants in deterministic id-ordered DFS. Shared descendants are emitted once, with
all `parent_ids` retained for edge rendering. Histories containing only legacy `parent_id` edges
therefore preserve their historical tree order.

Node shape mirrors the frontend contract's `trace` message. `d` is a deterministic display
depth: the shortest known distance from any component root. It is not event severity and it does
not affect cascade scheduling. `cascade_clipped` surfaces the §6.4.3 depth-cap marker.
"""

from __future__ import annotations

from meddler.engine.events import Event, EventLog

TraceValue = int | str | bool | None | list[int]
TraceNode = dict[str, TraceValue]


def trace(log: EventLog, event_id: int) -> list[TraceNode]:
    """Return the selected event's whole causal DAG component without duplicate nodes.

    Roots and child edges are ordered by event id. A shared descendant appears at its
    first deterministic DFS position and exposes all parent ids for edge rendering.
    Single-parent histories therefore retain their historical DFS order exactly.
    """
    if event_id < 0 or event_id >= len(log):
        raise ValueError(f"no event with id {event_id}")

    ancestors = _ancestor_ids(log, event_id)
    roots = sorted(
        event_id
        for event_id in ancestors
        if not any(parent_id in ancestors for parent_id in log[event_id].parent_ids)
    )

    children: dict[int, tuple[int, ...]] = {}

    def child_ids(parent_id: int) -> tuple[int, ...]:
        if parent_id not in children:
            children[parent_id] = log.children_of(parent_id)
        return children[parent_id]

    component: set[int] = set()
    pending = list(reversed(roots))
    while pending:
        current = pending.pop()
        if current in component:
            continue
        component.add(current)
        pending.extend(reversed(child_ids(current)))

    display_depth: dict[int, int] = {}
    for current in sorted(component):
        parent_depths = [
            display_depth[parent_id]
            for parent_id in log[current].parent_ids
            if parent_id in display_depth
        ]
        display_depth[current] = min(parent_depths) + 1 if parent_depths else 0

    nodes: list[TraceNode] = []
    visited: set[int] = set()

    def visit(current: int) -> None:
        if current in visited or current not in component:
            return
        visited.add(current)
        nodes.append(_node(log[current], display_depth[current]))
        for child_id in child_ids(current):
            visit(child_id)

    for root_id in roots:
        visit(root_id)
    return nodes


def _ancestor_ids(log: EventLog, event_id: int) -> set[int]:
    ancestors: set[int] = set()
    pending = [event_id]
    while pending:
        current = pending.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        pending.extend(reversed(log[current].parent_ids))
    return ancestors


def _node(event: Event, display_depth: int) -> TraceNode:
    return {
        "id": event.id,
        "tick": event.tick,
        "kind": event.kind,
        "severity": event.severity,
        "country": event.country,
        "country2": event.country2,
        "parent_id": event.parent_id,
        "parent_ids": list(event.parent_ids),
        "d": display_depth,
        "is_intervention": event.is_intervention,
        "cascade_clipped": bool(event.payload.get("cascade_clipped", False)),
    }
