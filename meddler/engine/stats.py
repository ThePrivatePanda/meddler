"""Non-money stat deltas: the Ledger pattern extended to everything a system mutates
that isn't a money pool (stability, inflation, gdp_tick, population, grain_*, infra
condition, World.relations, ...). See docs/design-decisions.md.

Every system that changes such a field -- whether triggered by a discrete event or by
continuous per-tick drift (§6.2/§6.7.2/§5.2) -- must route the change through
apply_country_stat/apply_relation_delta instead of mutating the field directly, so
Timeline.world_at's replay can reproduce it via replay_stat_delta without re-running
any system or consuming RNG (§4.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from meddler.engine import config
from meddler.engine.events import StatDelta

if TYPE_CHECKING:
    from meddler.engine.model import Country, World


def attrition_delta(
    population: float, requested_loss: float, max_fraction: float | None = None
) -> float:
    """The population a country actually loses when an event asks to take `requested_loss`.

    Negative, and never more than `POPULATION_ATTRITION_MAX_FRACTION` of whoever is left, so
    a population approaches zero without ever arriving there. Shared by the declarative path
    (cascade applies a spec's "population" stat_delta) and the structural one
    (systems/war.py's strike damage) so the two can never disagree about what a loss means.

    `max_fraction` overrides the cap for another stock-like quantity with the same failure
    mode (cascade uses it for crop shocks against food output).
    """
    if requested_loss <= 0.0 or population <= 0.0:
        return 0.0
    fraction = config.POPULATION_ATTRITION_MAX_FRACTION if max_fraction is None else max_fraction
    return -min(requested_loss, population * fraction)


def apply_country_stat(
    world: "World", entries: list[StatDelta], code: str, stat: str, delta: float
) -> None:
    """Record and apply one Country stat delta atomically. `delta` must already be the
    post-clamp actual change (new - old), not a raw pre-clamp formula output, so replay
    reproduces clamped results without needing to know the stat's clamp range."""
    country = world.country(code)
    before = float(getattr(country, stat))
    after = before + delta
    entries.append(StatDelta(target=code, stat=stat, delta=delta, before=before, after=after))
    setattr(country, stat, after)


def apply_relation_delta(
    world: "World", entries: list[StatDelta], code_a: str, code_b: str, delta: float
) -> None:
    """Record and apply one World.relations delta atomically (§5.2). Key is always the
    sorted pair, matching World.relations' own key convention."""
    a, b = sorted((code_a, code_b))
    before = world.relations.get((a, b), 0.0)
    after = before + delta
    entries.append(
        StatDelta(
            target="relation",
            stat=f"{a}:{b}",
            delta=delta,
            before=before,
            after=after,
        )
    )
    world.relations[(a, b)] = after


def apply_infra_condition(
    world: "World", entries: list[StatDelta], code: str, asset_class: str, delta: float
) -> None:
    """Record and apply one infrastructure asset class's condition delta (§6.7).
    Encoded as stat=f"infra:{asset_class}" (Country.infrastructure is nested, unlike
    apply_country_stat's direct-attribute fields) so replay_stat_delta can route it
    without a third StatDelta shape."""
    asset = getattr(world.country(code).infrastructure, asset_class)
    before = asset.condition
    after = before + delta
    entries.append(
        StatDelta(
            target=code,
            stat=f"infra:{asset_class}",
            delta=delta,
            before=before,
            after=after,
        )
    )
    asset.condition = after


def apply_commodity_stat(
    world: "World", entries: list[StatDelta], code: str, bucket: str, name: str, delta: float
) -> None:
    """Record + apply a change to one commodity of one country (M10).

    Mirrors apply_infra_condition: a dict-valued field is not a plain attribute, so it
    needs its own StatDelta stat-string convention plus a matching branch in
    replay_stat_delta. Stat string: "commodity:<bucket>:<name>", e.g.
    "commodity:stock:energy". bucket is one of output/need/stock.

    `delta` MUST be the post-clamp actual change (new - old), per this module's contract --
    replay re-adds it blindly and must never re-derive a clamp.
    """
    values = _commodity_bucket(world.country(code), bucket)
    before = values[name]
    after = before + delta
    entries.append(
        StatDelta(
            target=code,
            stat=f"commodity:{bucket}:{name}",
            delta=delta,
            before=before,
            after=after,
        )
    )
    values[name] = after


def _commodity_bucket(country: "Country", bucket: str) -> dict[str, float]:
    if bucket == "output":
        return country.commodity_output
    if bucket == "need":
        return country.commodity_need
    if bucket == "stock":
        return country.commodity_stock
    raise ValueError(f"unknown commodity bucket {bucket!r}")


def replay_stat_delta(world: "World", delta: StatDelta) -> None:
    """Apply one already-recorded StatDelta to World state. Used both by the system
    that creates it (via apply_country_stat/apply_relation_delta/apply_infra_condition
    above, so the log and world state never diverge) and by Timeline.world_at's replay
    (§4.4)."""
    if delta.target == "relation":
        a, b = delta.stat.split(":")
        key = (a, b)
        world.relations[key] = world.relations.get(key, 0.0) + delta.delta
    elif delta.stat.startswith("infra:"):
        asset_class = delta.stat.removeprefix("infra:")
        asset = getattr(world.country(delta.target).infrastructure, asset_class)
        asset.condition += delta.delta
    elif delta.stat.startswith("commodity:"):
        _, bucket, name = delta.stat.split(":", 2)
        _commodity_bucket(world.country(delta.target), bucket)[name] += delta.delta
    else:
        country = world.country(delta.target)
        setattr(country, delta.stat, getattr(country, delta.stat) + delta.delta)
