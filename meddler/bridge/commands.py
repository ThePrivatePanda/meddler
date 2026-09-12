"""Client -> server command handling. docs/frontend-contract.md §4/§6/§7/§9/§10.

`handle(session, cmd) -> list[dict]` dispatches on `cmd["cmd"]`, mutates `session`/its
Multiverse through engine/ functions ONLY (no simulation logic here, §4.1), and returns
the reply message(s) to send back over the socket -- pure translation + orchestration.
Unrecognized commands are ignored; a known command with a bad/missing field returns a
`toast` rather than crashing the connection (§12: "the server must ignore unrecognized
command fields").

Timeline disambiguation: commands that reference a specific timeline accept an optional
`tl` field ("A" | a fork id), defaulting to "A" (prime) -- the same convention the
contract's own `countryDetail` command already uses.

DOCUMENTED SIMPLIFICATION: Event ids are unique WITHIN a timeline's own log (engine
guarantee, §4.3.2) but are NOT remapped to be globally unique ACROSS timelines here,
unlike the contract's Event-object doc line ("id sequential, unique across both
timelines"). Every message carrying events also carries which timeline they belong to
(`timelines.A`/`timelines.B`, or this `tl` field), so a client can always disambiguate
via (timeline, id). Revisit if this proves insufficient in practice; full global-id
remapping was judged out of scope for this milestone.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from meddler.bridge import adapter
from meddler.bridge.session import ForkMeta, Session, new_session
from meddler.engine import god
from meddler.engine import settings as world_settings
from meddler.engine.diff import diff as diff_worlds
from meddler.engine.diff import impact_diff
from meddler.engine.god import InterventionError
from meddler.engine.model import World
from meddler.engine.rng import Rng
from meddler.engine.timeline import Timeline

# contract §6 v1.1: "default is 1 tick/sec; setSpeed.tps in {0.5,1,2,4}"
VALID_TPS = (0.5, 1.0, 2.0, 4.0)

# contract godEdit field -> engine Country attribute (or "treasury"/"grain_stock"
# special-cased below). "grainDays" is display-derived (grain_stock/grain_need), not a
# stored field -- see _god_edit's conversion.
_GOD_EDIT_FIELD_MAP = {
    "stability": "stability",
    "inflation": "inflation",
    "gdp": "gdp_tick",
    "pop": "population",
    "treasury": "treasury",
    "grainDays": "grain_stock",
    "fx": "exchange_rate",
}

# WorldSettings keys flagged "restart required" in settingsAck (§6.9's own table).
RESTART_REQUIRED_SETTINGS = frozenset(
    {
        "starting_country_count",
        "starting_stability_range",
        "starting_inflation_range",
        "rival_pairs",
        "friendly_pairs",
        "region_count",
    }
)


def _timeline_world(session: Session, tl: str) -> World:
    if tl == "A":
        return session.multiverse.prime.world
    fork = session.multiverse.forks.get(tl)
    return fork.world if fork is not None else session.multiverse.prime.world


def _timeline(session: Session, tl: str) -> "Timeline":
    """The resolved Timeline (prime for "A", else the fork, falling back to prime) --
    used where the caller needs timeline-level state (e.g. stat_history), not just its
    World. Mirrors _timeline_world's fallback."""
    if tl == "A":
        return session.multiverse.prime
    return session.multiverse.forks.get(tl) or session.multiverse.prime


def _historical_world(timeline: Timeline, tick: int) -> World:
    """A timeline's world at `tick`, refusing ticks it has not lived through.

    `Timeline.world_at` will happily stamp a reconstruction with a tick beyond the live
    edge (there is a snapshot at or before it, after all), which would answer a scrub past
    the present with a world labelled as a future that has not happened."""
    if tick < 0 or tick > timeline.world.tick:
        raise ValueError(f"no history at tick {tick}")
    return timeline.world_at(tick)


def _rng_for(session: Session, tl: str) -> Rng:
    if tl == "A":
        return session.multiverse.prime.rng
    fork = session.multiverse.forks.get(tl)
    return fork.rng if fork is not None else session.multiverse.prime.rng


