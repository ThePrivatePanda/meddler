"""The commodity registry and CommodityProductionSystem (M10, v2 spec §3)."""

from __future__ import annotations

import pytest

from meddler.engine import cascade, commodities, config
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world


def test_every_ordered_commodity_is_registered() -> None:
    assert tuple(commodities.ORDER) == config.COMMODITY_ORDER
    for name in config.COMMODITY_ORDER:
        assert commodities.commodity(name).name == name


def test_extractive_commodities_match_the_endowment_keys() -> None:
    """The extractive buckets are exactly the ones territory produces -- so each must map
    to an endowment M9 actually seeds, or its output formula reads a missing key."""
    extractive = {c.name for c in commodities.COMMODITIES.values() if c.extractive}
    assert extractive == {"food", "energy", "raw_materials"}
    for name in extractive:
        assert commodities.commodity(name).endowment_key in config.EXTRACTIVE_COMMODITIES


def test_produced_commodities_have_inputs_and_extractive_ones_do_not() -> None:
    for name in config.COMMODITY_ORDER:
        c = commodities.commodity(name)
        if c.extractive:
            assert c.inputs == {}, f"{name} is extracted from territory, not manufactured"
        else:
            assert c.inputs, f"{name} must consume something"


def test_production_chains_are_acyclic_and_only_reference_real_commodities() -> None:
    """A cycle (A needs B needs A) would deadlock production: neither could ever be made.
    Walks each chain to a fixed depth rather than trusting the table by eye."""
    for name in config.COMMODITY_ORDER:
        seen: set[str] = set()
        frontier = [name]
        for _ in range(len(config.COMMODITY_ORDER) + 1):
            nxt: list[str] = []
            for item in frontier:
                for dep in commodities.commodity(item).inputs:
                    assert dep in commodities.COMMODITIES, f"{item} needs unknown {dep}"
                    assert dep != name, f"cycle: {name} transitively needs itself"
                    if dep not in seen:
                        seen.add(dep)
                        nxt.append(dep)
            frontier = nxt
            if not frontier:
                break
        assert not frontier, f"{name}'s input chain is deeper than the commodity count"


def test_commodity_order_is_a_valid_topological_order() -> None:
    """COMMODITY_ORDER is not merely a determinism convention -- it is load-bearing.

    CommodityProductionSystem recomputes produced output in ORDER and reads each
    commodity's inputs as it goes, so every input must already have been recomputed THIS
    tick. Put "consumer" before "manufactured" and consumer silently reads LAST tick's
    manufactured output: a one-tick lag, no error, no other test catches it.
    """
    seen: set[str] = set()
    for name in config.COMMODITY_ORDER:
        for dep in commodities.commodity(name).inputs:
            assert dep in seen, (
                f"COMMODITY_ORDER lists {name} before its input {dep}; production would "
                f"read a stale {dep} every tick"
            )
        seen.add(name)


def test_unknown_commodity_raises() -> None:
    with pytest.raises(KeyError):
        commodities.commodity("unobtainium")


def test_grain_fields_alias_the_food_bucket() -> None:
    """grain_* are kept as the food bucket's public face so that every pre-M10 consumer
    (famine thresholds, trade, god mode, shock events' stat_deltas) keeps working against
    unified data instead of a duplicated second copy that could drift out of sync."""
    world = generate_world(1337, WorldSettings())
    country = world.countries[0]
    assert country.grain_stock == country.commodity_stock["food"]
    assert country.grain_output == country.commodity_output["food"]
    assert country.grain_need == country.commodity_need["food"]


def test_writing_grain_stock_writes_through_to_the_food_bucket() -> None:
    """god.py and every shock event's stat_deltas assign grain_stock via setattr; if the
    alias were read-only or a copy, those writes would silently vanish."""
    world = generate_world(1337, WorldSettings())
    country = world.countries[0]
    country.grain_stock = 123.5
    assert country.commodity_stock["food"] == 123.5
    country.commodity_stock["food"] = 7.0
    assert country.grain_stock == 7.0


def test_every_country_has_every_commodity_seeded() -> None:
    world = generate_world(1337, WorldSettings())
    for country in world.countries:
        for bucket in (country.commodity_output, country.commodity_need, country.commodity_stock):
            assert set(bucket) == set(config.COMMODITY_ORDER)
            for value in bucket.values():
                assert value >= 0.0


