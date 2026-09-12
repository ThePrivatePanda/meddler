"""M16 protocol-v2 authoritative globe projection tests."""

from __future__ import annotations

import json

import pytest

from meddler.bridge.server import new_session
from meddler.bridge.world_objects import (
    ARRIVED_SHIPMENT_RETENTION_TICKS,
    LOST_SHIPMENT_RETENTION_TICKS,
    STRIKE_VISIBLE_TICKS,
    TERMINAL_SHIPMENT_RETENTION_TICKS,
    project_world_objects,
)
from meddler.engine import assets, config
from meddler.engine.model import Bloc, CountryStatus, Shipment, Territory, WorldSettings
from meddler.engine.space import Position, distance_km
from meddler.engine.worldgen import generate_world


def _world():
    return generate_world(17, WorldSettings(starting_country_count=3))


def _shipment(
    shipment_id: str,
    origin: str,
    dest: str,
    *,
    carrier: str = "sea",
    qty: float = 4.0,
    depart: int = 10,
    arrive: int = 20,
) -> Shipment:
    return Shipment(
        id=shipment_id,
        origin=origin,
        dest=dest,
        commodity="food",
        qty=qty,
        carrier=carrier,
        buyer_pool="households",
        base_cost=100,
        tariff_duty=10,
        cost=110,
        proceeds=95,
        dispatch_event_id=7,
        depart_tick=depart,
        arrive_tick=arrive,
        relief=False,
    )


def _record_terminal(world, shipment: Shipment, *, kind: str, tick: int):
    dispatch = world.log.append(
        tick=shipment.depart_tick,
        kind="SHIPMENT_DISPATCHED",
        country=shipment.origin,
        country2=shipment.dest,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={
            "shipment_id": shipment.id,
            "origin": shipment.origin,
            "dest": shipment.dest,
            "commodity": shipment.commodity,
            "qty": shipment.qty,
            "carrier": shipment.carrier,
            "buyer_pool": shipment.buyer_pool,
            "base_cost": shipment.base_cost,
            "tariff_duty": shipment.tariff_duty,
            "cost": shipment.cost,
            "proceeds": shipment.proceeds,
            "arrive_tick": shipment.arrive_tick,
            "relief": int(shipment.relief),
        },
    )
    return world.log.append(
        tick=tick,
        kind=kind,
        country=shipment.dest,
        country2=shipment.origin,
        parent_id=dispatch.id,
        depth=1,
        is_intervention=False,
        payload={"shipment_id": shipment.id},
    )


def test_complete_shape_and_country_projection() -> None:
    world = _world()
    country = world.countries[0]
    country.territories.append(Territory(Position(12.5, -33.25), "Outer Reach"))

    payload = project_world_objects(world)

    assert set(payload) == {
        "tick",
        "countries",
        "blocs",
        "shipments",
        "recentShipments",
        "lanes",
        "tradeStats",
        "strikes",
    }
    assert payload["tick"] == world.tick
    assert list(payload["countries"]) == sorted(c.code for c in world.countries)
    projected = payload["countries"][country.code]
    assert projected["position"] == {"lat": country.position.lat, "lon": country.position.lon}
    assert projected["region"] == country.region
    assert {item["region"] for item in projected["territories"]} >= {
        country.region,
        "Outer Reach",
    }
    assert projected["endowments"] == dict(sorted(country.endowments.items()))
    assert list(projected["commodities"]) == list(config.COMMODITY_ORDER)
    for name in config.COMMODITY_ORDER:
        assert projected["commodities"][name] == {
            "output": country.commodity_output[name],
            "need": country.commodity_need[name],
            "stock": country.commodity_stock[name],
        }
    assert set(projected["assets"]) == set(assets.all_asset_classes())
    for name, item in projected["assets"].items():
        source = assets.get_asset(country.infrastructure, name)
        assert item == {
            "count": source.count,
            "condition": source.condition,
            "effectiveCapacity": assets.effective_capacity(source),
            "lastMaintainedTick": source.last_maintained_tick,
        }
    assert projected["status"] == country.status.value


