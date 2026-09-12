"""Threshold bridge: stat crossings emit crisis events with hysteresis. PROPOSAL §6.3.

No dedicated §6.1 system slot exists for this; it runs last (after RelationsSystem,
slot 8) since it reads every stat the earlier systems just updated this tick. Parent
attribution ("the causing system's event where one exists, else root") is implemented
for INFLATION_CRISIS (InflationSystem's same-tick event) and UNREST/CIVIL_WAR_RISK
(StabilitySystem's) -- FAMINE_WARNING/FAMINE/DEBT_CRISIS use root, since no single
system in M2 owns grain_stock or treasury exclusively enough to name one unambiguous
cause (grain_stock in particular isn't mutated by any M2 baseline system at all --
see trade.py).

Each threshold crossing fires via cascade.emit_event (not a bare world.log.append),
so the crisis kind's own EventSpec stat_deltas apply and its registered consequences
schedule normally -- e.g. FAMINE's REVOLUTION child, DEBT_CRISIS's MINT/CREDIT_FREEZE
children. Before M7.1 registered those consequence rules there was nothing downstream
to schedule, so the bare-append form was equivalent; M7.1's catalog expansion made it
a real gap (fixed here rather than in M7.1 itself since it only became observable via
M7.1's own catalog-coverage test, tests/unit/test_catalog_coverage.py).

Hysteresis: each threshold arms (fires once) on crossing INTO the trigger zone, then
requires recovering past a margin (a dead zone) before it can re-arm and fire again --
so the feed doesn't spam every tick a stat sits past a threshold. Recorded per
country in Country.armed (§5.1: "not serialised to save files; reconstructed on
load").
"""

from __future__ import annotations

from collections.abc import Callable

from meddler.engine import cascade, config
from meddler.engine.events import CauseLink, Event
from meddler.engine.model import World
from meddler.engine.rng import Rng

# Non-food shortage kinds (M10). food is deliberately absent: it already has
# FAMINE_WARNING/FAMINE above, and duplicating them would double-count the same shortage.
_SHORTAGE_KINDS = {
    "energy": "ENERGY_SHORTAGE",
    "raw_materials": "MATERIALS_SHORTAGE",
    "manufactured": "MANUFACTURING_SLUMP",
    "consumer": "CONSUMER_SHORTAGE",
    "high_tech": "TECH_STAGNATION",
}


def _find_same_tick_event(world: World, code: str, kind: str) -> Event | None:
    return world.log.latest_event(tick=world.tick, country=code, kind=kind)


def _recent_contributors(
    world: World,
    *,
    target: str,
    metrics: tuple[str, ...],
    direction: int,
    parent: Event | None,
    detail: str,
) -> Callable[[], tuple[CauseLink, ...]]:
    """Build a resolver for recent effects; query only if this threshold fires."""

    def resolve() -> tuple[CauseLink, ...]:
        excluded = frozenset({parent.id}) if parent is not None else frozenset()
        events = world.log.recent_effect_events(
            target=target,
            metrics=metrics,
            start_tick=world.tick - config.CAUSE_LOOKBACK_TICKS,
            direction=direction,
            limit=config.MAX_EVENT_CONTRIBUTORS,
            exclude_ids=excluded,
        )
        return tuple(CauseLink(event.id, "contributor", detail) for event in events)

    return resolve


