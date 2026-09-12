"""LogisticsSystem: physical in-flight shipments (M13, v2 spec §5).

A matched trade no longer settles instantly (M12 did). TradeSystem calls `dispatch` here,
which turns the fill into a `Shipment` that spends real ticks in transit by carrier. Each
tick this system's `run` advances the in-flight shipments: a sea shipment in a war zone may
be interdicted (SHIPMENT_LOST); a shipment reaching its arrive_tick lands (SHIPMENT_ARRIVED,
goods -> importer stock, money -> exporter). Every CONVOY_REPORT_INTERVAL_TICKS each
destination's unreported sinkings are folded into one CONVOY_LOSSES report -- the reader's
version of a loss that the per-shipment events are too numerous to tell.

DETERMINISM (the sacred part -- Shipment is the first multi-tick stateful object mutated
mid-tick since occupation): each lifecycle event carries its money on `event.ledger` and its
stock move on `event.stat_deltas` (both replayed generically by Timeline.world_at), while the
Shipment object's creation/removal is a STRUCTURAL effect with a matching RNG-free REPLAY
handler that reconstructs it from `event.payload`. Interdiction rolls RNG in a stable order
(shipments sorted by numeric id); replay never rolls -- it just re-applies the recorded
LOST/ARRIVED events.

Money conservation: dispatch has two importer-currency transfer legs. Pre-tariff border value
moves from the consuming sector (households for food/consumer, corporates otherwise) to
`fx:<IMP>`; duty moves separately from that sector to the importing treasury. Arrival moves the
pre-tariff exporter-currency proceeds from `fx:<EXP>` to exporter corporates. A lost shipment
strands only its border-value leg in the FX desk; tariff revenue is already collected. Every leg
conserves its named currency with the FX desk counted.
"""

from __future__ import annotations

from math import ceil

from meddler.engine import assets, cascade, commodities, config, space, structural, tariffs
from meddler.engine.events import (
    CauseLink,
    Event,
    LedgerEntry,
    StatDelta,
    record_structural_effect,
)
from meddler.engine.ledger import Ledger, apply_to_world, round_money
from meddler.engine.model import Country, Shipment, World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_commodity_stat


def _seq_of(shipment_id: str) -> int:
    return int(shipment_id[len("SHIP") :])


def _war_zone(world: World, ship: Shipment) -> bool:
    """A sea lane is unsafe when either endpoint is at war with anyone -- convoys sink in a
    war zone. (M13 has no route model; 'origin or dest belligerent' stands in for the third-
    party NAVAL_BLOCKADE the spec names.)"""
    return bool(world.country(ship.origin).at_war_with) or bool(world.country(ship.dest).at_war_with)


def _transit_ticks(angle: float, carrier: str, coordination: float = 1.0) -> int:
    """Ticks in transit, never fewer than ``MIN_TRANSPORT_TICKS`` for any carrier.

    Poor `communications` adds routing FRICTION (v2 spec §6's "how cleanly shipments
    route"), so a badly-coordinated or long-distance lane can still take longer.
    """
    per_radian = {
        "air": config.AIR_TICKS_PER_RADIAN,
        "rail": config.RAIL_TICKS_PER_RADIAN,
        "sea": config.SEA_TICKS_PER_RADIAN,
    }[carrier]
    friction = 1.0 + config.COMMS_FRICTION_COEFF * (1.0 - coordination)
    return max(config.MIN_TRANSPORT_TICKS, ceil(angle * per_radian * friction))


def select_carrier(world: World, origin: Country, dest: Country, commodity: str, relief: bool) -> str:
    """rail if same-region and near; air for emergency relief through a war zone or for
    high-value goods; sea otherwise (v2 spec §5). Air bypasses blockades; rail is land."""
    angle = space.central_angle(origin.position, dest.position)
    if origin.region == dest.region and angle <= config.RAIL_MAX_ANGLE:
        return "rail"
    sea_unsafe = bool(origin.at_war_with) or bool(dest.at_war_with)
    if relief and sea_unsafe:
        return "air"
    if commodity in config.HIGH_VALUE_COMMODITIES:
        return "air"
    return "sea"


