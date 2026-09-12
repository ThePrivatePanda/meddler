"""Authoritative engine-state projection for the protocol-v2 globe.

This module is deliberately observational: it does not mutate ``World``, consume RNG, or
invent simulation objects.  Stylisation (territory outlines and satellite orbits) belongs
to the frontend; positions, ownership, quantities, conditions, and event routes come from
the engine state projected here.
"""

from __future__ import annotations

from typing import Any

from meddler.engine import assets, config
from meddler.engine.events import Event
from meddler.engine.model import Country, CountryStatus, Shipment, World
from meddler.engine.space import Position, distance_km

STRIKE_VISIBLE_TICKS = 12
ARRIVED_SHIPMENT_RETENTION_TICKS = 4
LOST_SHIPMENT_RETENTION_TICKS = 8
TERMINAL_SHIPMENT_RETENTION_TICKS = max(
    ARRIVED_SHIPMENT_RETENTION_TICKS,
    LOST_SHIPMENT_RETENTION_TICKS,
)


def _position(position: Position) -> dict[str, float]:
    return {"lat": position.lat, "lon": position.lon}


def _active_countries(world: World) -> list[Country]:
    return sorted(
        (
            country
            for country in world.countries
            if country.status not in (CountryStatus.ANNEXED, CountryStatus.DISSOLVED)
        ),
        key=lambda country: country.code,
    )


def _asset_projection(country: Country) -> dict[str, dict[str, int | float]]:
    projected: dict[str, dict[str, int | float]] = {}
    for asset_class in assets.all_asset_classes():
        asset = assets.get_asset(country.infrastructure, asset_class)
        projected[asset_class] = {
            "count": asset.count,
            "condition": asset.condition,
            "effectiveCapacity": assets.effective_capacity(asset),
            "lastMaintainedTick": asset.last_maintained_tick,
        }
    return projected


def _country_projection(world: World, country: Country) -> dict[str, Any]:
    bloc = world.bloc_of(country.code)
    territories = sorted(
        country.territories,
        key=lambda territory: (
            territory.region,
            territory.position.lat,
            territory.position.lon,
        ),
    )
    return {
        "code": country.code,
        "position": _position(country.position),
        "region": country.region,
        "territories": [
            {"position": _position(territory.position), "region": territory.region}
            for territory in territories
        ],
        "endowments": {
            name: country.endowments[name] for name in sorted(country.endowments)
        },
        "commodities": {
            name: {
                "output": country.commodity_output[name],
                "need": country.commodity_need[name],
                "stock": country.commodity_stock[name],
            }
            for name in config.COMMODITY_ORDER
        },
        "assets": _asset_projection(country),
        "blocId": bloc.id if bloc is not None else None,
        "status": country.status.value,
    }


def _shipment_sort_key(shipment: Shipment) -> tuple[int, int, str]:
    """Put canonical ``SHIP<number>`` ids in numeric order, then malformed ids safely."""
    suffix = shipment.id[4:] if shipment.id.startswith("SHIP") else ""
    if suffix.isdigit():
        return (0, int(suffix), shipment.id)
    return (1, 0, shipment.id)


def _carrier_condition(country: Country, carrier: str) -> float:
    asset_class = assets.CARRIER_ASSET_CLASS.get(carrier)
    if asset_class is None:
        return 0.0
    return assets.get_asset(country.infrastructure, asset_class).condition


def _progress(shipment: Shipment, tick: int) -> float:
    duration = shipment.arrive_tick - shipment.depart_tick
    if duration <= 0:
        return 1.0 if tick >= shipment.arrive_tick else 0.0
    return max(0.0, min(1.0, (tick - shipment.depart_tick) / duration))


def _route(origin: Country, dest: Country) -> dict[str, dict[str, float]]:
    return {"from": _position(origin.position), "to": _position(dest.position)}


def _war_exposed(shipment: Shipment, countries: dict[str, Country]) -> bool:
    origin = countries.get(shipment.origin)
    dest = countries.get(shipment.dest)
    if origin is None or dest is None:
        return False
    return dest.code in origin.at_war_with or origin.code in dest.at_war_with