def test_annexed_and_dissolved_excluded_and_status_serialized() -> None:
    world = _world()
    active, annexed, dissolved = world.countries
    active.status = CountryStatus.OCCUPIED
    annexed.status = CountryStatus.ANNEXED
    dissolved.status = CountryStatus.DISSOLVED

    payload = project_world_objects(world)

    assert list(payload["countries"]) == [active.code]
    assert payload["countries"][active.code]["status"] == "OCCUPIED"


def test_blocs_sorted_with_sorted_active_members() -> None:
    world = _world()
    codes = [country.code for country in world.countries]
    world.blocs = [
        Bloc("ZED", [codes[2], codes[0]], 8),
        Bloc("ALPHA", [codes[1], codes[0]], 3),
    ]

    payload = project_world_objects(world)

    assert [bloc["id"] for bloc in payload["blocs"]] == ["ALPHA", "ZED"]
    assert payload["blocs"][0]["members"] == sorted([codes[1], codes[0]])
    assert payload["countries"][codes[0]]["blocId"] == "ZED"


@pytest.mark.parametrize(
    ("tick", "expected"),
    [(5, 0.0), (10, 0.0), (15, 0.5), (20, 1.0), (25, 1.0)],
)
def test_shipment_progress_depart_mid_arrival_and_clamping(tick: int, expected: float) -> None:
    world = _world()
    origin, dest = world.countries[:2]
    world.tick = tick
    world.shipments = [_shipment("SHIP1", origin.code, dest.code)]

    assert project_world_objects(world)["shipments"][0]["progress"] == expected


@pytest.mark.parametrize(
    ("tick", "expected"),
    [(10, 0.0), (11, 1 / 7), (13, 3 / 7), (16, 6 / 7), (17, 1.0)],
)
def test_minimum_duration_shipment_progress_and_distance_span_seven_ticks(
    tick: int, expected: float
) -> None:
    world = _world()
    origin, dest = world.countries[:2]
    world.tick = tick
    world.shipments = [
        _shipment("SHIP7", origin.code, dest.code, depart=10, arrive=17)
    ]

    projected = project_world_objects(world)["shipments"][0]
    total_distance = distance_km(origin.position, dest.position)
    assert projected["progress"] == pytest.approx(expected)
    assert projected["distanceKm"] == pytest.approx(total_distance)
    assert projected["remainingDistanceKm"] == pytest.approx(
        total_distance * (1.0 - expected)
    )


def test_zero_duration_shipment_progress_is_defensive() -> None:
    world = _world()
    origin, dest = world.countries[:2]
    world.shipments = [_shipment("SHIP1", origin.code, dest.code, depart=10, arrive=10)]
    world.tick = 9
    assert project_world_objects(world)["shipments"][0]["progress"] == 0.0
    world.tick = 10
    assert project_world_objects(world)["shipments"][0]["progress"] == 1.0


def test_shipment_fields_endpoint_conditions_numeric_sort_and_lane_aggregation() -> None:
    world = _world()
    origin, dest = world.countries[:2]
    origin.infrastructure.naval_fleet.condition = 0.7
    dest.infrastructure.naval_fleet.condition = 0.4
    world.tick = 12
    world.shipments = [
        _shipment("SHIP10", origin.code, dest.code, qty=3.0),
        _shipment("ODD", origin.code, dest.code, qty=7.0),
        _shipment("SHIP2", origin.code, dest.code, qty=5.0),
    ]

    payload = project_world_objects(world)

    assert [shipment["id"] for shipment in payload["shipments"]] == ["SHIP2", "SHIP10", "ODD"]
    sample = payload["shipments"][0]
    assert sample["originCondition"] == 0.7
    assert sample["destCondition"] == 0.4
    assert sample["condition"] == 0.4
    assert sample["route"] == {
        "from": {"lat": origin.position.lat, "lon": origin.position.lon},
        "to": {"lat": dest.position.lat, "lon": dest.position.lon},
    }
    lane = payload["lanes"][0]
    assert lane["id"] == f"sea:{origin.code}:{dest.code}"
    assert lane["volume"] == 15.0
    assert lane["shipmentCount"] == 3
    assert lane["shipmentIds"] == ["SHIP2", "SHIP10", "ODD"]
    assert lane["condition"] == 0.4
    assert lane["distanceKm"] == pytest.approx(
        distance_km(origin.position, dest.position)
    )


