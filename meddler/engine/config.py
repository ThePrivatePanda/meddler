"""Compile-time defaults for every magic number in the engine. PROPOSAL §6/§7.

Values that also appear in WorldSettings (PROPOSAL §6.9) are read from
world.settings at runtime by systems; the constants here are only their
compile-time defaults (used by worldgen and by WorldSettings' own defaults).
"""

SNAPSHOT_INTERVAL_DEFAULT = 50  # ticks between full world snapshots (PROPOSAL §4.4)
MAX_DEPTH_DEFAULT = 8  # cascade depth cap (PROPOSAL §6.4.3)
CASCADE_DECAY_DEFAULT = 0.70  # p_spawn = base_p * decay^depth (PROPOSAL §6.4.1)
CLIP_BUDGET_PER_1000_TICKS = 12  # depth-cap clips beyond this signal under-tuning (§6.4.3)

# --- Worldgen (§5.3) ---
# Population and GDP/capita tiers are specced by name only ("population 2-60M",
# "gdp/capita tiers"); the tier values and every ratio below are worldgen defaults
# with no PROPOSAL formula to transcribe -- see docs/design-decisions.md.
POPULATION_RANGE_MILLIONS = (2.0, 60.0)  # PROPOSAL §5.3
GDP_PER_CAPITA_TIERS = (800.0, 4000.0, 15000.0)  # low/mid/high annual gdp/capita
GDP_PER_CAPITA_JITTER = (0.8, 1.2)  # +-20% around the sampled tier
GDP_TICK_SHARE_OF_ANNUAL = 1.0 / 365.0  # 1 tick = 1 day (PROPOSAL §4.2)
# GRAIN_NEED_PER_CAPITA/GRAIN_SURPLUS_JITTER/STARTING_GRAIN_STOCK_DAYS were retired in M10:
# food is now a commodity bucket like any other. Their roles moved to
# COMMODITY_NEED_PER_CAPITA["food"], the arable-endowment output formula
# (EXTRACTIVE_OUTPUT_PER_CAPITA), and STARTING_COMMODITY_STOCK_DAYS respectively.
# Fraction (not percent-number) of gdp_tick taken as tax -- §6.2's formula is the
# literal "treasury += tax_rate * gdp_tick", no /100, so tax_rate must be fraction-
# scale (0.15 = 15%). Contrast inflation/stability/etc., which are percent-number
# scale (e.g. "inflation > 8%" in §6.3 compares directly against 8, not 0.08).
STARTING_TAX_RATE = 0.15
STARTING_TREASURY_TICKS = 20.0  # treasury seeded at N ticks' worth of gdp_tick
STARTING_HOUSEHOLDS_TICKS = 8.0
STARTING_CORPORATES_TICKS = 12.0
STARTING_CIVIL_RIGHTS = 55.0  # 0-100, legible mid-range genesis default
STARTING_PRESS_FREEDOM = 55.0
STARTING_EDUCATION = 50.0
STARTING_HEALTH = 65.0
INFRA_CONDITION_RANGE = (0.7, 0.9)  # per-class genesis condition (PROPOSAL §5.3/§6.7.1)
RIVAL_RELATION_RANGE = (10.0, 30.0)  # "slightly negative" seeded rivalry (§5.3)
# M11 (v2 spec §7): genesis FRIENDSHIPS. Pre-M11 the world had rivalries (negative) but no
# positive relations at all -- everything decayed toward 0, so "high mutual relations" for
# alliance formation was categorically unreachable. Friendly pairs are drawn from the nearest
# not-already-rival pairs and seeded high enough to survive the decay window during which
# blocs can crystallise. Sits alongside RIVAL_RELATION_RANGE; both are near-neighbour draws.
FRIENDLY_RELATION_RANGE = (60.0, 85.0)
# Election term length. Was 90 (~quarterly at ticks_per_year=365) which made ELECTION +
# LEADER_CHANGE the single largest headline source (~170/seed/1000t combined) -- terms now
# run ~1.4 years so leaders feel stable and an election is a real beat, not a constant drip.
# Part of the 2026-07-22 pacing pass (user: "events are extremely frequent"; target ~6x
# calmer). See docs/design-decisions.md ("Pacing pass").
ELECTION_INTERVAL_TICKS = 500  # ~every 1.4 years per country (PROPOSAL §6.1/§3.2)

# --- ProductionSystem (§6.2) ---
WAGE_SHARE_OF_GDP = 0.6  # corporates -> households transfer, fraction of gdp_tick

# --- TradeSystem (§6.2) ---
# base_price for grain is named but never numerically specced (§6.2's trade formula
# references it without a value). Denominated in veri (the abstract FX reference unit,
# glossary) since trade is settled cross-currency against an abstract world market, not
# a bilateral partner -- Country has no geography/trade-partner fields (§5.1). Tuning
# candidate; see docs/design-decisions.md.
GRAIN_BASE_PRICE_VERI = 1.0

# --- M12: bilateral trade & pricing (v2 spec §4) -------------------------------------
# Per-commodity reference price in veri. food keeps GRAIN_BASE_PRICE_VERI exactly so the
# food lane's magnitude is unchanged from the retired world-market model; the produced
# goods are pricier per unit than the extracted staples. Tuning candidates.
COMMODITY_BASE_PRICE_VERI = {
    "food": 1.0,
    "energy": 1.0,
    "raw_materials": 1.2,
    "manufactured": 2.5,
    "consumer": 2.0,
    "high_tech": 5.0,
}
# Commodity quantities are abstract million-population flow units while the Ledger stores
# integer minor currency units. Without an explicit conversion, a typical shipment valued
# 0.42 minor units and 71% rounded to zero. This scale targets trade spending at a material
# but non-dominant share of tax flow; M16 calibration evidence lives in docs/progress.md.
TRADE_VALUE_MINOR_SCALE = 4_000_000.0
# Imports are purchased by the sector that consumes them; tariff duty is split from the
# untaxed border value and transferred from this pool to the importing treasury.
HOUSEHOLD_IMPORT_COMMODITIES = ("food", "consumer")