def _shipment_projection(
    world: World,
    shipment: Shipment,
    countries: dict[str, Country],
    *,
    progress_tick: int | None = None,
) -> dict[str, Any] | None:
    origin = countries.get(shipment.origin)
    dest = countries.get(shipment.dest)
    if origin is None or dest is None:
        return None
    origin_condition = _carrier_condition(origin, shipment.carrier)
    dest_condition = _carrier_condition(dest, shipment.carrier)
    tick = world.tick if progress_tick is None else progress_tick
    progress = _progress(shipment, tick)
    route_distance_km = distance_km(origin.position, dest.position)
    return {
        "id": shipment.id,
        "origin": shipment.origin,
        "dest": shipment.dest,
        "commodity": shipment.commodity,
        "qty": shipment.qty,
        "carrier": shipment.carrier,
        "buyerPool": shipment.buyer_pool,
        "baseCost": shipment.base_cost,
        "tariffDuty": shipment.tariff_duty,
        "cost": shipment.cost,
        "proceeds": shipment.proceeds,
        "dispatchEventId": shipment.dispatch_event_id,
        "departTick": shipment.depart_tick,
        "arriveTick": shipment.arrive_tick,
        "progress": progress,
        "distanceKm": route_distance_km,
        "remainingDistanceKm": route_distance_km * (1.0 - progress),
        "etaTicks": max(0, shipment.arrive_tick - world.tick),
        "status": "in_transit",
        "warExposed": _war_exposed(shipment, countries),
        "relief": shipment.relief,
        "condition": min(origin_condition, dest_condition),
        "originCondition": origin_condition,
        "destCondition": dest_condition,
        "route": _route(origin, dest),
    }