def test_lanes_are_directional_and_separate_carriers() -> None:
    world = _world()
    a, b = world.countries[:2]
    world.shipments = [
        _shipment("SHIP1", a.code, b.code, carrier="sea"),
        _shipment("SHIP2", b.code, a.code, carrier="sea"),
        _shipment("SHIP3", a.code, b.code, carrier="air"),
    ]

    lanes = project_world_objects(world)["lanes"]

    assert {(lane["carrier"], lane["origin"], lane["dest"]) for lane in lanes} == {
        ("sea", a.code, b.code),
        ("sea", b.code, a.code),
        ("air", a.code, b.code),
    }


def test_recent_strike_cutoff_ordering_and_attacker_target_direction() -> None:
    world = _world()
    attacker, target = world.countries[:2]
    world.tick = 20
    old = world.tick - STRIKE_VISIBLE_TICKS - 1
    cutoff = world.tick - STRIKE_VISIBLE_TICKS
    world.log.append(
        tick=old,
        kind="STRIKE",
        country=target.code,
        country2=attacker.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"strike_power": 0.1},
    )
    boundary = world.log.append(
        tick=cutoff,
        kind="STRIKE",
        country=target.code,
        country2=attacker.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"strike_power": 0.4},
    )
    newest = world.log.append(
        tick=19,
        kind="STRIKE",
        country=attacker.code,
        country2=target.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"strike_power": 0.8},
    )

    strikes = project_world_objects(world)["strikes"]

    assert [strike["eventId"] for strike in strikes] == [newest.id, boundary.id]
    assert strikes[0]["origin"] == target.code
    assert strikes[0]["dest"] == attacker.code
    assert strikes[1]["origin"] == attacker.code
    assert strikes[1]["dest"] == target.code
    assert strikes[1]["age"] == STRIKE_VISIBLE_TICKS
    assert strikes[1]["route"]["from"] == {
        "lat": attacker.position.lat,
        "lon": attacker.position.lon,
    }


def test_empty_lists_deterministic_json_and_no_rng_consumption() -> None:
    session = new_session(seed=91)
    world = session.multiverse.prime.world
    world.shipments = []
    world.blocs = []
    before = session.multiverse.prime.rng.get_state()

    first = project_world_objects(world)
    second = project_world_objects(world)

    assert first["shipments"] == []
    assert first["lanes"] == []
    assert first["blocs"] == []
    assert first["strikes"] == []
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert session.multiverse.prime.rng.get_state() == before


def test_arrived_shipment_rests_at_one_hundred_percent_for_exactly_four_ticks() -> None:
    world = _world()
    origin, dest = world.countries[:2]
    shipment = _shipment(
        "SHIP77", origin.code, dest.code, depart=10, arrive=17, qty=6.0
    )
    terminal = _record_terminal(
        world, shipment, kind="SHIPMENT_ARRIVED", tick=17
    )

    world.tick = 17
    payload = project_world_objects(world)
    recent = payload["recentShipments"][0]
    assert recent["id"] == "SHIP77"
    assert recent["status"] == "arrived"
    assert recent["progress"] == 1.0
    assert recent["remainingDistanceKm"] == 0.0
    assert recent["terminalTick"] == 17
    assert recent["terminalEventId"] == terminal.id
    assert recent["retentionTicksRemaining"] == ARRIVED_SHIPMENT_RETENTION_TICKS
    assert TERMINAL_SHIPMENT_RETENTION_TICKS == LOST_SHIPMENT_RETENTION_TICKS
    assert payload["lanes"][0]["shipmentIds"] == []
    assert payload["lanes"][0]["recentShipmentIds"] == ["SHIP77"]

    world.tick = 20
    assert project_world_objects(world)["recentShipments"][0][
        "retentionTicksRemaining"
    ] == 1
    world.tick = 21
    expired = project_world_objects(world)
    assert expired["recentShipments"] == []
    assert expired["lanes"] == []