def test_world_at_reconstructs_commodity_stock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The M10 instance of the rule that a missing REPLAY_EFFECTS/StatDelta branch makes
    scrubbing silently disagree with the live run. Deliberately drives the real Timeline
    rather than asserting on a hand-built delta -- a hand-built one would pass even if the
    replay branch were missing entirely."""
    from meddler.engine import tickloop
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production
    from meddler.engine.timeline import Timeline

    monkeypatch.setattr(tickloop, "SYSTEMS", [commodity_production.run])
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    world = generate_world(5, settings)
    timeline = Timeline(seed=5, world=world, snapshots={}, rng=Rng(5))
    for _ in range(35):
        timeline.advance()
    live = {c.code: dict(c.commodity_stock) for c in timeline.world.countries}
    replayed_world = timeline.world_at(35)
    for code, stocks in live.items():
        assert replayed_world.country(code).commodity_stock == stocks, (
            f"{code}'s commodity stock diverged between the live run and world_at()"
        )


def test_replay_stat_delta_round_trips_a_commodity_delta() -> None:
    from meddler.engine import stats

    world = generate_world(1337, WorldSettings())
    country = world.countries[0]
    entries: list[stats.StatDelta] = []
    before = country.commodity_stock["energy"]
    stats.apply_commodity_stat(world, entries, country.code, "stock", "energy", -4.0)
    assert country.commodity_stock["energy"] == before - 4.0
    assert len(entries) == 1

    country.commodity_stock["energy"] = before  # rewind, then replay the recorded delta
    stats.replay_stat_delta(world, entries[0])
    assert country.commodity_stock["energy"] == before - 4.0


@pytest.mark.parametrize("seed", range(30))
def test_aggregate_supply_meets_aggregate_demand_for_every_commodity(seed: int) -> None:
    """v2 spec §14's worldgen INVARIANT, finally assertable now that demand exists.

    M12 makes money conserved and trade bilateral: if the whole world produces less energy
    than it needs, no amount of trading fixes it -- every country is short forever, the
    market never clears, and the shortage cascade fires permanently for everyone. That is a
    broken world, not a hard one. Asserted across 30 seeds because it must hold for EVERY
    world, not the median one -- this is an invariant, not a tendency.

    A FLOOR, not a band. See config.AGGREGATE_BALANCE_MIN for why an upper bound is both
    unsatisfiable (the per-seed spread exceeds any useful band) and measuring the wrong
    thing (dispersion drives trade, not the aggregate ratio).

    NOTE this test is only meaningful for the three EXTRACTIVE commodities, whose ratio
    varies per seed with the endowment draws. The three PRODUCED commodities derive from
    gdp_tick, so their ratio varies too -- but if you ever find a commodity whose ratio is
    IDENTICAL across all 30 seeds, that is a bug, not a pass: it means its output ignores
    every per-country input. An earlier draft had exactly that, and three of these six
    assertions were tautologies. test_produced_output_varies_across_countries guards it.
    """
    world = generate_world(seed, WorldSettings())
    for name in config.COMMODITY_ORDER:
        supply = sum(c.commodity_output[name] for c in world.countries)
        demand = sum(c.commodity_need[name] for c in world.countries)
        assert demand > 0, f"seed {seed}: {name} has no demand at all"
        ratio = supply / demand
        assert ratio >= config.AGGREGATE_BALANCE_MIN, (
            f"seed {seed}: {name} world supply/demand = {ratio:.3f}, below the "
            f"{config.AGGREGATE_BALANCE_MIN} floor -- trade can never clear"
        )


def test_produced_output_varies_across_countries() -> None:
    """Guards the failure mode that made an earlier draft's §14 assertions tautologies:
    if produced output is population * a_constant, every country has an identical
    supply/demand ratio, nobody is ever short, and CONSUMER_SHORTAGE/TECH_STAGNATION become
    unreachable by construction. Produced output MUST inherit gdp_tick's per-country
    variance (v2 spec §3's table)."""
    world = generate_world(1337, WorldSettings())
    for name in ("manufactured", "consumer", "high_tech"):
        ratios = {
            c.code: c.commodity_output[name] / c.commodity_need[name] for c in world.countries
        }
        spread = max(ratios.values()) / min(ratios.values())
        assert spread > 1.15, (
            f"{name} supply/demand ratio is nearly identical across countries "
            f"(spread {spread:.3f}) -- its output is ignoring per-country economics"
        )


def test_the_world_is_interdependent() -> None:
    """v2 spec §2: "nobody is self-sufficient in everything" — the property that gives
    trade something to do.

    Asserts this at the WORLD level, which is the level §2 states it at. An earlier draft
    of this test demanded that EVERY country be short of something; that is a stronger
    claim than the spec makes and than reality supports. MEASURED: 80% of countries
    (192/240 over 30 seeds) are short in at least one commodity, while ~20% are surplus in
    all six. Those 20% are high-GDP-tier countries that also drew decent endowments — i.e.
    self-sufficient economic giants. That is a legitimate and interesting world state, not a
    bug: they still export their surpluses, so they still trade. Demanding 100% would be
    asserting a fact about the world that simply is not true.

    The 60% floor is well below the measured 80%, so this fails on a real regression (a
    constant drifting until nobody is short of anything) rather than on seed noise.
    """
    short_any = 0
    total = 0
    for seed in range(30):
        world = generate_world(seed, WorldSettings())
        for country in world.countries:
            total += 1
            if any(
                country.commodity_output[n] < country.commodity_need[n]
                for n in config.COMMODITY_ORDER
            ):
                short_any += 1
    fraction = short_any / total
    assert fraction >= 0.60, (
        f"only {fraction:.0%} of countries are short in any commodity — the world is "
        f"too self-sufficient for trade to matter"
    )


@pytest.mark.parametrize("seed", range(10))
def test_every_commodity_has_at_least_one_net_exporter(seed: int) -> None:
    """The flip side: a commodity everyone is short of cannot be traded into balance --
    supply has to exist SOMEWHERE. MEASURED: holds for all 6 commodities across 30 seeds
    (0 violations), so this is an invariant, not a tendency.

    NOTE the converse is deliberately NOT asserted: 5 of 180 seed/commodity pairs have no
    net IMPORTER (everyone is comfortable in that commodity that world). That is fine --
    it just means nobody trades that commodity in that world.
    """
    world = generate_world(seed, WorldSettings())
    for name in config.COMMODITY_ORDER:
        assert any(
            c.commodity_output[name] > c.commodity_need[name] for c in world.countries
        ), f"seed {seed}: nobody exports {name}"


def test_extractive_output_scales_with_endowment() -> None:
    """Territory drives extractive output -- the premise that makes M15's conquest pay.

    Asserted at GENESIS, because extractive output is static after worldgen (see
    test_extractive_output_is_static_across_ticks for why).
    """
    rich = commodities.genesis_output(
        "energy", population=10.0, endowments={"energy": 1.0, "arable": 0.5,
        "raw_materials": 0.5}, gdp_tick=1e6, innovation_mult=1.0, education=50.0,
    )
    poor = commodities.genesis_output(
        "energy", population=10.0, endowments={"energy": 0.1, "arable": 0.5,
        "raw_materials": 0.5}, gdp_tick=1e6, innovation_mult=1.0, education=50.0,
    )
    assert rich == pytest.approx(poor * 10.0)


def test_shockable_extractive_output_is_static_across_ticks() -> None:
    """Shockable extractive output must NOT be recomputed per tick.

    kinds/natural.py's DROUGHT carries {"grain_output": -2.0} and fires from slot 10
    (exogenous). If this system recomputed food output in slot 2, that -2.0 would be erased
    on the next tick having been read by zero systems, silently turning DROUGHT into a
    no-op. Pre-M10, grain_output was static after worldgen and DROUGHT's hit was permanent.
    This test is the guard on that.

    M14 NOTE: this stand-in originally used `energy`, which is now GRID-FED and therefore
    deliberately recomputed each tick (spec §6 lists energy among the power_grid-driven
    commodities). It was retargeted to `raw_materials` -- still extractive, still static,
    still shockable -- so the guard keeps its teeth for every commodity a shock can actually
    reach. `energy`'s own protection is now
    test_no_event_kind_shocks_a_grid_fed_commodity_output, which fails LOUDLY if a future
    kind tries to shock a recomputed bucket, plus the real DROUGHT test below.
    """
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    commodity_production.run(world, Rng(1))
    country.commodity_output["raw_materials"] -= 3.0  # stand in for a shock's stat_delta
    shocked = country.commodity_output["raw_materials"]
    commodity_production.run(world, Rng(1))
    assert country.commodity_output["raw_materials"] == shocked, (
        "a shock's extractive-output delta was erased by a per-tick recompute"
    )


def test_drought_still_dents_food_output_permanently() -> None:
    """The M10 instance of the same guard, exercised through the REAL DROUGHT spec rather
    than a hand-written delta -- the version that would actually catch a regression."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    before = country.grain_output
    cascade.emit_event(
        world, Rng(1), kind="DROUGHT", primary=country.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    assert country.grain_output < before
    dented = country.grain_output
    for _ in range(3):
        commodity_production.run(world, Rng(1))
    assert country.grain_output == dented, "DROUGHT's grain_output hit must persist"


@pytest.mark.parametrize("kind", ["DROUGHT", "LOCUST_SWARM"])
def test_a_crop_shock_dents_food_output_without_erasing_it(kind: str) -> None:
    """A crop shock takes a bounded SHARE of food output, never all of it.

    DROUGHT's -2.0 and LOCUST_SWARM's -3.0 are flat v1 magnitudes, but the largest possible
    genesis food output is 60M x 0.05543 x arable 1.0 = 3.3, and a typical country produces
    well under 2.0. Floored at zero, one drought zeroed a country's farms for the rest of the
    run: food output is static after worldgen, so nothing ever restored it. Seed 7's TEA
    exported food at genesis (1.24 against a need of 0.89), lost all 1.24 to a tick-15
    drought, and sat at stability 0 for ~1,080 of 1,500 ticks.
    """
    world = generate_world(7, WorldSettings())
    country = world.country("TEA")
    before = country.grain_output
    cascade.emit_event(
        world, Rng(1), kind=kind, primary=country.code, secondary=None,
        parent_id=None, depth=0, is_intervention=False, payload={},
    )
    assert country.grain_output < before
    assert country.grain_output > 0.0, f"{kind} erased all of {country.code}'s food output"
    assert country.grain_output >= before * (1.0 - config.CROP_SHOCK_MAX_FRACTION)


def test_produced_output_falls_when_gdp_falls() -> None:
    """v2 spec §3's headline: "GDP falls => exports fall". An earlier draft derived produced
    output from population alone, which made this false and made every country's
    supply/demand ratio identical."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    commodity_production.run(world, Rng(1))
    healthy = country.commodity_output["manufactured"]
    country.gdp_tick *= 0.5
    commodity_production.run(world, Rng(1))
    assert country.commodity_output["manufactured"] < healthy


def test_production_chain_starves_manufactured_when_inputs_are_missing() -> None:
    """v2 spec §3's causal chain: "a country starved of energy can't fully convert its GDP
    into manufactured exports". The whole point of one-hop chains.

    Zeroes energy OUTPUT (not the endowment) because extractive output is static -- setting
    the endowment after genesis would change nothing, and this test would pass for the wrong
    reason.
    """
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    commodity_production.run(world, Rng(1))
    healthy = country.commodity_output["manufactured"]

    country.commodity_output["energy"] = 0.0
    country.commodity_stock["energy"] = 0.0
    commodity_production.run(world, Rng(1))
    assert country.commodity_output["manufactured"] < healthy, (
        "a country with no energy should not manufacture at full throughput"
    )


def test_manufacturing_consumes_energy_from_stock() -> None:
    """Inputs must be genuinely DEBITED, not merely measured.

    Without the debit, energy feeds manufactured AND high_tech simultaneously with no
    accounting that it can only be spent once -- the chain gates nothing and downstream
    shortages become unreachable by construction (measured: 0/240 country-runs).
    """
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    # Force the flow to be insufficient so the debit must land on the buffer.
    country.commodity_output["energy"] = 0.0
    before = country.commodity_stock["energy"]
    assert before > 0
    commodity_production.run(world, Rng(1))
    assert country.commodity_stock["energy"] < before, "manufacturing consumed no energy"


def test_commodity_production_events_are_ambient_roots() -> None:
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=3))
    events = commodity_production.run(world, Rng(1))
    assert events
    for event in events:
        assert event.parent_id is None
        assert event.depth == 0
        assert event.is_intervention is False
        assert event.severity == 0


