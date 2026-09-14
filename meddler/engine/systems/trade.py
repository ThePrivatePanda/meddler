"""TradeSystem: bilateral, spatial trade matching across all six commodities (M12/M13).

v2 spec §4. Per commodity, greedily match each importer's deficit to the available
surpluses -- allies/friends first (a discrete priority tier ahead of price, so a distant
ally beats a cheap stranger), then cheapest landed cost, ties by code; partial fills across
suppliers. Lanes blocked by war / embargo (World.embargoes) / declared enmity (relation <=
RELATION_TRADE_BLOCK_THRESHOLD). Consumes NO RNG.

M13 change: a matched fill no longer settles instantly. TradeSystem prices it and hands it
to `logistics.dispatch`, which turns it into an in-flight Shipment (goods + money leg-1 leave
now; goods + money leg-2 land at arrival). The stock BALANCE (output - need) is folded by
commodity_production; trade goods move as shipment deltas -- so there is no TRADE_SETTLE here
any more. Emergency relief: a famine-critical importer buying from a high-relation supplier
gets a subsidised (discounted) shipment, air-routed if the sea lane is a war zone (spec §5).

Only ~5-14% global slack exists per commodity (§14's AGGREGATE_BALANCE_MIN floor), so greedy
priority allocation genuinely leaves tail importers short, and a shipment's transit delay
means relief can arrive too late -- both keep shortages/famine reachable. Money is unbounded
this milestone (a poor country imports into debt, which is what DEBT_CRISIS models).
"""

from __future__ import annotations

from meddler.engine import assets, commodities, config, space, tariffs
from meddler.engine.events import Event
from meddler.engine.model import Country, CountryStatus, World
from meddler.engine.rng import Rng
from meddler.engine.systems import commodity_production, logistics


def _relation(world: World, a: str, b: str) -> float:
    x, y = sorted((a, b))
    return world.relations.get((x, y), 0.0)


def _same_bloc(world: World, a: str, b: str) -> bool:
    bloc = world.bloc_of(a)
    return bloc is not None and b in bloc.members


def _lane_blocked(world: World, imp: Country, exp: Country) -> bool:
    """A trade lane is closed by war, embargo, or declared enmity. Seeded rivalries sit
    above RELATION_TRADE_BLOCK_THRESHOLD so they trade (dearer); only relations driven down
    by WAR_DECLARED (-90) etc. fall through it."""
    if exp.code in imp.at_war_with or imp.code in exp.at_war_with:
        return True
    pair = (min(imp.code, exp.code), max(imp.code, exp.code))
    if pair in world.embargoes:
        return True
    return _relation(world, imp.code, exp.code) <= config.RELATION_TRADE_BLOCK_THRESHOLD


def _priority_tier(world: World, imp: str, exp: str) -> int:
    """0 = same bloc, 1 = friendly (positive relation), 2 = neutral. Sorted ascending so
    allies are drawn down before friends before neutrals (spec §4's prioritisation, which
    is independent of price -- a distant ally beats a cheap stranger)."""
    if _same_bloc(world, imp, exp):
        return 0
    if _relation(world, imp, exp) > 0:
        return 1
    return 2


def _price_veri(world: World, imp: Country, exp: Country, name: str, deficit: float) -> float:
    """v2 spec §4:
    base x (1 + distance_cost) x relation_mod x scarcity_mult x (1 + tariff), then an extra
    intra-bloc discount. `deficit` is the importer's FULL commodity deficit (not the per-fill
    qty) so the scarcity term is stable across partial fills from different suppliers.

    M14 adds the importer's two QUALITY assets to the same formula (v2 spec §6), both read
    off the IMPORTER because it is the party searching for a supplier and negotiating:
    - satellites: a discovery `spread` on every trade, plus a graded premium on a partner
      beyond `trade_reach_angle`. Beyond reach is dearer, never blocked -- and since
      candidates sort by price, that premium REORDERS suppliers toward near ones.
    - communications: how much of an earned DISCOUNT (relation_mod below 1, the intra-bloc
      discount) the country can actually realise.
    """
    base = config.COMMODITY_BASE_PRICE_VERI[name]
    angle = space.central_angle(imp.position, exp.position)
    distance_cost = config.TRADE_DISTANCE_COST_COEFF * angle
    rel = _relation(world, imp.code, exp.code)
    coordination = assets.comms_quality(imp)
    relation_mod = max(
        config.TRADE_RELATION_MOD_FLOOR,
        min(
            config.TRADE_RELATION_MOD_CAP,
            1.0 - config.TRADE_RELATION_PRICE_SENSITIVITY * rel / 100.0,
        ),
    )
    relation_mod = assets.realised_discount(relation_mod, coordination)
    need = imp.commodity_need[name]
    shortage_ratio = deficit / need if need > 0 else 0.0
    # float ** float is typed Any by typeshed (pow can return complex); cast keeps the
    # declared -> float return honest.
    scarcity_mult = float((1.0 + shortage_ratio) ** config.TRADE_SCARCITY_EXPONENT)
    intra_bloc = _same_bloc(world, imp.code, exp.code)
    tariff_rate = tariffs.effective_rate(world, imp.code, exp.code, name)
    price = base * (1.0 + distance_cost) * relation_mod * scarcity_mult * (1.0 + tariff_rate)
    if intra_bloc:
        price *= assets.realised_discount(1.0 - config.TRADE_BLOC_DISCOUNT, coordination)
    price *= assets.price_discovery_spread(imp)
    reach = assets.trade_reach_angle(imp)
    if reach > 0 and angle > reach:
        price *= 1.0 + config.TRADE_BEYOND_REACH_PREMIUM * (angle - reach) / reach
    return price