def dispatch(
    world: World,
    rng: Rng,
    *,
    importer: Country,
    exporter: Country,
    commodity: str,
    qty: float,
    price_veri: float,
    base_price_veri: float,
    tariff_rate: float,
    relief: bool,
    carrier: str,
) -> Event:
    """Emit SHIPMENT_DISPATCHED: exporter's goods leave, importer pays leg-1, a Shipment is
    created. Called by TradeSystem for each matched fill.

    `carrier` is chosen by the caller (M14): TradeSystem needs it BEFORE dispatch to meter the
    fill against that carrier's fleet capacity, and deciding it twice risks the two decisions
    disagreeing.
    """
    angle = space.central_angle(exporter.position, importer.position)
    # A lane routes as cleanly as its WORST-coordinated end (M14).
    coordination = min(assets.comms_quality(exporter), assets.comms_quality(importer))
    arrive_tick = world.tick + _transit_ticks(angle, carrier, coordination)
    base_value_veri = base_price_veri * qty * config.TRADE_VALUE_MINOR_SCALE
    base_cost = round_money(base_value_veri * importer.exchange_rate)
    tariff_duty = round_money(base_cost * tariff_rate)
    cost = base_cost + tariff_duty
    proceeds = round_money(base_value_veri * exporter.exchange_rate)
    buyer_pool = tariffs.buyer_pool(commodity)

    ledger: list[LedgerEntry] = []
    if base_cost > 0:
        Ledger.transfer(
            ledger,
            f"{importer.code}.{buyer_pool}",
            f"fx:{importer.code}",
            base_cost,
            importer.code,
        )
    if tariff_duty > 0:
        Ledger.transfer(
            ledger,
            f"{importer.code}.{buyer_pool}",
            f"{importer.code}.treasury",
            tariff_duty,
            importer.code,
        )
    deltas: list[StatDelta] = []
    apply_commodity_stat(world, deltas, exporter.code, "stock", commodity, -qty)

    event = world.log.append(
        tick=world.tick,
        kind="SHIPMENT_DISPATCHED",
        country=exporter.code,
        country2=importer.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={
            "origin": exporter.code,
            "dest": importer.code,
            "commodity": commodity,
            "qty": qty,
            "carrier": carrier,
            "price_veri": price_veri,
            "base_price_veri": base_price_veri,
            "tariff_rate": tariff_rate,
            "buyer_pool": buyer_pool,
            "base_cost": base_cost,
            "tariff_duty": tariff_duty,
            "cost": cost,
            "proceeds": proceeds,
            "arrive_tick": arrive_tick,
            "relief": int(relief),
        },
        ledger=tuple(ledger),
        stat_deltas=tuple(deltas),
        severity=0,
    )
    for entry in event.ledger:
        apply_to_world(world, entry)
    structural.run(world, rng, event)
    return event


def _dispatch_structural(world: World, rng: Rng, event: Event) -> None:
    """Create the Shipment from the recorded payload and assign its id from shipment_seq.
    Writes the id back onto the payload so the replay handler recreates the same object."""
    p = event.payload
    shipment_id = f"SHIP{world.shipment_seq}"
    world.shipment_seq += 1
    p["shipment_id"] = shipment_id
    world.shipments.append(_shipment_from_payload(shipment_id, event.id, event.tick, p))
    record_structural_effect(
        event,
        target=shipment_id,
        metric="shipment",
        before="absent",
        after="in_transit",
    )


def _replay_dispatch(world: World, event: Event) -> None:
    shipment_id = event.payload.get("shipment_id")
    if not isinstance(shipment_id, str):
        return
    world.shipment_seq = max(world.shipment_seq, _seq_of(shipment_id) + 1)
    world.shipments.append(
        _shipment_from_payload(shipment_id, event.id, event.tick, event.payload)
    )


def _shipment_from_payload(
    shipment_id: str,
    dispatch_event_id: int,
    depart_tick: int,
    p: dict[str, float | int | str],
) -> Shipment:
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
        dispatch_event_id=dispatch_event_id,
        depart_tick=depart_tick,
        arrive_tick=int(p["arrive_tick"]),
        relief=bool(p["relief"]),
    )


def _remove_shipment(world: World, shipment_id: str) -> None:
    world.shipments = [s for s in world.shipments if s.id != shipment_id]


