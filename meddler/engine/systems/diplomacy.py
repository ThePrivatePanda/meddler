"""DiplomacySystem (M11, v2 spec §7): structural blocs -- formation, mutual-defense
coalition wars, invisible relation propagation, and strain-driven breaking.

Runs after politics, before exogenous (tickloop.py). Draws RNG only in the fixed orders
below (sorted countries / blocs / members) so a fixed seed is byte-deterministic. Everything
it *does* is an event -- registered kinds (ALLIANCE/ALLIANCE_BROKEN/WAR_DECLARED) via
cascade.emit_event so their structural handlers + consequences run; the invisible
BLOC_COHESION propagation record via world.log.append with self-computed stat_deltas -- so
Timeline.world_at reconstructs bloc state and relations from snapshot + replayed events
without re-running this system.

rng.roll(p) is `random() < p` with NO clamp, so every probability passes through _clamp01
first (the strain factor can exceed 1.0 -> a bloc would otherwise break every tick).
"""

from __future__ import annotations

from meddler.engine import cascade, config, space
from meddler.engine.events import Event, StatDelta
from meddler.engine.model import CountryStatus, World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_relation_delta


def _clamp01(p: float) -> float:
    return max(0.0, min(1.0, p))


def _rel(world: World, a: str, b: str) -> float:
    x, y = sorted((a, b))
    return world.relations.get((x, y), 0.0)


def _active_codes(world: World) -> list[str]:
    return [c.code for c in world.living_countries() if c.status == CountryStatus.ACTIVE]


def _shared_rival(world: World, a: str, b: str, codes: list[str]) -> bool:
    thr = config.ALLIANCE_SHARED_RIVAL_RELATION
    return any(
        c not in (a, b) and _rel(world, a, c) < thr and _rel(world, b, c) < thr
        for c in codes
    )


def _bloc_strength(world: World, members: list[str]) -> float:
    pairs = [
        (members[i], members[j])
        for i in range(len(members))
        for j in range(i + 1, len(members))
    ]
    if not pairs:
        return 0.0
    avg = sum(_rel(world, a, b) for a, b in pairs) / len(pairs)
    return max(0.0, min(1.0, avg / 100.0))


def _active_war_pairs(world: World) -> int:
    seen: set[tuple[str, str]] = set()
    for c in world.living_countries():
        for foe in c.at_war_with:
            x, y = sorted((c.code, foe))
            seen.add((x, y))
    return len(seen)


def _formation(world: World, rng: Rng, codes: list[str]) -> list[Event]:
    events: list[Event] = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            ba, bb = world.bloc_of(a), world.bloc_of(b)
            if ba is not None and ba is bb:
                continue  # already allied
            if ba is not None and bb is not None:
                continue  # both in different blocs -> merge deferred
            if _rel(world, a, b) < config.ALLIANCE_FORMATION_RELATION_MIN:
                continue
            angle = space.central_angle(world.country(a).position, world.country(b).position)
            if angle > config.ALLIANCE_FORMATION_MAX_ANGLE:
                continue
            # shared rival is a DRIVER (probability bonus), not a hard gate: rivalries are
            # sparse, so requiring one made formation categorically unreachable (measured).
            p = config.ALLIANCE_FORMATION_BASE_P
            if _shared_rival(world, a, b, codes):
                p *= config.ALLIANCE_SHARED_RIVAL_BONUS
            if rng.roll(_clamp01(p)):
                events.append(
                    cascade.emit_event(
                        world, rng, kind="ALLIANCE", primary=a, secondary=b,
                        parent_id=None, depth=0, is_intervention=False, payload={},
                    )
                )
    return events


def _mutual_defense(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    war_pairs = _active_war_pairs(world)  # running count; incremented as allies join
    for bloc in sorted(world.blocs, key=lambda bl: bl.id):
        members = sorted(bloc.members)
        strength = _bloc_strength(world, members)
        for defender in members:
            dc = world.country(defender)
            if dc.status != CountryStatus.ACTIVE:
                continue
            for attacker in sorted(dc.at_war_with):
                if attacker in members:
                    continue  # intra-bloc war, not a defense trigger
                for ally in members:
                    if ally == defender:
                        continue
                    ac = world.country(ally)
                    if ac.status != CountryStatus.ACTIVE or attacker in ac.at_war_with:
                        continue
                    if war_pairs >= world.settings.max_wars_concurrent:
                        continue
                    angle = space.central_angle(ac.position, world.country(attacker).position)
                    p = _clamp01(
                        config.MUTUAL_DEFENSE_BASE_P
                        * strength
                        * max(0.0, _rel(world, ally, defender) / 100.0)
                        / (1.0 + config.MUTUAL_DEFENSE_DISTANCE_K * angle)
                    )
                    if rng.roll(p):
                        events.append(
                            cascade.emit_event(
                                world, rng, kind="WAR_DECLARED", primary=ally,
                                secondary=attacker, parent_id=None, depth=0,
                                is_intervention=False, payload={},
                            )
                        )
                        war_pairs += 1  # the ally->attacker pair is a new war front
    return events


def _propagation(world: World) -> list[Event]:
    """One invisible BLOC_COHESION event per bloc, carrying that bloc's relation deltas:
    intra-bloc pairs warm; each member cools toward anyone a bloc-mate is at war with."""
    events: list[Event] = []
    for bloc in sorted(world.blocs, key=lambda bl: bl.id):
        members = sorted(bloc.members)
        deltas: list[StatDelta] = []
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                apply_relation_delta(world, deltas, a, b, config.BLOC_INTRA_WARMING)
        enemies = sorted(
            {
                foe
                for m in members
                for foe in world.country(m).at_war_with
                if foe not in members
            }
        )
        for m in members:
            for e in enemies:
                apply_relation_delta(world, deltas, m, e, -config.BLOC_ENEMY_COOLING)
        if not deltas:
            continue
        events.append(
            world.log.append(
                tick=world.tick, kind="BLOC_COHESION", country=members[0], country2=None,
                parent_id=None, depth=0, is_intervention=False,
                payload={"bloc_id": bloc.id}, stat_deltas=tuple(deltas), severity=0,
            )
        )
    return events


def _strain(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for bloc in sorted(world.blocs, key=lambda bl: bl.id):
        members = sorted(bloc.members)
        for m in members:
            others = [o for o in members if o != m]
            if not others:
                continue
            avg = sum(_rel(world, m, o) for o in others) / len(others)
            factor = max(1.0, config.ALLIANCE_STRAIN_RELATION_REF / max(avg, 1.0))
            p = _clamp01(config.ALLIANCE_STRAIN_BASE_P * factor)
            if rng.roll(p):
                partner = others[-1]  # deterministic: highest-code bloc-mate
                events.append(
                    cascade.emit_event(
                        world, rng, kind="ALLIANCE_BROKEN", primary=m, secondary=partner,
                        parent_id=None, depth=0, is_intervention=False, payload={},
                    )
                )
                break  # at most one break per bloc per tick (membership just changed)
    return events


def run(world: World, rng: Rng) -> list[Event]:
    codes = _active_codes(world)
    events: list[Event] = []
    events += _formation(world, rng, codes)
    events += _mutual_defense(world, rng)
    events += _propagation(world)
    events += _strain(world, rng)
    return events
