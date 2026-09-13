"""Shared cascade mechanics for ExogenousSystem (slot 10) and ConsequenceSystem
(slot 11). PROPOSAL §6.4 (decay/delay/depth-cap), §4.3.7 (queue order), §4.7 (specs).

Both systems, once they decide an event fires, run the identical path: apply the
spec's stat_deltas + pool_transfers, append the Event carrying those exact deltas
(so Timeline.world_at replay reproduces state without re-running systems), then roll
each ConsequenceRule to schedule children. That common path lives here so there is
no per-kind special-casing (§4.7) and no duplicated logic between the two systems.

RNG DRAW ORDER -- pinned once, obeyed everywhere (guards the M3.3 golden master,
which the M3.1 statistical test cannot catch, §4.3.7):

  Exogenous roots (systems/exogenous.py), specs iterated sorted by kind string:
      1. roll(exogenous_base_p * drama_multiplier)          -- ALWAYS, one draw/spec/tick
      2. only if it hit: rng.choice(primary) over sorted eligible countries
      3. only if targets == 2: rng.choice(secondary) over sorted others

  Scheduling children (schedule_children below), rules in spec.consequences order:
      1. rng.roll(p_spawn)                                  -- spawn-roll FIRST
      2. only if it hit: resolve target (rng.choice ONLY when target == "random")
      3. only if a target exists: rng.randint(delay_min, delay_max)  -- delay LAST

  Structural effects (M3.2, engine/structural.py) run between event append and
  schedule_children -- see emit_event below. Handlers may consume RNG (e.g.
  systems/politics.py's LEADER_CHANGE handler draws a successor name + traits); that
  draw happens for EVERY fired event whose kind has a registered structural handler,
  unconditionally, before that event's own consequences are rolled.

Depth cap (§6.4.3): a fired event at depth == max_depth would spawn children at
max_depth + 1; those are dropped. We clip at the EVENT level -- when a fired event is
at the cap AND its spec actually has consequence rules, we skip the whole rule loop
(consuming no spawn RNG), set payload["cascade_clipped"] = True once, and bump the
per-timeline clip counter once. Leaf events at the cap are never marked (nothing to
clip). Mutating payload in place is safe: Timeline.world_at deep-copies events on
replay (timeline.py) precisely so this cannot leak into a snapshot/fork/scrub.
"""

from __future__ import annotations

from typing import Callable

from meddler.engine.events import CauseLink, Event, LedgerEntry, ScheduleEntry, StatDelta
from meddler.engine.ledger import apply_to_world, round_money
from meddler.engine.model import Country, World
from meddler.engine.registry import EVENT_REGISTRY, Condition, ConsequenceRule, EventSpec
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_country_stat, attrition_delta, apply_infra_condition
from meddler.engine import assets, structural

# Clamp ranges for stat_deltas. apply_country_stat's contract requires the RECORDED
# delta to be the post-clamp actual change (new - old), so replay reproduces clamped
# results without re-clamping. (lo, hi); None = unbounded on that side. Fields not
# listed (inflation, gdp_tick, exchange_rate, ...) are applied raw.
_STAT_CLAMP: dict[str, tuple[float | None, float | None]] = {
    "stability": (0.0, 100.0),
    "civil_rights": (0.0, 100.0),
    "press_freedom": (0.0, 100.0),
    "education": (0.0, 100.0),
    "health": (0.0, 100.0),
    "population": (0.0, None),
    "grain_stock": (0.0, None),
    "grain_output": (0.0, None),
    "innovation_mult": (0.0, None),
    "tax_rate": (0.0, None),
}


def _resolve_pool_name(name: str | None, primary: str, secondary: str | None) -> str | None:
    if name is None:
        return None
    out = name.replace("{primary}", primary)
    if secondary is not None:
        out = out.replace("{secondary}", secondary)
    return out


