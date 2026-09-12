"""PoliticsSystem: calendar elections, coup rolls, leader changes, conquest/absorption
rolls (including M7.3's annexation/liberation completion). PROPOSAL §6.1 slot 9, §6.6.3,
§6.8.

None of §6.6.3's "p=varies (incumbent traits, stability)" (elections, coups) or §6.8's
occupation/annexation/liberation triggers (stat gates given exactly; no roll
probabilities) are numeric formulas in PROPOSAL -- the constants used here live in
config.py's "PoliticsSystem"/"M7.3" sections and are documented tuning candidates
(docs/design-decisions.md).

LEADER_CHANGE fires from two distinct triggers but is replaced by exactly one piece of
code (the structural handler below), registered once by kind:
  - Direct: an election decides "incumbent changes" here, using the trait/stability
    formula below, and this system fires LEADER_CHANGE immediately as ELECTION's child
    (§6.6.3's per-election formula isn't expressible as a single ConsequenceRule.base_p,
    so ELECTION's registered EventSpec carries no `consequences` -- see kinds/politics.py).
  - Indirect: COUP's (and INTERVENE_ASSASSINATE's) registered ConsequenceRule schedules a
    delayed LEADER_CHANGE through the ordinary generic cascade queue (consequence.py); when
    it fires, cascade.emit_event calls the exact same structural handler.

Both paths converge on cascade.emit_event, which (per M3.2) calls structural.run() after
applying stat_deltas/pool_transfers -- see engine/structural.py for why this dispatch
exists instead of an if/elif chain in cascade.py. M7.3 adds structural handlers for
WAR_DECLARED (populates at_war_with + sets relation -90, §5.2 -- this is what finally
makes OCCUPATION_BEGIN's own gate organically reachable, closing a gap M3.1/M3.2 left
open) and PEACE (clears at_war_with; also replaces god.py's own duplicate copy of this
logic, since it now fires generically for every PEACE event), plus ANNEXATION/
OCCUPATION_END for the conquest chain's terminal states. run()'s section 5 (M7.4) is
what actually fires PEACE organically once both sides' stability recovers -- until then
it only fired via god_peace or as a rare cascade consequence, which is why M7.1's
catalog-coverage test caught it as unreachable.
"""

from __future__ import annotations

from meddler.engine import assets, cascade, commodities, config, structural, worldgen
from meddler.engine.events import Event, record_structural_effect
from meddler.engine.model import Bloc, Country, CountryStatus, World
from meddler.engine.registry import world_rule_allows
from meddler.engine.rng import Rng


def _election_change_probability(country: Country) -> float:
    p = config.ELECTION_BASE_CHANGE_P
    if country.stability < config.ELECTION_LOW_STABILITY_THRESHOLD:
        p += config.ELECTION_LOW_STABILITY_BONUS
    if "corrupt" in country.leader.traits:
        p += config.ELECTION_CORRUPT_PENALTY
    if "reformist" in country.leader.traits or "technocrat" in country.leader.traits:
        p -= config.ELECTION_GOOD_TRAIT_BONUS
    return min(1.0, max(0.0, p))


def _coup_probability(country: Country) -> float:
    if country.stability >= config.COUP_STABILITY_THRESHOLD:
        return 0.0
    p = config.COUP_BASE_P
    if "warhawk" in country.leader.traits or "corrupt" in country.leader.traits:
        p += config.COUP_RISK_TRAIT_BONUS
    if "technocrat" in country.leader.traits or "reformist" in country.leader.traits:
        p -= config.COUP_RISK_TRAIT_PENALTY
    return min(1.0, max(0.0, p))


def _occupation_target(world: World, country: Country) -> str | None:
    """§6.8: the lexically-first at-war partner whose relation is below the occupation
    threshold, or None if no eligible enemy exists. Deterministic, no RNG."""
    candidates = sorted(
        code
        for code in country.at_war_with
        if world.relations.get((min(country.code, code), max(country.code, code)), 0.0)
        < config.OCCUPATION_RELATION_THRESHOLD
    )
    return candidates[0] if candidates else None


