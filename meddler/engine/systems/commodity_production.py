"""CommodityProductionSystem -- per-tick commodity output, consumption, and stock (M10).

v2 spec §3. Ambient system in the production.py/trade.py mould: it emits one unregistered
COMMODITY_PRODUCTION event per country per tick carrying its stat_deltas, and no narrative
events at all (the shortage kinds in kinds/commodities.py own those).

Consumes NO RNG: output is a deterministic function of endowments, population and input
availability. Runs immediately after ProductionSystem (which sets gdp_tick) and before
TradeSystem (which will, from M12, read this tick's surpluses/deficits).
"""

from __future__ import annotations

from meddler.engine import assets, commodities, config
from meddler.engine.events import Event, StatDelta
from meddler.engine.model import Country, CountryStatus, World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_commodity_stat


def _consume_inputs(
    world: World, deltas: list[StatDelta], country: Country, name: str, target: float
) -> float:
    """Debit `name`'s inputs and return the output actually achievable, in [0, target].

    Two things happen here that MUST happen together (see the plan's design constraint 6):
    the scarcest input BINDS (a factory with ore but no power builds nothing -- hence min,
    not mean), and every unit used is genuinely REMOVED from stock. Without the debit,
    energy would feed manufacturing and high-tech simultaneously with no accounting, the
    chain would gate nothing, and downstream shortages would be unreachable.

    Requirement is a FLOW against a FLOW: `target * per_unit` units of input per tick, drawn
    from this tick's input output plus whatever buffer exists. Comparing a flow against a
    stock is a units error that silently pins availability at 1.0 forever.
    """
    spec = commodities.commodity(name)
    if not spec.inputs or target <= 0:
        return target
    achievable = target
    for input_name in sorted(spec.inputs):  # sorted: pins the debit order
        per_unit = spec.inputs[input_name]
        required = target * per_unit
        if required <= 0:
            continue
        available = country.commodity_output[input_name] + max(
            0.0, country.commodity_stock[input_name]
        )
        if available < required:
            achievable = min(achievable, target * (available / required))
    for input_name in sorted(spec.inputs):
        used = achievable * spec.inputs[input_name]
        if used <= 0:
            continue
        # Spend this tick's output first, then draw down the buffer -- only the buffer is
        # state we can debit (output is recomputed/static), so the debit lands on stock for
        # whatever the flow could not cover.
        from_stock = max(0.0, used - country.commodity_output[input_name])
        if from_stock > 0:
            drawn = min(from_stock, max(0.0, country.commodity_stock[input_name]))
            if drawn > 0:
                apply_commodity_stat(world, deltas, country.code, "stock", input_name, -drawn)
    return achievable


def _recomputed_names() -> tuple[str, ...]:
    """Commodities whose output is re-derived every tick, in COMMODITY_ORDER (so energy is
    refreshed before the commodities that consume it).

    = produced (industry, always derived) + grid-fed extractives (M14: energy). food and
    raw_materials stay STATIC after worldgen so shocks against them persist -- see
    test_shockable_extractive_output_is_static_across_ticks.
    """
    return tuple(
        name
        for name in commodities.ORDER
        if not commodities.commodity(name).extractive or name in config.GRID_FED_COMMODITIES
    )


def _target_output(country: Country, name: str) -> float:
    """Unconstrained target, before input availability. THE formula lives in
    commodities.genesis_output so genesis and tick 1 cannot disagree.

    M14 applies the power_grid multiplier HERE rather than inside genesis_output, for the
    same reason input availability is applied by the caller: genesis_output states POTENTIAL
    output, which is what M10's §14 aggregate supply/demand floor is calibrated against.
    Folding a genesis-condition multiplier (0.7-0.9) into it would silently invalidate that
    calibration.
    """
    target = commodities.genesis_output(
        name,
        population=country.population,
        endowments=country.endowments,
        gdp_tick=country.gdp_tick,
        innovation_mult=country.innovation_mult,
        education=country.education,
    )
    if name in config.GRID_FED_COMMODITIES:
        target *= assets.production_multiplier(country)
    return target


