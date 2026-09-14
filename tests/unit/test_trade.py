"""M12/M13 trade matching, pricing, and lane-blocking.

TradeSystem's job is matching + pricing + blocking; the money/goods SETTLEMENT (now via
in-flight shipments) is exercised in test_logistics.py. A matched fill surfaces here as a
SHIPMENT_DISPATCHED event carrying the price and quantity."""

import pytest

from meddler.engine import commodities, config, space
from meddler.engine.model import Bloc, CountryStatus, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import logistics, trade
from meddler.engine.worldgen import generate_world


def _pair(a: str, b: str) -> tuple[str, str]:
    return (min(a, b), max(a, b))


def _clean_world(n: int = 2, seed: int = 5):
    """Every relation/bloc/war/shipment cleared and every commodity balanced, so a test
    trades exactly the imbalance it sets and nothing else."""
    world = generate_world(seed, WorldSettings(starting_country_count=n))
    world.relations = {}
    world.blocs = []
    world.embargoes = []
    world.shipments = []
    world.shipment_seq = 0
    for c in world.countries:
        c.at_war_with = []
        for name in commodities.ORDER:
            c.commodity_output[name] = c.commodity_need[name]
            c.commodity_stock[name] = c.commodity_need[name] * 30.0
        # M14: ample freight capacity, so these tests exercise matching/pricing/settlement
        # rather than throughput metering. Metering has its own tests (test_trade.py's
        # "M14 throughput metering" section) which set capacity deliberately.
        for cls in ("naval_fleet", "rail_network", "air_fleet"):
            asset = getattr(c.infrastructure, cls)
            asset.count = 1000
            asset.condition = 1.0
    return world


def _sorted_countries(world):
    return sorted(world.countries, key=lambda c: c.code)


def _dispatches(events, commodity="food"):
    return [
        e for e in events if e.kind == "SHIPMENT_DISPATCHED" and e.payload["commodity"] == commodity
    ]


def test_matched_fill_dispatches_with_price_and_quantity():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 20.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 30.0
    fills = _dispatches(trade.run(world, Rng(1)))
    assert len(fills) == 1
    ev = fills[0]
    assert ev.country == exp.code and ev.country2 == imp.code  # origin, dest
    assert ev.payload["qty"] == pytest.approx(20.0)
    assert ev.payload["price_veri"] > 0


def test_war_blocks_the_lane():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 20.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 30.0
    imp.at_war_with = [exp.code]
    assert _dispatches(trade.run(world, Rng(1))) == []


def test_embargo_blocks_the_lane():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 20.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 30.0
    world.embargoes = [_pair(imp.code, exp.code)]
    assert _dispatches(trade.run(world, Rng(1))) == []


def test_declared_enemy_relation_blocks_the_lane():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 20.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 30.0
    world.relations[_pair(imp.code, exp.code)] = -60.0  # below RELATION_TRADE_BLOCK_THRESHOLD
    assert _dispatches(trade.run(world, Rng(1))) == []


def _food_price(relation: float | None, same_bloc: bool) -> float:
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    if relation is not None:
        world.relations[_pair(imp.code, exp.code)] = relation
    if same_bloc:
        world.blocs = [Bloc(id="B1", members=sorted([imp.code, exp.code]), formed_at_tick=0)]
    imp.commodity_output["food"] = imp.commodity_need["food"] - 20.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 30.0
    return _dispatches(trade.run(world, Rng(1)))[0].payload["price_veri"]


def test_friend_pays_less_than_neutral():
    assert _food_price(70.0, same_bloc=False) < _food_price(0.0, same_bloc=False)


def test_intra_bloc_is_cheaper_than_out_of_bloc_at_the_same_relation():
    # Same +60 relation both ways; the bloc adds the tariff waiver + extra discount.
    assert _food_price(60.0, same_bloc=True) < _food_price(60.0, same_bloc=False)


