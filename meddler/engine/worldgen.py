"""Genesis world construction. PROPOSAL §5.3. Pure function of (seed, settings).

Name/code/currency/leader generation lives here rather than in text/names.py, as the
implementation-spec's M1.3 file list suggests. Reason: engine/ may never import text/
(PROPOSAL §4.1's hard layering wall, enforced by M0.4's import-linter contract) -- but
worldgen is engine/ code and needs this data at genesis. text/names.py is reserved for
M3.3's headline flavor-word lists, which are a disjoint concern (prose, not world state)
and don't need engine imports. Documented in docs/design-decisions.md.
"""

from __future__ import annotations

import itertools

from meddler.engine import assets, commodities, config, space
from meddler.engine.events import EventLog
from meddler.engine.ledger import round_money
from meddler.engine.model import (
    AssetClass,
    Country,
    CountryStatus,
    GovtType,
    InfrastructureBlock,
    Leader,
    Territory,
    World,
    WorldSettings,
)
from meddler.engine.rng import Rng

CONSONANTS = "bcdfglmnprstvz"
VOWELS = "aeiou"
NAME_SUFFIXES = ["onia", "avia", "ania", "oria", "adia", "ustria", "esia", "ovia"]

# (currency name, symbol), curated (PROPOSAL §5.3).
CURRENCIES: list[tuple[str, str]] = [
    ("Crown", "¤"),
    ("Dollar", "$"),
    ("Franc", "₣"),
    ("Mark", "₥"),
    ("Peso", "₱"),
    ("Ruble", "₽"),
    ("Shilling", "§"),
    ("Guilder", "ƒ"),
    ("Lira", "₺"),
    ("Krona", "kr"),
    ("Dinar", "din"),
    ("Rupee", "₹"),
]

# Fixed trait list (PROPOSAL §5.3); biases event probabilities and headline word choice.
TRAITS = [
    "paranoid",
    "reformist",
    "corrupt",
    "populist",
    "technocrat",
    "warhawk",
    "frugal",
    "flamboyant",
]

_LEADER_TITLES = {
    GovtType.DEMOCRACY: "President",
    GovtType.AUTOCRACY: "Chairman",
    GovtType.MONARCHY: "King",
    GovtType.JUNTA: "General",
    GovtType.ANARCHY: "Warlord",
}


def _syllable(rng: Rng) -> str:
    return str(rng.choice(list(CONSONANTS))) + str(rng.choice(list(VOWELS)))


def _generate_country_name(rng: Rng) -> str:
    syllable_count = rng.randint(1, 2)
    root = "".join(_syllable(rng) for _ in range(syllable_count))
    suffix = str(rng.choice(NAME_SUFFIXES))
    return (root + suffix).capitalize()


def _generate_code(name: str, used: set[str], rng: Rng) -> str:
    base = "".join(ch for ch in name.upper() if ch.isalpha())
    candidate = base[:3]
    if len(candidate) == 3 and candidate not in used:
        return candidate
    consonants_only = "".join(ch for ch in base if ch not in "AEIOU")
    candidate = (consonants_only + base)[:3]
    while len(candidate) < 3 or candidate in used:
        candidate = "".join(rng.choice(list(CONSONANTS)).upper() for _ in range(3))
    return candidate


def generate_leader_name(rng: Rng) -> str:
    """Public: also used by PoliticsSystem (M3.2) to generate a successor leader's name."""
    first = _syllable(rng) + rng.choice(list(VOWELS))
    last = "".join(_syllable(rng) for _ in range(rng.randint(1, 2)))
    return f"{first.capitalize()} {last.capitalize()}"


def sample_two_traits(rng: Rng) -> list[str]:
    """Public: also used by PoliticsSystem (M3.2) to re-bias a successor leader's traits."""
    remaining = list(TRAITS)
    first = rng.choice(remaining)
    remaining.remove(first)
    second = rng.choice(remaining)
    return [first, second]


# Prefixes a breakaway state takes in front of its parent's name ("Free Veronia").
BREAKAWAY_PREFIXES: tuple[str, ...] = (
    "North", "South", "East", "West", "New", "Free", "Upper", "Lower", "Old",
)