def _replace_leader(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for LEADER_CHANGE: swap in a fresh successor. The office's
    title is unchanged (govt_type doesn't change on a mere handover, §6.6.3's LEADER_CHANGE
    is distinct from GOVT_TYPE_CHANGE).

    The outcome is also recorded on event.payload (mutated in place -- Event is frozen but
    its payload dict is not, same precedent as cascade.py's "cascade_clipped"). §4.4's
    world_at replay reconstructs state from stored deltas WITHOUT re-running systems, so it
    never calls structural.run() -- without this record, a scrubbed/forked world would keep
    the pre-change leader forever. Traits are comma-joined since payload values are
    `float | int | str` (no list). See _replay_leader_change below (registered as this
    kind's REPLAY handler, M4.2) for how world_at applies this record deterministically.
    """
    if event.country is None:
        return
    country = world.country(event.country)
    new_name = worldgen.generate_leader_name(rng)
    new_traits = worldgen.sample_two_traits(rng)
    country.leader.name = new_name
    country.leader.traits = new_traits
    event.payload["new_leader_name"] = new_name
    event.payload["new_leader_traits"] = ",".join(new_traits)


def _begin_occupation(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for OCCUPATION_BEGIN: primary becomes occupied by secondary
    (§6.8). Tribute/degradation/annexation-after-90-ticks are M7.3.

    event.country2 already records the occupier (no extra payload key needed) -- see
    _replace_leader's docstring for why this handler's effect must be payload-recorded at
    all. See _replay_occupation_begin below for the replay-side counterpart (M4.2).

    M7.4: also clears the occupied country's at_war_with (same shape as _make_peace,
    including "both sides" bookkeeping and the ended_wars_with payload record for
    replay) -- otherwise StabilitySystem's war penalty (which exceeds the recovery
    bonus, see config.PEACE_BASE_P's comment) keeps draining an occupied country's
    stability forever, and since neither ANNEXATION (needs stability > 15) nor
    LIBERATION_WAR (needs stability > 50) can then ever trigger, occupation became a
    permanent dead end -- found via M7.4's tuning pass: 3/5 sampled runs ended with
    <=1 ACTIVE country out of 8, every OCCUPIED country pinned at stability 0.0.
    Occupation replacing the war with subjugation (rather than leaving it open) is
    also the more sensible reading of what "occupied" means.
    """
    if event.country is None or event.country2 is None:
        return
    occupied = world.country(event.country)
    occupied.status = CountryStatus.OCCUPIED
    occupied.occupied_by = event.country2
    occupied.occupation_start_tick = world.tick
    ended_with = list(occupied.at_war_with)
    for foe_code in ended_with:
        foe = world.country(foe_code)
        if event.country in foe.at_war_with:
            foe.at_war_with.remove(event.country)
    occupied.at_war_with = []
    event.payload["ended_wars_with"] = ",".join(sorted(ended_with))


def _replay_leader_change(world: World, event: Event) -> None:
    """Replay handler for LEADER_CHANGE (M4.2, engine/structural.py's REPLAY_EFFECTS):
    deterministic counterpart to _replace_leader, driven only by the recorded payload --
    never draws a fresh name/traits (world_at must reproduce the LIVE run's leader, not a
    new random one). Hand-built events with no recorded outcome (e.g. some unit tests)
    are tolerated as a no-op rather than raising."""
    if event.country is None:
        return
    name = event.payload.get("new_leader_name")
    traits_csv = event.payload.get("new_leader_traits")
    if not isinstance(name, str) or not isinstance(traits_csv, str):
        return
    country = world.country(event.country)
    country.leader.name = name
    country.leader.traits = traits_csv.split(",")


def _replay_occupation_begin(world: World, event: Event) -> None:
    """Replay handler for OCCUPATION_BEGIN (M4.2): identical effect to the live handler,
    since it needs no RNG in the first place -- event.country2 already is the occupier.
    M7.4: mirrors the live handler's war-clearing via the recorded ended_wars_with
    payload (same pattern as _replay_make_peace)."""
    if event.country is None or event.country2 is None:
        return
    occupied = world.country(event.country)
    occupied.status = CountryStatus.OCCUPIED
    occupied.occupied_by = event.country2
    occupied.occupation_start_tick = event.tick
    ended_with_csv = event.payload.get("ended_wars_with")
    if not isinstance(ended_with_csv, str) or not ended_with_csv:
        return
    for foe_code in ended_with_csv.split(","):
        if foe_code in occupied.at_war_with:
            occupied.at_war_with.remove(foe_code)
        foe = world.country(foe_code)
        if event.country in foe.at_war_with:
            foe.at_war_with.remove(event.country)


def _declare_war(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for WAR_DECLARED (M7.3): populates at_war_with on both sides
    and sets their relation to -90 (§5.2: "War: relation set to -90 on WAR_DECLARED").
    Fires for EVERY WAR_DECLARED regardless of path (exogenous roll, WAR_SPARK's
    consequence, or a future INTERVENE_WAR wiring) since it's a cascade.emit_event hook,
    not embedded in this system's own per-tick roll -- this is what finally makes
    OCCUPATION_BEGIN's own gate (relation < -80 AND at war) organically reachable.

    The relation set is recorded on event.payload rather than routed through
    apply_relation_delta: by the time a structural handler runs, emit_event has already
    built the (frozen) Event, so there is no live StatDelta list left to append to --
    same reason LEADER_CHANGE's outcome lives on payload (_replace_leader's docstring).

    M7.4: no-ops if either side isn't ACTIVE (occupied/annexed/dissolved). Without this,
    an already-OCCUPIED country could be drawn into a fresh WAR_DECLARED (via the
    exogenous root, WAR_SPARK's cascade, or CEASEFIRE's) -- which re-populated
    at_war_with right after _begin_occupation had just cleared it, re-triggering
    StabilitySystem's war penalty and re-deadlocking ANNEXATION/LIBERATION_WAR's own
    stability gates the same way the un-cleared-war bug did (found chasing the same
    tuning-pass symptom: mutual OCCUPIED pairs occupying each other, stability pinned
    at 0 again after the _begin_occupation fix). LIBERATION_WAR is unaffected -- it has
    no structural handler of its own (declarative stat_delta + OCCUPATION_END
    consequence only), so it never reaches this function. The WAR_DECLARED event and
    its declarative stat_deltas still apply even when this no-ops (a "declared but
    doesn't take" war reads as a diplomatic fizzle, not a bug).
    """
    if event.country is None or event.country2 is None:
        return
    a, b = world.country(event.country), world.country(event.country2)
    if a.status != CountryStatus.ACTIVE or b.status != CountryStatus.ACTIVE:
        return
    if event.country2 not in a.at_war_with:
        a.at_war_with.append(event.country2)
    if event.country not in b.at_war_with:
        b.at_war_with.append(event.country)
    code_a, code_b = sorted((event.country, event.country2))
    key = (code_a, code_b)
    before = world.relations.get(key, 0.0)
    world.relations[key] = -90.0
    event.payload["relation_before"] = before
    event.payload["relation_after"] = -90.0
    record_structural_effect(
        event,
        target="relation",
        metric=f"{code_a}:{code_b}",
        before=before,
        after=-90.0,
        delta=-90.0 - before,
    )
    record_structural_effect(
        event, target=a.code, metric="at_war_with", before="absent", after=b.code
    )
    record_structural_effect(
        event, target=b.code, metric="at_war_with", before="absent", after=a.code
    )


def _replay_declare_war(world: World, event: Event) -> None:
    """M7.4: relation_after is only payload-recorded when the live handler actually
    applied the war (see _declare_war's ACTIVE-only guard); its absence is the replay
    signal that this WAR_DECLARED no-op'd, so at_war_with must stay untouched here too."""
    if event.country is None or event.country2 is None:
        return
    after = event.payload.get("relation_after")
    if not isinstance(after, (int, float)):
        return
    a, b = world.country(event.country), world.country(event.country2)
    if event.country2 not in a.at_war_with:
        a.at_war_with.append(event.country2)
    if event.country not in b.at_war_with:
        b.at_war_with.append(event.country)
    code_a, code_b = sorted((event.country, event.country2))
    world.relations[(code_a, code_b)] = float(after)


def _make_peace(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for PEACE (M7.3): clears at_war_with on both sides, for ANY
    PEACE event (organic, consequence-fired, or god.god_peace's intervention path --
    see god.py's updated docstring, which used to duplicate this logic itself)."""
    if event.country is None:
        return
    country = world.country(event.country)
    ended_with = list(country.at_war_with)
    for foe_code in ended_with:
        foe = world.country(foe_code)
        if event.country in foe.at_war_with:
            foe.at_war_with.remove(event.country)
        record_structural_effect(
            event,
            target=event.country,
            metric="at_war_with",
            before=foe_code,
            after="absent",
        )
        record_structural_effect(
            event,
            target=foe_code,
            metric="at_war_with",
            before=event.country,
            after="absent",
        )
    country.at_war_with = []
    event.payload["ended_wars_with"] = ",".join(sorted(ended_with))


def _replay_make_peace(world: World, event: Event) -> None:
    if event.country is None:
        return
    ended_with_csv = event.payload.get("ended_wars_with")
    if not isinstance(ended_with_csv, str) or not ended_with_csv:
        return
    country = world.country(event.country)
    for foe_code in ended_with_csv.split(","):
        if foe_code in country.at_war_with:
            country.at_war_with.remove(foe_code)
        foe = world.country(foe_code)
        if event.country in foe.at_war_with:
            foe.at_war_with.remove(event.country)


def _annex_country(world: World, rng: Rng, event: Event) -> None:
    """M15 annexation capture (v2 spec §8), extending M7.3/M10.

    The annexer receives population, every commodity stock, all territory markers, a
    population-weighted endowment level that preserves the two countries' extractive
    capacity, and the surviving share of every M14 asset class. Produced-industry output is
    not captured; extractive output is recomputed from the newly combined territory.

    Every outcome is payload-recorded after calculation so replay sets exact values without
    re-deriving formulas or depending on current tuning constants.
    """
    if event.country is None or event.country2 is None:
        return
    annexed = world.country(event.country)
    annexer = world.country(event.country2)
    annexed_pop = annexed.population
    annexer_pop = annexer.population
    merged_pop = annexer_pop + annexed_pop

    annexer.population = merged_pop
    annexed.population = 0.0
    event.payload["population_transferred"] = annexed_pop
    event.payload["annexer_population_after"] = merged_pop
    record_structural_effect(
        event,
        target=annexer.code,
        metric="population",
        before=annexer_pop,
        after=merged_pop,
        delta=annexed_pop,
    )
    record_structural_effect(
        event,
        target=annexed.code,
        metric="population",
        before=annexed_pop,
        after=0.0,
        delta=-annexed_pop,
    )

    for name in commodities.ORDER:
        transferred = annexed.commodity_stock[name]
        annexer_stock_before = annexer.commodity_stock[name]
        annexer.commodity_stock[name] = annexer_stock_before + transferred
        annexed.commodity_stock[name] = 0.0
        event.payload[f"commodity_transferred_{name}"] = transferred
        record_structural_effect(
            event,
            target=annexer.code,
            metric=f"commodity:stock:{name}",
            before=annexer_stock_before,
            after=annexer.commodity_stock[name],
            delta=transferred,
        )
        record_structural_effect(
            event,
            target=annexed.code,
            metric=f"commodity:stock:{name}",
            before=transferred,
            after=0.0,
            delta=-transferred,
        )

    # Endowment levels are intensities while output is level × population. A population-
    # weighted merge therefore preserves the combined extractive capacity at capture time.
    for key in config.EXTRACTIVE_COMMODITIES:
        annexer_endowment_before = annexer.endowments[key]
        annexed_endowment_before = annexed.endowments[key]
        if merged_pop > 0.0:
            merged = (
                annexer_endowment_before * annexer_pop
                + annexed_endowment_before * annexed_pop
            ) / merged_pop
        else:
            merged = annexer_endowment_before
        annexer.endowments[key] = merged
        annexed.endowments[key] = 0.0
        event.payload[f"annexer_endowment_after_{key}"] = merged
        record_structural_effect(
            event,
            target=annexer.code,
            metric=f"endowment:{key}",
            before=annexer_endowment_before,
            after=merged,
            delta=merged - annexer_endowment_before,
        )
        record_structural_effect(
            event,
            target=annexed.code,
            metric=f"endowment:{key}",
            before=annexed_endowment_before,
            after=0.0,
            delta=-annexed_endowment_before,
        )

    captured_territories = list(annexed.territories)
    annexer_territory_count = len(annexer.territories)
    annexed_territory_count = len(captured_territories)
    annexer.territories.extend(captured_territories)
    annexed.territories = []
    event.payload["territory_count_captured"] = len(captured_territories)
    record_structural_effect(
        event,
        target=annexer.code,
        metric="territories",
        before=annexer_territory_count,
        after=len(annexer.territories),
        delta=annexed_territory_count,
    )
    record_structural_effect(
        event,
        target=annexed.code,
        metric="territories",
        before=annexed_territory_count,
        after=0,
        delta=-annexed_territory_count,
    )

    for asset_class in assets.all_asset_classes():
        winner_asset = assets.get_asset(annexer.infrastructure, asset_class)
        loser_asset = assets.get_asset(annexed.infrastructure, asset_class)
        winner_count_before = winner_asset.count
        winner_condition_before = winner_asset.condition
        loser_count_before = loser_asset.count
        loser_condition_before = loser_asset.condition
        captured_count = int(loser_asset.count * config.ANNEXATION_ASSET_CAPTURE_SHARE)
        combined_count = winner_asset.count + captured_count
        if combined_count > 0:
            combined_condition = (
                winner_asset.count * winner_asset.condition
                + captured_count * loser_asset.condition
            ) / combined_count
        else:
            combined_condition = 0.0
        winner_asset.count = combined_count
        winner_asset.condition = combined_condition
        loser_asset.count = 0
        loser_asset.condition = 0.0
        event.payload[f"asset_captured_{asset_class}"] = captured_count
        event.payload[f"annexer_asset_count_after_{asset_class}"] = combined_count
        event.payload[f"annexer_asset_condition_after_{asset_class}"] = combined_condition
        for target, metric, before_value, after_value in (
            (annexer.code, f"asset_count:{asset_class}", winner_count_before, combined_count),
            (
                annexer.code,
                f"infra:{asset_class}",
                winner_condition_before,
                combined_condition,
            ),
            (annexed.code, f"asset_count:{asset_class}", loser_count_before, 0),
            (annexed.code, f"infra:{asset_class}", loser_condition_before, 0.0),
        ):
            record_structural_effect(
                event,
                target=target,
                metric=metric,
                before=before_value,
                after=after_value,
                delta=after_value - before_value,
            )

    # Recompute extractive capacity only after captured grid quality has been folded into
    # the annexer's infrastructure, so M14's energy coupling bites in the capture tick.
    for name in commodities.ORDER:
        annexer_need_before = annexer.commodity_need[name]
        annexed_need_before = annexed.commodity_need[name]
        annexer_output_before = annexer.commodity_output[name]
        annexed_output_before = annexed.commodity_output[name]
        annexer_need = merged_pop * config.COMMODITY_NEED_PER_CAPITA[name]
        annexer.commodity_need[name] = annexer_need
        annexed.commodity_need[name] = 0.0
        event.payload[f"annexer_need_after_{name}"] = annexer_need

        if commodities.commodity(name).extractive:
            output = commodities.genesis_output(
                name,
                population=merged_pop,
                endowments=annexer.endowments,
                gdp_tick=annexer.gdp_tick,
                innovation_mult=annexer.innovation_mult,
                education=annexer.education,
            )
            if name in config.GRID_FED_COMMODITIES:
                output *= assets.production_multiplier(annexer)
            annexer.commodity_output[name] = output
            event.payload[f"annexer_output_after_{name}"] = output
        annexed.commodity_output[name] = 0.0
        for target, metric, before_value, after_value in (
            (
                annexer.code,
                f"commodity:need:{name}",
                annexer_need_before,
                annexer.commodity_need[name],
            ),
            (
                annexed.code,
                f"commodity:need:{name}",
                annexed_need_before,
                0.0,
            ),
            (
                annexer.code,
                f"commodity:output:{name}",
                annexer_output_before,
                annexer.commodity_output[name],
            ),
            (
                annexed.code,
                f"commodity:output:{name}",
                annexed_output_before,
                0.0,
            ),
        ):
            record_structural_effect(
                event,
                target=target,
                metric=metric,
                before=before_value,
                after=after_value,
                delta=after_value - before_value,
            )

    innovation_before = annexer.innovation_mult
    annexer.innovation_mult += config.ANNEXATION_GDP_BOOST
    event.payload["annexer_innovation_after"] = annexer.innovation_mult
    annexed.status = CountryStatus.ANNEXED
    record_structural_effect(
        event,
        target=annexer.code,
        metric="innovation_mult",
        before=innovation_before,
        after=annexer.innovation_mult,
        delta=annexer.innovation_mult - innovation_before,
    )
    record_structural_effect(
        event,
        target=annexed.code,
        metric="status",
        before=CountryStatus.OCCUPIED.value,
        after=CountryStatus.ANNEXED.value,
    )


def _replay_annex_country(world: World, event: Event) -> None:
    if event.country is None or event.country2 is None:
        return
    annexed = world.country(event.country)
    annexer = world.country(event.country2)

    annexer_pop_after = event.payload.get("annexer_population_after")
    if isinstance(annexer_pop_after, (int, float)):
        annexer.population = float(annexer_pop_after)
        annexed.population = 0.0

    for name in commodities.ORDER:
        transferred = event.payload.get(f"commodity_transferred_{name}")
        if isinstance(transferred, (int, float)):
            annexer.commodity_stock[name] += float(transferred)
            annexed.commodity_stock[name] = 0.0
        need_after = event.payload.get(f"annexer_need_after_{name}")
        if isinstance(need_after, (int, float)):
            annexer.commodity_need[name] = float(need_after)
            annexed.commodity_need[name] = 0.0
        output_after = event.payload.get(f"annexer_output_after_{name}")
        if isinstance(output_after, (int, float)):
            annexer.commodity_output[name] = float(output_after)
        # The dissolved polity owns no productive flow, extractive or industrial.
        annexed.commodity_output[name] = 0.0

    for key in config.EXTRACTIVE_COMMODITIES:
        endowment_after = event.payload.get(f"annexer_endowment_after_{key}")
        if isinstance(endowment_after, (int, float)):
            annexer.endowments[key] = float(endowment_after)
            annexed.endowments[key] = 0.0

    territory_count = event.payload.get("territory_count_captured")
    if isinstance(territory_count, (int, float)):
        count = max(0, int(territory_count))
        captured = list(annexed.territories[:count])
        annexer.territories.extend(captured)
        annexed.territories = annexed.territories[count:]

    for asset_class in assets.all_asset_classes():
        count_after = event.payload.get(f"annexer_asset_count_after_{asset_class}")
        condition_after = event.payload.get(f"annexer_asset_condition_after_{asset_class}")
        if isinstance(count_after, (int, float)) and isinstance(condition_after, (int, float)):
            winner_asset = assets.get_asset(annexer.infrastructure, asset_class)
            loser_asset = assets.get_asset(annexed.infrastructure, asset_class)
            winner_asset.count = int(count_after)
            winner_asset.condition = float(condition_after)
            loser_asset.count = 0
            loser_asset.condition = 0.0

    innovation_after = event.payload.get("annexer_innovation_after")
    if isinstance(innovation_after, (int, float)):
        annexer.innovation_mult = float(innovation_after)
    annexed.status = CountryStatus.ANNEXED


def _end_occupation(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for OCCUPATION_END (M7.3): an OCCUPIED country returns to ACTIVE.

    Only an occupied one. An occupation can end while the country is already ANNEXED --
    annexation is what an occupation turns into -- and restoring that country to ACTIVE
    brought a state back from the dead: ANNEXATION transfers its whole population to the
    annexer and leaves it at zero, so what returned was a live country with no people, no
    output and no needs, still holding elections and signing treaties. It is also what
    crashed the tick at t777 of seed 1337, because its food need was zero and FXSystem
    divided by it. A garrison withdrawing cannot undo a border being redrawn.
    """
    if event.country is None:
        return
    country = world.country(event.country)
    if country.status != CountryStatus.OCCUPIED:
        return
    country.status = CountryStatus.ACTIVE
    country.occupied_by = None


def _replay_end_occupation(world: World, event: Event) -> None:
    # Same guard as the live handler, or replay would resurrect a country the live tick
    # left annexed and world_at would diverge from prime.
    if event.country is None:
        return
    country = world.country(event.country)
    if country.status != CountryStatus.OCCUPIED:
        return
    country.status = CountryStatus.ACTIVE
    country.occupied_by = None


def _sorted_pair(a: str, b: str) -> tuple[str, str]:
    x, y = sorted((a, b))
    return (x, y)


def _form_alliance(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for ALLIANCE (M11, §7): the pair form or join a bloc, and their
    relation is set to config.ALLIANCE_RELATION_SET (§5.2's "+70 on ALLIANCE"). A country is
    in at most one bloc; formation only ever seeds a fresh pair or adds a bloc-less country
    to the other's bloc (bloc merging is deferred -- see Bloc's docstring). The resulting bloc
    id / member set / relation are payload-recorded so replay reconstructs without re-deriving
    the create-vs-join branch (the WAR_DECLARED relation-payload precedent). ALLIANCE's own
    RELATION_SHIFT consequence is a pure record (kinds/diplomatic.py), so this direct set is
    the sole relation change -- no double count."""
    if event.country is None or event.country2 is None:
        return
    a, b = event.country, event.country2
    ca, cb = world.country(a), world.country(b)
    if ca.status != CountryStatus.ACTIVE or cb.status != CountryStatus.ACTIVE:
        return
    bloc_a, bloc_b = world.bloc_of(a), world.bloc_of(b)
    bloc: Bloc | None
    if bloc_a is not None and bloc_a is bloc_b:
        bloc = bloc_a  # already allied; still (re)set the relation below
    elif bloc_a is not None and bloc_b is None:
        bloc = bloc_a
        bloc.members = sorted([*bloc.members, b])
    elif bloc_b is not None and bloc_a is None:
        bloc = bloc_b
        bloc.members = sorted([*bloc.members, a])
    elif bloc_a is None and bloc_b is None:
        world.bloc_seq += 1
        bloc = Bloc(id=f"BLOC{world.bloc_seq}", members=sorted([a, b]), formed_at_tick=world.tick)
        world.blocs.append(bloc)
    else:
        # both already in DIFFERENT blocs -> merge is deferred; leave state untouched.
        bloc = None
    key = _sorted_pair(a, b)
    before = world.relations.get(key, 0.0)
    world.relations[key] = config.ALLIANCE_RELATION_SET
    event.payload["relation_before"] = before
    event.payload["relation_after"] = config.ALLIANCE_RELATION_SET
    record_structural_effect(
        event,
        target="relation",
        metric=f"{key[0]}:{key[1]}",
        before=before,
        after=config.ALLIANCE_RELATION_SET,
        delta=config.ALLIANCE_RELATION_SET - before,
    )
    if bloc is not None:
        event.payload["bloc_id"] = bloc.id
        event.payload["bloc_members"] = ",".join(bloc.members)
        event.payload["bloc_formed_at"] = bloc.formed_at_tick
        record_structural_effect(
            event,
            target=bloc.id,
            metric="members",
            before="unallied pair",
            after=event.payload["bloc_members"],
        )


def _replay_form_alliance(world: World, event: Event) -> None:
    """RNG-free reconstruction of _form_alliance from payload (§4.4 replay contract)."""
    after = event.payload.get("relation_after")
    if isinstance(after, (int, float)) and event.country and event.country2:
        world.relations[_sorted_pair(event.country, event.country2)] = float(after)
    members_csv = event.payload.get("bloc_members")
    bloc_id = event.payload.get("bloc_id")
    if not isinstance(members_csv, str) or not isinstance(bloc_id, str) or not members_csv:
        return
    members = sorted(members_csv.split(","))
    formed_at = event.payload.get("bloc_formed_at")
    existing = next((bl for bl in world.blocs if bl.id == bloc_id), None)
    if existing is None:
        world.blocs.append(
            Bloc(
                id=bloc_id,
                members=members,
                formed_at_tick=int(formed_at) if isinstance(formed_at, (int, float)) else world.tick,
            )
        )
        # keep bloc_seq monotonic so a later live tick never reuses this id
        seq = int(bloc_id[len("BLOC"):]) if bloc_id.startswith("BLOC") else 0
        world.bloc_seq = max(world.bloc_seq, seq)
    else:
        existing.members = members


def _break_alliance(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for ALLIANCE_BROKEN (M11, §7): event.country defects from the bloc
    it shares with event.country2. A bloc that falls below 2 members dissolves. The pair's
    relation is reset. Payload-recorded for replay."""
    if event.country is None or event.country2 is None:
        return
    a, b = event.country, event.country2
    bloc = world.bloc_of(a)
    key = _sorted_pair(a, b)
    before = world.relations.get(key, 0.0)
    world.relations[key] = config.ALLIANCE_BROKEN_RELATION_SET
    event.payload["relation_before"] = before
    event.payload["relation_after"] = config.ALLIANCE_BROKEN_RELATION_SET
    record_structural_effect(
        event,
        target="relation",
        metric=f"{key[0]}:{key[1]}",
        before=before,
        after=config.ALLIANCE_BROKEN_RELATION_SET,
        delta=config.ALLIANCE_BROKEN_RELATION_SET - before,
    )
    if bloc is None or a not in bloc.members or b not in bloc.members:
        event.payload["departed"] = ""
        event.payload["dissolved"] = "0"
        return
    members_before = ",".join(bloc.members)
    bloc.members = [m for m in bloc.members if m != a]
    event.payload["departed"] = a
    event.payload["bloc_id"] = bloc.id
    if len(bloc.members) < 2:
        world.blocs = [bl for bl in world.blocs if bl.id != bloc.id]
        event.payload["dissolved"] = "1"
        members_after = "dissolved"
    else:
        event.payload["dissolved"] = "0"
        members_after = ",".join(bloc.members)
    record_structural_effect(
        event,
        target=bloc.id,
        metric="members",
        before=members_before,
        after=members_after,
    )


def _replay_break_alliance(world: World, event: Event) -> None:
    after = event.payload.get("relation_after")
    if isinstance(after, (int, float)) and event.country and event.country2:
        world.relations[_sorted_pair(event.country, event.country2)] = float(after)
    departed = event.payload.get("departed")
    bloc_id = event.payload.get("bloc_id")
    if not isinstance(departed, str) or not departed or not isinstance(bloc_id, str):
        return
    bloc = next((bl for bl in world.blocs if bl.id == bloc_id), None)
    if bloc is None:
        return
    bloc.members = [m for m in bloc.members if m != departed]
    if event.payload.get("dissolved") == "1" or len(bloc.members) < 2:
        world.blocs = [bl for bl in world.blocs if bl.id != bloc_id]


def _impose_embargo(world: World, rng: Rng, event: Event) -> None:
    """Structural handler for EMBARGO (M11, §7): record the trade lane as embargoed on
    World.embargoes. The *effect* of a blocked lane (and intra-bloc discounts) is M12's --
    no bilateral lanes exist yet -- so M11 only records the state for M12 to consult; the
    kind's stability/currency bite already rides its TRADE_HALT/CURRENCY_SLIDE consequences."""
    if event.country is None or event.country2 is None:
        return
    pair = _sorted_pair(event.country, event.country2)
    if pair not in world.embargoes:
        world.embargoes.append(pair)
    event.payload["embargo_pair"] = ",".join(pair)
    record_structural_effect(
        event,
        target=f"{pair[0]}:{pair[1]}",
        metric="embargo",
        before="open",
        after="blocked",
    )


def _replay_impose_embargo(world: World, event: Event) -> None:
    csv = event.payload.get("embargo_pair")
    if not isinstance(csv, str) or csv.count(",") != 1:
        return
    a, b = csv.split(",")
    pair = _sorted_pair(a, b)
    if pair not in world.embargoes:
        world.embargoes.append(pair)


structural.register_structural("LEADER_CHANGE", _replace_leader)
structural.register_structural("OCCUPATION_BEGIN", _begin_occupation)
structural.register_structural("WAR_DECLARED", _declare_war)
structural.register_structural("PEACE", _make_peace)
structural.register_structural("ANNEXATION", _annex_country)
structural.register_structural("OCCUPATION_END", _end_occupation)
structural.register_replay("LEADER_CHANGE", _replay_leader_change)
structural.register_replay("OCCUPATION_BEGIN", _replay_occupation_begin)
structural.register_replay("WAR_DECLARED", _replay_declare_war)
structural.register_replay("PEACE", _replay_make_peace)
structural.register_replay("ANNEXATION", _replay_annex_country)
structural.register_replay("OCCUPATION_END", _replay_end_occupation)
structural.register_structural("ALLIANCE", _form_alliance)
structural.register_structural("ALLIANCE_BROKEN", _break_alliance)
structural.register_structural("EMBARGO", _impose_embargo)
structural.register_replay("ALLIANCE", _replay_form_alliance)
structural.register_replay("ALLIANCE_BROKEN", _replay_break_alliance)
structural.register_replay("EMBARGO", _replay_impose_embargo)

# God-mode twins run the SAME live and replay handlers as their organic kinds, so an
# intervened war/peace/alliance/embargo is indistinguishable in state from an organic one
# and world_at reconstructs it the same way. The shared guards apply to both: a war with
# a non-ACTIVE side fizzles, and an alliance between members of two different blocs only
# sets the relation (bloc merging is deferred).
for _organic, _intervention in (
    ("WAR_DECLARED", "INTERVENE_WAR"),
    ("PEACE", "INTERVENE_PEACE"),
    ("ALLIANCE", "INTERVENE_ALLIANCE"),
    ("EMBARGO", "INTERVENE_EMBARGO"),
):
    structural.register_structural(_intervention, structural.STRUCTURAL_EFFECTS[_organic])
    structural.register_replay(_intervention, structural.REPLAY_EFFECTS[_organic])


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in sorted(world.countries, key=lambda c: c.code):
        # 1. Calendar elections (~every 90 ticks via election_due_tick, §6.1/§3.2).
        # Elections and coups model a living government, so they need a country still in
        # the world. Paths 3-5 need no guard on this country: each requires it to be ACTIVE
        # or OCCUPIED. (Path 4's occupier is not checked; see docs/progress.md.)
        if (
            country.in_world
            and country.election_due_tick is not None
            and world.tick >= country.election_due_tick
        ):
            country.election_due_tick = world.tick + config.ELECTION_INTERVAL_TICKS
            changes = rng.roll(_election_change_probability(country))
            election = cascade.emit_event(
                world,
                rng,
                kind="ELECTION",
                primary=country.code,
                secondary=None,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={"incumbent_changed": changes},
            )
            events.append(election)
            if changes:
                leader_change = cascade.emit_event(
                    world,
                    rng,
                    kind="LEADER_CHANGE",
                    primary=country.code,
                    secondary=None,
                    parent_id=election.id,
                    depth=election.depth + 1,
                    is_intervention=False,
                    payload={},
                )
                events.append(leader_change)

        # 2. Coup rolls (§6.6.3: COUP's own registered consequences -- LEADER_CHANGE
        # p=1.0, CRACKDOWN p=0.6 -- are scheduled generically by emit_event below).
        if country.in_world and rng.roll(_coup_probability(country)):
            coup = cascade.emit_event(
                world,
                rng,
                kind="COUP",
                primary=country.code,
                secondary=None,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={},
            )
            events.append(coup)

        # 3. Conquest/absorption rolls (§6.8): stability==0 AND at war AND some enemy
        # relation < -80. Only ACTIVE countries are eligible (no re-occupying).
        if (
            country.status == CountryStatus.ACTIVE
            and country.stability <= 0.0
            and country.at_war_with
        ):
            occupier = _occupation_target(world, country)
            # allow_conquest off means no country is ever taken: the roll is skipped
            # entirely rather than rolled and discarded, so turning the rule off does not
            # shift the RNG stream for everything after it.
            if (
                occupier is not None
                and world_rule_allows("OCCUPATION_BEGIN", world.settings)
                and rng.roll(config.OCCUPATION_BASE_P)
            ):
                occupation = cascade.emit_event(
                    world,
                    rng,
                    kind="OCCUPATION_BEGIN",
                    primary=country.code,
                    secondary=occupier,
                    parent_id=None,
                    depth=0,
                    is_intervention=False,
                    payload={},
                )
                events.append(occupation)

        # 4. Occupied countries: roll for ANNEXATION (long, low-resistance occupation)
        # or LIBERATION_WAR (recovered stability, a relief war begins), §6.8.
        if country.status == CountryStatus.OCCUPIED and country.occupied_by is not None:
            start_tick = country.occupation_start_tick
            annexed = False
            if (
                start_tick is not None
                and world_rule_allows("ANNEXATION", world.settings)
                and world.tick - start_tick >= config.ANNEXATION_MIN_OCCUPATION_TICKS
                and country.stability > config.ANNEXATION_LOW_RESISTANCE_STABILITY
                and rng.roll(config.ANNEXATION_BASE_P)
            ):
                annexation = cascade.emit_event(
                    world,
                    rng,
                    kind="ANNEXATION",
                    primary=country.code,
                    secondary=country.occupied_by,
                    parent_id=None,
                    depth=0,
                    is_intervention=False,
                    payload={},
                )
                events.append(annexation)
                annexed = True
            if (
                not annexed
                and country.stability > config.LIBERATION_STABILITY_THRESHOLD
                and rng.roll(config.LIBERATION_BASE_P)
            ):
                liberation = cascade.emit_event(
                    world,
                    rng,
                    kind="LIBERATION_WAR",
                    primary=country.code,
                    secondary=country.occupied_by,
                    parent_id=None,
                    depth=0,
                    is_intervention=False,
                    payload={},
                )
                events.append(liberation)

        # 5. Organic peace (§6.8 TODO closed): flat per-tick war-weariness roll, no
        # stability gate (see config.PEACE_BASE_P's comment -- stability falls, not
        # rises, for the duration of a war, so gating on "recovered" stability was
        # nearly unsatisfiable). Rolled once per war cluster, from the alphabetically-
        # lowest code in it, so a 1-vs-1 war (or a country fighting several at once)
        # doesn't double-fire PEACE the same tick.
        if (
            country.status == CountryStatus.ACTIVE
            and country.at_war_with
            and country.code == min([country.code, *country.at_war_with])
            and rng.roll(config.PEACE_BASE_P)
        ):
            peace = cascade.emit_event(
                world,
                rng,
                kind="PEACE",
                primary=country.code,
                secondary=None,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={},
            )
            events.append(peace)
    return events