def _leave_scrub(session: Session, *, resume: bool) -> None:
    """Drop the read-only historical view. Every command that moves the world forward goes
    through here: a scrub holds the tick loop (runtime.advance_live), so a command that
    leaves `scrub_tick` set behind leaves the user watching a dead clock. `resume` restores
    the pre-scrub play state (resumeLive, forking, focusing); `step` passes False because
    stepping is itself a paused action."""
    if session.scrub_tick is None:
        return
    session.scrub_tick = None
    if resume:
        session.running = session.pre_scrub_running


def _current_view(session: Session) -> dict[str, Any]:
    """The one message that re-states what the client should currently be looking at: a
    two-column `timelineFocus` while a fork is focused, otherwise a live prime `snapshot`.

    Replies go through this instead of always sending a `snapshot`, because a snapshot means
    "prime, one column" to the frontend -- answering a fork-mode command with one collapses
    the comparison the user is in the middle of."""
    if session.focused_fork_id is not None and session.focused_fork() is not None:
        return adapter.timeline_focus_message(session, session.focused_fork_id)
    return adapter.snapshot_message(
        session, session.multiverse.prime.world, live=True, scrubbed=False
    )


def _focused_timeline_id(session: Session) -> str:
    fork_id = session.focused_fork_id
    return fork_id if fork_id is not None and session.focused_fork() is not None else "A"


def advance_one_tick(session: Session) -> list[dict[str, Any]]:
    """One tick of whichever timeline is focused, plus the messages it produces.

    Prime is the clock while prime is focused. While a FORK is focused the fork is the
    clock: prime is only advanced when the fork catches up to it, so a fork taken in the
    past replays prime's recorded history in the A column (aligned, §5.3) instead of racing
    a live prime further into the future. Either way both columns move exactly one tick per
    frame, and `countryAdded` precedes the frame that first carries a new country.
    """
    prime = session.multiverse.prime
    fork = session.focused_fork()
    messages: list[dict[str, Any]] = []
    if fork is None:
        events = prime.advance()
        messages.extend(adapter.newborn_country_messages(prime.world, "A"))
        messages.append(adapter.frame_message(session, events))
        return messages

    fork_events = fork.advance()
    while prime.world.tick < fork.world.tick:
        prime.advance()
        messages.extend(adapter.newborn_country_messages(prime.world, "A"))
    messages.extend(adapter.newborn_country_messages(fork.world, session.focused_fork_id or "B"))
    messages.append(adapter.frame_message(session, [], new_events_b=fork_events))
    return messages


