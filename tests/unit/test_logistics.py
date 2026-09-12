"""M13 logistics: shipment dispatch/arrival lifecycle, carrier selection, transit,
interdiction, relief, and world_at replay of in-flight shipments."""

import pytest

from meddler.engine import commodities, config
from meddler.engine.model import Position, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import logistics, trade
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _clean_world(n: int = 2, seed: int = 5):
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


def _set_food_gap(imp, exp, deficit=20.0, surplus=30.0):
    imp.commodity_output["food"] = imp.commodity_need["food"] - deficit
    exp.commodity_output["food"] = exp.commodity_need["food"] + surplus


def test_dispatch_removes_exporter_stock_pays_leg1_and_creates_shipment():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    _set_food_gap(imp, exp)
    exp_stock0 = exp.commodity_stock["food"]
    imp_households0 = imp.pools["households"]
    imp_treasury0 = imp.pools["treasury"]

    events = trade.run(world, Rng(1))
    dispatched = [e for e in events if e.kind == "SHIPMENT_DISPATCHED"]
    assert len(dispatched) == 1
    ev = dispatched[0]
    assert ev.country == exp.code and ev.country2 == imp.code
    assert ev.payload["qty"] == pytest.approx(20.0)

    # exporter goods left; importer paid leg-1 into the fx desk
    assert world.country(exp.code).commodity_stock["food"] == pytest.approx(exp_stock0 - 20.0)
    cost = ev.payload["cost"]
    base_cost = ev.payload["base_cost"]
    assert cost == base_cost > 0
    assert world.country(imp.code).pools["households"] == imp_households0 - base_cost
    assert world.country(imp.code).pools["treasury"] == imp_treasury0
    assert world.fx_pools[f"fx:{imp.code}"] == base_cost

    # a shipment now exists, arriving in the future; exporter NOT yet paid
    assert len(world.shipments) == 1
    ship = world.shipments[0]
    assert ship.origin == exp.code and ship.dest == imp.code and ship.commodity == "food"
    assert ship.arrive_tick > world.tick
    assert world.fx_pools.get(f"fx:{exp.code}", 0) == 0


def test_arrival_adds_importer_stock_and_pays_exporter_leg2():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    _set_food_gap(imp, exp)
    imp.commodity_stock["food"] = 0.0  # room to receive (below ceiling)
    trade.run(world, Rng(1))
    ship = world.shipments[0]
    exp_corporates0 = exp.pools["corporates"]
    imp_stock0 = imp.commodity_stock["food"]

    # jump the world clock to the arrival tick and run logistics
    world.tick = ship.arrive_tick
    logistics.run(world, Rng(1))

    assert not world.shipments  # delivered, removed
    assert world.country(imp.code).commodity_stock["food"] == pytest.approx(imp_stock0 + ship.qty)
    assert world.country(exp.code).pools["corporates"] == exp_corporates0 + ship.proceeds
    assert world.fx_pools[f"fx:{exp.code}"] == -ship.proceeds


def _place(c, lat, lon, region):
    c.position = Position(lat=lat, lon=lon)
    c.region = region


def test_carrier_rail_for_near_same_region():
    world = _clean_world(2, seed=5)
    a, b = _sorted_countries(world)
    _place(a, 0.0, 0.0, "R1")
    _place(b, 0.0, 3.0, "R1")  # ~0.05 rad apart, same region
    assert logistics.select_carrier(world, a, b, "food", relief=False) == "rail"


def test_carrier_sea_for_cross_region():
    world = _clean_world(2, seed=5)
    a, b = _sorted_countries(world)
    _place(a, 0.0, 0.0, "R1")
    _place(b, 0.0, 3.0, "R2")  # near but DIFFERENT region -> no rail
    assert logistics.select_carrier(world, a, b, "food", relief=False) == "sea"


def test_carrier_air_for_high_value_commodity():
    world = _clean_world(2, seed=5)
    a, b = _sorted_countries(world)
    _place(a, 0.0, 0.0, "R1")
    _place(b, 0.0, 80.0, "R2")
    assert logistics.select_carrier(world, a, b, "high_tech", relief=False) == "air"


def test_carrier_air_for_relief_through_a_war_zone():
    world = _clean_world(2, seed=5)
    a, b = _sorted_countries(world)
    _place(a, 0.0, 0.0, "R1")
    _place(b, 0.0, 80.0, "R2")
    b.at_war_with = ["ZZZ"]  # dest is a war zone
    assert logistics.select_carrier(world, a, b, "food", relief=True) == "air"