def _check(
    world: World,
    rng: Rng,
    events: list[Event],
    code: str,
    armed: dict[str, bool],
    key: str,
    *,
    triggered: bool,
    recovered: bool,
    kind: str,
    parent: Event | None,
    payload: dict[str, float | int | str],
    causes: tuple[CauseLink, ...] | Callable[[], tuple[CauseLink, ...]] = (),
) -> None:
    if not armed.get(key, False) and triggered:
        resolved_causes = causes() if callable(causes) else causes
        event = cascade.emit_event(
            world,
            rng,
            kind=kind,
            primary=code,
            secondary=None,
            parent_id=parent.id if parent is not None else None,
            depth=parent.depth + 1 if parent is not None else 0,
            is_intervention=False,
            payload=payload,
            causes=resolved_causes,
        )
        events.append(event)
        armed[key] = True
    elif armed.get(key, False) and recovered:
        armed[key] = False


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in sorted(world.countries, key=lambda c: c.code):
        if not country.in_world:
            continue
        armed = country.armed

        inflation_event = _find_same_tick_event(world, country.code, "INFLATION_UPDATE")
        _check(
            world,
            rng,
            events,
            country.code,
            armed,
            "inflation_crisis",
            triggered=country.inflation > config.INFLATION_CRISIS_THRESHOLD,
            recovered=country.inflation
            < config.INFLATION_CRISIS_THRESHOLD - config.INFLATION_CRISIS_RECOVERY_MARGIN,
            kind="INFLATION_CRISIS",
            parent=inflation_event,
            payload={"inflation": country.inflation},
            causes=_recent_contributors(
                world,
                target=country.code,
                metrics=("inflation",),
                direction=1,
                parent=inflation_event,
                detail="raised inflation toward crisis",
            ),
        )

        stability_event = _find_same_tick_event(world, country.code, "STABILITY_UPDATE")
        _check(
            world,
            rng,
            events,
            country.code,
            armed,
            "unrest",
            triggered=country.stability < config.UNREST_THRESHOLD,
            recovered=country.stability > config.UNREST_THRESHOLD + config.UNREST_RECOVERY_MARGIN,
            kind="UNREST",
            parent=stability_event,
            payload={"stability": country.stability},
            causes=_recent_contributors(
                world,
                target=country.code,
                metrics=("stability",),
                direction=-1,
                parent=stability_event,
                detail="reduced stability toward crisis",
            ),
        )
        _check(
            world,
            rng,
            events,
            country.code,
            armed,
            "civil_war_risk",
            triggered=country.stability < config.CIVIL_WAR_RISK_THRESHOLD,
            recovered=country.stability
            > config.CIVIL_WAR_RISK_THRESHOLD + config.CIVIL_WAR_RISK_RECOVERY_MARGIN,
            kind="CIVIL_WAR_RISK",
            parent=stability_event,
            payload={"stability": country.stability},
            causes=_recent_contributors(
                world,
                target=country.code,
                metrics=("stability",),
                direction=-1,
                parent=stability_event,
                detail="reduced stability toward crisis",
            ),
        )

        _check(
            world,
            rng,
            events,
            country.code,
            armed,
            "famine_warning",
            triggered=country.grain_stock < country.grain_need * config.FAMINE_WARNING_DAYS,
            recovered=country.grain_stock
            > country.grain_need * config.FAMINE_WARNING_RECOVERY_DAYS,
            kind="FAMINE_WARNING",
            parent=None,
            payload={"grain_stock": country.grain_stock},
            causes=_recent_contributors(
                world,
                target=country.code,
                metrics=("grain_stock",),
                direction=-1,
                parent=None,
                detail="reduced food reserves",
            ),
        )
        _check(
            world,
            rng,
            events,
            country.code,
            armed,
            "famine",
            triggered=country.grain_stock <= 0,
            recovered=country.grain_stock > 0,
            kind="FAMINE",
            parent=None,
            payload={"grain_stock": country.grain_stock},
            causes=_recent_contributors(
                world,
                target=country.code,
                metrics=("grain_stock",),
                direction=-1,
                parent=None,
                detail="reduced food reserves",
            ),
        )

        for name, kind in _SHORTAGE_KINDS.items():
            need = country.commodity_need[name]
            _check(
                world,
                rng,
                events,
                country.code,
                armed,
                key=f"shortage_{name}",
                triggered=need > 0
                and country.commodity_stock[name] < need * config.COMMODITY_SHORTAGE_DAYS,
                recovered=need <= 0
                or country.commodity_stock[name] > need * config.COMMODITY_SHORTAGE_RECOVERY_DAYS,
                kind=kind,
                parent=None,
                payload={"stock": country.commodity_stock[name]},
                causes=_recent_contributors(
                    world,
                    target=country.code,
                    metrics=(f"commodity:stock:{name}", f"commodity:output:{name}"),
                    direction=-1,
                    parent=None,
                    detail=f"reduced {name} availability",
                ),
            )

        treasury = country.pools.get("treasury", 0)
        _check(
            world,
            rng,
            events,
            country.code,
            armed,
            "debt_crisis",
            triggered=treasury < 0,
            recovered=treasury > config.DEBT_CRISIS_RECOVERY_MARGIN,
            kind="DEBT_CRISIS",
            parent=None,
            payload={"treasury": treasury},
            causes=_recent_contributors(
                world,
                target=f"{country.code}.treasury",
                metrics=("balance",),
                direction=-1,
                parent=None,
                detail="drained the treasury",
            ),
        )
    return events