def _pause(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    session.running = False
    return [adapter.status_message(session)]


def _resume(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    session.running = True
    return [adapter.status_message(session)]


def _set_speed(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    tps = float(cmd.get("tps", session.tps))
    if tps not in VALID_TPS:
        return [adapter.toast_message(f"invalid tps: {tps}", tone="warn")]
    session.tps = tps
    return [adapter.status_message(session)]


def _step(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    """Advance one tick. Stepping out of a scrub view returns to the present first (and
    says so with the current view) rather than stepping a world the client cannot see."""
    messages: list[dict[str, Any]] = []
    if session.scrub_tick is not None:
        _leave_scrub(session, resume=False)
        messages.append(_current_view(session))
    return messages + advance_one_tick(session)


def _world_at(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    tick = int(cmd["tick"])
    world = _historical_world(session.multiverse.prime, tick)  # may raise ValueError: no such tick
    if session.scrub_tick is None:
        session.pre_scrub_running = session.running
    session.running = False
    session.scrub_tick = tick
    return [
        adapter.snapshot_message(session, world, live=False, scrubbed=True),
        adapter.status_message(session),
    ]


def _resume_live(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    _leave_scrub(session, resume=True)
    return [_current_view(session), adapter.status_message(session)]


def _event_impact(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    tl = str(cmd.get("tl", "A"))
    timeline: Timeline
    if tl == "A":
        timeline = session.multiverse.prime
    else:
        fork = session.multiverse.forks.get(tl)
        if fork is None:
            raise ValueError(f"no such timeline: {tl}")
        timeline = fork

    event_id = int(cmd["eventId"])
    horizon_raw = cmd.get("horizon")
    horizon = int(horizon_raw) if horizon_raw is not None else None
    if event_id < 0 or event_id >= len(timeline.world.log):
        raise ValueError(f"no event with id {event_id}")
    horizon_diff = _event_horizon_diff(session, tl, event_id, horizon)
    return [
        adapter.impact_message(
            timeline.world,
            event_id,
            timeline_id=tl,
            horizon=horizon,
            horizon_diff=horizon_diff,
        )
    ]


def _annals_impact(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    tl = str(cmd.get("tl", "A"))
    world = _timeline_world(session, tl)
    sort_by = str(cmd.get("sortBy", "descendants"))
    limit = int(cmd.get("limit", 30))
    return [
        adapter.annals_impact_message(
            world,
            timeline_id=tl,
            sort_by=sort_by,
            limit=limit,
        )
    ]


def _event_horizon_diff(
    session: Session, tl: str, event_id: int, horizon: int | None
) -> dict[str, Any]:
    if tl == "A":
        return {
            "available": False,
            "reason": "Prime has no separate baseline; fork the timeline for comparison.",
        }
    if horizon is None:
        return {
            "available": False,
            "reason": "A tick horizon is required for an aligned fork comparison.",
        }
    if horizon < 0:
        raise ValueError("horizon must be non-negative")

    fork = session.multiverse.forks[tl]
    endpoint = fork.world.log[event_id].tick + horizon
    fork_tick = session.fork_meta[tl].fork_tick
    if endpoint < fork_tick:
        return {
            "available": False,
            "reason": "The requested horizon ends before this fork's divergence point.",
        }
    available_tick = min(session.multiverse.prime.world.tick, fork.world.tick)
    if endpoint > available_tick:
        return {
            "available": False,
            "reason": f"Aligned histories are only available through tick {available_tick}.",
        }

    prime_world = session.multiverse.prime.world_at(endpoint)
    fork_world = fork.world_at(endpoint)
    return {
        "available": True,
        "basis": "alignedFork",
        "tick": endpoint,
        "scope": "Whole-world fork minus prime difference; not organic-event suppression.",
        "diff": diff_worlds(prime_world, fork_world),
        "effects": impact_diff(prime_world, fork_world),
    }


def _trace(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    world = _timeline_world(session, cmd.get("tl", "A"))
    return [adapter.trace_message(world, int(cmd["eventId"]))]


def _intervene(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    """Fork prime at `atTick` and plant the intervention there. `atTick` is optional: a
    client intervening "now" simply omits it, and a client intervening from a scrub view
    gets the tick it is looking at. Forking always returns the session to a live clock --
    the whole point is to watch the counterfactual unfold."""
    kind = cmd["kind"]
    country = cmd["country"]
    at_tick_raw = cmd.get("atTick")
    if at_tick_raw is None:
        at_tick = (
            session.scrub_tick
            if session.scrub_tick is not None
            else session.multiverse.prime.world.tick
        )
    else:
        at_tick = int(at_tick_raw)
    target2 = cmd.get("target2")

    fork_id = session.multiverse.fork(at_tick=at_tick, intervention=None)  # may raise ValueError
    fork = session.multiverse.forks[fork_id]
    roster_before = {c.code for c in fork.world.countries}
    try:
        god.intervene(fork.world, fork.rng, kind=kind, country=country, country2=target2)
    except InterventionError as exc:
        session.multiverse.drop_fork(fork_id)
        return [adapter.toast_message(str(exc), tone="warn")]

    session.fork_meta[fork_id] = ForkMeta(label=f"{kind} @ {country}", fork_tick=at_tick)
    session.focused_fork_id = fork_id
    _leave_scrub(session, resume=True)
    shared_recent = list(fork.world.log.tail(adapter.FORK_SHARED_RECENT_LIMIT))
    # An intervention that grows the roster (INTERVENE_SECEDE) announces each new country
    # before the fork's first world projection, so clients hold its identity first.
    added = [
        adapter.country_added_message(fork.world, c.code, c.parent_code, timeline=fork_id)
        for c in fork.world.countries
        if c.code not in roster_before
    ]
    return [
        *added,
        adapter.fork_started_message(session, fork_id, fork.world, shared_recent),
        adapter.status_message(session),
    ]


def _drop_fork(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    fork_id = cmd.get("id")
    if fork_id is None or fork_id not in session.multiverse.forks:
        return [adapter.toast_message("no such fork", tone="warn")]
    session.multiverse.drop_fork(fork_id)
    session.fork_meta.pop(fork_id, None)
    if session.focused_fork_id == fork_id:
        session.focused_fork_id = next(iter(session.multiverse.forks), None)
    _leave_scrub(session, resume=True)
    return [
        adapter.fork_dropped_message(str(fork_id), _focused_timeline_id(session)),
        _current_view(session),
        adapter.status_message(session),
    ]


def _focus_timeline(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    target = cmd.get("id", "A")
    if target != "A" and target not in session.multiverse.forks:
        return [adapter.toast_message("no such fork", tone="warn")]
    session.focused_fork_id = None if target == "A" else str(target)
    _leave_scrub(session, resume=True)
    return [_current_view(session), adapter.status_message(session)]


def _adopt_fork(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    fork_id = cmd.get("id")
    if fork_id is None or fork_id not in session.multiverse.forks:
        return [adapter.toast_message("no such fork", tone="warn")]
    session.multiverse.adopt_fork(fork_id)
    session.fork_meta.clear()
    session.focused_fork_id = None
    _leave_scrub(session, resume=True)
    return [
        adapter.fork_adopted_message(session, str(fork_id)),
        adapter.snapshot_message(
            session, session.multiverse.prime.world, live=True, scrubbed=False
        ),
        adapter.status_message(session),
    ]


_SCRUB_REFUSAL = (
    "God edits change the present, not the past. Return to live — or fork from here "
    "with an intervention."
)


def _god_target(session: Session, cmd: dict[str, Any]) -> str:
    """Which timeline a god edit hits: the focused one by default (contract §7: "mutate the
    focused timeline in place"), or whatever explicit `tl` the client sent."""
    return str(cmd.get("tl") or _focused_timeline_id(session))


def _god_edit(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    if session.scrub_tick is not None:
        return [adapter.toast_message(_SCRUB_REFUSAL, tone="warn")]
    world = _timeline_world(session, _god_target(session, cmd))
    code = cmd["code"]
    field = cmd["field"]
    value = float(cmd["value"])

    engine_field = _GOD_EDIT_FIELD_MAP.get(field)
    if engine_field is None:
        return [adapter.toast_message(f"unknown god-edit field: {field}", tone="warn")]

    if field == "grainDays":
        engine_value = value * world.country(code).grain_need
    else:
        engine_value = value

    god.god_edit(world, code, engine_field, engine_value)
    return [_current_view(session)]


def _god_relation(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    if session.scrub_tick is not None:
        return [adapter.toast_message(_SCRUB_REFUSAL, tone="warn")]
    world = _timeline_world(session, _god_target(session, cmd))
    a, b = cmd["a"], cmd["b"]
    delta = float(cmd["delta"])
    key = tuple(sorted((a, b)))
    current = world.relations.get(key, 0.0)
    god.god_relation(world, a, b, current + delta)
    return [_current_view(session)]


def _god_peace(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    if session.scrub_tick is not None:
        return [adapter.toast_message(_SCRUB_REFUSAL, tone="warn")]
    tl = _god_target(session, cmd)
    world = _timeline_world(session, tl)
    rng = _rng_for(session, tl)
    god.god_peace(world, rng, cmd["code"])
    return [_current_view(session)]


def _restart(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    """Rewind PRIME to a checkpoint tick, keeping the world it grew from (§9). Genesis-only
    settings are untouched by design -- changing those needs `newWorld`."""
    tick = int(cmd["tick"])
    try:
        session.multiverse.restart(tick)
    except ValueError as exc:
        return [adapter.toast_message(str(exc), tone="warn")]
    session.focused_fork_id = None
    session.fork_meta.clear()
    _leave_scrub(session, resume=True)
    return [
        adapter.snapshot_message(
            session, session.multiverse.prime.world, live=True, scrubbed=False
        ),
        adapter.status_message(session),
    ]


def _new_world(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    """Genesis again: build a brand-new world from a seed and the current settings.

    `restart` rewinds the world it has; only this regenerates one, which is what the
    genesis-only settings (`starting_country_count`, `rival_pairs`, `region_count`, ...)
    actually need. History is reset honestly -- the old timeline, its forks and its SQLite
    store are gone, not hidden -- so the reply is a full `hello`/`status`/`snapshot`
    handshake, exactly what a client gets on a fresh connection.
    """
    seed_raw = cmd.get("seed")
    seed = session.seed if seed_raw is None else int(seed_raw)
    settings = copy.deepcopy(session.multiverse.prime.world.settings)
    incoming = cmd.get("settings")
    if incoming is not None:
        for key, value in world_settings.normalize_changes(settings, incoming).items():
            setattr(settings, key, value)
    try:
        # Built BEFORE the old world is torn down: a settings combination the generator
        # refuses must leave the running world untouched.
        fresh = new_session(seed, settings)
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        return [adapter.toast_message(f"cannot build that world: {exc}", tone="warn")]

    session.close()
    session.seed = seed
    session.multiverse = fresh.multiverse
    session.scrub_tick = None
    session.pre_scrub_running = session.running
    session.focused_fork_id = None
    session.fork_meta.clear()
    return [
        adapter.hello_message(session),
        adapter.status_message(session),
        adapter.snapshot_message(
            session, session.multiverse.prime.world, live=True, scrubbed=False
        ),
    ]


def _annals(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    """The engine's real archive for one timeline at one tick (§9). Defaults to what the
    client is looking at: the focused timeline, at the scrub tick while scrubbing."""
    tl = str(cmd.get("tl") or _focused_timeline_id(session))
    timeline = _timeline(session, tl)
    country_raw = cmd.get("country")
    country = str(country_raw) if country_raw not in (None, "", "ALL") else None
    tick_raw = cmd.get("tick")
    if tick_raw is None and tl == "A" and session.scrub_tick is not None:
        tick_raw = session.scrub_tick
    world = timeline.world
    if tick_raw is not None and int(tick_raw) != world.tick:
        world = _historical_world(timeline, int(tick_raw))  # may raise ValueError
    return [adapter.annals_data_message(world, timeline_id=tl, country=country)]


def _country_detail(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    tl = str(cmd.get("tl", "A"))
    detail = adapter.country_detail_message(
        _timeline_world(session, tl), cmd["code"], _timeline(session, tl).stat_history
    )
    detail["tl"] = tl
    return [detail]


def _country_chart_events(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    tl = str(cmd.get("tl", "A"))
    timeline = _timeline(session, tl)
    code = str(cmd["code"])
    start_tick = int(cmd["startTick"])
    end_tick = int(cmd["endTick"])
    if start_tick < -1 or end_tick > timeline.world.tick:
        raise ValueError("chart event interval is outside this timeline")
    return [
        adapter.country_chart_events_message(
            timeline.world,
            code,
            start_tick,
            end_tick,
            timeline_id=tl,
        )
    ]


def _update_settings(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    timeline = session.multiverse.prime
    changed = timeline.update_settings(cmd.get("settings", {}))
    applied_restart_required = [
        key for key in changed if key in RESTART_REQUIRED_SETTINGS
    ]
    return [
        adapter.settings_ack_message(
            timeline.world.settings, applied_restart_required
        )
    ]


_HANDLERS: dict[str, Callable[[Session, dict[str, Any]], list[dict[str, Any]]]] = {
    "pause": _pause,
    "resume": _resume,
    "setSpeed": _set_speed,
    "step": _step,
    "worldAt": _world_at,
    "resumeLive": _resume_live,
    "trace": _trace,
    "eventImpact": _event_impact,
    "annalsImpact": _annals_impact,
    "intervene": _intervene,
    "dropFork": _drop_fork,
    "focusTimeline": _focus_timeline,
    "adoptFork": _adopt_fork,
    "godEdit": _god_edit,
    "godRelation": _god_relation,
    "godPeace": _god_peace,
    "restart": _restart,
    "newWorld": _new_world,
    "annals": _annals,
    "countryDetail": _country_detail,
    "countryChartEvents": _country_chart_events,
    "updateSettings": _update_settings,
}


def handle(session: Session, cmd: dict[str, Any]) -> list[dict[str, Any]]:
    kind = cmd.get("cmd")
    handler = _HANDLERS.get(kind) if isinstance(kind, str) else None
    if handler is None:
        return []
    try:
        messages = handler(session, cmd)
    except InterventionError as exc:
        messages = [adapter.toast_message(str(exc), tone="warn")]
    except (KeyError, ValueError, TypeError) as exc:
        messages = [adapter.toast_message(f"{kind}: {exc}", tone="warn")]
    request_id = cmd.get("requestId")
    if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
        for message in messages:
            message["requestId"] = request_id
    return messages


__all__ = ["advance_one_tick", "handle", "VALID_TPS", "RESTART_REQUIRED_SETTINGS"]