# Real tariff policy. Organic policy is bilateral+blanket (commodity="*"); the state model
# also supports commodity-specific and exporter="*" policies. Rates are ad valorem [0,1].
TARIFF_RELATION_THRESHOLD = -20.0
TARIFF_IMPOSE_BASE_P = 0.001
TARIFF_RETALIATE_BASE_P = 0.08
TARIFF_REPEAL_RELATION = -5.0
TARIFF_REPEAL_BASE_P = 0.02
TARIFF_MIN_RATE = 0.10
TARIFF_MAX_RATE = 0.35
# price *= (1 + TRADE_DISTANCE_COST_COEFF * central_angle). central_angle is in radians
# [0, pi]; antipodal (~3.14) at this coeff ~= +47% freight. Distance is a first-class cost
# (v2 spec §1) -- far trade is dearer, near trade cheap.
TRADE_DISTANCE_COST_COEFF = 0.15
# relation_mod = clamp(1 - TRADE_RELATION_PRICE_SENSITIVITY * relation/100, floor, cap).
# A +70 friend at sensitivity 0.30 pays 0.79x; a neutral pair pays 1.0x. Enemies do not
# reach pricing at all -- they are BLOCKED below RELATION_TRADE_BLOCK_THRESHOLD.
TRADE_RELATION_PRICE_SENSITIVITY = 0.30
TRADE_RELATION_MOD_FLOOR = 0.60
TRADE_RELATION_MOD_CAP = 1.40
# scarcity_mult = (1 + importer_shortage_ratio) ** TRADE_SCARCITY_EXPONENT, same shape as
# the retired grain markup ( (1+ratio)**2 ) but gentler so a deep deficit does not price
# the importer out of the only relief available.
TRADE_SCARCITY_EXPONENT = 1.3
# Intra-bloc trade waives the importer tariff AND takes this extra multiplicative discount
# (v2 spec §7: "tariff-free + extra discount"). Allies trade cheap.
TRADE_BLOC_DISCOUNT = 0.15
# A lane is blocked outright when the pair's relation is at or below this. Seeded
# rivalries (-10..-30, RIVAL_RELATION_RANGE negated in worldgen) sit ABOVE this, so a fresh
# rivalry still trades -- just dearer via relation_mod. Only relations driven down by events
# (WAR_DECLARED sets -90) fall through it, so declared enemies cannot trade. War and
# embargoes block independently of relation.
RELATION_TRADE_BLOCK_THRESHOLD = -50.0

# --- M13: logistics -- physical in-flight shipments (v2 spec §5) --------------------
# A matched trade becomes a Shipment that spends real ticks in transit by carrier.
# transit = max(MIN_TRANSPORT_TICKS,
#               ceil(central_angle * <carrier>_TICKS_PER_RADIAN * comms_friction));
# central_angle is in [0, pi]. The floor gives every air, rail, and sea shipment enough
# visible and causal lifetime for progress to matter; distance and poor communications can
# still make a route slower.
MIN_TRANSPORT_TICKS = 7
AIR_TICKS_PER_RADIAN = 2.0
RAIL_TICKS_PER_RADIAN = 4.0
SEA_TICKS_PER_RADIAN = 6.0
# rail is land freight: same region AND within this great-circle angle. Beyond it (or
# cross-region), goods go by sea or air.
RAIL_MAX_ANGLE = 0.9
# Per-tick probability a SEA shipment is lost when its origin or dest is in a war (a war
# zone -- convoys sink). air bypasses blockades; rail (land, same region) is never
# interdicted. This is a PER-TICK hazard that compounds over a shipment's whole sea transit
# (~10-15 ticks) AND over the many shipments in flight, so it is deliberately tiny. TUNED:
# 0.04 made ~46% of war-zone sea runs sink and drove famine 14x over the M12 baseline
# (transit delay alone did NOT -- famine held at ~M12 with interdiction off). Lowered again
# in the 2026-07-22 pacing pass so SHIPMENT_LOST is a rare beat (~a handful/seed), not a
# top-5 flooding headline.
SEA_INTERDICTION_P = 0.0015
SHIPMENT_LOST_STABILITY_HIT = -1.5  # the dest loses expected relief; SHIPMENT_LOST's EventSpec
# Convoy loss reporting (systems/logistics.py). One sunk ship is a fact, not news: a feed
# that prints every sinking is a shipping manifest (34 of 393 lines on seed 7). Losses are
# folded into a per-destination report instead, checked on this cadence. A destination's
# unreported sinkings are reported once there are at least MIN_LOSSES of them, once relief is
# among them, or once the oldest has waited MAX_WAIT ticks -- the last rule is what keeps a
# lone sinking from ever going unreported (a sinking waits at most MAX_WAIT + INTERVAL - 1).
CONVOY_REPORT_INTERVAL_TICKS = 12
CONVOY_REPORT_MIN_LOSSES = 2
CONVOY_REPORT_MAX_WAIT_TICKS = 24
# Commodities flown by preference (high value per unit, low bulk) even absent an emergency.
HIGH_VALUE_COMMODITIES = ("high_tech",)
# Emergency relief: when the importer is famine-critical for a commodity AND the supplier's
# relation is at least this, the shipment is subsidised (price * (1 - discount)) and prefers
# air if the sea lane is blocked. This is "a famine-struck friend gets relief by boat/airlift".
RELIEF_RELATION_MIN = 40.0
RELIEF_PRICE_DISCOUNT = 0.5
# An importer is "famine-critical" for a commodity when its stock is below this many ticks'
# need (mirrors FAMINE_WARNING_DAYS; relief kicks in around the warning line, not only at 0).
RELIEF_CRITICAL_DAYS = 15.0

