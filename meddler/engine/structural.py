"""Structural effects: the escape hatch for event kinds whose full effect exceeds the
declarative EventSpec model (scalar stat_deltas / pool_transfers / consequence scheduling).

PROPOSAL §4.7 says ConsequenceSystem's execution path has "no special-case code per
kind" -- true for the common case, but a few kinds are structural by nature: LEADER_CHANGE
replaces a Leader object, SECESSION creates a country, OCCUPATION_BEGIN flips
status/occupied_by. Rather than special-casing these with an if/elif chain in cascade.py
(which the "no special-case code per kind" rule is aimed at preventing), they register a
handler here by kind string -- the same registry-dispatch shape as EVENT_REGISTRY itself
(§4.7's own plugin philosophy), just for structural mutation instead of numeric deltas.

cascade.emit_event calls run(world, rng, event) for every fired event; kinds with no
registered handler are a no-op (the common case, stat_deltas/pool_transfers already
covered it). Handlers mutate `world` in place; they do NOT append new Events (that stays
the caller's/consequence-scheduling's job) -- see docs/design-decisions.md ("M3.2 politics").
One deliberate exception: INTERVENE_SECEDE (systems/secession.py) appends a single
SECESSION_SETTLEMENT bookkeeping event carrying the new state's opening balances, because
those ledger entries name a currency that only exists once the handler has run.

M4.2 ADDITION -- replay: `Timeline.world_at` (§4.4) reconstructs a World from a snapshot
plus recorded events, WITHOUT re-running systems (so it needs no RNG and cannot diverge).
The `run()` handlers above are wrong for that path -- they draw fresh RNG (e.g. a NEW
random successor name), which would silently reconstruct a DIFFERENT leader than the one
the live tick actually produced. So structural kinds also register a REPLAY handler here:
deterministic, RNG-free, and driven only by what the live handler already recorded onto
`event.payload` (LEADER_CHANGE's `new_leader_name`/`new_leader_traits`; OCCUPATION_BEGIN
needs no extra payload since `event.country2` already is the occupier). `Timeline.world_at`
calls `structural.replay(world, event)` for every replayed event, mirroring how it already
replays `event.ledger`/`event.stat_deltas`. A kind with no registered replay handler is a
silent no-op, same as `run()`'s default -- including hand-built test events with no
recorded payload, which replay handlers must tolerate (see each handler's own guard).
"""

from __future__ import annotations

from typing import Callable

from meddler.engine.events import Event
from meddler.engine.model import World
from meddler.engine.rng import Rng

StructuralHandler = Callable[[World, Rng, Event], None]
ReplayHandler = Callable[[World, Event], None]

STRUCTURAL_EFFECTS: dict[str, StructuralHandler] = {}
REPLAY_EFFECTS: dict[str, ReplayHandler] = {}


def register_structural(kind: str, handler: StructuralHandler) -> None:
    if kind in STRUCTURAL_EFFECTS:
        raise ValueError(f"structural effect already registered: {kind}")
    STRUCTURAL_EFFECTS[kind] = handler


def register_replay(kind: str, handler: ReplayHandler) -> None:
    if kind in REPLAY_EFFECTS:
        raise ValueError(f"replay effect already registered: {kind}")
    REPLAY_EFFECTS[kind] = handler


def run(world: World, rng: Rng, event: Event) -> None:
    handler = STRUCTURAL_EFFECTS.get(event.kind)
    if handler is not None:
        handler(world, rng, event)


def replay(world: World, event: Event) -> None:
    handler = REPLAY_EFFECTS.get(event.kind)
    if handler is not None:
        handler(world, event)