def test_partial_fill_across_multiple_suppliers():
    world = _clean_world(3, seed=8)
    a, b, c = _sorted_countries(world)
    a.commodity_output["food"] = a.commodity_need["food"] - 50.0  # deficit 50
    b.commodity_output["food"] = b.commodity_need["food"] + 30.0
    c.commodity_output["food"] = c.commodity_need["food"] + 40.0
    fills = _dispatches(trade.run(world, Rng(1)))
    assert all(e.country2 == a.code for e in fills)  # a is the importer/dest
    assert len(fills) == 2  # 50 cannot come from either 30 or 40 alone
    assert sum(e.payload["qty"] for e in fills) == pytest.approx(50.0)


def test_ally_supplier_preferred_over_a_neutral_one():
    world = _clean_world(3, seed=8)
    a, b, c = _sorted_countries(world)
    a.commodity_output["food"] = a.commodity_need["food"] - 20.0
    b.commodity_output["food"] = b.commodity_need["food"] + 20.0
    c.commodity_output["food"] = c.commodity_need["food"] + 20.0
    world.blocs = [Bloc(id="B1", members=sorted([a.code, b.code]), formed_at_tick=0)]
    fills = _dispatches(trade.run(world, Rng(1)))
    assert len(fills) == 1
    assert fills[0].country == b.code  # the ally supplier (origin), not neutral c


def test_balanced_world_produces_no_fills():
    world = _clean_world(2, seed=5)  # every commodity already balanced
    assert trade.run(world, Rng(1)) == []


def test_dispatch_events_are_root_ambient():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 5.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 5.0
    for event in trade.run(world, Rng(1)):
        assert event.parent_id is None
        assert event.depth == 0
        assert event.is_intervention is False


# --- M14 throughput metering (v2 spec §6) --------------------------------------------
#
# "effective throughput per carrier per country = capacity x fleet_condition -- caps qty
# dispatched per tick". M13 dispatched the full matched qty as one shipment; M14 meters it
# against BOTH endpoints' fleets, and the unlifted remainder simply re-matches next tick
# (deficits are recomputed from flow every tick, so no queue state is needed).


def _set_fleet(country, asset_class: str, count: int, condition: float = 1.0) -> None:
    asset = getattr(country.infrastructure, asset_class)
    asset.count = count
    asset.condition = condition


def _sea_pair(seed: int = 5):
    """A cross-region pair, so their lane routes by sea (naval_fleet-metered)."""
    world = _clean_world(2, seed=seed)
    imp, exp = _sorted_countries(world)
    assert logistics.select_carrier(world, exp, imp, "food", False) == "sea"
    return world, imp, exp


def test_fill_is_capped_by_the_exporters_fleet_capacity():
    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 2)  # 2 * 1.0 * CARRIER_CAPACITY_PER_UNIT["sea"]

    fills = _dispatches(trade.run(world, Rng(1)))

    cap = 2 * config.CARRIER_CAPACITY_PER_UNIT["sea"]
    assert sum(f.payload["qty"] for f in fills) == pytest.approx(cap)
    assert cap < 40.0  # the fill really was cut down, not coincidentally satisfied


def test_fill_is_capped_by_the_importers_fleet_capacity():
    """Both ends are charged: a lane needs a fleet to load AND one to land. Without this an
    importer's own NAVAL_LOSS would cost it nothing."""
    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 500)
    _set_fleet(imp, "naval_fleet", 3)

    fills = _dispatches(trade.run(world, Rng(1)))

    assert sum(f.payload["qty"] for f in fills) == pytest.approx(
        3 * config.CARRIER_CAPACITY_PER_UNIT["sea"]
    )