# --- M14: asset coupling (v2 spec §6) ------------------------------------------------
# Every infra asset class gets a real economic job. QUANTITY couplings (freight) use
# count x max(condition, FREIGHT_CONDITION_FLOOR) for extant fleets; QUALITY couplings
# (production multiplier, trade reach, coordination) use CONDITION ALONE -- a country does
# not buy better price discovery by owning more satellites, and scaling a multiplier by
# count would make large countries superlinear.
#
# Freight: per-tick throughput per (country, carrier) =
# count x max(condition, FREIGHT_CONDITION_FLOOR) x this.
# CALIBRATED, per carrier, at 2x the measured MEAN demand per fleet unit -- the user's
# "~2x headroom" call, read as its own description: "typical trade clears fully, but
# peak-demand ticks and any condition loss meter the flow". Measured over 6 seeds x 600
# ticks (qty routed per fleet unit per tick, counting both endpoints of every lane):
#     sea   mean 0.135  p90 0.287  p99 0.518  max 0.595  -> 0.27
#     rail  mean 0.085  p90 0.191  p99 0.230  max 0.415  -> 0.17
#     air   mean 0.030  p90 0.043  p99 0.101  max 0.289  -> 0.06
# TWO earlier passes were measured and REJECTED, because demand is heavily right-skewed
# (peak ~4-5x mean) and calibrating against the peak makes the whole mechanism inert:
#   - set by eye (1.6/1.6/0.5): ~15x headroom, utilisation never above 58%, NEVER bound.
#   - 2x PEAK (1.2/0.85/0.6): utilisation max 0.496 -- and a direct A/B against unlimited
#     freight moved ZERO units on 3 seeds x 600 ticks, including a seed with 100 ticks of
#     sub-0.5 fleet condition. "2x the peak" means the peak itself only half-fills the hold.
# CAVEAT (adversarial review, 2026-07-24): these means are CONDITIONAL on non-zero demand,
# so real headroom varies by carrier -- sea 2.2x, rail 3.3x, air 4.6x against true means of
# 0.121/0.052/0.013. Saturation is 11.3% of sea slots but 5.6% rail / 3.7% air (6.9% overall),
# so "~11%" describes sea, not the aggregate. Recalibrating rail/air to TRUE mean would even
# the bite out; not done, because it is a behaviour change with no evidence the current
# unevenness is wrong.
# An earlier version of this comment claimed metering was "if anything, calming (crises
# 36.3 -> 29.7, famine 3.3 -> 2.0), total qty shipped unchanged". BOTH claims were WRONG and
# are retracted: they came from 6 unpaired seeds. Paired measurement (n=24, and independently
# n=32 by review) shows metering CUTS total flow ~10% and food flow ~8-24%; the crisis/famine
# figures were noise. See docs/progress.md for the corrected numbers.
# Both endpoints of a lane are charged (a lane needs a fleet to load AND one to unload),
# which is what gives an IMPORTER's own NAVAL_LOSS a cost.
CARRIER_CAPACITY_PER_UNIT = {
    "sea": 0.27,
    "rail": 0.17,
    "air": 0.06,  # air is the low-capacity carrier (spec §5) -- relief/high-value only
}
# A nonzero fleet retains skeletal loading/landing capacity even at condition zero. Without
# this floor, long unstable runs enter an absorbing state: condition reaches zero, trade
# stops, shortages hold stability at zero, and funded recovery exactly cancels instability
# degradation forever. Five percent preserves severe collapse while keeping recovery paths
# and visible traffic alive; a literal count of zero still means zero capacity.
FREIGHT_CONDITION_FLOOR = 0.05
# power_grid -> production multiplier for energy/manufactured/consumer/high_tech (spec §6's
# own list; food/raw_materials are farm and mine, not grid-fed -- which also keeps the only
# SHOCKED extractive commodity, food, out of the per-tick recompute set). Deliberately gentle:
# at genesis condition (0.7-0.9) output is 0.94-0.98x, so M10's aggregate supply/demand floor
# is not disturbed by ordinary operation, while a collapsed grid (0.2) costs ~16% of output.
GRID_PRODUCTION_FLOOR = 0.8
# The commodities the grid feeds (spec §6's own list). NOTE energy is EXTRACTIVE yet appears
# here, so unlike food/raw_materials it is recomputed every tick -- which means an event
# stat_delta against energy OUTPUT would be erased the next tick (the DROUGHT trap that made
# extractive output static in M10). Nothing shocks energy today and
# test_no_event_kind_shocks_a_grid_fed_commodity_output fails loudly if anything starts.
GRID_FED_COMMODITIES = ("energy", "manufactured", "consumer", "high_tech")
# satellites -> trade reach. reach_angle = pi * (floor + (1-floor) * condition).
# CALIBRATION CAVEAT (adversarial review 2026-07-24): this floor was originally set against
# DISPATCHED lane angles (min 0.37 / median 0.95 / max 2.79) -- which is circular, because
# that sample has already been filtered by the very pricing this term is meant to reorder.
# The population it must actually act on is the CANDIDATE pair set: min 0.30 / median 1.83 /
# max 3.07. Against that, reach at condition 0 is pi*0.35 = 1.10 rad, BELOW the candidate
# median, so a failed satellite does price out the majority of a country's options; but
# beyond-reach fires on only ~3% of priced candidates organically, which is why this coupling
# measures as the weakest of the four. See docs/progress.md for the honest assessment.
SAT_REACH_FLOOR = 0.35
# Beyond reach the lane is NOT blocked (user decision 2026-07-23: with only 5-14% global
# slack, hard-gating lanes risks a famine spike). It is priced: a graded premium in proportion
# to how far past reach the partner sits. Since candidates sort by price, this REORDERS who
# supplies whom -- a physical reallocation toward near suppliers, not just a money effect.
TRADE_BEYOND_REACH_PREMIUM = 0.8
# Price DISCOVERY quality (spec §6's "widens spreads"): a degraded satellite pays a spread on
# every trade, in reach or not.
SAT_SPREAD_COEFF = 0.25
# communications -> coordination. A discount d < 1 realises only in proportion to comms
# quality: realised = 1 - (1 - d) * quality. A blacked-out country cannot cash in its
# friendships or its bloc membership. Floor keeps a total blackout at half-realisation rather
# than erasing diplomacy's economic teeth entirely.
COMMS_QUALITY_FLOOR = 0.5
# ... and routes shipments worse: transit *= 1 + COMMS_FRICTION_COEFF * (1 - quality), taking
# the WORST end of the lane (a lane routes as cleanly as its worst-coordinated endpoint).
COMMS_FRICTION_COEFF = 0.5

# --- FxSystem (§6.2) ---
FX_TRADE_DRIFT_COEFFICIENT = 0.002  # specced constant in "1 + 0.002*tanh(trade_balance_norm)"
# drift_from_inflation is named but not formulated in §6.2. Coefficient chosen so its
# typical magnitude (inflation 2-6%) is comparable to the trade term's max (0.002).
# Tuning candidate; see docs/design-decisions.md.
FX_INFLATION_DRIFT_COEFFICIENT = 0.0005

# --- InflationSystem (§6.2) ---
INFLATION_PER_PCT_MINTED = 0.05  # +0.05pp per 1% of money supply minted this tick
INFLATION_SHORTAGE_BUMP = 0.3  # +0.3pp flat if grain shortage
INFLATION_DECAY_RATE = 0.02  # decays 2% of itself per tick...
INFLATION_BASELINE = 2.0  # ...toward this baseline