def breakaway_identity(
    rng: Rng, parent: Country, taken_names: set[str], used_codes: set[str]
) -> tuple[str, str]:
    """(name, code) for a state splitting off `parent`, e.g. ("Free Veronia", "FVE").

    The name is the parent's with a prefix; the code is the prefix initial plus the first
    two letters of the parent's code, falling back to worldgen's consonant codes on a
    clash. Consumes one choice draw, plus fallback draws only on a clash.
    """
    prefixes = [p for p in BREAKAWAY_PREFIXES if f"{p} {parent.name}" not in taken_names]
    prefix = str(rng.choice(prefixes or list(BREAKAWAY_PREFIXES)))
    name = f"{prefix} {parent.name}"
    candidate = (prefix[0] + parent.code[:2]).upper()
    if candidate not in used_codes:
        return name, candidate
    return name, _generate_code(name, used_codes, rng)


def pick_currency(rng: Rng, avoid_name: str) -> tuple[str, str]:
    """A curated currency other than `avoid_name` (one choice draw)."""
    options = [c for c in CURRENCIES if c[0] != avoid_name]
    name, symbol = rng.choice(options)
    return str(name), str(symbol)


def new_leader(rng: Rng, govt_type: GovtType) -> Leader:
    """A freshly generated leader with the title for `govt_type`."""
    return Leader(
        name=generate_leader_name(rng),
        title=_LEADER_TITLES[govt_type],
        traits=sample_two_traits(rng),
    )


def _generate_infrastructure(rng: Rng, population: float) -> InfrastructureBlock:
    # The counts table lives in engine/assets.py (M14): upkeep_cost needs the same baseline
    # to judge whether an asset stock is oversized for its population, and two copies would
    # drift apart silently.
    counts = assets.infra_counts_for_population(population)
    lo, hi = config.INFRA_CONDITION_RANGE

    def asset(key: str) -> AssetClass:
        return AssetClass(count=counts[key], condition=rng.uniform(lo, hi), last_maintained_tick=0)

    return InfrastructureBlock(
        satellites=asset("satellites"),
        naval_fleet=asset("naval_fleet"),
        air_fleet=asset("air_fleet"),
        rail_network=asset("rail_network"),
        power_grid=asset("power_grid"),
        communications=asset("communications"),
    )


def _assign_specialties(rng: Rng, country_count: int) -> list[str]:
    """One specialty commodity per country, round-robin over a shuffled commodity order.

    Round-robin rather than independent random draws: independent draws can leave a
    commodity with no specialist at all (~4% of 8-country worlds per commodity), and
    "every commodity has a supplier" should be a worldgen INVARIANT, not a probability --
    a commodity nobody is good at is a market that can never clear. Shuffling first keeps
    which-country-gets-what varied across seeds.

    HOLDS ONLY FOR country_count >= len(EXTRACTIVE_COMMODITIES) (i.e. >= 3). Below that
    there are literally more commodities than countries, so somebody must go unsupplied;
    2-country worlds appear throughout the test suite. This is not a live problem (nothing
    reads endowments until M10) but M10 must not inherit the guarantee as unconditional.

    Consumes len(EXTRACTIVE_COMMODITIES) - 1 RNG draws.
    """
    remaining = list(config.EXTRACTIVE_COMMODITIES)
    order: list[str] = []
    while len(remaining) > 1:
        pick = str(rng.choice(remaining))
        remaining.remove(pick)
        order.append(pick)
    order.append(remaining[0])
    return [order[i % len(order)] for i in range(country_count)]


def _generate_endowments(rng: Rng, specialty: str) -> dict[str, float]:
    """Mediocre at everything, good at one thing (v2 spec §2). Iterates the commodity
    tuple in its fixed order so the RNG draw order is pinned."""
    endowments: dict[str, float] = {}
    for commodity in config.EXTRACTIVE_COMMODITIES:
        span = (
            config.ENDOWMENT_SPECIALIST_RANGE
            if commodity == specialty
            else config.ENDOWMENT_BASE_RANGE
        )
        endowments[commodity] = rng.uniform(*span)
    return endowments