def test_commodity_production_emits_in_sorted_country_order() -> None:
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=4))
    events = commodity_production.run(world, Rng(1))
    codes = [e.country for e in events]
    assert codes == sorted(codes)


def test_commodity_production_consumes_no_rng() -> None:
    """Production is a deterministic function of state. If it ever draws, every downstream
    system's stream shifts and the golden breaks for a reason nobody will find quickly."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=3))
    rng = Rng(99)
    before = rng.get_state()
    commodity_production.run(world, rng)
    assert rng.get_state() == before


def test_a_deficit_country_draws_down_stock() -> None:
    """Non-food only: nothing trades energy yet, so a structural deficit eats the buffer."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    country.commodity_output["energy"] = 0.0
    country.commodity_need["energy"] = 5.0
    before = country.commodity_stock["energy"]
    commodity_production.run(world, Rng(1))
    assert country.commodity_stock["energy"] < before


def test_food_stock_is_folded_by_production_like_every_commodity() -> None:
    """M12/M13 CLOSED the M10 food/non-food asymmetry: food no longer settles in cash via an
    abstract world market -- it trades bilaterally as physical shipments, so its stock is
    folded by production (output - need) exactly like energy or raw_materials. A severe food
    deficit therefore drains grain_stock here, which is what drives FAMINE through the same
    mechanism as an energy shortage."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=3))
    deficit_country = world.countries[0]
    deficit_country.commodity_output["food"] = 0.0  # a severe structural food deficit
    need = deficit_country.commodity_need["food"]
    before = deficit_country.commodity_stock["food"]
    commodity_production.run(world, Rng(1))
    # stock dropped by exactly the unmet need (0 output - need), floored at 0
    assert deficit_country.commodity_stock["food"] == pytest.approx(max(0.0, before - need))


def test_surplus_stock_stops_at_the_ceiling() -> None:
    """Without a ceiling a surplus country accumulates without bound and can never be
    shocked into shortage -- the mechanism that decides whether shortages stay reachable."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    # raw_materials, not energy: M14 made energy grid-fed and therefore recomputed every
    # tick, so a hand-set energy output would be overwritten before the fold ever saw it.
    # The ceiling behaviour under test is identical for every commodity.
    country.commodity_output["raw_materials"] = country.commodity_need["raw_materials"] * 5
    ceiling = country.commodity_need["raw_materials"] * config.STARTING_COMMODITY_STOCK_DAYS
    for _ in range(50):
        commodity_production.run(world, Rng(1))
    assert country.commodity_stock["raw_materials"] == pytest.approx(ceiling)