def _shipment_from_dispatch(event: Event) -> Shipment | None:
    p = event.payload
    shipment_id = p.get("shipment_id")
    if event.kind != "SHIPMENT_DISPATCHED" or not isinstance(shipment_id, str):
        return None
    try:
        return Shipment(
            id=shipment_id,
            origin=str(p["origin"]),
            dest=str(p["dest"]),
            commodity=str(p["commodity"]),
            qty=float(p["qty"]),
            carrier=str(p["carrier"]),
            buyer_pool=str(p.get("buyer_pool", "treasury")),
            base_cost=int(p.get("base_cost", p["cost"])),
            tariff_duty=int(p.get("tariff_duty", 0)),
            cost=int(p["cost"]),
            proceeds=int(p["proceeds"]),
            dispatch_event_id=event.id,
            depart_tick=event.tick,
            arrive_tick=int(p["arrive_tick"]),
            relief=bool(int(p.get("relief", 0))),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _recent_shipment_projection(
    world: World,
    countries: dict[str, Country],
    recent_history: tuple[Event, ...],
) -> list[dict[str, Any]]:
    cutoff = world.tick - TERMINAL_SHIPMENT_RETENTION_TICKS + 1
    recent: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in reversed(recent_history):
        if event.tick < cutoff or event.kind not in (
            "SHIPMENT_ARRIVED",
            "SHIPMENT_LOST",
        ):
            continue
        shipment_id = event.payload.get("shipment_id")
        if not isinstance(shipment_id, str) or shipment_id in seen:
            continue
        if event.parent_id is None or not 0 <= event.parent_id < len(world.log):
            continue
        shipment = _shipment_from_dispatch(world.log[event.parent_id])
        if shipment is None or shipment.id != shipment_id:
            continue
        age = world.tick - event.tick
        status = "arrived" if event.kind == "SHIPMENT_ARRIVED" else "lost"
        retention_ticks = (
            ARRIVED_SHIPMENT_RETENTION_TICKS
            if status == "arrived"
            else LOST_SHIPMENT_RETENTION_TICKS
        )
        if age >= retention_ticks:
            continue
        projected = _shipment_projection(
            world, shipment, countries, progress_tick=event.tick
        )
        if projected is None:
            continue
        if status == "arrived":
            projected["progress"] = 1.0
            projected["remainingDistanceKm"] = 0.0
            projected["etaTicks"] = 0
        else:
            projected["etaTicks"] = None
        projected.update(
            {
                "status": status,
                "terminalTick": event.tick,
                "terminalEventId": event.id,
                "ageTicks": age,
                "retentionTicksRemaining": retention_ticks - age,
            }
        )
        recent.append(projected)
        seen.add(shipment_id)
    return sorted(
        recent,
        key=lambda shipment: (
            int(shipment["terminalTick"]),
            int(shipment["dispatchEventId"]),
            str(shipment["id"]),
        ),
    )


def _lane_projection(
    shipments: list[dict[str, Any]],
    recent_shipments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate active cargo and retain terminal membership without inflating volume."""
    lanes: dict[tuple[str, str, str], dict[str, Any]] = {}

    def lane_for(shipment: dict[str, Any]) -> dict[str, Any]:
        key = (shipment["carrier"], shipment["origin"], shipment["dest"])
        lane = lanes.get(key)
        if lane is None:
            lane = {
                "id": ":".join(key),
                "origin": shipment["origin"],
                "dest": shipment["dest"],
                "carrier": shipment["carrier"],
                "volume": 0.0,
                "shipmentCount": 0,
                "shipmentIds": [],
                "recentShipmentCount": 0,
                "recentShipmentIds": [],
                "condition": shipment["condition"],
                "distanceKm": shipment["distanceKm"],
                "route": shipment["route"],
            }
            lanes[key] = lane
        return lane

    for shipment in shipments:
        lane = lane_for(shipment)
        lane["volume"] += shipment["qty"]
        lane["shipmentCount"] += 1
        lane["shipmentIds"].append(shipment["id"])
        lane["condition"] = min(lane["condition"], shipment["condition"])
    for shipment in recent_shipments:
        lane = lane_for(shipment)
        lane["recentShipmentCount"] += 1
        lane["recentShipmentIds"].append(shipment["id"])
    return [lanes[key] for key in sorted(lanes)]


def _trade_stats(
    shipments: list[dict[str, Any]],
    recent_shipments: list[dict[str, Any]],
    lanes: list[dict[str, Any]],
) -> dict[str, Any]:
    carrier_order = ("sea", "air", "rail")
    by_carrier: dict[str, dict[str, int | float]] = {
        carrier: {
            "shipments": 0,
            "quantity": 0.0,
            "remainingDistanceKm": 0.0,
            "averageProgress": 0.0,
            "averageCondition": 0.0,
        }
        for carrier in carrier_order
    }
    by_commodity: dict[str, dict[str, int | float]] = {
        commodity: {"shipments": 0, "quantity": 0.0}
        for commodity in config.COMMODITY_ORDER
    }
    progress_total = 0.0
    for shipment in shipments:
        carrier = str(shipment["carrier"])
        bucket = by_carrier.setdefault(
            carrier,
            {
                "shipments": 0,
                "quantity": 0.0,
                "remainingDistanceKm": 0.0,
                "averageProgress": 0.0,
                "averageCondition": 0.0,
            },
        )
        bucket["shipments"] += 1
        bucket["quantity"] += float(shipment["qty"])
        bucket["remainingDistanceKm"] += float(shipment["remainingDistanceKm"])
        bucket["averageProgress"] += float(shipment["progress"])
        bucket["averageCondition"] += float(shipment["condition"])
        commodity = str(shipment["commodity"])
        commodity_bucket = by_commodity.setdefault(
            commodity, {"shipments": 0, "quantity": 0.0}
        )
        commodity_bucket["shipments"] += 1
        commodity_bucket["quantity"] += float(shipment["qty"])
        progress_total += float(shipment["progress"])
    for bucket in by_carrier.values():
        count = int(bucket["shipments"])
        if count:
            bucket["averageProgress"] = float(bucket["averageProgress"]) / count
            bucket["averageCondition"] = float(bucket["averageCondition"]) / count
    arrivals = [s for s in recent_shipments if s["status"] == "arrived"]
    losses = [s for s in recent_shipments if s["status"] == "lost"]
    return {
        "activeShipments": len(shipments),
        "activeLanes": sum(1 for lane in lanes if lane["shipmentCount"] > 0),
        "inTransitQuantity": sum(float(s["qty"]) for s in shipments),
        "remainingDistanceKm": sum(
            float(s["remainingDistanceKm"]) for s in shipments
        ),
        "averageProgress": progress_total / len(shipments) if shipments else 0.0,
        "reliefShipments": sum(1 for s in shipments if s["relief"]),
        "warExposedShipments": sum(1 for s in shipments if s["warExposed"]),
        "arrivalsLast4Ticks": len(arrivals),
        "lossesLast8Ticks": len(losses),
        "arrivedQuantityLast4Ticks": sum(float(s["qty"]) for s in arrivals),
        "lostQuantityLast8Ticks": sum(float(s["qty"]) for s in losses),
        "byCarrier": by_carrier,
        "byCommodity": by_commodity,
    }


def _strike_projection(
    world: World,
    countries: dict[str, Country],
    recent_history: tuple[Event, ...],
) -> list[dict[str, Any]]:
    cutoff = world.tick - STRIKE_VISIBLE_TICKS
    strikes: list[dict[str, Any]] = []
    for event in reversed(recent_history):
        if event.tick < cutoff or event.kind != "STRIKE":
            continue
        if event.country is None or event.country2 is None:
            continue
        target = countries.get(event.country)
        attacker = countries.get(event.country2)
        if attacker is None or target is None:
            continue
        raw_power = event.payload.get("strike_power", 0.0)
        power = float(raw_power) if isinstance(raw_power, (int, float)) else 0.0
        strikes.append(
            {
                "id": f"strike:{event.id}",
                "eventId": event.id,
                "tick": event.tick,
                "origin": attacker.code,
                "dest": target.code,
                "power": power,
                "age": world.tick - event.tick,
                "visibleTicks": STRIKE_VISIBLE_TICKS,
                "route": _route(attacker, target),
            }
        )
    return strikes


def project_world_objects(world: World) -> dict[str, Any]:
    """Return a deterministic, JSON-serializable protocol-v2 globe projection."""
    active = _active_countries(world)
    countries = {country.code: country for country in active}
    shipment_countries = {country.code: country for country in world.countries}
    projected_shipments = [
        projected
        for shipment in sorted(world.shipments, key=_shipment_sort_key)
        if (
            projected := _shipment_projection(world, shipment, shipment_countries)
        )
        is not None
    ]
    history_start = min(
        world.tick - TERMINAL_SHIPMENT_RETENTION_TICKS,
        world.tick - STRIKE_VISIBLE_TICKS - 1,
    )
    recent_history = world.log.events_of_kinds_between(
        ("SHIPMENT_ARRIVED", "SHIPMENT_LOST", "STRIKE"),
        history_start,
        world.tick,
    )
    recent_shipments = _recent_shipment_projection(
        world, shipment_countries, recent_history
    )
    lanes = _lane_projection(projected_shipments, recent_shipments)
    return {
        "tick": world.tick,
        "countries": {
            country.code: _country_projection(world, country) for country in active
        },
        "blocs": [
            {
                "id": bloc.id,
                "members": sorted(code for code in bloc.members if code in countries),
                "formedAt": bloc.formed_at_tick,
            }
            for bloc in sorted(world.blocs, key=lambda bloc: bloc.id)
            if any(code in countries for code in bloc.members)
        ],
        "shipments": projected_shipments,
        "recentShipments": recent_shipments,
        "lanes": lanes,
        "tradeStats": _trade_stats(projected_shipments, recent_shipments, lanes),
        "strikes": _strike_projection(world, countries, recent_history),
    }


__all__ = [
    "ARRIVED_SHIPMENT_RETENTION_TICKS",
    "LOST_SHIPMENT_RETENTION_TICKS",
    "STRIKE_VISIBLE_TICKS",
    "TERMINAL_SHIPMENT_RETENTION_TICKS",
    "project_world_objects",
]