# --- StabilitySystem (§6.2) ---
# Stability reverts toward a per-country target instead of climbing a flat bonus into the
# clamp at 100. The old rule added +0.22 whenever a country was not actively penalised, so
# the clamp was the only attractor in the model and every country reached it in
# (100 - genesis_draw)/0.22 ticks -- 68 from a draw of 85, 273 from 40. Lowering the bonus
# changed the arrival time and not the destination, which is what made it a mechanism
# problem rather than a tuning one. With everyone at 100, UNREST_THRESHOLD (35) was
# unreachable and a whole class of news could no longer fire.
#
# The rate deliberately matches INFLATION_DECAY_RATE: both of the engine's mean-reverting
# stats close 2% of their gap per tick, and saying it the same way twice is worth more than
# two independently tuned numbers.
STABILITY_REVERSION_RATE = 0.02
# A country's equilibrium is its genesis temperament (Country.base_stability, drawn from
# settings.starting_stability_range) shifted by how well it treats its people, and capped
# below 100 so the ceiling is approached and never sat on. The reference is the genesis mean
# of the four social stats, so the shift is zero at t0 and grows only as a country's record
# actually diverges.
STABILITY_TARGET_CEILING = 92.0
STABILITY_TARGET_SOCIAL_WEIGHT = 0.6
STABILITY_TARGET_SOCIAL_REFERENCE = (
    STARTING_CIVIL_RIGHTS + STARTING_PRESS_FREEDOM + STARTING_EDUCATION + STARTING_HEALTH
) / 4.0
# War is the one condition with no equilibrium: its target is 0, so the reversion term is
# itself a drain at every stability above zero and monotonicity holds by construction
# rather than by the old arithmetic accident of recovery 0.22 < penalty 0.3.
#
# The war penalty SURVIVES that change, and not as a leftover. Reversion alone is
# geometric, and geometric decay never reaches zero: stability would pass under UNREST at
# t+52 and then approach 0 asymptotically, so OCCUPATION_BEGIN's `stability <= 0.0` gate
# (systems/politics.py) would become unreachable -- a war that today guarantees occupation
# at t+333 would never produce one at all. A code review caught it. The additive term makes
# the approach linear near the bottom, so the drain TERMINATES: from 100, a country at war
# reaches exactly 0 in ~102 ticks, about 3.3x faster than the old flat drain and, unlike
# pure reversion, in finite time at all.
#
# Inflation and shortage are additive for a gentler reason: they act as displacements, so a
# country under permanent shortage parks at target - PENALTY/REVERSION_RATE rather than
# sliding to zero.
STABILITY_HIGH_INFLATION_THRESHOLD = 8.0
STABILITY_HIGH_INFLATION_PENALTY = 0.4
STABILITY_SHORTAGE_PENALTY = 0.4
STABILITY_WAR_PENALTY = 0.3
# A god edit to stability carries this share of itself into base_stability, so the edit
# moves where the country SETTLES and not just where it is today. Without it a hand-raised
# country slides visibly back to its genesis temperament within a couple of hundred ticks,
# which would make the most-used god lever the most obviously temporary one. Partial rather
# than whole: the meddler shifts a nation's character, they do not redefine it.
GOD_EDIT_TEMPERAMENT_SHARE = 0.5
# Regime change moves the temperament too, so a nation's equilibrium is not fixed at its
# genesis draw for its whole life. Every change of ruler -- a lost election, a coup, a
# revolution -- converges on LEADER_CHANGE, and the new leader's traits shift where the
# country settles: each of LEADER_GOOD_TRAITS adds the shift, each of LEADER_BAD_TRAITS
# subtracts it. Two good and two bad traits out of eight, so the expected shift of a random
# successor is zero and repeated handovers wander rather than ratchet toward a clamp. Sized
# so one handover is a visible glide (reversion closes 2% of the gap per tick, so about
# 50 ticks) without redrawing the country.
LEADER_TEMPERAMENT_TRAIT_SHIFT = 3.0
LEADER_GOOD_TRAITS = frozenset({"reformist", "technocrat"})
LEADER_BAD_TRAITS = frozenset({"corrupt", "warhawk"})
# A revolution replaces the social contract rather than one ruler: the temperament moves
# this share of the way toward the middle of the genesis range. That is the way off the
# bottom for a collapsed nation -- a coup cannot provide it, because coups only happen below
# COUP_STABILITY_THRESHOLD and a coup-driven fall would rebuild a clamp attractor at zero.
REVOLUTION_TEMPERAMENT_RESET_SHARE = 0.5

# --- InfrastructureSystem (§6.7.2) ---
# §6.7.2 gives the maintenance/degradation/recovery FORMULAS exactly, but every rate is
# named per-class ("BASE_COST[class]", "DEGRADATION_RATE[class]", etc.) with no numeric
# values anywhere in PROPOSAL -- that's 6 classes x 4 rate tables with no basis for
# differentiating them, so v1 uses one uniform constant per rate type across all classes
# rather than inventing unjustified relative costs. Tuning candidates; see
# docs/design-decisions.md.
INFRA_ASSET_CLASSES = (
    "satellites",
    "naval_fleet",
    "air_fleet",
    "rail_network",
    "power_grid",
    "communications",
)
# M14 (2026-07-23) REPLACED the flat `INFRA_BASE_COST_PER_UNIT = 50` minor units per asset
# per tick with a base_gdp-anchored bill (assets.upkeep_cost).
# Measured, the flat constant made upkeep 0.001%-0.1% of tax revenue (a country with a
# 1.1bn treasury and 8.5m/tick of tax paid 1,450/tick of upkeep), so `maintenance_ratio` was
# ALWAYS 1.0, condition pinned at 1.0 for ~98% of country-ticks, and the §6.7.3 failure
# thresholds (0.25-0.40) were effectively unreachable -- which would have left every M14 asset
# coupling inert. Same magnitude mismatch M7.4 flagged behind an unreachable DEBT_CRISIS.
#
# Upkeep is now a PHYSICAL obligation anchored to the country's economic POTENTIAL (base_gdp),
# NOT to current output: revenue is cyclical (gdp_tick scales with 0.5 + stability/200) while
# the asset stock is sticky, so a country in political collapse can no longer cover the upkeep
# it committed to when it was healthy. Anchoring to gdp_tick instead would move both sides
# together and change nothing.
#
# CALIBRATION (why 0.11): tax revenue is STARTING_TAX_RATE * gdp_tick = 0.15 * base_gdp *
# (0.5 + stability/200), i.e. 0.15 * base_gdp at stability 100 down to 0.075 at stability 0.
# Condition only falls when the funding ratio drops below 2/3 (where the degradation and
# recovery rates below cross over), so upkeep must sit near revenue at HIGH stability and
# ~1.5x revenue at LOW stability.
#
# WHAT ACTUALLY DOES THE WORK (corrected by adversarial review 2026-07-24 -- the original
# comment here claimed 0.11 produces "genuine, gradual rot" through the FUNDING channel, and
# that was wrong). Spending is capped at a STOCK (INFRA_MAINTENANCE_SHARE_CAP * treasury), so
# in equilibrium treasury settles where 0.05*T == tax, the cap cancels, and the funding ratio
# converges to tax/upkeep = 0.15*(0.5 + stability/200) / share. At share=0.11 that is 0.682
# even at STABILITY ZERO -- just ABOVE the 2/3 crossover, giving a funding-only condition
# delta of +0.00023/tick. The funding channel alone can therefore never rot an asset; the
# crossover sits at share = 0.1125. What actually rots infrastructure is the PRE-EXISTING
# INFRA_INSTABILITY_DEGRADATION_RATE, which used to be exactly cancelled by the always-1.0
# recovery term -- M14's real mechanism is REMOVING THAT CANCELLATION, not out-spending
# revenue. Measured 2:1 instability:funding (disabling the instability channel alone lifts
# t=5000 mean condition 0.488 -> 0.832), and the funding channel's latency is ~1555 ticks
# median from a country falling below stability 20 to its first underfunded tick.
# 0.11 is KEPT rather than pushed past 0.1125: the observed dynamics are the ones wanted, and
# raising it is a behaviour change that would need its own tuning pass. Tuning candidate.
INFRA_UPKEEP_GDP_SHARE = 0.11
# Upkeep also scales with how big the asset stock is RELATIVE to what this population would
# normally carry (assets.expected_units) -- ~1.0 by construction at genesis, drifting only when
# population moves under a frozen asset stock ("you built for a bigger nation"). The cap stops
# the small-population count floors and M15 annexation capture from bankrupting a country
# outright. M15 transfers surviving asset counts into the winner through this same ratio.
INFRA_UPKEEP_UNIT_RATIO_CAP = 2.0
INFRA_MAINTENANCE_SHARE_CAP = 0.05  # max fraction of treasury spendable on maintenance/tick
INFRA_DEGRADATION_RATE = 0.01  # condition lost per tick at zero maintenance funding
INFRA_INSTABILITY_DEGRADATION_RATE = 0.005  # extra condition lost per tick at stability=0
INFRA_RECOVERY_RATE = 0.005  # condition gained per tick at full maintenance funding