def _apply_spec_effects(
    world: World, spec: EventSpec, primary: str, secondary: str | None
) -> tuple[tuple[LedgerEntry, ...], tuple[StatDelta, ...]]:
    """Apply the spec's stat_deltas (to the primary country, clamped) and pool_transfers
    (templated to concrete pools) to world state, returning the recorded delta tuples to
    store on the Event. All mutation flows through the ledger/stat helpers, so the log and
    world state never diverge and world_at replay reproduces both (§4.4)."""
    stat_deltas: list[StatDelta] = []
    country = world.country(primary)
    for stat, raw in spec.stat_deltas.items():
        if stat == "infra_all":
            # M7.1/M7.2: §6.7.2's "(condition delta on all asset classes)" -- the ONLY
            # infra stat_deltas key this milestone wires (a class-specific "infra:<name>"
            # key would need per-firing class selection, e.g. via payload, which no
            # registered kind needs yet -- deferred, not hacked in). Routes through
            # apply_infra_condition (not a direct Country attribute) for every class in
            # engine/assets.py's canonical list, clamped [0, 1] same as apply_country_stat.
            for asset_class in assets.all_asset_classes():
                asset = getattr(country.infrastructure, asset_class)
                old = asset.condition
                new = max(0.0, min(1.0, old + raw))
                apply_infra_condition(world, stat_deltas, primary, asset_class, new - old)
            continue
        if not hasattr(country, stat):
            # Multiplier-style keys like "gdp_mult" (§4.7 example) have no direct Country
            # field; they need a dedicated apply path that M3.1's core subset does not use.
            # Skipped rather than silently mis-applied. See docs/design-decisions.md.
            continue
        lo, hi = _STAT_CLAMP.get(stat, (None, None))
        old = float(getattr(country, stat))
        # Population losses scale with whoever is left (see config's own note): a flat
        # subtraction floored at zero ARRIVES there, and a country with no people still gets
        # simulated. A gain is unaffected -- only the loss is capped.
        new = old + (attrition_delta(old, -raw) if stat == "population" and raw < 0 else raw)
        if lo is not None:
            new = max(lo, new)
        if hi is not None:
            new = min(hi, new)
        apply_country_stat(world, stat_deltas, primary, stat, new - old)

    ledger: list[LedgerEntry] = []
    for pt in spec.pool_transfers:
        entry = LedgerEntry(
            kind=pt.kind,
            src_pool=_resolve_pool_name(pt.src_pool, primary, secondary),
            dst_pool=_resolve_pool_name(pt.dst_pool, primary, secondary),
            amount=round_money(
                pt.amount if pt.gdp_ticks is None else country.gdp_tick * pt.gdp_ticks
            ),
            currency=_resolve_pool_name(pt.currency, primary, secondary) or primary,
        )
        ledger.append(entry)
        apply_to_world(world, entry)

    return tuple(ledger), tuple(stat_deltas)


def _stat_value(country: Country, stat: str) -> float | None:
    if stat == "treasury":
        return float(country.pools.get("treasury", 0))
    if hasattr(country, stat):
        return float(getattr(country, stat))
    return None  # unsupported stat (e.g. "asset.satellites.count") -- not used by core subset


