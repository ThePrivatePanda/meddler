"""Geometry primitives for the v2 spatial foundation (M9, v2 spec §1)."""

from __future__ import annotations

import itertools
import math

import pytest

from meddler.engine import config, space
from meddler.engine.rng import Rng


def test_position_unit_vector_is_unit_length() -> None:
    p = space.Position(lat=37.0, lon=-122.0)
    x, y, z = p.unit_vector()
    assert math.isclose(math.sqrt(x * x + y * y + z * z), 1.0, rel_tol=1e-12)


def test_central_angle_of_a_position_with_itself_is_zero() -> None:
    p = space.Position(lat=12.5, lon=44.0)
    assert space.central_angle(p, p) == pytest.approx(0.0, abs=1e-9)


def test_central_angle_of_antipodes_is_pi() -> None:
    north = space.Position(lat=90.0, lon=0.0)
    south = space.Position(lat=-90.0, lon=0.0)
    assert space.central_angle(north, south) == pytest.approx(math.pi, abs=1e-9)


def test_central_angle_of_a_quarter_turn_is_half_pi() -> None:
    a = space.Position(lat=0.0, lon=0.0)
    b = space.Position(lat=0.0, lon=90.0)
    assert space.central_angle(a, b) == pytest.approx(math.pi / 2, abs=1e-9)


def test_central_angle_is_symmetric() -> None:
    a = space.Position(lat=10.0, lon=20.0)
    b = space.Position(lat=-40.0, lon=100.0)
    assert space.central_angle(a, b) == pytest.approx(space.central_angle(b, a), abs=1e-12)


def test_distance_km_scales_the_angle_by_the_world_radius() -> None:
    a = space.Position(lat=0.0, lon=0.0)
    b = space.Position(lat=0.0, lon=90.0)
    expected = (math.pi / 2) * config.WORLD_RADIUS_KM
    assert space.distance_km(a, b) == pytest.approx(expected, rel=1e-9)


def test_region_centers_returns_the_requested_count_of_unit_vectors() -> None:
    centers = space.region_centers(4)
    assert len(centers) == 4
    for c in centers:
        assert math.isclose(math.sqrt(sum(x * x for x in c)), 1.0, rel_tol=1e-12)


def test_region_centers_are_well_separated() -> None:
    """The Fibonacci lattice's whole job: no two region centers may bunch up, or two
    'different' landmasses would overlap. Compares against the spacing you'd get from a
    perfectly even spread (4*pi/count steradians each => this angular radius)."""
    for count in (2, 3, 4, 5, 6):
        centers = space.region_centers(count)
        even_spread = 2 * math.asin(math.sqrt(1.0 / count))
        assert space.min_center_separation(centers) > 0.75 * even_spread


@pytest.mark.parametrize("region_count", [2, 3, 4, 5, 6])
def test_region_caps_are_disjoint(region_count: int) -> None:
    """Two caps of radius r whose centers are d apart overlap iff 2r > d. Disjoint caps
    are what make cross-region country overlap structurally impossible."""
    centers = space.region_centers(region_count)
    smallest_gap = space.min_center_separation(centers)
    assert 2 * space.cap_radius(region_count) < smallest_gap


@pytest.mark.parametrize("region_count", [2, 3, 4, 5, 6])
@pytest.mark.parametrize("country_count", [2, 4, 8, 20])
def test_cross_region_gap_is_never_tighter_than_the_required_country_separation(
    region_count: int, country_count: int
) -> None:
    """Cap geometry never works AGAINST the invariant.

    Pins the exact boundary rather than hiding it: gap = 0.20*S and min_sep =
    0.20*S/sqrt(per_region), so the gap merely EQUALS min_sep when per_region == 1 and
    exceeds it beyond that -- it is never tighter. Note this is a >= assertion, not >:
    at country_count <= region_count they are exactly equal, and country_count in
    [2, 4] at region_count 4 is a config the wider suite runs constantly.

    Cap geometry is therefore NOT what guarantees no-overlap -- generate_layout's direct
    clearance check against every placed country is (see test_no_two_countries_overlap).
    This test only proves the geometry doesn't fight it.
    """
    centers = space.region_centers(region_count)
    gap = space.min_center_separation(centers) - 2 * space.cap_radius(region_count)
    assert gap >= space.min_country_separation(region_count, country_count) - 1e-12