# --- Infrastructure failure rolls (§6.7.3, M7.2) ---
# §6.7.5's own worked example gives the one concrete number in this area: "SATELLITE_
# FAILURE (T+60..90, p=0.4/tick while condition < 0.4)" -- read as "roll each tick while
# below threshold until it fires once," matching the codebase's existing hysteresis
# pattern (systems/thresholds.py): armed on first crossing, one roll sequence per
# below-threshold episode, re-armed only after recovering past a margin. Applied
# uniformly to every class/threshold in engine/assets.py's FAILURE_THRESHOLDS(_EXTRA)
# since no PROPOSAL text differentiates the rate by class. Tuning candidate.
INFRA_FAILURE_ROLL_P = 0.4
INFRA_FAILURE_RECOVERY_MARGIN = 0.1  # re-arms once condition > threshold + margin
# God-mode infrastructure interventions (systems/infrastructure.py). "Disabled" sits below
# every failure threshold in assets.FAILURE_THRESHOLDS, so a blockaded fleet, a blacked-out
# network, or a failed grid is metered by the M14 couplings until upkeep repairs it.
INTERVENTION_DISABLED_CONDITION = 0.1
INTERVENTION_RESTORED_CONDITION = 0.9

# --- RelationsSystem (§5.2) ---
# "-60 -> war-eligible, -30 -> rivals, 0 -> neutral, +50 -> allies" (§6.3's own wording).
RELATION_SHIFT_THRESHOLDS = (-60.0, -30.0, 0.0, 50.0)

# --- Threshold bridge (§6.3) ---
# Trigger points are specced exactly (8%, 35, 15, 10 days, 0, <0). Recovery margins
# (the hysteresis dead-zone width, "re-arm only after recovering past a margin" --
# PROPOSAL's own words, no number given) are tuning candidates; see
# docs/design-decisions.md. 2026-07-22 pacing pass: these margins were WIDENED substantially
# so a crisis is announced ONCE on entry and does not re-fire while a country merely hovers
# near the line -- narrow margins made UNREST/CIVIL_WAR_RISK/INFLATION_CRISIS the bulk of the
# severity-2 flood (a country stuck at low stability re-announced unrest every few ticks).
# A wider dead-zone means the stat must genuinely recover, then relapse, to headline again.
INFLATION_CRISIS_THRESHOLD = 8.0
INFLATION_CRISIS_RECOVERY_MARGIN = 5.0  # re-arms once inflation < threshold - margin (< 3%)
UNREST_THRESHOLD = 35.0
UNREST_RECOVERY_MARGIN = 25.0  # re-arms only once stability climbs back above 60
CIVIL_WAR_RISK_THRESHOLD = 15.0
CIVIL_WAR_RISK_RECOVERY_MARGIN = 25.0  # re-arms only once stability climbs back above 40
FAMINE_WARNING_DAYS = 10.0
FAMINE_WARNING_RECOVERY_DAYS = 25.0
DEBT_CRISIS_RECOVERY_MARGIN = 0.0  # re-arms once treasury > 0

# --- PoliticsSystem (§6.1 slot 9, §6.6.3, §6.8) ---
# None of these have PROPOSAL formulas (§6.6.3 says "p=varies (incumbent traits,
# stability)" and "p=varies" for COUP; §6.8 gives the occupation TRIGGER exactly --
# stability==0 AND at war AND relation < -80 -- but no roll probability for it). All
# tuning candidates; see docs/design-decisions.md ("M3.2 politics").
ELECTION_BASE_CHANGE_P = 0.25  # incumbent voted out, baseline
ELECTION_LOW_STABILITY_THRESHOLD = 50.0  # below this, unrest swings the vote
ELECTION_LOW_STABILITY_BONUS = 0.30
ELECTION_CORRUPT_PENALTY = 0.20  # corrupt incumbents poll worse
ELECTION_GOOD_TRAIT_BONUS = 0.15  # reformist/technocrat incumbents poll better

COUP_STABILITY_THRESHOLD = 25.0  # coups are only rolled for below this
# per-tick roll while below the stability threshold. Was 0.01, which fired ~47 coups/seed
# (a coup somewhere every ~21 ticks) -- lowered ~7x in the 2026-07-22 pacing pass so a coup
# is a rare, punchy event and its CRACKDOWN/LEADER_CHANGE children stop flooding the ticker.
COUP_BASE_P = 0.0014  # per-tick roll while below threshold
COUP_RISK_TRAIT_BONUS = 0.01  # warhawk/corrupt leaders invite coups
COUP_RISK_TRAIT_PENALTY = 0.005  # technocrat/reformist leaders suppress them

OCCUPATION_RELATION_THRESHOLD = -80.0  # §6.8's exact trigger
OCCUPATION_BASE_P = 0.05  # per-tick roll once the §6.8 trigger condition holds