def test_transit_time_has_seven_tick_floor_and_grows_beyond_it():
    for carrier in ("sea", "air", "rail"):
        assert logistics._transit_ticks(0.0, carrier) == config.MIN_TRANSPORT_TICKS
        assert logistics._transit_ticks(0.01, carrier) == config.MIN_TRANSPORT_TICKS

    # Distance/carrier speed still matters once the raw duration exceeds the floor.
    assert logistics._transit_ticks(3.0, "sea") > logistics._transit_ticks(3.0, "rail")
    assert logistics._transit_ticks(3.0, "rail") > logistics._transit_ticks(3.0, "air")


@pytest.mark.parametrize("carrier", ["sea", "air", "rail"])
def test_every_carrier_remains_in_flight_for_at_least_seven_ticks(carrier):
    world = _clean_world(2, seed=5)
    importer, exporter = _sorted_countries(world)
    _place(importer, 0.0, 0.0, "R1")
    _place(exporter, 0.0, 0.0, "R1")

    logistics.dispatch(
        world,
        Rng(1),
        importer=importer,
        exporter=exporter,
        commodity="food",
        qty=1.0,
        price_veri=1.0,
        base_price_veri=1.0,
        tariff_rate=0.0,
        relief=False,
        carrier=carrier,
    )
    shipment = world.shipments[0]
    assert shipment.depart_tick == 0
    assert shipment.arrive_tick == config.MIN_TRANSPORT_TICKS

    for tick in range(1, config.MIN_TRANSPORT_TICKS):
        world.tick = tick
        logistics.run(world, Rng(1))
        assert [item.id for item in world.shipments] == [shipment.id]

    world.tick = config.MIN_TRANSPORT_TICKS
    logistics.run(world, Rng(1))
    assert world.shipments == []


def test_sea_shipment_in_a_war_zone_can_be_interdicted(monkeypatch):
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    _place(exp, 0.0, 0.0, "R1")
    _place(imp, 0.0, 80.0, "R2")  # forces sea
    _set_food_gap(imp, exp)
    trade.run(world, Rng(1))
    ship = world.shipments[0]
    assert ship.carrier == "sea"
    imp.at_war_with = ["ZZZ"]  # now a war zone
    imp_treasury_after_dispatch = imp.pools["treasury"]

    monkeypatch.setattr(Rng, "roll", lambda self, p: True)  # force interdiction
    events = logistics.run(world, Rng(1))

    assert [e.kind for e in events] == ["SHIPMENT_LOST"]
    assert not world.shipments  # lost, removed
    # importer's leg-1 money is stranded in the fx desk (the economic loss); never refunded,
    # exporter never paid
    assert world.country(imp.code).pools["treasury"] == imp_treasury_after_dispatch
    assert world.fx_pools[f"fx:{imp.code}"] == ship.cost
    assert world.fx_pools.get(f"fx:{exp.code}", 0) == 0


def test_relief_flag_set_for_famine_critical_importer_buying_from_a_friend():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    _set_food_gap(imp, exp)
    imp.commodity_stock["food"] = 0.0  # famine-critical
    world.relations[(min(imp.code, exp.code), max(imp.code, exp.code))] = 70.0  # friend
    trade.run(world, Rng(1))
    assert world.shipments[0].relief is True


def test_no_relief_when_supplier_is_not_a_friend():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    _set_food_gap(imp, exp)
    imp.commodity_stock["food"] = 0.0  # famine-critical
    # relation left at 0 (neutral) -> below RELIEF_RELATION_MIN
    trade.run(world, Rng(1))
    assert world.shipments[0].relief is False


def test_balanced_world_dispatches_nothing():
    world = _clean_world(2, seed=5)  # all commodities balanced
    assert trade.run(world, Rng(1)) == []
    assert world.shipments == []


def _shipment_fp(w):
    return (
        w.shipment_seq,
        sorted(
            (
                s.id,
                s.origin,
                s.dest,
                s.commodity,
                round(s.qty, 6),
                s.carrier,
                s.buyer_pool,
                s.base_cost,
                s.tariff_duty,
                s.cost,
                s.proceeds,
                s.dispatch_event_id,
                s.arrive_tick,
            )
            for s in w.shipments
        ),
        sorted((c.code, tuple(sorted(c.pools.items()))) for c in w.countries),
        sorted(w.fx_pools.items()),
    )


