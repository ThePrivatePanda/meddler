"""World state model. PROPOSAL §5.1 (Country), §6.7.1 (InfrastructureBlock/AssetClass),
§6.9 (WorldSettings), §5.2 (World.relations).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from meddler.engine import config
from meddler.engine.events import EventLog, ScheduleEntry
from meddler.engine.space import Position


class CountryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    OCCUPIED = "OCCUPIED"
    ANNEXED = "ANNEXED"
    DISSOLVED = "DISSOLVED"


#: The statuses that mean a country is no longer part of the world. An OCCUPIED country
#: still is -- it has a population, an economy and a future, it is merely somebody else's
#: problem -- so only these two end its participation. Read through `Country.in_world`
#: rather than compared against directly, so a system added later inherits the rule instead
#: of repeating the omission that let an annexed nation keep holding elections.
GONE_STATUSES = frozenset({CountryStatus.ANNEXED, CountryStatus.DISSOLVED})


class GovtType(StrEnum):
    DEMOCRACY = "DEMOCRACY"
    AUTOCRACY = "AUTOCRACY"
    MONARCHY = "MONARCHY"
    JUNTA = "JUNTA"
    ANARCHY = "ANARCHY"


class EventCategory(StrEnum):
    """One member per file in engine/kinds/. PROPOSAL §4.7/§7."""

    NATURAL = "natural"
    ECONOMY = "economy"
    POLITICS = "politics"
    MILITARY = "military"
    DIPLOMATIC = "diplomatic"
    INFRASTRUCTURE = "infrastructure"
    SOCIAL = "social"


@dataclass
class Leader:
    """PROPOSAL §5.3: name + 2 personality traits; contract leader:{title,name,traits}."""

    name: str
    title: str  # role-appropriate to govt_type, e.g. "President", "Chairman", "King"
    traits: list[str]  # 2 of: paranoid, reformist, corrupt, populist, technocrat,
    # warhawk, frugal, flamboyant


@dataclass
class AssetClass:
    """One row of an InfrastructureBlock. PROPOSAL §6.7.1."""

    count: int  # total units (functional + degraded)
    condition: float  # 0.0-1.0 average; drives failure probability
    last_maintained_tick: int


@dataclass
class InfrastructureBlock:
    """PROPOSAL §6.7.1."""

    satellites: AssetClass
    naval_fleet: AssetClass  # cargo + military combined
    air_fleet: AssetClass
    rail_network: AssetClass  # condition = fraction of routes functional
    power_grid: AssetClass
    communications: AssetClass  # press_freedom x condition = effective media reach


@dataclass(frozen=True)
class Territory:
    """One spatial holding owned by a country (M15, v2 spec §8).

    `Country.position` remains the administrative capital used by distance systems. A
    conquest moves these markers between owners instead of teleporting that capital or
    discarding one side of a multi-region state. The marker is immutable; ownership is the
    containing Country.territories list, which snapshots deep-copy.
    """

    position: Position
    region: str


@dataclass
class Country:
    """Full per-country stat block. PROPOSAL §5.1, transcribed field-for-field.

    currency_name/currency_symbol are a documented addition, not in the PROPOSAL §5.1
    listing verbatim — see docs/design-decisions.md ("Country currency fields").
    """

    code: str  # unique 3-letter, immutable
    name: str
    parent_code: str | None  # None for founding countries; set on secession
    status: CountryStatus
    born_at_tick: int

    # --- spatial (M9, v2 spec §1) ---
    # Position on the abstract unit sphere (NOT Earth). Distance between any two countries
    # is derived from these, so it is self-consistent by construction. The capital stays
    # immutable; M15 conquest transfers `territories` markers instead of moving it.
    position: Position
    # Landmass. Same region => land-connected (rail); cross-region => sea or air only.
    region: str
    # Per-extractive-commodity level in [0, 1], keyed by config.EXTRACTIVE_COMMODITIES.
    # Territory-bound: annexation transfers these to the annexer (M15).
    endowments: dict[str, float]
    # Every spatial holding currently owned by this polity. Genesis seeds one marker;
    # annexation moves the loser's complete list into the winner and clears the loser.
    territories: list[Territory]

    # economics
    population: float  # millions, live stat
    # base_gdp is the country's roughly-fixed structural output capacity; gdp_tick
    # (below) is the live, fluctuating figure ProductionSystem recomputes every tick
    # as base_gdp * (0.5 + stability/200) * innovation_mult (§6.2). Not in PROPOSAL
    # §5.1's Country listing -- without a separate base figure, gdp_tick would decay
    # toward zero every tick (the multiplier is always <= 1.0). See
    # docs/design-decisions.md ("base_gdp").
    base_gdp: float
    gdp_tick: float
    # --- commodities (M10, v2 spec §3) ---
    # Per-bucket output/need/stock, keyed by config.COMMODITY_ORDER. These REPLACE the
    # pre-M10 scalar grain_* fields, which survive as properties below aliasing the "food"
    # bucket -- unified data with a backwards-compatible face, rather than two copies that
    # could drift apart.
    # stock is a SHOCK BUFFER, not a running per-tick balance: production and consumption
    # net to zero while supply covers need, so stock only moves when a shock or a shortfall
    # disturbs it. This preserves the pre-M10 grain_stock semantics exactly
    # (see systems/trade.py's docstring and docs/design-decisions.md).
    commodity_output: dict[str, float]
    commodity_need: dict[str, float]
    commodity_stock: dict[str, float]
    inflation: float  # %
    tax_rate: float  # %
    innovation_mult: float  # 1.0 baseline; buffed by INNOVATION events
    exchange_rate: float  # local per 1 veri
    pools: dict[str, int]  # "treasury","households","corporates" -> minor units

    currency_name: str
    currency_symbol: str

    # social
    stability: float  # 0-100
    civil_rights: float  # 0-100
    press_freedom: float  # 0-100
    education: float  # 0-100
    health: float  # 0-100

    # infrastructure
    infrastructure: InfrastructureBlock

    # political
    leader: Leader
    govt_type: GovtType
    election_due_tick: int | None
    at_war_with: list[str]
    occupied_by: str | None  # code of occupying country, or None

    # threshold hysteresis arms (not serialised to save files; reconstructed on load)
    armed: dict[str, bool]

    # Tick occupation began, set by OCCUPATION_BEGIN's structural + replay handlers
    # (systems/politics.py). Replaces the former O(ticks) backward log scan
    # (_occupation_start_tick) with direct state, same as occupied_by is a direct field.
    # None until first occupation; overwritten on each new OCCUPATION_BEGIN and only ever
    # read while status is OCCUPIED, so a stale value after OCCUPATION_END is never read
    # (not cleared on end, exactly matching the old latest-OCCUPATION_BEGIN scan).
    occupation_start_tick: int | None = None

    # The country's stability equilibrium, drawn at worldgen from
    # settings.starting_stability_range and moved only by recorded events (a god edit to
    # stability, a change of leader, a revolution). Its national temperament:
    # systems/stability.py pulls stability toward it, so two countries in identical
    # circumstances settle at different levels. Before mean reversion this draw decided only
    # how many ticks a country took to reach 100 like everyone else, so the world-generation
    # setting behind it was doing no lasting work. Defaulted for the benefit of hand-built
    # test fixtures; worldgen and secession always pass it.
    base_stability: float = 50.0
    # The genesis draw itself, kept after base_stability starts moving: the identity a
    # changing temperament reverts toward (systems/politics.py) and a revolution resets
    # toward. Never changes after birth, so snapshots carry it and no event needs to.
    genesis_stability: float = 50.0

    # --- grain_* aliases (M10) ---
    # The food bucket's public face. Every pre-M10 consumer -- systems/thresholds.py's
    # famine checks, systems/trade.py, god.py's _EDITABLE_FIELDS, kinds/natural.py's
    # {"grain_stock": -5.0} style stat_deltas, and bridge/adapter.py -- reaches for these
    # names via getattr/setattr. Aliasing rather than renaming keeps all of them working
    # against the unified dict. Read AND write: apply_country_stat does
    # setattr(c, stat, getattr(c, stat) + delta), so a read-only property would break
    # every food stat_delta silently.

    @property
    def in_world(self) -> bool:
        """False once this country has been annexed or dissolved.

        The authoritative answer to "does this nation still exist", and the one the bridge
        has always given the client: `bridge/adapter.py` excludes these from the country
        roster and `bridge/world_objects.py` from the globe. Every per-country system loop
        asks this before simulating, so the engine and the client agree about who is in the
        world -- they did not, and a viewer could watch a country vanish from the roster and
        then read its election results in the feed.
        """
        return self.status not in GONE_STATUSES

    @property
    def grain_output(self) -> float:
        return self.commodity_output["food"]

    @grain_output.setter
    def grain_output(self, value: float) -> None:
        self.commodity_output["food"] = value

    @property
    def grain_need(self) -> float:
        return self.commodity_need["food"]

    @grain_need.setter
    def grain_need(self, value: float) -> None:
        self.commodity_need["food"] = value

    @property
    def grain_stock(self) -> float:
        return self.commodity_stock["food"]

    @grain_stock.setter
    def grain_stock(self, value: float) -> None:
        self.commodity_stock["food"] = value


@dataclass
class WorldSettings:
    """User-configurable rules. PROPOSAL §6.9. All systems read tunables that appear
    here from world.settings, never from config.py directly."""

    # World rules
    allow_secession: bool = True
    allow_conquest: bool = True
    allow_nukes: bool = False
    max_countries: int = 20
    max_wars_concurrent: int = 4
    protected_countries: list[str] = field(default_factory=list)
    observer_only: bool = False

    # Simulation tuning
    cascade_decay: float = 0.70
    max_depth: int = 8
    # Global scaler on every exogenous root's per-tick probability (systems/exogenous.py).
    # Default lowered 1.0 -> 0.4 in the 2026-07-22 pacing pass so the shipped world is calm by
    # default (disasters/scandals/exogenous wars are occasional, not constant); the settings
    # overlay lets a viewer crank it back up for a chaotic run. See docs/design-decisions.md.
    drama_multiplier: float = 0.4
    relation_decay_rate: float = 0.005
    ticks_per_year: int = 365
    snapshot_interval: int = 50
    enabled_event_tags: list[str] = field(default_factory=lambda: ["*"])
    disabled_event_tags: list[str] = field(default_factory=list)
    silent_god_edits: bool = False

    # World generation (restart required)
    starting_country_count: int = 8
    starting_stability_range: tuple[float, float] = (40.0, 85.0)
    starting_inflation_range: tuple[float, float] = (1.0, 6.0)
    rival_pairs: int = 4
    # M11 (v2 spec §7): genesis friendly pairs, the positive-relation seed alliances form
    # from. Drawn from the nearest not-already-rival pairs. 0 => a world with no friendships
    # (and thus no organic blocs), which is a valid but lonelier world.
    friendly_pairs: int = 4
    # M9 (v2 spec §1): landmasses countries are clustered onto. Capped by the number of
    # names in config.REGION_NAMES; changing it re-rolls the whole map, hence restart-required.
    region_count: int = config.REGION_COUNT_DEFAULT


@dataclass
class Bloc:
    """A structural alliance/coalition (M11, v2 spec §7). A country is in AT MOST ONE bloc
    (bloc merging -- two established blocs combining -- is deliberately deferred; formation
    only ever seeds a fresh pair or adds a bloc-less country to the other's bloc). `members`
    is kept sorted so iteration order is fixed for determinism. Lives on World, so snapshots
    deep-copy it automatically; mid-tick changes are reproduced by the ALLIANCE/
    ALLIANCE_BROKEN structural REPLAY handlers (systems/politics.py)."""

    id: str  # stable unique, e.g. "BLOC1"; assigned from World.bloc_seq
    members: list[str]  # country codes, sorted
    formed_at_tick: int


@dataclass(frozen=True)
class TariffPolicy:
    """One importer-side ad-valorem duty (real tariff policy).

    exporter/commodity may be "*" for blanket coverage. The most-specific matching policy
    wins; intra-bloc trade is tariff-free regardless of stored policy. `source_event_id`
    anchors retaliation and audit trails to the policy event that created the state.
    """

    importer: str
    exporter: str
    commodity: str
    rate: float
    imposed_at_tick: int
    source_event_id: int


@dataclass
class Shipment:
    """A trade in physical transit (M13, v2 spec §5). Created by TradeSystem's dispatch,
    resolved by LogisticsSystem's arrival or interdiction. Lives on World, so snapshots
    deep-copy in-flight shipments automatically; its mid-tick creation/removal is reproduced
    by the SHIPMENT_DISPATCHED/ARRIVED/LOST structural REPLAY handlers (systems/logistics.py)
    -- the first multi-tick stateful object since occupation, so replay is load-bearing.

    Money is settled in two conserved legs across time: `cost` (importer minor units) is paid
    into the fx desk at dispatch; `proceeds` (exporter minor units) is paid out at arrival. A
    lost shipment fires only the first leg -- the importer's payment is the economic loss."""

    id: str  # "SHIP<seq>", assigned from World.shipment_seq
    origin: str  # exporter code
    dest: str  # importer code
    commodity: str
    qty: float
    carrier: str  # "rail" | "sea" | "air"
    buyer_pool: str  # "households" | "corporates" in the importing country
    base_cost: int  # importer-currency value paid to the FX desk, excluding duty
    tariff_duty: int  # importer-currency duty transferred to its treasury at dispatch
    cost: int  # total importer payment = base_cost + tariff_duty
    proceeds: int  # exporter-currency minor units, paid to corporates at arrival
    dispatch_event_id: int
    depart_tick: int
    arrive_tick: int
    relief: bool  # emergency famine relief (subsidised, air-preferred)


@dataclass
class World:
    tick: int
    countries: list[Country]  # ordered; fixed iteration order
    relations: dict[tuple[str, str], float]  # key = sorted code pair, value in [-100,100]
    settings: WorldSettings
    log: EventLog
    # "fx:<pair>" bookkeeping pools for cross-currency conversions (§4.5). Not owned by
    # any single Country -- added here since §4.5 requires them to exist somewhere and
    # §5.1's World skeleton predates this need. See docs/design-decisions.md.
    fx_pools: dict[str, int]

    # --- Cascade machinery (M3.1, §6.4). Lives on World (not module-level) so it is
    # deep-copied per timeline by snapshots/forks and can never be shared between two
    # timelines running in the same process (the property-test failure mode). ---
    # ConsequenceSystem's pending-consequence queue (§6.4.2), ordered by
    # (fire_tick, schedule_seq). schedule_seq is the next counter value to hand out.
    # clip_count is the per-timeline depth-cap overflow tally (§6.4.3) checked against
    # CLIP_BUDGET_PER_1000_TICKS. All default-empty so M0-M2 worldgen/tests are unaffected.
    schedule: list[ScheduleEntry] = field(default_factory=list)
    schedule_seq: int = 0
    clip_count: int = 0

    # --- Diplomacy (M11, v2 spec §7). All default-empty so pre-M11 worldgen/tests are
    # unaffected; deep-copied per timeline by snapshots/forks like the cascade machinery. ---
    blocs: list[Bloc] = field(default_factory=list)
    bloc_seq: int = 0  # next bloc id counter (mirrors schedule_seq's role for schedule ids)
    embargoes: list[tuple[str, str]] = field(default_factory=list)  # sorted code pairs
    tariffs: list[TariffPolicy] = field(default_factory=list)

    # --- Logistics (M13, v2 spec §5). In-flight shipments; default-empty so pre-M13
    # worldgen/tests are unaffected; deep-copied per timeline by snapshots/forks. ---
    shipments: list[Shipment] = field(default_factory=list)
    shipment_seq: int = 0  # next shipment id counter (the bloc_seq/schedule_seq pattern)

    def country(self, code: str) -> Country:
        for c in self.countries:
            if c.code == code:
                return c
        raise KeyError(code)

    def living_countries(self) -> list[Country]:
        """The countries still in the world, sorted by code: what a system iterates.

        A country that has been annexed or dissolved stays in `countries` -- replay, diffs,
        snapshots and the client's stable roster indices all need the full record -- but it
        must not be simulated. Every per-country system reads this instead of `countries`,
        and tests/unit/test_living_countries.py fails on any engine read of `countries`
        outside a short allowlist of bookkeeping sites, so a new system cannot quietly
        iterate the dead. The guards this replaces were added one system at a time, after
        each omission had already shown up in the feed.
        """
        return sorted((c for c in self.countries if c.in_world), key=lambda c: c.code)

    def bloc_of(self, code: str) -> Bloc | None:
        for bloc in self.blocs:
            if code in bloc.members:
                return bloc
        return None
