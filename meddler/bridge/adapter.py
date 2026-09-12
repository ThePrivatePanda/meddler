"""Engine -> contract message translation. docs/frontend-contract.md.

Pure functions: World/Event/Country -> the exact JSON-serializable dicts the contract
specifies. No simulation logic (§4.1) -- every value here is read from engine state some
engine/ function already computed; this module only reshapes, renames, and (per §12.3.4/
§12.3.8) derives lists from EVENT_REGISTRY rather than hardcoding them.

Field-name translation (engine snake_case -> contract camelCase, plus a few renames with
no 1:1 engine field -- gdp/gdp_tick, pop/population, fx/exchange_rate, grainDays derived
from grain_stock/grain_need) lives here and in commands.py's reverse direction for
godEdit. See docs/design-decisions.md ("M6 bridge") for why this translation is a bridge
concern, not an engine one: the engine has no notion of UI display units.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from meddler.bridge.world_objects import project_world_objects
from meddler.engine import config
from meddler.engine.annals import annals as engine_annals
from meddler.engine.diff import diff as diff_worlds
from meddler.engine.events import CauseLink, Effect, Event
from meddler.engine.impact import EventImpact
from meddler.engine.impact import analyze as analyze_impact
from meddler.engine.model import Country, CountryStatus, World, WorldSettings
from meddler.engine.registry import EVENT_REGISTRY
from meddler.engine.trace import trace as trace_events
from meddler.text.headlines import TEMPLATES, render
from meddler.text.interventions import meta_for

if TYPE_CHECKING:
    from meddler.bridge.session import Session

PROTOCOL_VERSION = 2
RECENT_EVENTS_LIMIT = 30
SNAPSHOT_RECENT_LIMIT = 30
FORK_SHARED_RECENT_LIMIT = 20
COUNTRY_DETAIL_SERIES_LIMIT = 240


def grain_days(country: Country) -> float:
    return country.grain_stock / country.grain_need if country.grain_need > 0 else 0.0


def active_countries(world: World) -> list[Country]:
    """§6/§9: annexed/dissolved countries "stop appearing in stats" -- clients must
    prune them from country lists and the globe (restart's own pruning contract, §3.7,
    is the same idea one layer down). Every world.countries iteration point in this
    module goes through here (M7.3) so a country's disappearance from frames is a single
    filter, not N ad hoc ones."""
    return [
        c
        for c in world.countries
        if c.status not in (CountryStatus.ANNEXED, CountryStatus.DISSOLVED)
    ]


def country_stats(country: Country) -> dict[str, Any]:
    """contract §2 CountryStats."""
    return {
        "stability": country.stability,
        "inflation": country.inflation,
        "gdp": country.gdp_tick,
        "grainDays": grain_days(country),
        "fx": country.exchange_rate,
        "treasury": country.pools.get("treasury", 0),
        "war": bool(country.at_war_with),
        "pop": country.population,
    }


def all_stats(world: World) -> dict[str, dict[str, Any]]:
    return {c.code: country_stats(c) for c in active_countries(world)}


def bloc_info(world: World, code: str) -> dict[str, Any] | None:
    """v2 §7 bloc membership for the dossier. None if unaligned. Dossier-only; blocs are not
    in per-tick frame/snapshot (M16/contract-v2 owns globe-level bloc rendering)."""
    bloc = world.bloc_of(code)
    if bloc is None:
        return None
    return {"id": bloc.id, "members": list(bloc.members), "formedAt": bloc.formed_at_tick}


def commodities_block(country: Country) -> dict[str, dict[str, float]]:
    """v2 §3 per-commodity output/need/stock, keyed by config.COMMODITY_ORDER. Dossier-only
    (countryDetail): 6x the per-country payload of a scalar stat, and only the dossier reads
    it, so it never rides the per-tick frame/snapshot. `food` here is the same bucket the
    grainDays scalar summarises -- this is the full six, unsummarised."""
    return {
        name: {
            "output": country.commodity_output[name],
            "need": country.commodity_need[name],
            "stock": country.commodity_stock[name],
        }
        for name in config.COMMODITY_ORDER
    }


def leader_info(country: Country) -> dict[str, Any]:
    return {
        "title": country.leader.title,
        "name": country.leader.name,
        "traits": list(country.leader.traits),
    }


def all_leaders(world: World) -> dict[str, dict[str, Any]]:
    return {c.code: leader_info(c) for c in active_countries(world)}


def war_pairs(world: World) -> list[list[str]]:
    """[[a,b], ...] deduplicated pairs currently at war, derived from at_war_with
    (populated since M7.3 by WAR_DECLARED's structural handler, systems/politics.py)."""
    seen: set[tuple[str, str]] = set()
    pairs: list[list[str]] = []
    for country in active_countries(world):
        for foe in country.at_war_with:
            a_code, b_code = sorted((country.code, foe))
            key = (a_code, b_code)
            if key not in seen:
                seen.add(key)
                pairs.append([key[0], key[1]])
    return pairs


def cause_to_json(link: CauseLink) -> dict[str, Any]:
    return {"eventId": link.event_id, "role": link.role, "detail": link.detail}


def effect_to_json(effect: Effect) -> dict[str, Any]:
    return {
        "type": effect.effect_type,
        "target": effect.target,
        "metric": effect.metric,
        "delta": effect.delta,
        "before": effect.before,
        "after": effect.after,
        "unit": effect.unit,
    }


def event_to_json(event: Event, world: World) -> dict[str, Any] | None:
    """contract §2 Event. Returns None for ambient/untemplated kinds -- rendering those
    would KeyError (text/headlines.py's TEMPLATES is the single source of truth for "is
    this narratively renderable"; same guard as cli.py's headless run)."""
    if event.kind not in TEMPLATES:
        return None
    return {
        "id": event.id,
        "tick": event.tick,
        "kind": event.kind,
        "severity": event.severity,
        "country": event.country,
        "country2": event.country2,
        "parentId": event.parent_id,
        "parentIds": list(event.parent_ids),
        "causes": [cause_to_json(link) for link in event.causal_links()],
        "depth": event.depth,
        "intervention": event.is_intervention,
        "headline": render(event, world),
        "payload": dict(event.payload),
        "effects": [effect_to_json(effect) for effect in event.effects],
        "ledger": [
            {
                "from": entry.src_pool,
                "to": entry.dst_pool,
                "amount": entry.amount,
                "currency": entry.currency,
            }
            for entry in event.ledger
        ],
    }


def renderable_events(events: list[Event], world: World) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        json_event = event_to_json(event, world)
        if json_event is not None:
            out.append(json_event)
    return out


def recent_events(world: World, limit: int = RECENT_EVENTS_LIMIT) -> list[dict[str, Any]]:
    templated = world.log.recent_events_of_kinds(tuple(TEMPLATES), limit=limit)
    return renderable_events(list(templated), world)


def country_identity(world: World, country: Country) -> dict[str, Any]:
    """The naming/identity half of a country -- everything a client needs to render a code
    it has never seen before. Shared by `hello.countries`, the `roster` every world
    projection carries, and `countryAdded`."""
    return {
        "code": country.code,
        "name": country.name,
        "currency": {"name": country.currency_name, "symbol": country.currency_symbol},
        "leader": leader_info(country),
        "population": country.population,
        "parent": country.parent_code,
        "bornAt": country.born_at_tick,
        "ordinal": next(
            (index for index, c in enumerate(world.countries) if c.code == country.code), 0
        ),
    }


def roster(world: World) -> list[dict[str, Any]]:
    """Identity for every country currently in this world's stats, in genesis order
    (secessions append). A client that reconnects into a fork -- or scrubs forward across a
    secession -- learns the new nation's name from here instead of seeing a bare code."""
    return [country_identity(world, c) for c in active_countries(world)]


def hello_message(session: "Session") -> dict[str, Any]:
    """contract §3 `hello`. `countries` is prime's active set; `roster` lists every country
    prime has ever had, annexed and dissolved included, with its status and a stable
    `ordinal` (index in genesis order, secessions appended), so a client that connects late
    can still name and colour a nation it never saw alive."""
    world = session.multiverse.prime.world
    countries = [
        {
            "code": c.code,
            "name": c.name,
            "currency": {"name": c.currency_name, "symbol": c.currency_symbol},
            "leader": leader_info(c),
            "population": c.population,
            "ordinal": index,
        }
        for index, c in enumerate(world.countries)
        if c.status not in (CountryStatus.ANNEXED, CountryStatus.DISSOLVED)
    ]
    full_roster = [
        {
            "code": c.code,
            "name": c.name,
            "currency": {"name": c.currency_name, "symbol": c.currency_symbol},
            "status": c.status.value,
            "ordinal": index,
            "parent": c.parent_code,
            "bornAt": c.born_at_tick,
        }
        for index, c in enumerate(world.countries)
    ]
    interventions = [
        {"kind": kind, **meta_for(kind)}
        for kind, spec in sorted(EVENT_REGISTRY.items())
        if spec.is_intervention
    ]
    return {
        "type": "hello",
        "protocol": PROTOCOL_VERSION,
        "seed": session.seed,
        "tps": session.tps,
        "countries": countries,
        "roster": full_roster,
        "interventions": interventions,
        "settings": settings_dict(session.multiverse.prime.world.settings),
        "worldObjects": project_world_objects(world),
    }


def status_message(session: "Session") -> dict[str, Any]:
    forks = [
        {
            "id": fork_id,
            "tick": timeline.world.tick,
            "forkTick": session.fork_meta[fork_id].fork_tick,
            "label": session.fork_meta[fork_id].label,
        }
        for fork_id, timeline in sorted(session.multiverse.forks.items())
    ]
    fork_status = None
    focused = session.focused_fork()
    if focused is not None and session.focused_fork_id is not None:
        fork_status = {
            "tick": focused.world.tick,
            "label": session.fork_meta[session.focused_fork_id].label,
        }
    return {
        "type": "status",
        "running": session.running,
        "tps": session.tps,
        "fork": fork_status,
        "focus": session.focused_fork_id,
        "forks": forks,
    }


SPARK_WINDOW = 90  # contract §3: snapshot.spark is the last ~90 ticks
SERIES_MAX_POINTS = 240  # contract §6: countryDetail.series downsampled to <= 240 points


def build_spark(
    stat_history: Mapping[int, dict[str, dict[str, float]]],
    up_to_tick: int,
    window: int = SPARK_WINDOW,
) -> dict[str, dict[str, list[float]]]:
    """contract §3 `snapshot.spark`: per country, the last `window` ticks' stability/
    inflation/fx, sliced to ticks <= up_to_tick (so a scrubbed snapshot shows history up
    to the scrub point). Sourced from Timeline.stat_history (engine/timeline.py)."""
    ticks = sorted(t for t in stat_history if t <= up_to_tick)[-window:]
    spark: dict[str, dict[str, list[float]]] = {}
    if not ticks:
        return spark
    for code in stat_history[ticks[-1]]:  # countries present at the most recent tick
        present = [t for t in ticks if code in stat_history[t]]
        spark[code] = {
            "stability": [stat_history[t][code]["stability"] for t in present],
            "inflation": [stat_history[t][code]["inflation"] for t in present],
            "fx": [stat_history[t][code]["fx"] for t in present],
        }
    return spark


def _downsample(ticks: list[int], max_points: int) -> list[int]:
    if len(ticks) <= max_points:
        return ticks
    stride = (len(ticks) + max_points - 1) // max_points
    sampled = ticks[::stride]
    if sampled[-1] != ticks[-1]:
        sampled.append(ticks[-1])  # always keep the most recent point
    return sampled


def build_series(
    stat_history: Mapping[int, dict[str, dict[str, float]]],
    up_to_tick: int,
    code: str,
    max_points: int = SERIES_MAX_POINTS,
) -> tuple[list[int], dict[str, list[float]]]:
    """contract §6 `countryDetail.ticks`/`series`: full history for one country of all
    five charted stats, downsampled to <= max_points, sliced to ticks <= up_to_tick."""
    ticks = _downsample(
        sorted(t for t in stat_history if t <= up_to_tick and code in stat_history[t]), max_points
    )
    series = {
        stat: [stat_history[t][code][stat] for t in ticks]
        for stat in ("stability", "inflation", "gdp", "fx", "treasury")
    }
    return ticks, series


def snapshot_message(
    session: "Session", world: World, *, live: bool, scrubbed: bool
) -> dict[str, Any]:
    """contract §3 `snapshot`. `spark` (last-90-tick sparklines) is sourced from the prime
    timeline's `stat_history` buffer (engine/timeline.py), sliced to ticks <= world.tick so
    a scrubbed snapshot shows history up to the scrub point (M8.2a).

    `live` stays the boolean "is this the live world"; `liveTick` says WHERE live is, so a
    client that reloads mid-scrub can draw the ribbon without waiting for a frame."""
    return {
        "type": "snapshot",
        "tick": world.tick,
        "live": live,
        "liveTick": session.multiverse.prime.world.tick,
        "scrubbed": scrubbed,
        "roster": roster(world),
        "stats": all_stats(world),
        "wars": war_pairs(world),
        "recentEvents": recent_events(world, SNAPSHOT_RECENT_LIMIT),
        "leaders": all_leaders(world),
        "spark": build_spark(session.multiverse.prime.stat_history, world.tick),
        "worldObjects": project_world_objects(world),
    }


def aligned_prime_world(session: "Session", tick: int) -> World:
    """Prime as it was at `tick` -- its live world when that IS the tick, else reconstructed
    history. Used for the A column of a fork frame, which is always aligned to B's tick."""
    prime = session.multiverse.prime
    if prime.world.tick == tick:
        return prime.world
    return prime.world_at(tick)


def prime_events_at(session: "Session", world: World, tick: int) -> list[dict[str, Any]]:
    """Prime's chronicle FOR ONE TICK, read from history rather than from whatever prime
    happens to be emitting live. While a past fork is focused prime is usually ahead of the
    frame's tick, so its live events belong to a moment the A column is not showing."""
    log = session.multiverse.prime.world.log
    return renderable_events(list(log.events_between(tick - 1, tick)), world)


def frame_message(
    session: "Session",
    new_events_a: list[Event],
    *,
    new_events_b: list[Event] | None = None,
) -> dict[str, Any]:
    """contract §3 `frame`. In fork mode `tick` is the focused fork's tick, `A` carries
    prime's state reconstructed AT THAT SAME TICK (aligned comparison, §5.3) together with
    prime's events from that tick's history, and `B` carries the fork's own new events.
    `liveTick` is always prime's live tick, which in fork mode may be ahead of `tick`."""
    prime_world = session.multiverse.prime.world
    focused = session.focused_fork()

    if focused is None:
        return {
            "type": "frame",
            "tick": prime_world.tick,
            "liveTick": prime_world.tick,
            "timelines": {
                "A": {
                    "stats": all_stats(prime_world),
                    "events": renderable_events(new_events_a, prime_world),
                    "worldObjects": project_world_objects(prime_world),
                }
            },
            "wars": war_pairs(prime_world),
            "leaders": all_leaders(prime_world),
        }

    tick = focused.world.tick
    aligned_a = aligned_prime_world(session, tick)
    return {
        "type": "frame",
        "tick": tick,
        "liveTick": prime_world.tick,
        "focus": session.focused_fork_id,
        "timelines": {
            "A": {
                "stats": all_stats(aligned_a),
                "events": prime_events_at(session, aligned_a, tick),
                "worldObjects": project_world_objects(aligned_a),
            },
            "B": {
                "stats": all_stats(focused.world),
                "events": renderable_events(new_events_b or [], focused.world),
                "worldObjects": project_world_objects(focused.world),
            },
        },
        "wars": war_pairs(aligned_a),
        "warsB": war_pairs(focused.world),
        "diff": diff_worlds(aligned_a, focused.world),
        "leaders": all_leaders(aligned_a),
        "leadersB": all_leaders(focused.world),
    }


def trace_message(world: World, event_id: int) -> dict[str, Any]:
    """contract §3 `trace`: `{selectedId, nodes:[{...,headline,intervention,d}]}`. Adds
    `headline` (via text.render) and renames engine snake_case -- engine/trace.py itself
    stays text-free (§4.1)."""
    nodes = []
    for node in trace_events(world.log, event_id):
        node_id = node["id"]
        if not isinstance(node_id, int):
            continue
        event = world.log[node_id]
        nodes.append(
            {
                "id": node_id,
                "tick": node["tick"],
                "kind": node["kind"],
                "severity": node["severity"],
                "country": node["country"],
                "headline": render(event, world) if event.kind in TEMPLATES else event.kind,
                "intervention": node["is_intervention"],
                "parentIds": node["parent_ids"],
                "causes": [cause_to_json(link) for link in event.causal_links()],
                "d": node["d"],
                "cascadeClipped": node["cascade_clipped"],
            }
        )
    return {"type": "trace", "selectedId": event_id, "nodes": nodes}


def fork_started_message(
    session: "Session", fork_id: str, world: World, shared_recent: list[Event]
) -> dict[str, Any]:
    return {
        "type": "forkStarted",
        "id": fork_id,
        "tick": world.tick,
        "liveTick": session.multiverse.prime.world.tick,
        "label": session.fork_meta[fork_id].label,
        "roster": roster(world),
        "sharedRecent": renderable_events(shared_recent[-FORK_SHARED_RECENT_LIMIT:], world),
        "worldObjects": project_world_objects(world),
    }


def _impact_event_summary(event: Event, world: World) -> dict[str, Any]:
    return {
        "id": event.id,
        "tick": event.tick,
        "kind": event.kind,
        "severity": event.severity,
        "country": event.country,
        "country2": event.country2,
        "headline": render(event, world) if event.kind in TEMPLATES else event.kind,
        "parentId": event.parent_id,
        "parentIds": list(event.parent_ids),
        "causes": [cause_to_json(link) for link in event.causal_links()],
        "depth": event.depth,
        "intervention": event.is_intervention,
        "payload": dict(event.payload),
        "effects": [effect_to_json(effect) for effect in event.effects],
        "ledger": [],
    }


def _impact_payload(impact: EventImpact, world: World) -> dict[str, Any]:
    event = impact.event
    direct_causes = []
    for link in event.causal_links():
        cause = world.log[link.event_id]
        direct_causes.append(
            {
                **cause_to_json(link),
                "kind": cause.kind,
                "tick": cause.tick,
                "headline": render(cause, world) if cause.kind in TEMPLATES else cause.kind,
            }
        )
    return {
        "event": _impact_event_summary(event, world),
        "causes": direct_causes,
        "ancestors": [_impact_event_summary(item, world) for item in impact.ancestors],
        "descendants": [_impact_event_summary(item, world) for item in impact.descendants],
        "immediateEffects": [effect_to_json(effect) for effect in impact.immediate_effects],
        "downstreamEffects": [
            {
                "eventId": record.event_id,
                "eventKind": record.event_kind,
                "effect": effect_to_json(record.effect),
            }
            for record in impact.downstream_effects
        ],
        "cumulativeTotals": [
            {
                "type": total.effect_type,
                "target": total.target,
                "metric": total.metric,
                "unit": total.unit,
                "delta": total.delta,
                "events": list(total.event_ids),
            }
            for total in impact.cumulative_totals
        ],
    }


def impact_message(
    world: World,
    event_id: int,
    *,
    timeline_id: str,
    horizon: int | None,
    horizon_diff: dict[str, Any],
) -> dict[str, Any]:
    impact = analyze_impact(world.log, event_id, horizon)
    return {
        "type": "eventImpact",
        "tl": timeline_id,
        "eventId": event_id,
        "horizon": horizon,
        **_impact_payload(impact, world),
        "horizonDiff": horizon_diff,
    }


def fork_dropped_message(fork_id: str, kept: str) -> dict[str, Any]:
    """contract §7 `forkDropped`. `id` is the discarded fork; `kept` is the timeline now in
    focus ("A" for prime, or a surviving sibling fork's id)."""
    return {"type": "forkDropped", "id": fork_id, "kept": kept}


# How far back `annalsImpact` ranks. Bounded because an unbounded pass grows with history
# and stalls the tick loop; 500 is well beyond the life of a cascade, so it rarely clips a
# live one. The reply carries this as `scope`, and the client is REQUIRED to display it --
# "the most consequential events" is only an honest claim about the window actually ranked,
# and becomes an overclaim the moment a UI presents a windowed ranking as the whole record.
ANNALS_IMPACT_WINDOW_TICKS = 500


def annals_impact_message(
    world: World,
    *,
    timeline_id: str,
    sort_by: str = "descendants",
    limit: int = 30,
    window_ticks: int = ANNALS_IMPACT_WINDOW_TICKS,
) -> dict[str, Any]:
    """Rank real events by explicit causal reach without combining unlike effect units.

    The ordering is lexicographic over recorded counts, not a fabricated weighted score.
    Descendants reached through multiple DAG paths are deduplicated by event id.

    The ranking pass streams one bounded tick window (`scope`) instead of the whole branch,
    and hydrates full event summaries only for the rows it actually returns. An unbounded
    pass over a long history hydrated every event twice and stalled the server's tick loop
    for tens of seconds around t1700; cascades are short-lived, so a window of
    ANNALS_IMPACT_WINDOW_TICKS keeps the answer honest while bounding the work. `scope` says
    exactly what was ranked, including whether it covered the whole timeline.
    """
    start_tick = max(-1, world.tick - window_ticks)
    # One streaming pass: keep only the few numbers the ranking needs per event, never the
    # hydrated Event (that is what made this O(history) in both time and cache churn).
    ticks: dict[int, int] = {}
    severities: dict[int, int] = {}
    effect_counts: dict[int, int] = {}
    codes: dict[int, tuple[str, ...]] = {}
    children: dict[int, list[int]] = {}
    for event in world.log.events_between(start_tick, world.tick):
        ticks[event.id] = event.tick
        severities[event.id] = event.severity
        effect_counts[event.id] = len(event.effects)
        codes[event.id] = tuple(
            code for code in (event.country, event.country2) if code is not None
        )
        for parent_id in event.parent_ids:
            children.setdefault(parent_id, []).append(event.id)
    for child_ids in children.values():
        child_ids.sort()

    rows: list[dict[str, Any]] = []
    for event_id in sorted(ticks):
        direct_ids = [child for child in children.get(event_id, []) if child in ticks]
        if not direct_ids:
            continue
        seen: set[int] = set()
        pending = [(child_id, 1) for child_id in reversed(direct_ids)]
        max_generation = 0
        while pending:
            current, generation = pending.pop()
            if current in seen or current not in ticks:
                continue
            seen.add(current)
            max_generation = max(max_generation, generation)
            pending.extend(
                (child_id, generation + 1) for child_id in reversed(children.get(current, []))
            )

        affected = {code for item in (event_id, *seen) for code in codes[item]}
        rows.append(
            {
                "id": event_id,
                "directChildren": len(direct_ids),
                "descendants": len(seen),
                "generations": max_generation,
                "affectedCountries": sorted(affected),
                "recordedEffects": effect_counts[event_id]
                + sum(effect_counts[item] for item in seen),
                "crisisDescendants": sum(1 for item in seen if severities[item] >= 2),
                "lastDescendantTick": max((ticks[item] for item in seen), default=ticks[event_id]),
                "severity": severities[event_id],
                "children": direct_ids[:6],
            }
        )

    sort_fields = {
        "descendants": ("descendants", "directChildren", "recordedEffects", "affectedCountries"),
        "children": ("directChildren", "descendants", "recordedEffects", "affectedCountries"),
        "effects": ("recordedEffects", "descendants", "directChildren", "affectedCountries"),
        "countries": ("affectedCountries", "descendants", "directChildren", "recordedEffects"),
    }
    normalized_sort = sort_by if sort_by in sort_fields else "descendants"

    def rank_key(row: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
        values = {
            "descendants": int(row["descendants"]),
            "directChildren": int(row["directChildren"]),
            "recordedEffects": int(row["recordedEffects"]),
            "affectedCountries": len(row["affectedCountries"]),
        }
        primary = sort_fields[normalized_sort]
        return (
            values[primary[0]],
            values[primary[1]],
            values[primary[2]],
            values[primary[3]],
            int(row["severity"]),
            -int(row["id"]),
        )

    rows.sort(key=rank_key, reverse=True)
    bounded_limit = max(1, min(int(limit), 100))
    leaders = [
        {
            "event": _impact_event_summary(world.log[row["id"]], world),
            "directChildren": row["directChildren"],
            "descendants": row["descendants"],
            "generations": row["generations"],
            "affectedCountries": row["affectedCountries"],
            "recordedEffects": row["recordedEffects"],
            "crisisDescendants": row["crisisDescendants"],
            "lastDescendantTick": row["lastDescendantTick"],
            "children": [
                _impact_event_summary(world.log[child_id], world) for child_id in row["children"]
            ],
        }
        for row in rows[:bounded_limit]
    ]
    return {
        "type": "annalsImpact",
        "tl": timeline_id,
        "tick": world.tick,
        "sortBy": normalized_sort,
        "eventCount": len(world.log),
        "scope": {
            "startTick": max(0, start_tick + 1),
            "endTick": world.tick,
            "rankedEvents": len(ticks),
            "complete": start_tick < 0,
        },
        "leaders": leaders,
    }


def toast_message(text: str, tone: str = "info") -> dict[str, Any]:
    return {"type": "toast", "text": text, "tone": tone}


def settings_dict(settings: WorldSettings) -> dict[str, Any]:
    """Full WorldSettings as a JSON-able dict. Key list derived from `dataclasses.fields`
    so it always tracks WorldSettings itself, never hardcoded (§12.3.8). Shared by the
    `hello` payload (so the settings overlay can render current values on load, M8.2b) and
    `settingsAck`."""
    return {f.name: getattr(settings, f.name) for f in dataclasses.fields(settings)}


def settings_ack_message(settings: WorldSettings, restart_required: list[str]) -> dict[str, Any]:
    """contract §10 `settingsAck` (M6.4): full current settings + which of the applied
    keys need a restart to take effect."""
    return {
        "type": "settingsAck",
        "settings": settings_dict(settings),
        "restartRequired": restart_required,
    }


def timeline_focus_message(session: "Session", fork_id: str) -> dict[str, Any]:
    """contract §7 `timelineFocus` (reply to `focusTimeline` when the target is a fork,
    not prime)."""
    focused = session.multiverse.forks[fork_id]
    aligned_a = session.multiverse.prime.world_at(focused.world.tick)
    meta = session.fork_meta[fork_id]
    return {
        "type": "timelineFocus",
        "id": fork_id,
        "tick": focused.world.tick,
        "live": session.multiverse.prime.world.tick,
        "liveTick": session.multiverse.prime.world.tick,
        "forkTick": meta.fork_tick,
        "label": meta.label,
        "rosterA": roster(aligned_a),
        "rosterB": roster(focused.world),
        "statsA": all_stats(aligned_a),
        "statsB": all_stats(focused.world),
        "warsA": war_pairs(aligned_a),
        "warsB": war_pairs(focused.world),
        "leadersA": all_leaders(aligned_a),
        "leadersB": all_leaders(focused.world),
        "recentA": recent_events(aligned_a),
        "recentB": recent_events(focused.world),
        "worldObjectsA": project_world_objects(aligned_a),
        "worldObjectsB": project_world_objects(focused.world),
    }


def country_added_message(
    world: World, code: str, parent: str | None, *, timeline: str = "A"
) -> dict[str, Any]:
    """contract §6 `countryAdded`, sent the moment a country comes into existence (today
    only INTERVENE_SECEDE creates one) and always before the first world projection whose
    stats carry the new code. `tl` names the timeline it exists in: a god-mode secession
    lives in its fork until that fork is adopted."""
    country = world.country(code)
    return {
        "type": "countryAdded",
        "tl": timeline,
        "tick": world.tick,
        "parent": parent,
        "country": country_identity(world, country),
        "worldObject": project_world_objects(world)["countries"].get(code),
    }


def newborn_country_messages(world: World, timeline: str) -> list[dict[str, Any]]:
    """`countryAdded` for every country this world gained on its current tick."""
    return [
        country_added_message(world, c.code, c.parent_code, timeline=timeline)
        for c in active_countries(world)
        if c.born_at_tick == world.tick and world.tick > 0
    ]


def fork_adopted_message(session: "Session", fork_id: str) -> dict[str, Any]:
    """contract §7 `adoptFork`: the fork is now prime. Sent before the new prime snapshot so
    a client can retire its sibling timelines before it redraws the world."""
    return {
        "type": "forkAdopted",
        "id": fork_id,
        "tick": session.multiverse.prime.world.tick,
    }


ANNALS_MAJOR_EVENT_LIMIT = 200


def _annals_event(event_summary: dict[str, Any], world: World) -> dict[str, Any]:
    event = world.log[int(event_summary["id"])]
    return {
        "id": event_summary["id"],
        "tick": event_summary["tick"],
        "kind": event_summary["kind"],
        "severity": event_summary["severity"],
        "country": event_summary["country"],
        "country2": event_summary["country2"],
        "intervention": event_summary["is_intervention"],
        "headline": render(event, world) if event.kind in TEMPLATES else event.kind,
    }


def annals_data_message(
    world: World, *, timeline_id: str, country: str | None
) -> dict[str, Any]:
    """The engine's real archive (engine/annals.py) in contract shape: structural eras, wars
    derived from WAR_DECLARED/PEACE, records that carry a comparable number, and the major
    events themselves with server-rendered headlines. Nothing here is invented -- there are
    no death tolls or war names because the engine does not record any.

    `majorEvents` is capped at the most recent ANNALS_MAJOR_EVENT_LIMIT; `majorEventCount`
    is the exact total so a client can say "showing the last 200 of 1,412"."""
    archive = engine_annals(world, country)
    major = archive["majorEvents"][-ANNALS_MAJOR_EVENT_LIMIT:]
    return {
        "type": "annalsData",
        "tl": timeline_id,
        "tick": world.tick,
        "country": country,
        "eras": [
            {
                "startTick": era["start_tick"],
                "endTick": era["end_tick"],
                "eventCount": era["event_count"],
                "dominantKind": era["dominant_kind"],
            }
            for era in archive["eras"]
        ],
        "wars": [
            {
                "aggressor": war["aggressor"],
                "defender": war["defender"],
                "startTick": war["start_tick"],
                "endTick": war["end_tick"],
                "ticks": war["toll"],
                "outcome": war["outcome"],
            }
            for war in archive["wars"]
        ],
        "records": _annals_records(archive["records"]),
        "majorEvents": [_annals_event(summary, world) for summary in major],
        "majorEventCount": len(archive["majorEvents"]),
    }


def _annals_records(records: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    worst_inflation = records.get("worst_inflation")
    if worst_inflation is not None:
        out["worstInflation"] = {
            "country": worst_inflation["country"],
            "tick": worst_inflation["tick"],
            "value": worst_inflation["value"],
        }
    longest_war = records.get("longest_war")
    if longest_war is not None:
        out["longestWar"] = {
            "aggressor": longest_war["aggressor"],
            "defender": longest_war["defender"],
            "startTick": longest_war["start_tick"],
            "endTick": longest_war["end_tick"],
            "ticks": longest_war["toll"],
        }
    most_coups = records.get("most_coups")
    if most_coups is not None:
        out["mostCoups"] = {"country": most_coups["country"], "count": most_coups["count"]}
    return out


def country_chart_events_message(
    world: World,
    code: str,
    start_tick: int,
    end_tick: int,
    *,
    timeline_id: str,
    limit: int = 6,
) -> dict[str, Any]:
    """Authoritative country-involving events for one sampled chart interval.

    The exact total is reported while only the strongest bounded summaries are hydrated
    and transferred. Visual proximity is context, never a claim that an event caused a
    metric movement.
    """
    if end_tick < start_tick:
        raise ValueError("chart event interval ends before it starts")
    events, total = world.log.country_events(code, start_tick, end_tick, limit=limit)
    return {
        "type": "countryChartEvents",
        "tl": timeline_id,
        "code": code,
        "startTick": start_tick,
        "endTick": end_tick,
        "total": total,
        "events": [_impact_event_summary(event, world) for event in events],
    }


def country_detail_message(
    world: World, code: str, stat_history: Mapping[int, dict[str, dict[str, float]]]
) -> dict[str, Any]:
    """contract §6 `countryDetail` with exact downsampled statistic ticks.

    Chart event context is fetched on demand per sampled interval so a dossier remains
    bounded regardless of timeline age. ``worldObject`` carries authoritative spatial,
    commodity, infrastructure, and bloc state.
    """
    ticks, series = build_series(stat_history, world.tick, code)
    country = world.country(code)
    relations = []
    for other in active_countries(world):
        if other.code == code:
            continue
        a_code, b_code = sorted((code, other.code))
        relations.append({"code": other.code, "value": world.relations.get((a_code, b_code), 0.0)})
    return {
        "type": "countryDetail",
        "code": code,
        "meta": {
            "name": country.name,
            "govtType": country.govt_type.value,
            "status": country.status.value,
        },
        "stats": country_stats(country),
        "commodities": commodities_block(country),
        "bloc": bloc_info(world, code),
        "leader": leader_info(country),
        "relations": relations,
        "ticks": ticks,
        "series": series,
        "chartEventMode": "interval",
        "chartEvents": [],
        "recentEvents": recent_events(world),
        "bornAt": country.born_at_tick,
        "worldObject": project_world_objects(world)["countries"].get(code),
    }