def test_degraded_condition_meters_throughput_proportionally():
    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 4, condition=1.0)
    _set_fleet(imp, "naval_fleet", 500)
    healthy = sum(f.payload["qty"] for f in _dispatches(trade.run(world, Rng(1))))

    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 4, condition=0.25)
    _set_fleet(imp, "naval_fleet", 500)
    degraded = sum(f.payload["qty"] for f in _dispatches(trade.run(world, Rng(1))))

    assert degraded == pytest.approx(0.25 * healthy)


def test_zero_condition_fleet_dispatches_at_residual_capacity():
    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 4, condition=0.0)

    fills = _dispatches(trade.run(world, Rng(1)))

    assert sum(fill.payload["qty"] for fill in fills) == pytest.approx(
        4
        * config.FREIGHT_CONDITION_FLOOR
        * config.CARRIER_CAPACITY_PER_UNIT["sea"]
    )


def test_zero_count_fleet_dispatches_nothing_by_that_carrier():
    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 0, condition=1.0)

    assert _dispatches(trade.run(world, Rng(1))) == []


def test_the_unlifted_remainder_re_matches_next_tick():
    """No queue state: the deficit is recomputed from flow each tick, so what the fleet
    could not lift this tick is simply still short next tick."""
    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 2)
    _set_fleet(imp, "naval_fleet", 500)

    first = _dispatches(trade.run(world, Rng(1)))
    second = _dispatches(trade.run(world, Rng(2)))

    cap = 2 * config.CARRIER_CAPACITY_PER_UNIT["sea"]
    assert sum(f.payload["qty"] for f in first) == pytest.approx(cap)
    assert sum(f.payload["qty"] for f in second) == pytest.approx(cap)


def test_capacity_is_a_per_tick_budget_shared_across_partners():
    """One exporter supplying two importers spends ONE fleet budget, not one per lane."""
    world = _clean_world(3, seed=5)
    a, b, c = _sorted_countries(world)
    exp = c
    for imp in (a, b):
        imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
        _set_fleet(imp, "naval_fleet", 500)
        _set_fleet(imp, "rail_network", 500)
        _set_fleet(imp, "air_fleet", 500)
    exp.commodity_output["food"] = exp.commodity_need["food"] + 80.0
    for cls in ("naval_fleet", "rail_network", "air_fleet"):
        _set_fleet(exp, cls, 2)

    fills = _dispatches(trade.run(world, Rng(1)))

    by_carrier: dict[str, float] = {}
    for f in fills:
        by_carrier[f.payload["carrier"]] = (
            by_carrier.get(f.payload["carrier"], 0.0) + f.payload["qty"]
        )
    for carrier, moved in by_carrier.items():
        assert moved <= 2 * config.CARRIER_CAPACITY_PER_UNIT[carrier] + 1e-9


def test_metering_consumes_no_rng():
    """Trade stayed RNG-free through M12/M13 and must stay so: a roll here would shift the
    whole downstream event stream."""
    world, imp, exp = _sea_pair()
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 2)
    rng = Rng(1)
    before = rng.get_state()
    trade.run(world, rng)
    assert rng.get_state() == before


# --- M14 satellites: trade reach & price discovery (v2 spec §6) ----------------------
#
# Reach is a PRICE penalty, not a lane gate (user decision 2026-07-23): with only 5-14%
# global slack per commodity, hard-gating distant lanes would manufacture famine. Since
# candidates sort by price, a shrunken reach still REORDERS who supplies whom.


def _set_quality(country, asset_class: str, condition: float) -> None:
    getattr(country.infrastructure, asset_class).condition = condition


def test_a_degraded_satellite_raises_the_price_of_a_distant_supplier():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 10.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 10.0

    _set_quality(imp, "satellites", 1.0)
    healthy = _dispatches(trade.run(world, Rng(1)))[0].payload["price_veri"]

    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 10.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 10.0
    _set_quality(imp, "satellites", 0.0)
    blind = _dispatches(trade.run(world, Rng(1)))[0].payload["price_veri"]

    assert blind > healthy


