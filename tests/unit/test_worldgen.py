import itertools
import re
import statistics

import pytest

from meddler.engine import config, space
from meddler.engine.model import WorldSettings
from meddler.engine.worldgen import generate_world

# Leading vowel cluster, then repeated (consonant-cluster, vowel-cluster) syllables,
# then an optional single trailing consonant. Matches Freedonia/Elbonia/Kravonia/Zubrowka-style
# fictional country names built from CV syllables (PROPOSAL §5.3).
PRONOUNCEABLE_RE = re.compile(
    r"^[aeiou]{0,2}(?:[bcdfghjklmnpqrstvwxyz]{1,3}[aeiou]{1,2})+[bcdfghjklmnpqrstvwxyz]?$",
    re.IGNORECASE,
)


def _world_fields_equal(a, b) -> bool:
    return a.tick == b.tick and a.countries == b.countries and a.relations == b.relations


def test_same_seed_produces_deeply_equal_world():
    settings = WorldSettings()
    w1 = generate_world(1337, settings)
    w2 = generate_world(1337, settings)
    assert _world_fields_equal(w1, w2)
    assert len(w1.log) == 0
    assert len(w2.log) == 0


def test_different_seed_produces_different_names_and_stats():
    settings = WorldSettings()
    w1 = generate_world(1337, settings)
    w2 = generate_world(7331, settings)
    assert [c.name for c in w1.countries] != [c.name for c in w2.countries]
    assert [c.population for c in w1.countries] != [c.population for c in w2.countries]


def test_names_are_pronounceable():
    settings = WorldSettings()
    for seed in range(20):
        world = generate_world(seed, settings)
        for country in world.countries:
            assert PRONOUNCEABLE_RE.match(country.name), (
                f"seed={seed} name={country.name!r} failed pronounceability regex"
            )


def test_exactly_starting_country_count_with_unique_codes():
    settings = WorldSettings(starting_country_count=12)
    world = generate_world(99, settings)
    assert len(world.countries) == 12
    codes = [c.code for c in world.countries]
    assert len(set(codes)) == 12
    assert all(len(code) == 3 and code.isupper() for code in codes)


def test_relations_keys_are_sorted_pairs_and_in_range():
    # M11: genesis now seeds BOTH rivalries (negative) and friendships (positive), so the
    # relation count is bounded by rival_pairs + friendly_pairs, and values span both signs.
    settings = WorldSettings(rival_pairs=4, friendly_pairs=4)
    world = generate_world(42, settings)
    codes = {c.code for c in world.countries}
    assert len(world.relations) <= settings.rival_pairs + settings.friendly_pairs
    for (a, b), value in world.relations.items():
        assert a < b
        assert a in codes and b in codes
        assert -100.0 <= value <= 100.0
    # both a rivalry and a friendship are actually present (the M11 positive-relation seed)
    assert any(v < 0 for v in world.relations.values()), "no rivalry seeded"
    assert any(v > 0 for v in world.relations.values()), "no friendship seeded (M11)"


def test_population_within_specced_range():
    settings = WorldSettings()
    for seed in range(10):
        world = generate_world(seed, settings)
        for country in world.countries:
            assert 2.0 <= country.population <= 60.0


def test_infrastructure_condition_within_genesis_range():
    settings = WorldSettings()
    world = generate_world(5, settings)
    for country in world.countries:
        for asset in (
            country.infrastructure.satellites,
            country.infrastructure.naval_fleet,
            country.infrastructure.air_fleet,
            country.infrastructure.rail_network,
            country.infrastructure.power_grid,
            country.infrastructure.communications,
        ):
            assert 0.7 <= asset.condition <= 0.9
            assert asset.count >= 1


def test_pools_are_int():
    settings = WorldSettings()
    world = generate_world(3, settings)
    for country in world.countries:
        for amount in country.pools.values():
            assert isinstance(amount, int)