def test_world_at_reconstructs_in_flight_shipments():
    # Full loop over many ticks capturing the LIVE fingerprint each tick, then scrub back:
    # the in-flight Shipment set, shipment_seq, pools and fx must reconstruct exactly from
    # snapshot + replayed SHIPMENT_DISPATCHED/ARRIVED/LOST events.
    settings = WorldSettings(starting_country_count=6, snapshot_interval=25)
    world = generate_world(31, settings)
    tl = Timeline(seed=31, world=world, snapshots={}, rng=Rng(31))
    live: dict[int, tuple] = {}
    for _ in range(160):
        tl.advance()
        live[tl.world.tick] = _shipment_fp(tl.world)

    for at in (40, 88, 137, 159):
        assert _shipment_fp(tl.world_at(at)) == live[at], f"divergence at t{at}"
    # the test is only meaningful if some of those ticks actually had shipments in flight
    assert any(tl.world_at(t).shipments for t in (40, 88, 137, 159))


# --- M14 communications: shipments route worse on a badly-coordinated lane -----------


def test_bad_comms_slows_transit():
    """spec §6's "how cleanly shipments route". The lane takes the WORSE of its two ends."""
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    for c in (imp, exp):
        c.infrastructure.communications.condition = 1.0
    _set_food_gap(imp, exp)
    fast = trade.run(world, Rng(1))[0].payload["arrive_tick"]

    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    for c in (imp, exp):
        c.infrastructure.communications.condition = 1.0
    exp.infrastructure.communications.condition = 0.0  # one bad end is enough
    _set_food_gap(imp, exp)
    slow = trade.run(world, Rng(1))[0].payload["arrive_tick"]

    assert slow > fast


def test_transit_friction_takes_the_worst_end_of_the_lane():
    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    imp.infrastructure.communications.condition = 0.0
    exp.infrastructure.communications.condition = 1.0
    _set_food_gap(imp, exp)
    one_bad_end = trade.run(world, Rng(1))[0].payload["arrive_tick"]

    world = _clean_world(2, seed=5)
    imp, exp = _sorted_countries(world)
    for c in (imp, exp):
        c.infrastructure.communications.condition = 0.0
    _set_food_gap(imp, exp)
    both_bad = trade.run(world, Rng(1))[0].payload["arrive_tick"]

    assert one_bad_end == both_bad


# --- CONVOY_LOSSES: the periodic per-destination report ------------------------------------


def _lose(world, tick, dest, origin, commodity="food", relief=False, qty=5.0):
    """A synthetic SHIPMENT_LOST, shaped exactly as logistics.run emits one."""
    return world.log.append(
        tick=tick,
        kind="SHIPMENT_LOST",
        country=dest,
        country2=origin,
        parent_id=None,
        depth=1,
        is_intervention=False,
        payload={
            "shipment_id": f"SHIP{len(world.log)}",
            "carrier": "sea",
            "commodity": commodity,
            "qty": qty,
            "relief": int(relief),
        },
        severity=1,
    )


def _report(world, tick, dest, count=2):
    """A synthetic CONVOY_LOSSES, as the system would have appended one."""
    return world.log.append(
        tick=tick,
        kind="CONVOY_LOSSES",
        country=dest,
        country2=None,
        parent_id=None,
        depth=2,
        is_intervention=False,
        payload={"count": count, "recent_total": count, "commodities": "food",
                 "relief": 0, "qty": 1.0, "since_tick": 0},
        severity=1,
    )


def _report_world():
    world = _clean_world(3, seed=5)
    a, b, c = [x.code for x in _sorted_countries(world)]
    world.tick = config.CONVOY_REPORT_INTERVAL_TICKS
    return world, a, b, c


def test_convoy_losses_reports_a_destination_that_lost_several():
    world, a, b, c = _report_world()
    first = _lose(world, 10, a, b)
    second = _lose(world, 12, a, c)
    _lose(world, 11, b, c)  # a single loss is not a report

    reports = [e for e in logistics.run(world, Rng(1)) if e.kind == "CONVOY_LOSSES"]

    assert len(reports) == 1
    report = reports[0]
    assert report.country == a and report.country2 is None
    assert report.payload["count"] == 2
    assert report.payload["recent_total"] == 2
    assert report.payload["since_tick"] == 0
    # Traceable to every sinking it speaks for -- that is the whole point of the report.
    assert report.parent_ids == (first.id, second.id)
    assert {link.role for link in report.causal_links()} == {"trigger"}
    # No money and no stat moved: SHIPMENT_LOST already took the bite, so world_at replays
    # this event as a no-op without needing a handler.
    assert report.ledger == () and report.stat_deltas == ()