def _generate_commodities(
    population: float,
    endowments: dict[str, float],
    gdp_tick: float,
    innovation_mult: float,
    education: float,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Seed per-commodity output/need/stock (v2 spec §3).

    Extractive output scales with the country's ENDOWMENT (territory). Produced output
    scales with ECONOMIC capacity (gdp_tick/innovation/education) per §3's table -- which
    is what gives produced commodities real per-country variance and makes "GDP falls =>
    exports fall" true. Delegates to systems/commodity_production.py's own formulas so
    genesis and tick 1 cannot disagree; there is exactly ONE definition of output.

    Consumes NO RNG -- all variation comes from population, the M9 endowment draws, and the
    gdp/stability draws that already happened. Keeping this draw-free means the §14 balance
    is a deterministic function of state rather than an extra source of per-seed variance.
    """
    need = {
        name: population * config.COMMODITY_NEED_PER_CAPITA[name]
        for name in config.COMMODITY_ORDER
    }
    output = {
        name: commodities.genesis_output(
            name,
            population=population,
            endowments=endowments,
            gdp_tick=gdp_tick,
            innovation_mult=innovation_mult,
            education=education,
        )
        for name in config.COMMODITY_ORDER
    }
    stock = {
        name: need[name] * config.STARTING_COMMODITY_STOCK_DAYS
        for name in config.COMMODITY_ORDER
    }
    return output, need, stock


def _generate_country(
    rng: Rng,
    settings: WorldSettings,
    used_codes: set[str],
    placement: space.Placement,
    specialty: str,
) -> Country:
    name = _generate_country_name(rng)
    code = _generate_code(name, used_codes, rng)
    used_codes.add(code)
    endowments = _generate_endowments(rng, specialty)

    population = rng.uniform(*config.POPULATION_RANGE_MILLIONS)
    tier = rng.choice(list(config.GDP_PER_CAPITA_TIERS))
    gdp_per_capita = tier * rng.uniform(*config.GDP_PER_CAPITA_JITTER)
    base_gdp = population * 1_000_000 * gdp_per_capita * config.GDP_TICK_SHARE_OF_ANNUAL

    stability = rng.uniform(*settings.starting_stability_range)
    inflation = rng.uniform(*settings.starting_inflation_range)

    # Consistent with ProductionSystem's own §6.2 formula from tick 1 onward
    # (innovation_mult starts at 1.0).
    gdp_tick = base_gdp * (0.5 + stability / 200)

    commodity_output, commodity_need, commodity_stock = _generate_commodities(
        population,
        endowments,
        gdp_tick=gdp_tick,
        innovation_mult=1.0,  # genesis baseline, matching the Country field below
        education=config.STARTING_EDUCATION,
    )

    currency_name, currency_symbol = rng.choice(CURRENCIES)

    govt_type = GovtType.DEMOCRACY
    leader = Leader(
        name=generate_leader_name(rng),
        title=_LEADER_TITLES[govt_type],
        traits=sample_two_traits(rng),
    )

    # Bare keys (§5.1: "'treasury','households','corporates' -> minor units"). The
    # "<CODE>." prefix is a ledger-entry-only convention (§4.5) for disambiguating
    # across countries; a Country's own pools dict is already scoped to one country.
    pools = {
        "treasury": round_money(gdp_tick * config.STARTING_TREASURY_TICKS),
        "households": round_money(gdp_tick * config.STARTING_HOUSEHOLDS_TICKS),
        "corporates": round_money(gdp_tick * config.STARTING_CORPORATES_TICKS),
    }

    return Country(
        code=code,
        name=name,
        parent_code=None,
        status=CountryStatus.ACTIVE,
        born_at_tick=0,
        position=placement.position,
        region=placement.region,
        endowments=endowments,
        territories=[Territory(position=placement.position, region=placement.region)],
        population=population,
        base_gdp=base_gdp,
        gdp_tick=gdp_tick,
        commodity_output=commodity_output,
        commodity_need=commodity_need,
        commodity_stock=commodity_stock,
        inflation=inflation,
        tax_rate=config.STARTING_TAX_RATE,
        innovation_mult=1.0,
        exchange_rate=1.0,
        pools=pools,
        currency_name=currency_name,
        currency_symbol=currency_symbol,
        stability=stability,
        base_stability=stability,
        genesis_stability=stability,
        civil_rights=config.STARTING_CIVIL_RIGHTS,
        press_freedom=config.STARTING_PRESS_FREEDOM,
        education=config.STARTING_EDUCATION,
        health=config.STARTING_HEALTH,
        infrastructure=_generate_infrastructure(rng, population),
        leader=leader,
        govt_type=govt_type,
        election_due_tick=config.ELECTION_INTERVAL_TICKS,
        at_war_with=[],
        occupied_by=None,
        armed={},
        occupation_start_tick=None,
    )


def _seed_relations(
    countries: list[Country], settings: WorldSettings, rng: Rng
) -> dict[tuple[str, str], float]:
    """Slightly negative relations between near neighbours (§5.3, v2 spec §1).

    M9 replaces the population-adjacency stand-in with real proximity: the Country model
    now HAS geography, so "neighbours" means neighbours. Rivalries are drawn from the
    nearest RIVAL_CANDIDATE_POOL_MULTIPLIER x rival_pairs pairs -- near countries are
    likelier to be rivals, and distant ones cannot be seeded as rivals at all.

    The sort key includes the code pair to break distance ties deterministically; float
    distances tying is vanishingly unlikely but a tie decided by list order would make the
    world depend on country construction order, which is exactly the kind of hidden
    coupling that breaks replay.
    """
    by_distance = sorted(
        itertools.combinations(countries, 2),
        key=lambda ab: (
            space.central_angle(ab[0].position, ab[1].position),
            ab[0].code,
            ab[1].code,
        ),
    )
    pool_size = min(
        len(by_distance), settings.rival_pairs * config.RIVAL_CANDIDATE_POOL_MULTIPLIER
    )
    remaining = [
        (min(a.code, b.code), max(a.code, b.code)) for a, b in by_distance[:pool_size]
    ]

    relations: dict[tuple[str, str], float] = {}
    for _ in range(min(settings.rival_pairs, len(remaining))):
        pick = rng.choice(remaining)
        remaining.remove(pick)
        relations[pick] = -rng.uniform(*config.RIVAL_RELATION_RANGE)
    # M11 (v2 spec §7): genesis friendships, the positive relations alliances form from.
    # Drawn AFTER rivalries (pinned order) from the SAME nearest-pair pool minus the pairs
    # already taken as rivals -- so friends, like rivals, are near-neighbour relationships.
    # Without these every relation decays toward 0 and no bloc can ever form (measured).
    for _ in range(min(settings.friendly_pairs, len(remaining))):
        pick = rng.choice(remaining)
        remaining.remove(pick)
        relations[pick] = rng.uniform(*config.FRIENDLY_RELATION_RANGE)
    return relations


def generate_world(seed: int, settings: WorldSettings) -> World:
    """Build the genesis world per PROPOSAL §5.3 + v2 spec §1-2. Pure function of
    (seed, settings).

    RNG draw order (pinned): layout (rotation + positions) -> specialties -> per-country
    fields -> relations. Layout comes first because a country's position is part of its
    identity, and relations last because they read every country's position.
    """
    rng = Rng(seed)
    layout = space.generate_layout(rng, settings.starting_country_count, settings.region_count)
    specialties = _assign_specialties(rng, settings.starting_country_count)
    used_codes: set[str] = set()
    countries = [
        _generate_country(rng, settings, used_codes, layout[i], specialties[i])
        for i in range(settings.starting_country_count)
    ]
    relations = _seed_relations(countries, settings, rng)
    return World(
        tick=0,
        countries=countries,
        relations=relations,
        settings=settings,
        log=EventLog(),
        fx_pools={},
    )