def test_every_country_has_a_position_and_region() -> None:
    world = generate_world(1337, WorldSettings())
    for country in world.countries:
        assert -90.0 <= country.position.lat <= 90.0
        assert -180.0 <= country.position.lon <= 180.0
        assert country.region in config.REGION_NAMES


def test_no_two_countries_share_a_position() -> None:
    """The no-overlap invariant, asserted at the worldgen level rather than only in the
    geometry unit tests -- this is the one a future refactor would actually break."""
    world = generate_world(1337, WorldSettings())
    settings = WorldSettings()
    required = space.min_country_separation(settings.region_count, settings.starting_country_count)
    for a, b in itertools.combinations(world.countries, 2):
        assert space.central_angle(a.position, b.position) >= required


@pytest.mark.parametrize("seed", [0, 1, 42, 1337, 8675309])
def test_no_two_countries_overlap_across_seeds(seed: int) -> None:
    settings = WorldSettings()
    world = generate_world(seed, settings)
    required = space.min_country_separation(settings.region_count, settings.starting_country_count)
    for a, b in itertools.combinations(world.countries, 2):
        assert space.central_angle(a.position, b.position) >= required


def test_every_country_has_an_endowment_for_every_extractive_commodity() -> None:
    world = generate_world(1337, WorldSettings())
    for country in world.countries:
        assert set(country.endowments) == set(config.EXTRACTIVE_COMMODITIES)
        for level in country.endowments.values():
            assert 0.0 <= level <= 1.0


def test_every_extractive_commodity_has_at_least_one_specialist() -> None:
    """If no country is good at energy, the energy market can never clear and everyone
    starves for it forever. Guaranteed by construction (round-robin specialties), not by
    luck -- so this holds for EVERY seed, not merely most.

    NOT v2 spec §14's invariant, which is the stronger "aggregate supply meets aggregate
    demand" and cannot be asserted until M10 defines demand. See the plan's Self-Review.

    Holds only for starting_country_count >= len(EXTRACTIVE_COMMODITIES); the default is 8.
    """
    assert WorldSettings().starting_country_count >= len(config.EXTRACTIVE_COMMODITIES)
    for seed in range(20):
        world = generate_world(seed, WorldSettings())
        for commodity in config.EXTRACTIVE_COMMODITIES:
            best = max(c.endowments[commodity] for c in world.countries)
            assert best >= config.ENDOWMENT_SPECIALIST_RANGE[0], (
                f"seed {seed}: nobody specialises in {commodity}"
            )


def test_worldgen_is_still_deterministic_for_a_seed() -> None:
    a = generate_world(1337, WorldSettings())
    b = generate_world(1337, WorldSettings())
    assert [(c.code, c.position, c.region, c.endowments) for c in a.countries] == [
        (c.code, c.position, c.region, c.endowments) for c in b.countries
    ]


def test_rivals_are_nearer_than_the_typical_country_pair() -> None:
    """Proximity-correlated seeding (v2 spec §1): friction breeds among neighbours.

    Deliberately does NOT re-derive the implementation's candidate-pool formula -- a test
    that recomputes `rival_pairs * RIVAL_CANDIDATE_POOL_MULTIPLIER` and sorts by the same
    key would pass even if that formula were wrong, since it would be wrong identically on
    both sides. Instead this asserts the PROPERTY the spec actually asks for, measured
    independently: rivals are closer together than a typical pair of countries.
    """
    distances: dict[tuple[str, str], float] = {}
    rival_distances: list[float] = []
    for seed in range(10):
        world = generate_world(seed, WorldSettings())
        distances = {
            (min(a.code, b.code), max(a.code, b.code)): space.central_angle(a.position, b.position)
            for a, b in itertools.combinations(world.countries, 2)
        }
        assert world.relations, f"seed {seed}: expected seeded rivalries"
        median = statistics.median(distances.values())
        for pair in world.relations:
            assert distances[pair] < median, (
                f"seed {seed}: rivals {pair} are farther apart than the median country pair"
            )
            rival_distances.append(distances[pair])
    assert rival_distances