# --- M7.3: conquest/absorption completion (§6.8) ---
# "Can be ANNEXED after 90+ ticks of occupation with low resistance, or LIBERATED if
# stability recovers and a relief war begins" -- §6.8 gives the 90-tick figure exactly;
# "low resistance"/"recovers" and both roll probabilities are undocumented, tuning
# candidates like OCCUPATION_BASE_P above.
ANNEXATION_MIN_OCCUPATION_TICKS = 90
ANNEXATION_LOW_RESISTANCE_STABILITY = 15.0  # above CIVIL_WAR_RISK_THRESHOLD: not revolting
ANNEXATION_BASE_P = 0.05
ANNEXATION_GDP_BOOST = 0.03  # one-shot innovation_mult bump to the annexer, "a GDP boost"
LIBERATION_STABILITY_THRESHOLD = 50.0  # "stability recovers"
LIBERATION_BASE_P = 0.02

# --- M7.4: organic peace (closes the M7.3 TODO in kinds/diplomatic.py -- wars now
# start organically, so they should be able to end organically too, not just via
# god_peace). No stability gate: a country at war reverts toward a target of 0
# (systems/stability.py), so stability falls monotonically for the entire duration
# of a war and never climbs back above a "recovered"
# threshold while the war is still on -- an earlier version of this gated on
# stability > 30 while at war and measurably never fired once in a 20-seed x
# 5000-tick run (wars lasted ~4500-4800/5000 ticks in every sampled case). Flat
# per-tick roll instead, same "condition holds -> roll" shape as OCCUPATION_BASE_P;
# war-weariness accruing regardless of stability is the more honest mechanism anyway.
# Sized against the war drain rather than chosen in isolation, and 0.010 is the same fact as
# "101 ticks". Stability reverts toward 0 while at war and reaches OCCUPATION_BEGIN's
# `stability <= 0.0` gate in 101 ticks from 100 (systems/stability.py), so a flat per-tick
# roll whose expected wait is 1/p matches that deadline at p = 1/101. The drain got 3.3x
# faster when stability became mean-reverting, and this is the same 3.3x applied to the
# other exit: before, peace won about a quarter of the race and, measured over 700 ticks of
# seed 1337, won none of it -- the world went from two wars ending in treaties with nobody
# conquered to no treaty and one annexation. Making conquest reachable had quietly made it
# the only way out, and a map that depopulates is worse than one that stalls.
#
# Note the two sizings differ and the difference is not pedantry: matching the MEAN wait to
# the deadline is not the same as winning half the races. Occupation needs ~121 ticks (the
# gate plus its own OCCUPATION_BASE_P roll), and P(peace first) = 1 - (1-p)^121, which is
# 70% at 0.010 and 52% at 0.006. So 0.010 leaves peace the more likely ending rather than an
# even one, and that is the point rather than a rounding of it: CONQUEST IS IRREVERSIBLE AND
# PEACE IS NOT. A war that ends in a treaty leaves two countries that can fight again, trade
# again, drift apart again; a war that ends in annexation removes a nation permanently, and
# with it every story that nation would have gone on to generate. An outcome that destroys
# future history should be the minority outcome, not a coin flip.
PEACE_BASE_P = 0.010

# --- M9: spatial foundation (v2 spec §1) ---
# The world is an abstract unit sphere, NOT Earth. The radius is Earth-like purely so
# distances read legibly in the UI ("1,530 km" beats "0.24 radians") -- no Earth geography
# is implied. Distance is DERIVED from positions, so the triangle inequality holds by
# construction and no pair of countries can disagree about how far apart they are.
WORLD_RADIUS_KM = 6371.0

# Region centers are a Fibonacci-sphere lattice (near-optimal spacing for any count),
# rotated per-seed. Countries live in disjoint spherical caps around those centers.
# cap_radius = REGION_CAP_FACTOR * (minimum angle between any two region centers).
# At 0.40 the caps use 80% of the available gap, leaving a 20% no-man's-land between
# regions -- that margin is what makes cross-region overlap impossible by construction.
REGION_CAP_FACTOR = 0.40

# min_country_separation = COUNTRY_SEPARATION_FACTOR * cap_radius / sqrt(countries_per_region).
# The sqrt keeps the packing fraction constant (~6.5%) as regions get crowded, so
# rejection sampling never jams regardless of country count. MEASURED: worst case 6 of 64
# attempts, 0 fallbacks across 1000 worlds (region counts 2-6, 8-20 countries).
COUNTRY_SEPARATION_FACTOR = 0.50

# Absolute floor, asserted in tests. Nothing computes against this -- it exists so that a
# future refactor which silently shrinks separation toward zero fails loudly instead of
# producing a globe with countries stacked on top of each other.
COUNTRY_SEPARATION_FLOOR_RAD = 0.05

# Rejection-sampling attempt cap. Measured worst case is 6; 64 is a ~10x safety margin.
# On exhaustion the sampler takes its best candidate rather than looping forever --
# see space.generate_layout. That path is unreached in practice and test-guarded.
POSITION_SAMPLE_ATTEMPTS = 64

REGION_NAMES = (
    "Meridia",
    "Borealis",
    "Australis",
    "Occidenta",
    "Orienta",
    "Pelagia",
)

# --- M9: endowments (v2 spec §2) ---
# The three EXTRACTIVE commodities -- the ones pulled out of the ground/soil, whose output
# is set by territory rather than by industry. M10's other three (manufactured, consumer,
# high-tech) are PRODUCED from these plus GDP, so they have no endowment.
# Endowments are levels in [0, 1]; M10 maps level -> output. Territory-bound: captured on
# annexation (v2 spec §2/§8), which is the payoff that motivates resource wars.
EXTRACTIVE_COMMODITIES = ("energy", "raw_materials", "arable")

# Every country is mediocre at most things and good at one. That asymmetry is the entire
# reason trade exists: a world of self-sufficient countries never trades (v2 spec §2's
# "nobody is self-sufficient in everything").
ENDOWMENT_BASE_RANGE = (0.20, 0.55)
ENDOWMENT_SPECIALIST_RANGE = (0.70, 1.00)

REGION_COUNT_DEFAULT = 4

# --- M9: proximity-correlated relation seeding (v2 spec §1) ---
# Rivalries are drawn from the N nearest country pairs, where N = rival_pairs * this.
# At 3x the pool is wide enough that seeds produce visibly different rivalries, but narrow
# enough that a rivalry between two countries on opposite sides of the world can't be
# seeded -- "friction breeds among neighbours" (§1). Tuning candidate.
RIVAL_CANDIDATE_POOL_MULTIPLIER = 3

# --- M10: commodities & production (v2 spec §3) ---
# Six causal buckets. Order is FIXED and load-bearing: every per-commodity loop iterates
# this tuple so the RNG draw order and event order are pinned. Appending a seventh is a
# deliberate behaviour change (golden regen); reordering is never correct.
COMMODITY_ORDER = (
    "food",
    "energy",
    "raw_materials",
    "manufactured",
    "consumer",
    "high_tech",
)