def test_out_of_reach_is_priced_but_never_blocked():
    """The famine-safety property of the price-only choice: a blind importer with exactly
    one distant supplier still gets fed, just dearly."""
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 10.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 10.0
    _set_quality(imp, "satellites", 0.0)

    fills = _dispatches(trade.run(world, Rng(1)))

    assert len(fills) == 1
    assert fills[0].payload["qty"] == pytest.approx(10.0)


def test_lost_reach_reorders_suppliers_toward_the_near_one():
    """A physical reallocation, not just a money effect: with reach intact the cheap distant
    supplier wins; once reach shrinks, the near one does.

    seed 21 is chosen for its geometry -- a near supplier at 1.12 rad and a far one at 2.23,
    far enough apart that a blinded importer's beyond-reach premium can actually overturn the
    friendship discount that wins the far supplier the lane at full reach.
    """
    world = _clean_world(3, seed=21)
    a, b, c = _sorted_countries(world)
    angles = sorted(
        (space.central_angle(a.position, other.position), other.code) for other in (b, c)
    )
    near_code, far_code = angles[0][1], angles[1][1]
    imp = a
    imp.commodity_output["food"] = imp.commodity_need["food"] - 10.0
    for other in (b, c):
        other.commodity_output["food"] = other.commodity_need["food"] + 100.0
    # Same priority tier for both (one bloc), so the choice is made on PRICE alone; the far
    # supplier is a much closer friend, which is what buys it the lane despite the distance
    # cost. Reach then has something real to overturn.
    world.blocs = [Bloc(id="BLOC1", members=[a.code, b.code, c.code], formed_at_tick=0)]
    world.relations[_pair(imp.code, far_code)] = 95.0
    world.relations[_pair(imp.code, near_code)] = 5.0

    _set_quality(imp, "satellites", 1.0)
    with_reach = _dispatches(trade.run(world, Rng(1)))[0].payload["origin"]
    _set_quality(imp, "satellites", 0.0)
    world.shipments = []
    without_reach = _dispatches(trade.run(world, Rng(1)))[0].payload["origin"]

    assert with_reach == far_code, "setup precondition: the distant supplier is preferred"
    assert without_reach == near_code


# --- M14 communications: coordination (v2 spec §6) -----------------------------------


def test_a_blacked_out_importer_realises_less_of_its_bloc_discount():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 10.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 10.0
    world.blocs = [Bloc(id="BLOC1", members=[imp.code, exp.code], formed_at_tick=0)]
    world.relations[_pair(imp.code, exp.code)] = 80.0

    _set_quality(imp, "communications", 1.0)
    coordinated = _dispatches(trade.run(world, Rng(1)))[0].payload["price_veri"]
    _set_quality(imp, "communications", 0.0)
    world.shipments = []
    blacked_out = _dispatches(trade.run(world, Rng(1)))[0].payload["price_veri"]

    assert blacked_out > coordinated


def test_comms_cannot_bargain_away_an_unfriendly_premium():
    """Coordination governs DISCOUNTS only. If poor comms also softened the premium a hostile
    pair pays, a blackout would be profitable."""
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 10.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 10.0
    world.relations[_pair(imp.code, exp.code)] = -40.0  # above the block threshold, dearer

    _set_quality(imp, "communications", 1.0)
    _set_quality(imp, "satellites", 1.0)
    coordinated = _dispatches(trade.run(world, Rng(1)))[0].payload["price_veri"]
    _set_quality(imp, "communications", 0.0)
    world.shipments = []
    blacked_out = _dispatches(trade.run(world, Rng(1)))[0].payload["price_veri"]

    assert blacked_out >= coordinated