def _arrive(world: World, rng: Rng, ship: Shipment) -> Event:
    """Emit SHIPMENT_ARRIVED: goods land in importer stock (clamped to ceiling), exporter is
    paid leg-2, the Shipment is removed."""
    importer = world.country(ship.dest)
    ceiling = importer.commodity_need[ship.commodity] * config.STARTING_COMMODITY_STOCK_DAYS
    current = importer.commodity_stock[ship.commodity]
    new_stock = min(ceiling, current + ship.qty)
    delta = new_stock - current

    ledger: list[LedgerEntry] = []
    if ship.proceeds > 0:
        Ledger.transfer(
            ledger, f"fx:{ship.origin}", f"{ship.origin}.corporates", ship.proceeds, ship.origin
        )
    deltas: list[StatDelta] = []
    if delta != 0:
        apply_commodity_stat(world, deltas, ship.dest, "stock", ship.commodity, delta)

    event = world.log.append(
        tick=world.tick,
        kind="SHIPMENT_ARRIVED",
        country=ship.dest,
        country2=ship.origin,
        parent_id=ship.dispatch_event_id,
        depth=1,
        is_intervention=False,
        payload={"shipment_id": ship.id, "commodity": ship.commodity, "qty": ship.qty},
        ledger=tuple(ledger),
        stat_deltas=tuple(deltas),
        severity=0,
    )
    for entry in event.ledger:
        apply_to_world(world, entry)
    structural.run(world, rng, event)
    return event


def _arrive_structural(world: World, rng: Rng, event: Event) -> None:
    shipment_id = event.payload.get("shipment_id")
    if isinstance(shipment_id, str):
        _remove_shipment(world, shipment_id)
        record_structural_effect(
            event,
            target=shipment_id,
            metric="shipment",
            before="in_transit",
            after="arrived",
        )


def _replay_arrive(world: World, event: Event) -> None:
    shipment_id = event.payload.get("shipment_id")
    if isinstance(shipment_id, str):
        _remove_shipment(world, shipment_id)


def _lose_structural(world: World, rng: Rng, event: Event) -> None:
    shipment_id = event.payload.get("shipment_id")
    if isinstance(shipment_id, str):
        _remove_shipment(world, shipment_id)
        record_structural_effect(
            event,
            target=shipment_id,
            metric="shipment",
            before="in_transit",
            after="lost",
        )


def _replay_lose(world: World, event: Event) -> None:
    shipment_id = event.payload.get("shipment_id")
    if isinstance(shipment_id, str):
        _remove_shipment(world, shipment_id)


structural.register_structural("SHIPMENT_DISPATCHED", _dispatch_structural)
structural.register_replay("SHIPMENT_DISPATCHED", _replay_dispatch)
structural.register_structural("SHIPMENT_ARRIVED", _arrive_structural)
structural.register_replay("SHIPMENT_ARRIVED", _replay_arrive)
structural.register_structural("SHIPMENT_LOST", _lose_structural)
structural.register_replay("SHIPMENT_LOST", _replay_lose)