@pytest.mark.parametrize("region_count", [2, 3, 4, 5, 6])
@pytest.mark.parametrize("country_count", [8, 20])
def test_min_country_separation_stays_above_the_absolute_floor(
    region_count: int, country_count: int
) -> None:
    assert (
        space.min_country_separation(region_count, country_count)
        >= config.COUNTRY_SEPARATION_FLOOR_RAD
    )


def test_land_connected_is_same_region() -> None:
    assert space.land_connected("Meridia", "Meridia") is True
    assert space.land_connected("Meridia", "Borealis") is False


def _layout(seed: int, country_count: int = 8, region_count: int = 4) -> list[space.Placement]:
    return space.generate_layout(Rng(seed), country_count, region_count)


def test_generate_layout_returns_one_placement_per_country() -> None:
    assert len(_layout(1337)) == 8


def test_generate_layout_is_deterministic_for_a_seed() -> None:
    a = _layout(1337)
    b = _layout(1337)
    assert [(p.region, p.position) for p in a] == [(q.region, q.position) for q in b]


def test_generate_layout_differs_across_seeds() -> None:
    assert [p.position for p in _layout(1)] != [p.position for p in _layout(2)]


def test_generate_layout_uses_only_named_regions() -> None:
    for p in _layout(1337):
        assert p.region in config.REGION_NAMES


def test_generate_layout_spreads_countries_across_all_regions() -> None:
    regions = {p.region for p in _layout(1337, country_count=8, region_count=4)}
    assert len(regions) == 4


@pytest.mark.parametrize("region_count", [2, 3, 4, 5, 6])
@pytest.mark.parametrize("country_count", [2, 4, 8, 20])
@pytest.mark.parametrize("seed", [0, 1, 7, 1337, 99999])
def test_no_two_countries_overlap(seed: int, country_count: int, region_count: int) -> None:
    """THE no-overlap invariant. A globe with two countries stacked on the same point is
    the specific failure this whole module's geometry exists to prevent.

    country_count includes 2 and 4 deliberately: those are the per_region == 1 configs
    where the cap-geometry margin collapses to zero, so they are precisely where a
    regression would hide. They are also what the wider suite actually runs.
    """
    placements = _layout(seed, country_count, region_count)
    required = space.min_country_separation(region_count, country_count)
    for a, b in itertools.combinations(placements, 2):
        angle = space.central_angle(a.position, b.position)
        assert angle >= required, (
            f"{a.region} and {b.region} countries are {angle:.4f} rad apart, "
            f"closer than the required {required:.4f}"
        )


@pytest.mark.parametrize("seed", range(25))
def test_no_two_countries_overlap_at_the_max_country_cap(seed: int) -> None:
    """WorldSettings.max_countries is 20 -- the densest world the engine allows."""
    placements = _layout(seed, country_count=20, region_count=4)
    required = space.min_country_separation(4, 20)
    for a, b in itertools.combinations(placements, 2):
        assert space.central_angle(a.position, b.position) >= required


def test_countries_sit_inside_their_own_regions_cap() -> None:
    """Membership is geometric, not just a label: a country tagged Meridia must actually
    BE in Meridia's cap, or land_connected() would be lying about who neighbours whom."""
    region_count = 4
    rng = Rng(1337)
    placements = space.generate_layout(rng, 12, region_count)
    centers = space.rotated_region_centers(Rng(1337), region_count)
    radius = space.cap_radius(region_count)
    for placement in placements:
        center = centers[config.REGION_NAMES.index(placement.region)]
        assert space.region_angle(placement.position, center) <= radius + 1e-9


def test_same_region_countries_are_land_connected() -> None:
    placements = _layout(1337, country_count=8, region_count=4)
    by_region: dict[str, list[space.Placement]] = {}
    for p in placements:
        by_region.setdefault(p.region, []).append(p)
    pairs = [group for group in by_region.values() if len(group) >= 2]
    assert pairs, "expected at least one region with two countries at 8/4"
    for group in pairs:
        assert space.land_connected(group[0].region, group[1].region) is True