def test_energy_shortage_fires_and_hysteresis_holds() -> None:
    """Same armed/re-arm contract as every other threshold: fire once on crossing, stay
    quiet while it persists, re-arm only after recovering past the margin."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import thresholds

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    country.commodity_stock["energy"] = 0.0
    country.commodity_need["energy"] = 10.0

    fired = [e for e in thresholds.run(world, Rng(1)) if e.kind == "ENERGY_SHORTAGE"]
    assert len(fired) == 1
    again = [e for e in thresholds.run(world, Rng(1)) if e.kind == "ENERGY_SHORTAGE"]
    assert not again, "an armed threshold must not re-fire while still triggered"

    country.commodity_stock["energy"] = 10.0 * config.COMMODITY_SHORTAGE_RECOVERY_DAYS + 1
    thresholds.run(world, Rng(1))
    country.commodity_stock["energy"] = 0.0
    refired = [e for e in thresholds.run(world, Rng(1)) if e.kind == "ENERGY_SHORTAGE"]
    assert len(refired) == 1, "must re-fire after recovering past the margin and relapsing"


def test_shortage_kinds_are_registered_and_fire_through_cascade() -> None:
    """The M7.4 lesson: a registered kind fired via a bare world.log.append silently skips
    its own stat_deltas and its consequence scheduling."""
    from meddler.engine.registry import EVENT_REGISTRY

    for kind in (
        "ENERGY_SHORTAGE",
        "MATERIALS_SHORTAGE",
        "MANUFACTURING_SLUMP",
        "CONSUMER_SHORTAGE",
        "TECH_STAGNATION",
    ):
        assert kind in EVENT_REGISTRY, f"{kind} must be registered"
        assert EVENT_REGISTRY[kind].stat_deltas, f"{kind} must actually bite"


def test_energy_shortage_dents_stability() -> None:
    from meddler.engine.rng import Rng
    from meddler.engine.systems import thresholds

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    country.commodity_stock["energy"] = 0.0
    country.commodity_need["energy"] = 10.0
    before = country.stability
    thresholds.run(world, Rng(1))
    assert country.stability < before


# --- M14: power_grid -> production multiplier (v2 spec §6) ----------------------------


def _grid_world(condition: float):
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    for c in world.countries:
        c.infrastructure.power_grid.condition = condition
    commodity_production.run(world, Rng(1))
    return world.countries[0]


def test_a_degraded_grid_cuts_every_grid_fed_commodity() -> None:
    healthy = _grid_world(1.0)
    blacked_out = _grid_world(0.0)
    for name in config.GRID_FED_COMMODITIES:
        assert blacked_out.commodity_output[name] < healthy.commodity_output[name], name


def test_a_degraded_grid_does_not_touch_farm_or_mine_output() -> None:
    """spec §6 lists energy/manufactured/consumer/high_tech. Food is agriculture and
    raw_materials is mining -- neither is grid-fed, and both must stay static so shocks
    against them persist."""
    healthy = _grid_world(1.0)
    blacked_out = _grid_world(0.0)
    for name in ("food", "raw_materials"):
        assert blacked_out.commodity_output[name] == pytest.approx(
            healthy.commodity_output[name]
        )


def test_full_grid_condition_is_a_no_op_multiplier() -> None:
    """The multiplier must be exactly 1.0 at full condition, or M10's calibrated aggregate
    supply/demand floor would shift under a perfectly healthy world."""
    from meddler.engine import assets

    healthy = _grid_world(1.0)
    assert assets.production_multiplier(healthy) == pytest.approx(1.0)


def test_energy_output_tracks_the_grid_and_is_recomputed() -> None:
    """energy is extractive (territory-driven) but IS grid-fed per spec §6, so unlike food
    and raw_materials it is recomputed every tick."""
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    world = generate_world(1337, WorldSettings(starting_country_count=2))
    country = world.countries[0]
    commodity_production.run(world, Rng(1))
    before = country.commodity_output["energy"]
    country.infrastructure.power_grid.condition = 0.2
    commodity_production.run(world, Rng(1))
    assert country.commodity_output["energy"] < before


def test_no_event_kind_shocks_a_grid_fed_commodity_output() -> None:
    """THE guard for M14's recompute decision.

    Grid-fed commodities are recomputed from formula every tick, so any event stat_delta
    writing their OUTPUT would be silently erased the following tick -- exactly the DROUGHT
    bug the static-extractive rule exists to prevent. Nothing writes these today (only
    `grain_output`, i.e. food); this test makes a future attempt fail loudly here instead of
    silently becoming a no-op in the simulation.
    """
    from meddler.engine.registry import EVENT_REGISTRY

    forbidden = {f"commodity:output:{name}" for name in config.GRID_FED_COMMODITIES}
    forbidden |= {f"{name}_output" for name in config.GRID_FED_COMMODITIES}
    forbidden |= {"energy_output", "power_output"}  # plausible aliases next to grain_output
    offenders = [
        (spec.kind, stat)
        for spec in EVENT_REGISTRY.values()
        for stat in spec.stat_deltas
        if stat in forbidden
    ]
    assert offenders == [], (
        f"these kinds shock a grid-fed (per-tick recomputed) commodity's output, which "
        f"commodity_production would erase next tick: {offenders}"
    )


def test_recompute_erases_output_shocks_and_static_commodities_keep_them() -> None:
    """The MECHANISM behind the registry guard above, asserted directly.

    String-matching stat names only catches spellings someone thought of. This asserts the
    actual property for EVERY commodity: a hand-written output delta survives on a static
    bucket and is erased on a recomputed one -- so if the recomputed SET ever changes, the
    contract that guard protects is re-checked automatically.
    """
    from meddler.engine.rng import Rng
    from meddler.engine.systems import commodity_production

    recomputed = set(commodity_production._recomputed_names())
    assert recomputed, "nothing is recomputed -- the guard would be vacuous"
    for name in config.COMMODITY_ORDER:
        world = generate_world(1337, WorldSettings(starting_country_count=2))
        country = world.countries[0]
        commodity_production.run(world, Rng(1))
        country.commodity_output[name] -= 1.0
        shocked = country.commodity_output[name]
        commodity_production.run(world, Rng(1))
        if name in recomputed:
            assert country.commodity_output[name] != shocked, (
                f"{name} is recomputed, so an output shock against it is silently erased -- "
                f"no event kind may write it (see the registry guard)"
            )
        else:
            assert country.commodity_output[name] == shocked, (
                f"{name} is static, so an output shock against it MUST persist (DROUGHT)"
            )