def test_convoy_losses_only_reports_on_the_interval():
    world, a, b, _ = _report_world()
    world.tick = config.CONVOY_REPORT_INTERVAL_TICKS - 1
    _lose(world, 10, a, b)
    _lose(world, 11, a, b)

    assert [e.kind for e in logistics.run(world, Rng(1))] == []


def test_convoy_losses_never_reports_a_quiet_window():
    world, a, b, _ = _report_world()
    _lose(world, 1, a, b)  # one loss only

    assert [e.kind for e in logistics.run(world, Rng(1))] == []


def test_convoy_losses_season_total_outlives_the_window():
    world, a, b, _ = _report_world()
    world.tick = config.CONVOY_REPORT_INTERVAL_TICKS * 2
    _report(world, 12, a, count=1)  # everything up to t12 has already been reported
    _lose(world, 20, a, b)
    _lose(world, 24, a, b)
    _lose(world, 2, a, b)  # before that report, inside the season

    report = [e for e in logistics.run(world, Rng(1)) if e.kind == "CONVOY_LOSSES"][0]

    assert report.payload["count"] == 2  # only what is unreported
    assert report.payload["recent_total"] == 3  # but the season total counts them all


def test_a_steady_drip_is_reported_rather_than_suppressed_forever():
    """The defect this rule exists to avoid. One sinking per twelve-tick window is the MODE
    on real seeds, not an edge case -- measured at 11 of 17 non-empty windows on one seed and
    9 of 10 on another. Counting within the window asks whether a harbour lost two ships in
    THESE twelve ticks, which a drip answers no to forever, leaving 44% and 82% of all
    sinkings permanently unreportable. Counting since the last report surfaces them."""
    world, a, b, _ = _report_world()
    world.tick = 0
    reported = 0
    for tick in range(1, config.CONVOY_REPORT_INTERVAL_TICKS * 6 + 1):
        world.tick = tick
        if tick % config.CONVOY_REPORT_INTERVAL_TICKS == 1:
            _lose(world, tick, a, b)  # exactly one sinking per window, forever
        reported += sum(
            int(e.payload["count"]) for e in logistics.run(world, Rng(1))
            if e.kind == "CONVOY_LOSSES"
        )

    assert reported >= 4, f"a drip of 6 sinkings surfaced only {reported}"


def test_a_destination_is_not_reported_twice_for_the_same_sinking():
    world, a, b, _ = _report_world()
    _lose(world, 10, a, b)
    _lose(world, 11, a, b)
    first = [e for e in logistics.run(world, Rng(1)) if e.kind == "CONVOY_LOSSES"]
    assert len(first) == 1 and first[0].payload["count"] == 2

    world.tick = config.CONVOY_REPORT_INTERVAL_TICKS * 2
    assert [e for e in logistics.run(world, Rng(1)) if e.kind == "CONVOY_LOSSES"] == []


def test_convoy_losses_names_the_cargo_and_flags_relief():
    world, a, b, _ = _report_world()
    _lose(world, 10, a, b, commodity="energy")
    _lose(world, 11, a, b, commodity="food", relief=True)
    _lose(world, 12, a, b, commodity="food")

    report = [e for e in logistics.run(world, Rng(1)) if e.kind == "CONVOY_LOSSES"][0]

    assert report.payload["commodity"] == "food"  # the most-lost cargo
    assert report.payload["commodities"] == "food,energy"  # canonical commodity order
    assert report.payload["relief"] == 1
    assert report.payload["qty"] == pytest.approx(15.0)


def test_convoy_losses_reports_each_destination_in_a_stable_order():
    world, a, b, c = _report_world()
    for dest in (c, a, b):
        _lose(world, 10, dest, a if dest != a else b)
        _lose(world, 11, dest, a if dest != a else b)

    reports = [e for e in logistics.run(world, Rng(1)) if e.kind == "CONVOY_LOSSES"]

    assert [e.country for e in reports] == sorted([a, b, c])
