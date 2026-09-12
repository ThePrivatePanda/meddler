"""M14 asset coupling: the engine/assets.py helpers every economic system now reads.

The r4 note's rule is that count/condition math lives in ONE module; these tests pin the
shape of each coupling (quantity vs quality) so a call site cannot quietly reinvent it.
"""

import math

import pytest

from meddler.engine import assets, config
from meddler.engine.model import WorldSettings
from meddler.engine.worldgen import generate_world


def _country():
    world = generate_world(11, WorldSettings(starting_country_count=2))
    return world.countries[0]


def _set_condition(country, asset_class: str, value: float) -> None:
    getattr(country.infrastructure, asset_class).condition = value


# --- upkeep (the fiscal magnitude fix) ------------------------------------------------


def test_upkeep_cost_is_a_real_share_of_the_economy_not_a_rounding_error():
    """The pre-M14 flat constant made upkeep ~0.01% of tax revenue, which is why condition
    never moved. Upkeep must now be comparable to what the country actually collects."""
    country = _country()
    revenue = config.STARTING_TAX_RATE * country.gdp_tick
    cost = assets.upkeep_cost(country)
    assert cost > 0.5 * revenue
    assert cost < 2.0 * revenue


def test_upkeep_cost_is_anchored_to_potential_not_to_current_output():
    """base_gdp, not gdp_tick: a country whose output collapses still owes the same upkeep.
    That gap is the whole mechanism by which instability rots infrastructure."""
    country = _country()
    before = assets.upkeep_cost(country)
    country.gdp_tick *= 0.4
    assert assets.upkeep_cost(country) == pytest.approx(before)


def test_upkeep_cost_scales_with_base_gdp():
    country = _country()
    before = assets.upkeep_cost(country)
    country.base_gdp *= 3.0
    assert assets.upkeep_cost(country) == pytest.approx(3.0 * before)


def test_upkeep_cost_rises_when_the_asset_stock_outgrows_the_population():
    country = _country()
    before = assets.upkeep_cost(country)
    country.population *= 0.5  # same frozen fleet, half the nation to pay for it
    assert assets.upkeep_cost(country) > before


def test_upkeep_unit_ratio_is_capped():
    country = _country()
    country.population = 0.001
    ratio_capped = (
        country.base_gdp * config.INFRA_UPKEEP_GDP_SHARE * config.INFRA_UPKEEP_UNIT_RATIO_CAP
    )
    assert assets.upkeep_cost(country) == pytest.approx(ratio_capped)


def test_expected_units_matches_the_genesis_asset_stock():
    """worldgen builds counts from this same function, so a fresh country's ratio is 1.0."""
    country = _country()
    actual = sum(
        getattr(country.infrastructure, cls).count for cls in config.INFRA_ASSET_CLASSES
    )
    assert assets.expected_units(country.population) == actual


# --- freight throughput (QUANTITY: count x condition) ---------------------------------


def test_freight_capacity_is_count_times_condition_times_the_per_unit_rate():
    country = _country()
    _set_condition(country, "naval_fleet", 0.5)
    expected = (
        country.infrastructure.naval_fleet.count * 0.5 * config.CARRIER_CAPACITY_PER_UNIT["sea"]
    )
    assert assets.freight_capacity(country, "sea") == pytest.approx(expected)


def test_each_carrier_reads_its_own_asset_class_with_residual_capacity():
    country = _country()
    for cls in ("naval_fleet", "rail_network", "air_fleet"):
        _set_condition(country, cls, 1.0)
    _set_condition(country, "rail_network", 0.0)
    expected_rail = (
        country.infrastructure.rail_network.count
        * config.FREIGHT_CONDITION_FLOOR
        * config.CARRIER_CAPACITY_PER_UNIT["rail"]
    )
    assert assets.freight_capacity(country, "rail") == pytest.approx(expected_rail)
    assert assets.freight_capacity(country, "sea") > expected_rail
    assert assets.freight_capacity(country, "air") > 0.0


def test_zero_condition_fleet_retains_five_percent_skeletal_capacity():
    country = _country()
    _set_condition(country, "naval_fleet", 0.0)
    expected = (
        country.infrastructure.naval_fleet.count
        * config.FREIGHT_CONDITION_FLOOR
        * config.CARRIER_CAPACITY_PER_UNIT["sea"]
    )
    assert assets.freight_capacity(country, "sea") == pytest.approx(expected)