def _is_relief(world: World, imp: Country, exp: Country, name: str) -> bool:
    """Emergency relief (spec §5): the importer is famine-critical for this commodity AND the
    supplier is a friend/ally. Discounts the price and (in logistics) prefers air if the sea
    lane is a war zone -- 'a famine-struck friend gets relief by boat/airlift'."""
    critical = imp.commodity_stock[name] < imp.commodity_need[name] * config.RELIEF_CRITICAL_DAYS
    return critical and _relation(world, imp.code, exp.code) >= config.RELIEF_RELATION_MIN


def _remaining_capacity(
    budgets: dict[tuple[str, str], float], country: Country, carrier: str
) -> float:
    """This tick's unspent freight budget for one (country, carrier), seeded on first use
    from the fleet's effective capacity (M14, v2 spec §6).

    A PER-TICK LOCAL, deliberately not World state: nothing here survives the tick, so no
    structural/replay handler is needed and `world_at` reconstruction is unaffected.
    """
    key = (country.code, carrier)
    if key not in budgets:
        budgets[key] = assets.freight_capacity(country, carrier)
    return budgets[key]


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    # An OCCUPIED country still exists and still eats, so it may IMPORT (and receive relief).
    # It may not EXPORT: its `commodity_output` is gross of the occupation tribute that
    # commodity_production hands the occupier, so a surplus read here would sell goods the
    # occupier has already taken. Excluding occupied importers outright used to starve them
    # into a shortage whose stability penalty blocked both exits from occupation.
    participants = world.living_countries()
    by_code = {c.code: c for c in participants}
    # The deficit must be sized net of that tribute too. Sized from gross output, imports
    # covered need - output while the tribute still drained the stock every tick: on seed
    # 1337 TEF's food stock ran from 27 days to 0 in 140 ticks of occupation, and the
    # shortage then held its stability under the annexation gate for good.
    outgoing_tribute, _ = commodity_production.occupation_tribute(world)
    # (country, carrier) -> commodity units this country can still load OR land this tick.
    budgets: dict[tuple[str, str], float] = {}

    for name in commodities.ORDER:
        deficits: list[tuple[str, float]] = []  # (code, deficit qty > 0)
        surplus: dict[str, float] = {}  # code -> remaining exportable qty
        for c in participants:
            balance = c.commodity_output[name] - c.commodity_need[name]
            balance -= outgoing_tribute.get((c.code, name), 0.0)
            if balance < 0:
                deficits.append((c.code, -balance))
            elif balance > 0 and c.status == CountryStatus.ACTIVE:
                surplus[c.code] = balance
        if not deficits or not surplus:
            continue

        for imp_code, deficit in sorted(deficits):
            imp = by_code[imp_code]
            candidates: list[tuple[int, float, str]] = []
            for exp_code in sorted(surplus):
                if surplus[exp_code] <= 0:
                    continue
                exp = by_code[exp_code]
                if _lane_blocked(world, imp, exp):
                    continue
                tier = _priority_tier(world, imp_code, exp_code)
                price = _price_veri(world, imp, exp, name, deficit)
                candidates.append((tier, price, exp_code))
            candidates.sort()

            remaining = deficit
            for _tier, price, exp_code in candidates:
                if remaining <= 0:
                    break
                available = surplus[exp_code]
                if available <= 0:
                    continue
                exp = by_code[exp_code]
                relief = _is_relief(world, imp, exp, name)
                # Carrier is chosen ONCE per fill and handed to dispatch: choosing it twice
                # (here and inside dispatch) could disagree and meter the wrong fleet.
                carrier = logistics.select_carrier(world, exp, imp, name, relief)
                # M14: both endpoints must have fleet left -- goods need loading AND landing.
                # EMERGENCY RELIEF BYPASSES THE BUDGET. Metering is a commercial-throughput
                # limit; applying it to relief would hard-gate exactly the flow that exists to
                # prevent famine -- the same failure mode that made satellite reach a price
                # penalty rather than a lane gate (§14 leaves only 5-14% global slack, so
                # gating relief manufactures the famine it is meant to avert). Measured before
                # this exemption: metering cut relief VOLUME ~35% while leaving the relief
                # shipment COUNT unchanged, i.e. it shrank every emergency lift. Relief still
                # DEBITS the budget, so it crowds out commercial cargo rather than being free.
                # Both budgets are SEEDED either way (relief debits them below, so the keys
                # must exist); relief simply does not let them cap the quantity.
                exp_budget = _remaining_capacity(budgets, exp, carrier)
                imp_budget = _remaining_capacity(budgets, imp, carrier)
                if relief:
                    qty = min(remaining, available)
                else:
                    qty = min(remaining, available, exp_budget, imp_budget)
                if qty <= 0:
                    # This lane's fleet is spent for the tick. Another supplier may still
                    # route by a different carrier, so try the next candidate rather than
                    # abandoning the importer.
                    continue
                discount = 1.0 - config.RELIEF_PRICE_DISCOUNT if relief else 1.0
                unit_price = price * discount
                tariff_rate = tariffs.effective_rate(world, imp.code, exp.code, name)
                base_unit_price = unit_price / (1.0 + tariff_rate)
                events.append(
                    logistics.dispatch(
                        world,
                        rng,
                        importer=imp,
                        exporter=exp,
                        commodity=name,
                        qty=qty,
                        price_veri=unit_price,
                        base_price_veri=base_unit_price,
                        tariff_rate=tariff_rate,
                        relief=relief,
                        carrier=carrier,
                    )
                )
                surplus[exp_code] -= qty
                remaining -= qty
                budgets[(exp.code, carrier)] -= qty
                budgets[(imp.code, carrier)] -= qty
    return events