def _target_need(country: Country, name: str) -> float:
    """Population-coupled demand (M15).

    M15 adds recurring strike casualties, so leaving genesis demand frozen would turn a
    smaller population into a permanent false shortage. Re-derive demand every tick from
    the same per-capita table worldgen uses.
    """
    return country.population * config.COMMODITY_NEED_PER_CAPITA[name]


def _occupation_tribute(
    world: World,
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    """Return outgoing/incoming extractive flow keyed by (country, commodity).

    A fixed share of an occupied country's gross endowment output is redirected to its
    active occupier. The two maps feed the same stock-balance equation, so the quantity
    removed from the occupied country exactly equals the quantity credited to the occupier
    before each side's existing stock ceiling/floor policy is applied.
    """
    outgoing: dict[tuple[str, str], float] = {}
    incoming: dict[tuple[str, str], float] = {}
    share = config.OCCUPATION_EXTRACTIVE_TRIBUTE_SHARE
    for occupied in world.living_countries():
        if occupied.status != CountryStatus.OCCUPIED or occupied.occupied_by is None:
            continue
        occupier = world.country(occupied.occupied_by)
        if occupier.status != CountryStatus.ACTIVE:
            continue
        for name in commodities.extractive_names():
            qty = max(0.0, occupied.commodity_output[name]) * share
            if qty <= 0.0:
                continue
            outgoing[(occupied.code, name)] = qty
            key = (occupier.code, name)
            incoming[key] = incoming.get(key, 0.0) + qty
    return outgoing, incoming


def run(world: World, rng: Rng) -> list[Event]:
    # Two deterministic phases are required for M15 occupation tribute: all current-tick
    # outputs must exist before tribute flows are calculated, regardless of whether the
    # occupied country sorts before or after its occupier.
    countries = world.living_countries()
    deltas_by_country: dict[str, list[StatDelta]] = {c.code: [] for c in countries}

    # Phase 1: population-linked need and current output/input consumption.
    for country in countries:
        deltas = deltas_by_country[country.code]
        for name in commodities.ORDER:
            target_need = _target_need(country, name)
            current_need = country.commodity_need[name]
            if target_need != current_need:
                apply_commodity_stat(
                    world, deltas, country.code, "need", name, target_need - current_need
                )

        # Produced commodities + the grid-fed extractive (M14: energy). food/raw_materials
        # stay static after worldgen and move only by shocks -- recomputing them here would
        # silently erase DROUGHT's grain_output delta before any system reads it.
        for name in _recomputed_names():
            target = _target_output(country, name)
            achieved = _consume_inputs(world, deltas, country, name, target)
            apply_commodity_stat(
                world, deltas, country.code, "output", name,
                achieved - country.commodity_output[name],
            )

    outgoing_tribute, incoming_tribute = _occupation_tribute(world)

    # Phase 2: production/consumption balance plus M15 tribute. Stock remains a bounded
    # shock buffer; incoming tribute follows the same ceiling policy as domestic surplus.
    for country in countries:
        deltas = deltas_by_country[country.code]
        for name in commodities.ORDER:
            balance = country.commodity_output[name] - country.commodity_need[name]
            balance -= outgoing_tribute.get((country.code, name), 0.0)
            balance += incoming_tribute.get((country.code, name), 0.0)
            ceiling = country.commodity_need[name] * config.STARTING_COMMODITY_STOCK_DAYS
            current = country.commodity_stock[name]
            new_stock = max(0.0, min(ceiling, current + balance))
            if new_stock != current:
                apply_commodity_stat(world, deltas, country.code, "stock", name, new_stock - current)

    events: list[Event] = []
    for country in countries:
        deltas = deltas_by_country[country.code]
        if not deltas:
            continue
        events.append(
            world.log.append(
                tick=world.tick,
                kind="COMMODITY_PRODUCTION",
                country=country.code,
                country2=None,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={},
                stat_deltas=tuple(deltas),
                severity=0,
            )
        )
    return events