# Per-capita per-tick need, in commodity units per million population. food matches the
# pre-M10 GRAIN_NEED_PER_CAPITA exactly (0.02) so the food bucket IS the old grain model
# rather than a re-tuned lookalike -- see model.py's grain_* alias properties.
COMMODITY_NEED_PER_CAPITA = {
    "food": 0.02,
    "energy": 0.030,
    "raw_materials": 0.012,
    "manufactured": 0.018,
    "consumer": 0.022,
    "high_tech": 0.006,
}

# Extractive output = endowment_level * this * population, set ONCE at worldgen and then
# moved only by shocks (see the "extractive output is static" decision in Task 5).
# CALIBRATED so that the WORST seed still clears AGGREGATE_BALANCE_MIN -- these are not
# free-hand numbers; re-derive with the Task 3 Step 5 script if you touch them.
EXTRACTIVE_OUTPUT_PER_CAPITA = {
    "food": 0.05543,
    "energy": 0.08477,
    "raw_materials": 0.03311,
}

# Manufactured/consumer/high_tech are PRODUCED, not extracted: per v2 spec §3's table
# their output is driven by ECONOMIC capacity, not territory --
#   manufactured = gdp_tick * innovation_mult
#   consumer     = gdp_tick + labour (population)
#   high_tech    = innovation_mult * education
# This is what delivers §3's headline causal claim, "GDP falls => exports fall".
# Scale factor converting gdp_tick (money/tick) into commodity units/tick. gdp_tick runs
# ~1e7-1e9, hence the tiny coefficients. MEASURED against the real worldgen over 30 seeds
# and calibrated so the WORST seed clears AGGREGATE_BALANCE_MIN with 3% headroom -- not
# hand-picked. Re-derive with the Task 3 Step 5 script if you touch anything upstream.
PRODUCED_GDP_COEFFICIENT = {
    "manufactured": 3.4741e-09,
    "consumer": 4.2461e-09,
    "high_tech": 1.1580e-09,
}
# Share of consumer output driven by labour (population) rather than gdp_tick, per §3's
# "gdp_tick + labour (pop)". Keeps a poor-but-populous country making its own basics.
CONSUMER_LABOUR_PER_CAPITA = 0.008
# high_tech scales with education/100 on top of innovation_mult, per §3's
# "innovation_mult x education".
HIGH_TECH_EDUCATION_REFERENCE = 50.0  # config.STARTING_EDUCATION; the 1.0-multiplier point

# One-hop production chains (v2 spec §3: "legible one-hop chains, not a full input-output
# matrix"). Units of input needed per unit of output. A country starved of energy cannot
# convert its GDP into manufactured exports -- that is the causal chain this encodes.
COMMODITY_INPUTS = {
    "manufactured": {"raw_materials": 0.5, "energy": 0.3},
    "consumer": {"manufactured": 0.4},
    "high_tech": {"energy": 0.2, "manufactured": 0.3},
}

# Starting stock, in ticks' worth of need. Matches the pre-M10 STARTING_GRAIN_STOCK_DAYS
# (30) for food. Stock is a SHOCK BUFFER, not a running balance (see trade.py's docstring).
STARTING_COMMODITY_STOCK_DAYS = 30.0

# Shortage thresholds, in ticks' worth of need remaining. Mirrors the FAMINE_WARNING_DAYS
# hysteresis pair; recovery margin is wider than the trigger, per the existing pattern.
COMMODITY_SHORTAGE_DAYS = 10.0
# Widened in the 2026-07-22 pacing pass (was 12): a wider recovery dead-zone stops the five
# shortage kinds (ENERGY/MATERIALS/MANUFACTURING/CONSUMER/TECH) re-firing while stock
# oscillates just under the trigger line -- one announcement per genuine shortage episode.
COMMODITY_SHORTAGE_RECOVERY_DAYS = 22.0

# How hard a sustained shortage bites. Applied by the registered shortage EventSpecs'
# declarative stat_deltas, NOT by the production system (which owns no narrative effects).
COMMODITY_SHORTAGE_STABILITY_HIT = -2.0
COMMODITY_SHORTAGE_GDP_INNOVATION_HIT = -0.02

# --- M10: the v2 spec §14 worldgen invariant ---
# World aggregate supply/demand ratio per commodity must clear this floor at genesis, for
# EVERY seed. Below it, no amount of trading helps: the world as a whole cannot feed
# itself, every country is short forever, and the shortage cascade fires permanently for
# everyone -- §14's named failure ("trade never clears").
#
# LOWER BOUND ONLY, deliberately. An earlier draft of this plan also imposed an upper
# bound, on the theory that "a world drowning in surplus never trades". That was wrong on
# two counts, both caught in review:
#   1. §14's own text only ever asserts a lower bound.
#   2. Trade volume is driven by the DISPERSION of per-country surplus/deficit, not by the
#      aggregate ratio. A world at aggregate 1.6 with half its countries in deficit trades
#      vigorously; a world at aggregate 1.0 where every country is exactly self-sufficient
#      trades nothing. The aggregate ratio simply does not measure the thing an upper bound
#      was trying to protect -- per-country dispersion does, and
#      test_no_country_is_self_sufficient_in_everything is what actually guards it.
# A band is also unsatisfiable in principle here: the ratio is a POPULATION-WEIGHTED mean
# of endowment level over 8 countries whose populations span 2-60M, so its per-seed spread
# (measured: 1.6-1.8x) exceeds any useful band's width. The ratio is linear in
# EXTRACTIVE_OUTPUT_PER_CAPITA, so tuning that constant slides the whole distribution and
# can never narrow it. A floor is satisfiable; a band is not.
AGGREGATE_BALANCE_MIN = 1.05

# --- M11 Diplomacy & alliances (v2 spec §7) --------------------------------------------
# Formation: a pair may ally when relation is high, they share >= 1 rival, and they are
# within a proximity gate. central_angle (space.py) is in radians, 0 (same point) .. pi
# (antipodal). Probabilities are per-tick per-eligible-pair -- keep LOW; blocs are rare,
# structural, and sticky (pacing: the user asked the world not to be noisy).
ALLIANCE_FORMATION_RELATION_MIN = 50.0  # the pair's relation must exceed this to be eligible
ALLIANCE_SHARED_RIVAL_RELATION = -20.0  # a third country both dislike below this = shared rival
ALLIANCE_SHARED_RIVAL_BONUS = 3.0  # a shared rival multiplies formation p (a driver, NOT a
# hard gate -- rivalries are sparse, so requiring one made formation categorically unreachable)
ALLIANCE_FORMATION_MAX_ANGLE = 1.2  # radians; farther pairs cannot ally (distance-gated §7)
ALLIANCE_FORMATION_BASE_P = 0.03  # per eligible pair per tick (before the shared-rival bonus)
ALLIANCE_RELATION_SET = 70.0  # §5.2: relation set to +70 on ALLIANCE (structural now)

