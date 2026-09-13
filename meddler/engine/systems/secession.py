"""Secession: a country splits and a new one is born mid-history (INTERVENE_SECEDE).

This is the one structural effect that grows the roster. The live handler decides every
number once -- identity, capital, the share of people/output/stock/assets it takes, its
relations -- and records the complete result on the event payload. The replay handler
rebuilds exactly that result without RNG, so scrubbing, forking, and adoption see the same
country the live tick created.

Placement keeps the no-overlap invariant. A parent that holds conquered territory lets the
farthest of those lands go (a former nation reasserting itself); otherwise a new capital is
sampled near the parent's and must clear the current minimum separation from every capital
and territory marker. If there is no room, or the roster is at `max_countries`, or the
parent is too small or not ACTIVE, the secession fizzles: the event and its declarative
stability hit stand, but no country is created (`payload["seceded"] == 0`).

Money is ledger-recorded and conserved per currency. A new state's opening balances are
the parent's share, converted through the FX desks: parent pools -> `fx:<PARENT>` in the
parent's currency, `fx:<NEW>` -> new pools in the new currency (the desk issues the new
money against the old, so each currency's total including its desk is unchanged). The
amounts depend on the new country's code, which is only known once this handler has run,
so they ride one follow-up bookkeeping event, SECESSION_SETTLEMENT, appended here -- the
single place a structural handler appends an event (see engine/structural.py).

Organic SECESSION (kinds/politics.py) is still declarative; routing it through this
handler is a pacing decision (it would grow the roster in ordinary play), not a wiring one.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from math import floor
from typing import Any

from meddler.engine import assets, commodities, config, space, structural, worldgen
from meddler.engine.events import Event, LedgerEntry, record_structural_effect
from meddler.engine.ledger import Ledger, apply_to_world, round_money
from meddler.engine.model import (
    AssetClass,
    Country,
    CountryStatus,
    GovtType,
    InfrastructureBlock,
    Leader,
    Territory,
    World,
)
from meddler.engine.rng import Rng
from meddler.engine.space import Position

_POOLS = ("treasury", "households", "corporates")


def _country_to_json(country: Country) -> str:
    return json.dumps(asdict(country), sort_keys=True)


def _position(raw: dict[str, Any]) -> Position:
    return Position(lat=float(raw["lat"]), lon=float(raw["lon"]))


def _country_from_json(text: str) -> Country:
    raw = json.loads(text)
    infra = raw["infrastructure"]
    return Country(
        code=raw["code"],
        name=raw["name"],
        parent_code=raw["parent_code"],
        status=CountryStatus(raw["status"]),
        born_at_tick=int(raw["born_at_tick"]),
        position=_position(raw["position"]),
        region=raw["region"],
        endowments={k: float(v) for k, v in raw["endowments"].items()},
        territories=[
            Territory(position=_position(t["position"]), region=t["region"])
            for t in raw["territories"]
        ],
        population=float(raw["population"]),
        base_gdp=float(raw["base_gdp"]),
        gdp_tick=float(raw["gdp_tick"]),
        commodity_output={k: float(v) for k, v in raw["commodity_output"].items()},
        commodity_need={k: float(v) for k, v in raw["commodity_need"].items()},
        commodity_stock={k: float(v) for k, v in raw["commodity_stock"].items()},
        inflation=float(raw["inflation"]),
        tax_rate=float(raw["tax_rate"]),
        innovation_mult=float(raw["innovation_mult"]),
        exchange_rate=float(raw["exchange_rate"]),
        pools={k: int(v) for k, v in raw["pools"].items()},
        currency_name=raw["currency_name"],
        currency_symbol=raw["currency_symbol"],
        stability=float(raw["stability"]),
        base_stability=float(raw["base_stability"]),
        civil_rights=float(raw["civil_rights"]),
        press_freedom=float(raw["press_freedom"]),
        education=float(raw["education"]),
        health=float(raw["health"]),
        infrastructure=InfrastructureBlock(
            **{
                cls: AssetClass(
                    count=int(infra[cls]["count"]),
                    condition=float(infra[cls]["condition"]),
                    last_maintained_tick=int(infra[cls]["last_maintained_tick"]),
                )
                for cls in assets.all_asset_classes()
            }
        ),
        leader=Leader(
            name=raw["leader"]["name"],
            title=raw["leader"]["title"],
            traits=list(raw["leader"]["traits"]),
        ),
        govt_type=GovtType(raw["govt_type"]),
        election_due_tick=raw["election_due_tick"],
        at_war_with=list(raw["at_war_with"]),
        occupied_by=raw["occupied_by"],
        armed={k: bool(v) for k, v in raw["armed"].items()},
        occupation_start_tick=raw["occupation_start_tick"],
    )


def _live_count(world: World) -> int:
    return len(world.living_countries())


def _choose_homeland(world: World, rng: Rng, parent: Country) -> tuple[Territory, int] | None:
    """(territory, index in parent.territories or -1 for a new one), or None if no room."""
    held = [
        (i, t)
        for i, t in enumerate(parent.territories)
        if t.position != parent.position
    ]
    if held:
        index, territory = max(
            held,
            key=lambda item: (
                space.central_angle(item[1].position, parent.position),
                item[1].position.lat,
                item[1].position.lon,
            ),
        )
        return territory, index
    required = space.min_country_separation(
        world.settings.region_count, _live_count(world) + 1
    )
    occupied = [c.position for c in world.countries] + [
        t.position for c in world.countries for t in c.territories
    ]
    position = space.place_near(
        rng,
        parent.position,
        occupied,
        required,
        required * config.SECESSION_PLACEMENT_RADIUS_FACTOR,
    )
    if position is None:
        return None
    return Territory(position=position, region=parent.region), -1


def _fizzle(event: Event, reason: str) -> None:
    event.payload["seceded"] = 0
    event.payload["fizzle_reason"] = reason


def _secede(world: World, rng: Rng, event: Event) -> None:
    if event.country is None:
        return
    parent = world.country(event.country)
    if parent.status != CountryStatus.ACTIVE:
        _fizzle(event, "parent not active")
        return
    if parent.population < config.SECESSION_MIN_PARENT_POPULATION:
        _fizzle(event, "parent too small")
        return
    if len(world.countries) >= world.settings.max_countries:
        _fizzle(event, "roster full")
        return
    homeland = _choose_homeland(world, rng, parent)
    if homeland is None:
        _fizzle(event, "no room")
        return
    territory, territory_index = homeland

    share = config.SECESSION_SHARE
    name, code = worldgen.breakaway_identity(
        rng, parent, {c.name for c in world.countries}, {c.code for c in world.countries}
    )
    currency_name, currency_symbol = worldgen.pick_currency(rng, parent.currency_name)
    leader = worldgen.new_leader(rng, GovtType.DEMOCRACY)

    population = parent.population * share
    base_gdp = parent.base_gdp * share
    stability = config.SECESSION_START_STABILITY
    infrastructure = InfrastructureBlock(
        **{
            cls: AssetClass(
                count=floor(assets.get_asset(parent.infrastructure, cls).count * share),
                condition=assets.get_asset(parent.infrastructure, cls).condition,
                last_maintained_tick=world.tick,
            )
            for cls in assets.all_asset_classes()
        }
    )
    child = Country(
        code=code,
        name=name,
        parent_code=parent.code,
        status=CountryStatus.ACTIVE,
        born_at_tick=world.tick,
        position=territory.position,
        region=territory.region,
        endowments=dict(parent.endowments),
        territories=[territory],
        population=population,
        base_gdp=base_gdp,
        gdp_tick=base_gdp * (0.5 + stability / 200),
        commodity_output={n: parent.commodity_output[n] * share for n in commodities.ORDER},
        commodity_need={
            n: population * config.COMMODITY_NEED_PER_CAPITA[n] for n in commodities.ORDER
        },
        commodity_stock={n: parent.commodity_stock[n] * share for n in commodities.ORDER},
        inflation=parent.inflation,
        tax_rate=parent.tax_rate,
        innovation_mult=parent.innovation_mult,
        exchange_rate=parent.exchange_rate,
        pools={pool: 0 for pool in _POOLS},
        currency_name=currency_name,
        currency_symbol=currency_symbol,
        stability=stability,
        # A seceding province is the same people: it keeps its parent's temperament even
        # though it starts calmer than the split it was born from.
        base_stability=parent.base_stability,
        civil_rights=parent.civil_rights,
        press_freedom=parent.press_freedom,
        education=parent.education,
        health=parent.health,
        infrastructure=infrastructure,
        leader=leader,
        govt_type=GovtType.DEMOCRACY,
        election_due_tick=world.tick + config.ELECTION_INTERVAL_TICKS,
        at_war_with=[],
        occupied_by=None,
        armed={},
    )

    relations: dict[str, float] = {parent.code: config.SECESSION_PARENT_RELATION}
    for (a, b), value in sorted(world.relations.items()):
        if parent.code in (a, b):
            other = b if a == parent.code else a
            if other != code:
                relations[other] = value * config.SECESSION_INHERITED_RELATION_FACTOR

    record: dict[str, str | int] = {
        "child": _country_to_json(child),
        "territory_index": territory_index,
        "relations": json.dumps(relations, sort_keys=True),
    }
    _apply(world, record)
    event.payload["seceded"] = 1
    event.payload["new_country_code"] = code
    event.payload["new_country_name"] = name
    for key, recorded in record.items():
        event.payload[key] = recorded
    record_structural_effect(
        event, target=code, metric="country", before="absent", after="ACTIVE"
    )
    record_structural_effect(
        event,
        target=parent.code,
        metric="population",
        before=parent.population + population,
        after=parent.population,
        delta=-population,
    )
    _settle_opening_balances(world, event, parent, child, share)


def _apply(world: World, record: dict[str, str | int]) -> None:
    """Create the child and shrink the parent exactly as recorded. Shared by live and
    replay so the two can never disagree.

    The parent is shrunk RELATIVELY -- by what the child took -- rather than restored to a
    recorded absolute. The child's own numbers already pin the split exactly, so a
    `parent_before` snapshot on the payload was redundant, and relative subtraction is the
    safer of the two: an absolute would silently overwrite anything else the parent's state
    had picked up between the snapshot replay started from and this event.
    """
    child = _country_from_json(str(record["child"]))
    parent_code = child.parent_code
    assert parent_code is not None
    parent = world.country(parent_code)
    parent.population -= child.population
    parent.base_gdp -= child.base_gdp
    parent.gdp_tick *= 1.0 - config.SECESSION_SHARE
    for name in commodities.ORDER:
        parent.commodity_output[name] -= child.commodity_output[name]
        parent.commodity_stock[name] -= child.commodity_stock[name]
    for cls in assets.all_asset_classes():
        assets.get_asset(parent.infrastructure, cls).count -= assets.get_asset(
            child.infrastructure, cls
        ).count
    index = int(record["territory_index"])
    if index >= 0:
        parent.territories = [t for i, t in enumerate(parent.territories) if i != index]
    world.countries.append(child)
    for other, value in json.loads(str(record["relations"])).items():
        a, b = sorted((child.code, other))
        world.relations[(a, b)] = float(value)


def _settle_opening_balances(
    world: World, event: Event, parent: Country, child: Country, share: float
) -> None:
    ledger: list[LedgerEntry] = []
    for pool in _POOLS:
        amount = round_money(max(0, parent.pools.get(pool, 0)) * share)
        if amount <= 0:
            continue
        converted = round_money(amount / parent.exchange_rate * child.exchange_rate)
        Ledger.transfer(ledger, f"{parent.code}.{pool}", f"fx:{parent.code}", amount, parent.code)
        Ledger.transfer(ledger, f"fx:{child.code}", f"{child.code}.{pool}", converted, child.code)
    settlement = world.log.append(
        tick=world.tick,
        kind="SECESSION_SETTLEMENT",
        country=child.code,
        country2=parent.code,
        parent_id=event.id,
        depth=event.depth + 1,
        # The settlement is a CONSEQUENCE of the act, not a second act. Every scheduled
        # consequence is recorded with is_intervention=False (systems/consequence.py), and
        # inheriting the flag here would make one god-mode secession report itself twice in
        # the Acts of God explorer and put two markers on the ribbon at the same tick.
        # parent_id already ties it to its cause. Code review caught it.
        is_intervention=False,
        payload={"share": share},
        ledger=tuple(ledger),
    )
    for entry in settlement.ledger:
        apply_to_world(world, entry)


def _replay_secede(world: World, event: Event) -> None:
    if event.payload.get("seceded") != 1:
        return
    record: dict[str, str | int] = {}
    for key in ("child", "territory_index", "relations"):
        value = event.payload.get(key)
        if not isinstance(value, (str, int)):
            return
        record[key] = value
    _apply(world, record)


structural.register_structural("INTERVENE_SECEDE", _secede)
structural.register_replay("INTERVENE_SECEDE", _replay_secede)