_OPS: dict[str, Callable[[float, float], bool]] = {
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _eval_one(world: World, cond: Condition, primary: str, secondary: str | None) -> bool:
    op = _OPS[cond.op]

    def holds(code: str) -> bool:
        val = _stat_value(world.country(code), cond.stat)
        return False if val is None else op(val, cond.value)

    if cond.target == "primary":
        return holds(primary)
    if cond.target == "secondary":
        return False if secondary is None else holds(secondary)
    codes = [c.code for c in world.living_countries()]
    if cond.target == "any":
        return any(holds(c) for c in codes)
    return all(holds(c) for c in codes)  # "all"


def eval_conditions(
    world: World, conditions: list[Condition], primary: str, secondary: str | None
) -> bool:
    """True iff every condition holds. Evaluated against current world state at both
    schedule time (exogenous eligibility) and fire time (consequences), §4.7."""
    return all(_eval_one(world, c, primary, secondary) for c in conditions)


def _resolve_target(
    world: World, rng: Rng, rule: ConsequenceRule, fired: Event
) -> tuple[str, str | None] | None:
    """Resolve a ConsequenceRule.target to (child_primary, child_secondary) deterministically,
    or None if no eligible country exists. rng.choice is consumed ONLY for target=='random'.

    M7.1 generalization: every branch below originally hardcoded child_secondary=None,
    which was correct as long as every reachable child was a 1-target kind (true through
    M6). M7.1 registered several 2-target children reachable via these same rules
    (WAR_SPARK->WAR_DECLARED via worst_relation; CEASEFIRE->WAR_DECLARED via foe;
    ALLIANCE/ALLIANCE_BROKEN/ARMS_DEAL/NUCLEAR_TEST->RELATION_SHIFT, itself targets=2),
    so child_secondary is now filled in whenever EVENT_REGISTRY[rule.child_kind].targets
    == 2, pairing the resolved target with `primary` (fired.country) as the second side --
    the natural "who else is involved" partner in every one of these cases. 1-target
    children are completely unaffected (child_secondary stays None for them, exactly as
    before)."""
    primary = fired.country
    if primary is None:
        return None
    child_targets_two = EVENT_REGISTRY[rule.child_kind].targets == 2

    if rule.target == "same":
        return primary, (fired.country2 if child_targets_two else None)
    if rule.target == "foe":
        # The other side of a two-target parent (e.g. WAR_DECLARED) is the foe; else the
        # lexically-first at-war partner. Deterministic, no RNG.
        foe: str | None
        if fired.country2 is not None:
            foe = fired.country2
        else:
            foes = sorted(world.country(primary).at_war_with)
            foe = foes[0] if foes else None
        if foe is None:
            return None
        return foe, (primary if child_targets_two else None)
    if rule.target == "ally":
        allies = sorted(
            c.code
            for c in world.living_countries()
            if c.code != primary
            and world.relations.get((min(primary, c.code), max(primary, c.code)), 0.0) > 0
        )
        if not allies:
            return None
        return allies[0], (primary if child_targets_two else None)
    if rule.target == "random":
        others = [c.code for c in world.living_countries() if c.code != primary]
        if not others:
            return None
        return rng.choice(others), (primary if child_targets_two else None)
    if rule.target == "worst_relation":
        # WAR_SPARK -> WAR_DECLARED (§6.6.4): "against the worst-relation eligible
        # pair." Deterministic -- sorts by (relation value, code) so ties break on the
        # lexically-first code, never on RNG or dict iteration order. Unlike the other
        # branches, `primary` (fired.country, the spark's own country) stays the child's
        # PRIMARY (the aggressor) -- only the resolved worst-relation partner is new.
        candidates = sorted(
            (
                (world.relations.get((min(primary, c.code), max(primary, c.code)), 0.0), c.code)
                for c in world.living_countries()
                if c.code != primary
            )
        )
        if not candidates:
            return None
        return primary, candidates[0][1]
    # "all_at_war": no registered kind fans out to every belligerent (checked at M7.1);
    # degrade to the primary (+ parent's own secondary, if the child needs one) so
    # validate_all's closure stays honest if one ever does.
    return primary, (fired.country2 if child_targets_two else None)


def schedule_children(world: World, rng: Rng, fired: Event) -> None:
    """Roll each of the fired event's consequence rules and enqueue the winners (§6.4).
    Honors the pinned RNG order and the event-level depth cap. See module docstring."""
    spec = EVENT_REGISTRY[fired.kind]
    if not spec.consequences:
        return
    max_depth = world.settings.max_depth
    if fired.depth >= max_depth:
        # Children would land at fired.depth + 1 > max_depth: clip the whole cascade here.
        fired.payload["cascade_clipped"] = True
        world.clip_count += 1
        return

    decay = world.settings.cascade_decay
    for rule in spec.consequences:
        p_spawn = rule.base_p * (decay**fired.depth)
        if not rng.roll(p_spawn):  # (1) spawn-roll first
            continue
        # Rule-level conditions are checked here at SCHEDULE time (they gate whether the
        # child is enqueued at all); the child SPEC's own conditions are re-checked at
        # FIRE time in consequence.run (world state may drift during the delay, §4.7).
        # This split avoids threading Condition objects through ScheduleEntry, which lives
        # in events.py and cannot import registry without a cycle. Fire-time gates for the
        # core subset (e.g. SECESSION only below stability 20) live on the child's spec.
        if not eval_conditions(world, rule.conditions, fired.country or "", fired.country2):
            continue
        target = _resolve_target(world, rng, rule, fired)  # (2) target (rng only if random)
        if target is None:
            continue
        child_primary, child_secondary = target
        # Fire-time conditions are re-checked when the entry is popped (consequence.run),
        # not here -- world state may change during the delay (§4.7).
        delay = rng.randint(rule.delay_min, rule.delay_max)  # (3) delay last
        payload: dict[str, float | int | str] = dict(rule.payload_template)
        world.schedule.append(
            ScheduleEntry(
                fire_tick=world.tick + delay,
                schedule_seq=world.schedule_seq,
                child_kind=rule.child_kind,
                parent_id=fired.id,
                parent_depth=fired.depth,
                primary=child_primary,
                secondary=child_secondary,
                payload=payload,
            )
        )
        world.schedule_seq += 1


def emit_event(
    world: World,
    rng: Rng,
    *,
    kind: str,
    primary: str,
    secondary: str | None,
    parent_id: int | None,
    depth: int,
    is_intervention: bool,
    payload: dict[str, float | int | str],
    causes: tuple[CauseLink, ...] = (),
) -> Event:
    """Apply a spec's effects, append the Event, and schedule its consequences. The one
    execution path shared by exogenous roots and scheduled consequences (§4.7)."""
    spec = EVENT_REGISTRY[kind]
    ledger, stat_deltas = _apply_spec_effects(world, spec, primary, secondary)
    event = world.log.append(
        tick=world.tick,
        kind=kind,
        country=primary,
        country2=secondary,
        parent_id=parent_id,
        depth=depth,
        is_intervention=is_intervention,
        payload=payload,
        ledger=ledger,
        stat_deltas=stat_deltas,
        severity=spec.severity,
        causes=causes,
    )
    structural.run(world, rng, event)  # M3.2: leader replacement, occupation, etc.
    schedule_children(world, rng, event)
    return event