# Mutual defense: when a bloc member is at war with an outsider, each other member MAY join.
# p = base * strength * (relation_to_defender/100) * 1/(1 + k*angle_to_attacker). Distant
# allies hesitate (§7). Gated by settings.max_wars_concurrent so coalitions stay bounded.
MUTUAL_DEFENSE_BASE_P = 0.30
MUTUAL_DEFENSE_DISTANCE_K = 1.5  # angle penalty steepness

# Relation propagation (per tick, per bloc, invisible BLOC_COHESION event): allies warm
# toward each other; a member cools toward anyone another member is at war with. Warming must
# out-run relation decay (relation_decay_rate * |value|, ~0.35/tick at +70) or every bloc
# erodes into a strain-break within ~150 ticks and nothing persists -- blocs are meant to be
# STRUCTURAL (mutual defense needs the bloc to still exist when a war lands). At 0.5 the
# intra-bloc relation settles near +100 (decay balances warming there), so blocs are durable.
BLOC_INTRA_WARMING = 0.5  # + per tick between each intra-bloc pair
BLOC_ENEMY_COOLING = 0.25  # - per tick from each member toward a bloc-mate's enemy

# Strain / breaking: a bloc member may defect. The BASE rate is deliberately tiny -- a healthy
# bloc (warming keeps intra relations near +100, factor==1.0) should almost never break, or it
# is not "structural". The real driver is `factor`, which multiplies the rate up as the
# member's avg relation to its bloc-mates falls below the reference (a bloc soured by
# enemy-cooling / diverging wars). At base 0.0005 a healthy bloc's expected lifetime is
# ~2000 ticks; a soured one (factor 5-40x) breaks in tens of ticks.
ALLIANCE_STRAIN_BASE_P = 0.0005  # per member per tick at/above the reference relation
ALLIANCE_STRAIN_RELATION_REF = 40.0  # avg intra-bloc relation at/below which strain rises
ALLIANCE_BROKEN_RELATION_SET = -10.0  # relation reset between defector and its old bloc-mate

# Embargo: made structural (records the lane on World.embargoes). Trade-lane blocking and
# intra-bloc discounts are M12's (no bilateral lanes exist yet). EMBARGO already carries a
# bite via its TRADE_HALT/CURRENCY_SLIDE consequences (kinds/economy.py), so the structural
# handler only records the lane -- no extra stability constant is needed here.


# --- M15: war & conquest economics (v2 spec §8) --------------------------------------
# Conventional strike fronts are evaluated in sorted attacker/foe order. Both the firing
# rate and the damage multiplier use 1/(1 + k*angle), so nearby enemies are hit sooner AND
# harder while antipodal fronts remain possible rather than being categorically unreachable.
STRIKE_BASE_P = 0.02
STRIKE_DISTANCE_K = 1.5
STRIKE_STABILITY_DAMAGE = 2.0  # maximum at zero distance; scaled by distance factor
# Population attrition is PROPORTIONAL: no single event may take more than this share of
# whoever is left. Losses used to be flat subtractions floored at zero, which meant they
# ARRIVED at exactly 0.0 -- and everything downstream assumes a population exists, so the
# food need scaled to zero with it and FXSystem divided by it (crash at t777 of seed 1337,
# the first thousand-tick run after occupation became reachable). Guarding that one division
# would have left the next one to find. Scaling the damage removes the whole class: a
# fraction of a positive number is still positive, so zero is approached and never reached.
# It is also the better world model -- a devastated rump state is a real thing, while a
# nation blinking out because a subtraction hit a floor is not. Healthy countries are
# unaffected: 0.3M off a 40M nation is far below the cap and still costs exactly 0.3M.
POPULATION_ATTRITION_MAX_FRACTION = 0.25
# Crop shocks (DROUGHT -2.0, LOCUST_SWARM -3.0 food output) take at most this share of a
# country's CURRENT food output per event. The flat magnitudes exceed almost every country's
# whole output (max genesis output is 60M x 0.05543 x arable 1.0 = 3.3; typical is under
# 1.5), and food output is static after worldgen, so a floored flat hit zeroed a country's
# farms permanently: seed 7's TEA, a genesis food exporter, lost all 1.24 to one tick-15
# drought and starved at stability 0 for the rest of the run. Hits still persist and stack.
CROP_SHOCK_MAX_FRACTION = 0.25
STRIKE_POPULATION_DAMAGE = 0.03  # millions (30,000) maximum; scaled by distance factor
STRIKE_INFRA_DAMAGE = 0.012  # condition damage to every asset class; distance-scaled

# Resource-war roots add an economic path to the existing unconditional WAR_DECLARED root.
# Each potential aggressor chooses its single strongest extractive scarcity/rich-neighbour
# opportunity deterministically, then consumes one roll. A hard angle gate prevents wars for
# resources on the far side of the world; the continuous distance factor ranks candidates
# inside it. The base is deliberately low because it is evaluated per active country/tick.
RESOURCE_WAR_BASE_P = 0.01
RESOURCE_WAR_MAX_ANGLE = 1.4
RESOURCE_WAR_DISTANCE_K = 1.5

# Occupation diverts this share of gross extractive flow before the occupied population's
# own consumption. It is credited to the occupier in the same replayable stock-balance event.
OCCUPATION_EXTRACTIVE_TRIBUTE_SHARE = 0.25

# Half of each infrastructure class survives annexation and is captured. Integer flooring is
# deterministic; the remainder is destroyed. Captured condition is count-weighted into the
# annexer's existing stock, giving M14's upkeep/capacity coupling a real conquest consequence.
ANNEXATION_ASSET_CAPTURE_SHARE = 0.50

# Secession (INTERVENE_SECEDE, systems/secession.py). The breakaway takes this share of the
# parent's population, output, stocks, assets, and money; it starts calmer than a country in
# the middle of a split, hostile to its parent, and carrying half of the parent's other
# relationships. A parent smaller than SECESSION_MIN_PARENT_POPULATION cannot split.
SECESSION_SHARE = 0.25
SECESSION_MIN_PARENT_POPULATION = 2.0  # millions
SECESSION_START_STABILITY = 55.0
SECESSION_PARENT_RELATION = -40.0
SECESSION_INHERITED_RELATION_FACTOR = 0.5
# New capitals are sampled within this many separation radii of the parent's capital.
SECESSION_PLACEMENT_RADIUS_FACTOR = 3.0

# Causal attribution is observational: recent mutations can contribute to a threshold
# crossing without changing the resulting event's intrinsic severity.
CAUSE_LOOKBACK_TICKS = 24
MAX_EVENT_CONTRIBUTORS = 3