def _season_ticks(world: World) -> int:
    """The trailing window a convoy report calls "this season" -- a literal quarter of the
    year, never shorter than one reporting interval."""
    return max(config.CONVOY_REPORT_INTERVAL_TICKS, world.settings.ticks_per_year // 4)


def _convoy_reports(world: World) -> list[Event]:
    """Report every sinking a destination has suffered SINCE ITS LAST REPORT.

    A single sunk convoy is a fact; a run of them is news. SHIPMENT_LOST keeps carrying the
    economic bite (its stability hit, its stranded money) and stays out of the feed; this is
    what a reader sees.

    The count is anchored to the destination's own history, not to the calendar. Counting
    only within the current window asks whether a harbour lost two ships in THESE twelve
    ticks, which is a question about the clock rather than about the war -- so a steady drip
    of one sinking per window answers no forever and is never reported at all. Measured on
    two seeds, the mode is exactly one sinking per window (11 of 17 non-empty windows on one
    seed, 9 of 10 on another), which left 44% and 82% of all sinkings permanently
    unreportable. Counting since the last report instead lets a drip accumulate and surface
    on its second ship, while a burst still reports no more than once per interval per
    destination and the minimum still guarantees the plural the headline writer needs.

    Derived entirely from the event log, so it needs no new World state and no replay
    handler: the report records no ledger and no stat_delta, which makes it a no-op for
    Timeline.world_at, exactly like the FAMINE/RELATION_SHIFT direct-append kinds. It is
    deliberately NOT in EVENT_REGISTRY -- it has no spec effects and no consequences, and
    registering it would only promise an exogenous root it can never have.
    """
    interval = config.CONVOY_REPORT_INTERVAL_TICKS
    if world.tick <= 0 or world.tick % interval != 0:
        return []
    # One query for both kinds: the sinkings, and the reports that already spoke for some of
    # them. The season bounds the lookback, so a destination whose last report has scrolled
    # out of it reports its whole season rather than its whole history.
    # Clamped at 0: a young world has no season behind it, and `since_tick` rides on the
    # payload, so a negative tick would reach the client as a date before the world began.
    season_start = max(0, world.tick - _season_ticks(world))
    history = world.log.events_of_kinds_between(
        ("SHIPMENT_LOST", "CONVOY_LOSSES"), season_start, world.tick
    )
    if not history:
        return []

    season_by_dest: dict[str, list[Event]] = {}
    reported_to: dict[str, int] = {}
    for event in history:
        dest = event.country
        if dest is None:
            continue
        if event.kind == "CONVOY_LOSSES":
            reported_to[dest] = max(reported_to.get(dest, season_start), event.tick)
        else:
            season_by_dest.setdefault(dest, []).append(event)

    events: list[Event] = []
    for dest in sorted(season_by_dest):  # stable order: the log is the only input
        since = reported_to.get(dest, season_start)
        unreported = [loss for loss in season_by_dest[dest] if loss.tick > since]
        if len(unreported) < config.CONVOY_REPORT_MIN_LOSSES:
            continue
        events.append(
            _convoy_report(world, dest, unreported, len(season_by_dest[dest]), since)
        )
    return events


def _convoy_report(
    world: World, dest: str, losses: list[Event], season_total: int, since_tick: int
) -> Event:
    counts: dict[str, int] = {}
    qty = 0.0
    relief = False
    for loss in losses:
        name = loss.payload.get("commodity")
        if isinstance(name, str) and name in commodities.ORDER:
            counts[name] = counts.get(name, 0) + 1
        amount = loss.payload.get("qty")
        if isinstance(amount, (int, float)):
            qty += float(amount)
        relief = relief or loss.payload.get("relief") in (True, 1)
    # Most-lost cargo, ties broken by the canonical commodity order so the choice is stable.
    modal = min(counts, key=lambda n: (-counts[n], commodities.ORDER.index(n)), default="")
    manifest = ",".join(n for n in commodities.ORDER if n in counts)

    ids = sorted(loss.id for loss in losses)
    payload: dict[str, float | int | str] = {
        "count": len(losses),
        "recent_total": season_total,
        "commodities": manifest,
        "relief": int(relief),
        "qty": qty,
        "since_tick": since_tick,
    }
    if modal:
        payload["commodity"] = modal
    return world.log.append(
        tick=world.tick,
        kind="CONVOY_LOSSES",
        country=dest,
        country2=None,  # the raiders have no single flag; the report is about the harbour
        parent_id=ids[0],
        depth=max(loss.depth for loss in losses) + 1,
        is_intervention=False,
        payload=payload,
        severity=1,
        causes=tuple(CauseLink(event_id, "trigger", "convoy lost") for event_id in ids[1:]),
    )


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    # Snapshot + numeric-id order: RNG rolls (interdiction) must fire in a stable order, and
    # emit handlers mutate world.shipments underneath us.
    for ship in sorted(world.shipments, key=lambda s: _seq_of(s.id)):
        # A shipment whose exporter was since annexed is still in transit and still
        # deliverable -- the goods left the dock before the flag changed.
        if ship.carrier == "sea" and _war_zone(world, ship) and rng.roll(config.SEA_INTERDICTION_P):
            events.append(
                cascade.emit_event(
                    world,
                    rng,
                    kind="SHIPMENT_LOST",
                    primary=ship.dest,
                    secondary=ship.origin,
                    parent_id=ship.dispatch_event_id,
                    depth=1,
                    is_intervention=False,
                    payload={
                        "shipment_id": ship.id,
                        "carrier": ship.carrier,
                        "commodity": ship.commodity,
                        "qty": ship.qty,
                        "relief": int(ship.relief),
                    },
                )
            )
            continue
        if ship.arrive_tick <= world.tick:
            events.append(_arrive(world, rng, ship))
    events.extend(_convoy_reports(world))
    return events