def test_lost_shipment_freezes_actual_progress_and_trade_stats_are_unit_safe() -> None:
    world = _world()
    origin, dest = world.countries[:2]
    active = _shipment("SHIP1", origin.code, dest.code, qty=5.0, depart=10, arrive=20)
    lost = _shipment("SHIP2", origin.code, dest.code, qty=3.0, depart=10, arrive=20)
    arrived = _shipment("SHIP3", origin.code, dest.code, qty=7.0, depart=15, arrive=17)
    _record_terminal(world, lost, kind="SHIPMENT_LOST", tick=14)
    _record_terminal(world, arrived, kind="SHIPMENT_ARRIVED", tick=17)
    world.shipments = [active]
    world.tick = 20
    origin.at_war_with.append(dest.code)

    payload = project_world_objects(world)

    assert [item["id"] for item in payload["recentShipments"]] == ["SHIP2", "SHIP3"]
    projected_lost = payload["recentShipments"][0]
    assert projected_lost["status"] == "lost"
    assert projected_lost["progress"] == pytest.approx(0.4)
    assert projected_lost["remainingDistanceKm"] == pytest.approx(
        projected_lost["distanceKm"] * 0.6
    )
    assert projected_lost["etaTicks"] is None
    assert projected_lost["retentionTicksRemaining"] == 2
    assert payload["shipments"][0]["warExposed"] is True
    assert payload["shipments"][0]["etaTicks"] == 0

    lane = payload["lanes"][0]
    assert lane["shipmentIds"] == ["SHIP1"]
    assert lane["recentShipmentIds"] == ["SHIP2", "SHIP3"]
    stats = payload["tradeStats"]
    assert stats["activeShipments"] == 1
    assert stats["activeLanes"] == 1
    assert stats["inTransitQuantity"] == 5.0
    assert stats["warExposedShipments"] == 1
    assert stats["arrivalsLast4Ticks"] == 1
    assert stats["lossesLast8Ticks"] == 1
    assert stats["arrivedQuantityLast4Ticks"] == 7.0
    assert stats["lostQuantityLast8Ticks"] == 3.0
    assert stats["byCarrier"]["sea"]["shipments"] == 1
    assert stats["byCommodity"]["food"] == {"shipments": 1, "quantity": 5.0}
    assert "totalValue" not in stats

    world.tick = 21
    retained_loss = project_world_objects(world)["recentShipments"]
    assert [item["id"] for item in retained_loss] == ["SHIP2"]
    assert retained_loss[0]["retentionTicksRemaining"] == 1
    world.tick = 22
    assert project_world_objects(world)["recentShipments"] == []


def test_in_transit_shipment_survives_endpoint_annexation_in_projection() -> None:
    world = _world()
    origin, dest = world.countries[:2]
    origin.status = CountryStatus.ANNEXED
    world.shipments = [_shipment("SHIP9", origin.code, dest.code)]

    payload = project_world_objects(world)

    assert origin.code not in payload["countries"]
    assert payload["shipments"][0]["id"] == "SHIP9"
    assert payload["lanes"][0]["origin"] == origin.code


def test_world_object_projection_uses_bounded_indexed_history(monkeypatch) -> None:
    world = _world()
    world.tick = 40
    for tick in range(40):
        world.log.append(
            tick=tick,
            kind="UNRELATED",
            country=world.countries[0].code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
        )

    def fail_reverse(_log):
        raise AssertionError("world-object projection scanned the full history")

    monkeypatch.setattr(type(world.log), "__reversed__", fail_reverse)

    payload = project_world_objects(world)

    assert payload["tick"] == 40
    assert payload["recentShipments"] == []
    assert payload["strikes"] == []