def test_literal_zero_count_fleet_can_carry_nothing():
    country = _country()
    country.infrastructure.naval_fleet.count = 0
    _set_condition(country, "naval_fleet", 1.0)
    assert assets.freight_capacity(country, "sea") == 0.0


# --- power_grid -> production multiplier (QUALITY: condition only) --------------------


def test_production_multiplier_is_one_at_full_grid_condition():
    country = _country()
    _set_condition(country, "power_grid", 1.0)
    assert assets.production_multiplier(country) == pytest.approx(1.0)


def test_production_multiplier_floors_at_a_blacked_out_grid():
    country = _country()
    _set_condition(country, "power_grid", 0.0)
    assert assets.production_multiplier(country) == pytest.approx(config.GRID_PRODUCTION_FLOOR)


def test_production_multiplier_ignores_grid_count():
    """Quality coupling: owning more power stations must not multiply output, or large
    countries become superlinear producers."""
    country = _country()
    _set_condition(country, "power_grid", 0.6)
    before = assets.production_multiplier(country)
    country.infrastructure.power_grid.count *= 10
    assert assets.production_multiplier(country) == pytest.approx(before)


# --- satellites -> reach & discovery (QUALITY) ----------------------------------------


def test_full_satellites_reach_the_whole_world():
    country = _country()
    _set_condition(country, "satellites", 1.0)
    assert assets.trade_reach_angle(country) == pytest.approx(math.pi)


def test_a_failed_satellite_pulls_reach_under_the_typical_lane():
    """Candidate pair angles: median 1.83, max 3.07 rad. Below the §6.7.3 SATELLITE_FAILURE
    threshold (0.4) reach must fall far enough that distant partners are actually penalised
    -- i.e. inside the observed lane range, not merely below the antipodal maximum. How far
    inside is the SAT_REACH_FLOOR tuning call, deliberately not pinned here."""
    country = _country()
    _set_condition(country, "satellites", 1.0)
    healthy = assets.trade_reach_angle(country)
    _set_condition(country, "satellites", 0.35)
    failed = assets.trade_reach_angle(country)
    # 3.07 rad is the longest CANDIDATE pair observed (the dispatched-lane max, 2.79, is a
    # post-selection figure and was the wrong bar to cite here -- adversarial review 2026-07-24).
    assert failed < 3.07
    assert failed < 0.75 * healthy


def test_reach_is_monotonic_in_condition():
    country = _country()
    reaches = []
    for cond in (0.0, 0.25, 0.5, 0.75, 1.0):
        _set_condition(country, "satellites", cond)
        reaches.append(assets.trade_reach_angle(country))
    assert reaches == sorted(reaches)
    assert reaches[0] == pytest.approx(math.pi * config.SAT_REACH_FLOOR)


# --- communications -> coordination (QUALITY) -----------------------------------------


def test_comms_quality_spans_floor_to_one():
    country = _country()
    _set_condition(country, "communications", 1.0)
    assert assets.comms_quality(country) == pytest.approx(1.0)
    _set_condition(country, "communications", 0.0)
    assert assets.comms_quality(country) == pytest.approx(config.COMMS_QUALITY_FLOOR)


def test_realised_discount_shrinks_with_comms():
    """A 0.8x discount realises fully with perfect comms and only partly with poor comms --
    but never inverts into a PREMIUM (that would make a blackout profitable)."""
    country = _country()
    _set_condition(country, "communications", 1.0)
    assert assets.realised_discount(0.8, assets.comms_quality(country)) == pytest.approx(0.8)
    _set_condition(country, "communications", 0.0)
    poor = assets.realised_discount(0.8, assets.comms_quality(country))
    assert 0.8 < poor < 1.0


def test_realised_discount_leaves_a_premium_untouched():
    """Coordination governs DISCOUNTS. A relation_mod above 1.0 (an unfriendly pair paying
    more) is not something good comms should be able to bargain away."""
    assert assets.realised_discount(1.3, 0.5) == pytest.approx(1.3)
