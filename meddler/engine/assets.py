"""Infrastructure asset helpers. PROPOSAL §6.7.1-§6.7.3, r4 note §12.1.1/§12.3.3.

r4 note (binding for v1 code shape): post-v1, asset classes become load-bearing (trade
volume capped by fleet capacity, staple distribution by rail, GDP by grid throughput) --
route all count/condition effect math through helpers here, never inline `count *
condition` at call sites, so future capacity-based mechanics only need to change this one
module. `InfrastructureSystem` (M2's maintenance/degradation) and its M7.2 failure-roll
extension both read thresholds from here rather than duplicating magic numbers.

M14 (v2 spec §6) is the milestone that cashed that note in: every class now has a real
economic job, and every one of them is computed here.

QUANTITY vs QUALITY -- the distinction the whole module turns on:
- FREIGHT throughput is `count x max(condition, FREIGHT_CONDITION_FLOOR)` for nonzero fleets.
  More ships genuinely move more cargo, while a collapsed but extant fleet retains skeletal
  capacity so trade cannot enter an absorbing zero-flow deadlock. Literal count zero remains zero.
- PRODUCTION multiplier, trade REACH and COORDINATION are functions of `condition` ALONE.
  A country does not buy better price discovery by owning more satellites, and scaling a
  multiplier by count would make large countries superlinear producers.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from meddler.engine import config
from meddler.engine.config import INFRA_ASSET_CLASSES
from meddler.engine.model import AssetClass, InfrastructureBlock

if TYPE_CHECKING:
    from meddler.engine.model import Country


def get_asset(block: InfrastructureBlock, asset_class: str) -> AssetClass:
    asset: AssetClass = getattr(block, asset_class)
    return asset


def effective_capacity(asset: AssetClass) -> float:
    """count x condition -- the ONE place this multiplication happens (r4 note)."""
    return asset.count * asset.condition


def all_asset_classes() -> tuple[str, ...]:
    return INFRA_ASSET_CLASSES


# --- asset stock sizing (shared with worldgen) ----------------------------------------


def infra_counts_for_population(population: float) -> dict[str, int]:
    """Counts derived from population tier (§5.3), scaled so small and large nations both
    get legible (nonzero, not enormous) asset fleets.

    Lives here rather than in worldgen because M14's upkeep formula needs the same baseline
    to answer "is this asset stock oversized for this population?" -- two copies of the
    table would drift.
    """
    scale = max(1, round(population / 10))
    return {
        "satellites": max(1, scale // 3),
        "naval_fleet": max(2, scale),
        "air_fleet": max(2, scale),
        "rail_network": max(3, scale * 2),
        "power_grid": max(3, scale * 2),
        "communications": max(2, scale),
    }


def expected_units(population: float) -> int:
    """Total asset units a country of this population would normally carry."""
    return sum(infra_counts_for_population(population).values())


def total_units(country: "Country") -> int:
    return sum(getattr(country.infrastructure, cls).count for cls in INFRA_ASSET_CLASSES)


# --- upkeep: the fiscal anchor (M14) --------------------------------------------------


def upkeep_cost(country: "Country") -> float:
    """Per-tick maintenance bill, in local minor units.

    Anchored to `base_gdp` (economic POTENTIAL), never to `gdp_tick` (current output): the
    asset stock is a sticky physical obligation while revenue is cyclical, so a country whose
    stability -- and therefore output -- collapses can no longer cover the upkeep it took on
    while healthy. That gap is precisely how instability comes to rot infrastructure, and it
    is the reason the pre-M14 flat per-unit constant produced no dynamics at all (see
    config.INFRA_UPKEEP_GDP_SHARE's own note for the measurements).
    """
    ratio = total_units(country) / expected_units(country.population)
    ratio = min(config.INFRA_UPKEEP_UNIT_RATIO_CAP, ratio)
    return country.base_gdp * config.INFRA_UPKEEP_GDP_SHARE * ratio


# --- freight throughput: QUANTITY (count x condition) ---------------------------------

CARRIER_ASSET_CLASS: dict[str, str] = {
    "sea": "naval_fleet",
    "rail": "rail_network",
    "air": "air_fleet",
}


def freight_capacity(country: "Country", carrier: str) -> float:
    """Commodity units this country can load or land by `carrier` in one tick (v2 spec §5's
    "effective throughput per carrier per country = capacity x fleet_condition").

    Charged at BOTH ends of a lane by TradeSystem: a shipment needs a fleet to load it and a
    fleet to land it, which is what gives an importer's own NAVAL_LOSS an economic cost.
    """
    asset = get_asset(country.infrastructure, CARRIER_ASSET_CLASS[carrier])
    if asset.count <= 0:
        return 0.0
    condition = max(asset.condition, config.FREIGHT_CONDITION_FLOOR)
    return asset.count * condition * config.CARRIER_CAPACITY_PER_UNIT[carrier]


# --- QUALITY couplings: condition only -------------------------------------------------


def production_multiplier(country: "Country") -> float:
    """power_grid -> output multiplier for the grid-fed commodities (v2 spec §6).

    Applied by systems/commodity_production.py, deliberately NOT inside
    commodities.genesis_output: that function states POTENTIAL supply, which is what M10's
    §14 aggregate supply/demand floor is calibrated against.
    """
    condition = country.infrastructure.power_grid.condition
    floor = config.GRID_PRODUCTION_FLOOR
    return floor + (1.0 - floor) * condition


def trade_reach_angle(country: "Country") -> float:
    """satellites -> the great-circle angle (radians) this country can trade over
    efficiently. Beyond it, partners are PRICED at a premium rather than blocked -- with only
    5-14% global slack per commodity, hard-gating lanes would manufacture famine.

    Judge this against the CANDIDATE pair distribution (median 1.83 rad), not the dispatched-
    lane one (median 0.95): dispatched lanes are already selected by the pricing this term
    exists to change, so calibrating against them is circular. See config.SAT_REACH_FLOOR.
    """
    condition = country.infrastructure.satellites.condition
    floor = config.SAT_REACH_FLOOR
    return math.pi * (floor + (1.0 - floor) * condition)


def price_discovery_spread(country: "Country") -> float:
    """satellites -> the spread a degraded observer pays on every trade (spec §6's "widens
    spreads"). 1.0 at full condition."""
    condition = country.infrastructure.satellites.condition
    return 1.0 + config.SAT_SPREAD_COEFF * (1.0 - condition)


def comms_quality(country: "Country") -> float:
    """communications -> coordination quality in [floor, 1]: how well this country realises
    the discounts its diplomacy has earned, and how cleanly its lanes route."""
    condition = country.infrastructure.communications.condition
    floor = config.COMMS_QUALITY_FLOOR
    return floor + (1.0 - floor) * condition


def realised_discount(factor: float, quality: float) -> float:
    """Interpolate a price DISCOUNT toward neutral as coordination degrades.

    A factor >= 1.0 is a premium, not a discount, and is returned untouched -- otherwise poor
    comms would bargain away the price an unfriendly pair is supposed to pay, making a
    blackout profitable.
    """
    if factor >= 1.0:
        return factor
    return 1.0 - (1.0 - factor) * quality


# §6.7.3's failure table: asset class -> (condition threshold, the EventSpec kind that
# rolls when condition falls below it). naval_fleet carries TWO independent thresholds
# (0.4 for NAVAL_LOSS, a lower 0.25 for PORT_CLOSURE) since the table lists both against
# the same asset class -- see FAILURE_THRESHOLDS_EXTRA below for the second one.
FAILURE_THRESHOLDS: dict[str, tuple[float, str]] = {
    "satellites": (0.4, "SATELLITE_FAILURE"),
    "naval_fleet": (0.4, "NAVAL_LOSS"),
    "rail_network": (0.35, "RAIL_COLLAPSE"),
    "power_grid": (0.35, "POWER_OUTAGE"),
    "communications": (0.3, "COMMS_BLACKOUT"),
}
# Second, lower threshold on naval_fleet (§6.7.3's own table lists PORT_CLOSURE at 0.25
# alongside NAVAL_LOSS at 0.4 for the same asset class) -- kept as a separate mapping
# rather than widening FAILURE_THRESHOLDS to a list, since it's the only asset class with
# two failure kinds and a list-per-class would complicate every other entry for one case.
FAILURE_THRESHOLDS_EXTRA: dict[str, tuple[float, str]] = {
    "naval_fleet": (0.25, "PORT_CLOSURE"),
}