def test_emergency_relief_bypasses_the_freight_budget():
    """Metering is a COMMERCIAL throughput limit. Applying it to relief would hard-gate the
    one flow that exists to prevent famine -- the same failure mode that made satellite reach
    a price penalty rather than a lane gate. Found by adversarial review: metering cut relief
    VOLUME ~35% while leaving the relief shipment COUNT unchanged (every lift was shrunk)."""
    world, imp, exp = _sea_pair()
    world.relations[_pair(imp.code, exp.code)] = 90.0  # a friend: relief-eligible
    imp.commodity_stock["food"] = 0.0  # famine-critical
    imp.commodity_output["food"] = imp.commodity_need["food"] - 40.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 40.0
    _set_fleet(exp, "naval_fleet", 1, condition=0.01)  # a budget far below the need
    _set_fleet(imp, "naval_fleet", 1, condition=0.01)

    fills = _dispatches(trade.run(world, Rng(1)))

    assert len(fills) == 1
    assert fills[0].payload["relief"] == 1
    assert fills[0].payload["qty"] == pytest.approx(40.0), "relief was metered by the fleet cap"


def test_relief_still_debits_the_budget_so_it_crowds_out_commercial_cargo():
    """Bypassing the cap must not make relief FREE -- an emergency lift consumes the fleet,
    leaving less for ordinary trade this tick."""
    world = _clean_world(3, seed=5)
    a, b, c = _sorted_countries(world)
    starving, buyer, seller = a, b, c
    world.relations[_pair(starving.code, seller.code)] = 90.0
    starving.commodity_stock["food"] = 0.0
    starving.commodity_output["food"] = starving.commodity_need["food"] - 30.0
    buyer.commodity_output["food"] = buyer.commodity_need["food"] - 30.0
    seller.commodity_output["food"] = seller.commodity_need["food"] + 60.0
    for cls in ("naval_fleet", "rail_network", "air_fleet"):
        _set_fleet(seller, cls, 1, condition=0.01)

    fills = _dispatches(trade.run(world, Rng(1)))

    by_dest = {f.payload["dest"]: f.payload["qty"] for f in fills}
    assert by_dest.get(starving.code) == pytest.approx(30.0)  # relief lifted in full
    # the commercial buyer is left with the exhausted (now negative) budget
    assert by_dest.get(buyer.code, 0.0) < 30.0


# --- Occupied countries: they still eat, but the occupier holds the exports -------------


def _occupy(occupied, occupier) -> None:
    occupied.status = CountryStatus.OCCUPIED
    occupied.occupied_by = occupier.code
    occupied.occupation_start_tick = 0


def test_an_occupied_country_imports_what_it_lacks():
    """Excluding occupied importers starved them into a shortage whose stability penalty
    blocked both annexation and liberation, so occupation never ended."""
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    _occupy(imp, exp)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 20.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 30.0
    fills = _dispatches(trade.run(world, Rng(1)))
    assert [(f.payload["origin"], f.payload["dest"]) for f in fills] == [(exp.code, imp.code)]
    assert fills[0].payload["qty"] == pytest.approx(20.0)


def test_a_famine_struck_occupied_country_gets_relief_from_a_friend():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    _occupy(imp, exp)
    imp.commodity_output["food"] = imp.commodity_need["food"] - 20.0
    exp.commodity_output["food"] = exp.commodity_need["food"] + 30.0
    imp.commodity_stock["food"] = 0.0  # famine-critical
    world.relations[_pair(imp.code, exp.code)] = 70.0
    fills = _dispatches(trade.run(world, Rng(1)))
    assert len(fills) == 1
    assert fills[0].payload["relief"] == 1


def test_an_occupied_country_does_not_export():
    """Its output is gross of the tribute the occupier already takes; selling the surplus
    would sell goods it no longer holds."""
    world = _clean_world(2, seed=5)
    occupied, occupier = _sorted_countries(world)
    _occupy(occupied, occupier)
    occupier.commodity_output["food"] = occupier.commodity_need["food"] - 20.0
    occupied.commodity_output["food"] = occupied.commodity_need["food"] + 30.0
    assert _dispatches(trade.run(world, Rng(1))) == []
