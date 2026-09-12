"""Real tariff policy system.

Runs after diplomacy. Policy changes affect the next tick's trade because TradeSystem has
already matched this tick; this avoids hidden mid-match policy changes. Ordering is fixed:
repeals, retaliation, then new hostile tariffs, each over sorted keys/pairs.
"""

from __future__ import annotations

from meddler.engine import cascade, config, structural, tariffs
from meddler.engine.events import CauseLink, Event, record_structural_effect
from meddler.engine.model import CountryStatus, TariffPolicy, World
from meddler.engine.rng import Rng


def _relation(world: World, a: str, b: str) -> float:
    x, y = sorted((a, b))
    return world.relations.get((x, y), 0.0)


def _policy_from_event(event: Event) -> tuple[str, str, str, float] | None:
    if event.country is None or event.country2 is None:
        return None
    commodity = event.payload.get("commodity", "*")
    rate = event.payload.get("rate")
    if not isinstance(commodity, str) or not isinstance(rate, (int, float)):
        return None
    return event.country, event.country2, commodity, float(rate)


def _impose(world: World, rng: Rng, event: Event) -> None:
    parsed = _policy_from_event(event)
    if parsed is None:
        return
    importer, exporter, commodity, rate = parsed
    rate = max(0.0, min(1.0, rate))
    existing = tariffs.find_policy(world, importer, exporter, commodity)
    before = existing.rate if existing is not None else 0.0
    if existing is not None:
        world.tariffs.remove(existing)
    world.tariffs.append(TariffPolicy(importer, exporter, commodity, rate, world.tick, event.id))
    world.tariffs.sort(key=tariffs.policy_key)
    event.payload["rate_before"] = before
    event.payload["rate_after"] = rate
    record_structural_effect(
        event,
        target=importer,
        metric=f"tariff:{exporter}:{commodity}",
        before=before,
        after=rate,
        delta=rate - before,
        unit="rate",
    )


def _replay_impose(world: World, event: Event) -> None:
    parsed = _policy_from_event(event)
    if parsed is None:
        return
    importer, exporter, commodity, _rate = parsed
    rate_after = event.payload.get("rate_after")
    if not isinstance(rate_after, (int, float)):
        return
    existing = tariffs.find_policy(world, importer, exporter, commodity)
    if existing is not None:
        world.tariffs.remove(existing)
    world.tariffs.append(
        TariffPolicy(importer, exporter, commodity, float(rate_after), event.tick, event.id)
    )
    world.tariffs.sort(key=tariffs.policy_key)


def _repeal(world: World, rng: Rng, event: Event) -> None:
    if event.country is None or event.country2 is None:
        return
    commodity = event.payload.get("commodity", "*")
    if not isinstance(commodity, str):
        return
    existing = tariffs.find_policy(world, event.country, event.country2, commodity)
    if existing is None:
        return
    world.tariffs.remove(existing)
    event.payload["rate"] = existing.rate
    event.payload["rate_before"] = existing.rate
    event.payload["rate_after"] = 0.0
    record_structural_effect(
        event,
        target=event.country,
        metric=f"tariff:{event.country2}:{commodity}",
        before=existing.rate,
        after=0.0,
        delta=-existing.rate,
        unit="rate",
    )


def _replay_repeal(world: World, event: Event) -> None:
    if event.country is None or event.country2 is None:
        return
    commodity = event.payload.get("commodity", "*")
    if not isinstance(commodity, str):
        return
    existing = tariffs.find_policy(world, event.country, event.country2, commodity)
    if existing is not None:
        world.tariffs.remove(existing)


def _recent_relation_causes(world: World, a: str, b: str, limit: int = 3) -> tuple[CauseLink, ...]:
    key = f"{min(a, b)}:{max(a, b)}"
    events = world.log.recent_effect_events(
        target="relation",
        metrics=(key,),
        start_tick=None,
        direction=None,
        limit=limit,
    )
    return tuple(
        sorted(
            (CauseLink(event.id, "contributor", "relation deterioration") for event in events),
            key=lambda link: link.event_id,
        )
    )


def _rate_for_relation(relation: float) -> float:
    hostility = max(0.0, min(1.0, (-relation - 20.0) / 80.0))
    return config.TARIFF_MIN_RATE + (config.TARIFF_MAX_RATE - config.TARIFF_MIN_RATE) * hostility


def _repeals(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for policy in sorted(world.tariffs, key=tariffs.policy_key):
        allied = tariffs.same_bloc(world, policy.importer, policy.exporter)
        thawed = _relation(world, policy.importer, policy.exporter) >= config.TARIFF_REPEAL_RELATION
        if not (allied or thawed) or not rng.roll(config.TARIFF_REPEAL_BASE_P):
            continue
        events.append(
            cascade.emit_event(
                world,
                rng,
                kind="TARIFF_REPEALED",
                primary=policy.importer,
                secondary=policy.exporter,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={
                    "commodity": policy.commodity,
                    "reason": "alliance" if allied else "relations_thaw",
                },
                causes=(CauseLink(policy.source_event_id, "trigger", "repeals this policy"),),
            )
        )
    return events


def _retaliation(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for policy in sorted(world.tariffs, key=tariffs.policy_key):
        if policy not in world.tariffs:
            continue
        importer, exporter = policy.exporter, policy.importer
        if tariffs.same_bloc(world, importer, exporter):
            continue
        if tariffs.find_policy(world, importer, exporter, policy.commodity) is not None:
            continue
        if world.country(importer).status != CountryStatus.ACTIVE:
            continue
        if not rng.roll(config.TARIFF_RETALIATE_BASE_P):
            continue
        events.append(
            cascade.emit_event(
                world,
                rng,
                kind="TARIFF_IMPOSED",
                primary=importer,
                secondary=exporter,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={
                    "commodity": policy.commodity,
                    "rate": policy.rate,
                    "reason": "retaliation",
                },
                causes=(CauseLink(policy.source_event_id, "trigger", "retaliatory tariff"),),
            )
        )
    return events


def _new_tariffs(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    active = sorted(
        country.code for country in world.countries if country.status == CountryStatus.ACTIVE
    )
    for importer in active:
        for exporter in active:
            if importer == exporter or tariffs.same_bloc(world, importer, exporter):
                continue
            if tariffs.find_policy(world, importer, exporter, "*") is not None:
                continue
            relation = _relation(world, importer, exporter)
            if relation > config.TARIFF_RELATION_THRESHOLD:
                continue
            if not rng.roll(config.TARIFF_IMPOSE_BASE_P):
                continue
            events.append(
                cascade.emit_event(
                    world,
                    rng,
                    kind="TARIFF_IMPOSED",
                    primary=importer,
                    secondary=exporter,
                    parent_id=None,
                    depth=0,
                    is_intervention=False,
                    payload={
                        "commodity": "*",
                        "rate": _rate_for_relation(relation),
                        "reason": "protection",
                    },
                    causes=_recent_relation_causes(world, importer, exporter),
                )
            )
    return events


def run(world: World, rng: Rng) -> list[Event]:
    events = _repeals(world, rng)
    events += _retaliation(world, rng)
    events += _new_tariffs(world, rng)
    return events


structural.register_structural("TARIFF_IMPOSED", _impose)
structural.register_replay("TARIFF_IMPOSED", _replay_impose)
structural.register_structural("TARIFF_REPEALED", _repeal)
structural.register_replay("TARIFF_REPEALED", _replay_repeal)
