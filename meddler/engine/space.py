"""Abstract spherical geometry: the distance primitive every v2 system reads.

v2 spec §1. The world is a unit sphere, NOT Earth -- lat/lon are storage coordinates
for an abstract globe, chosen over a raw (x, y, z) vector because the frontend needs
to place a blob and a human reading a dossier needs a legible number.

Distance is DERIVED from positions rather than stored per-pair, so it is automatically
self-consistent: the triangle inequality holds and no two countries can ever disagree
about how far apart they are.

LAYERING: this is a leaf module. It imports config and rng ONLY -- never model, because
model imports Position from here. Anything needing a Country belongs in worldgen or a
system, not here (hence land_connected takes region strings, not Country objects).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from meddler.engine import config
from meddler.engine.rng import Rng

Vector = tuple[float, float, float]


@dataclass(frozen=True)
class Position:
    """A point on the abstract unit sphere. Degrees, not radians -- the frontend and the
    dossier both want degrees, and radians are an implementation detail of distance."""

    lat: float  # degrees, [-90, 90]
    lon: float  # degrees, (-180, 180]

    def unit_vector(self) -> Vector:
        lat_r = math.radians(self.lat)
        lon_r = math.radians(self.lon)
        cos_lat = math.cos(lat_r)
        return (cos_lat * math.cos(lon_r), cos_lat * math.sin(lon_r), math.sin(lat_r))


def _from_vector(v: Vector) -> Position:
    x, y, z = _normalise(v)
    return Position(lat=math.degrees(math.asin(max(-1.0, min(1.0, z)))), lon=math.degrees(math.atan2(y, x)))


def _normalise(v: Vector) -> Vector:
    norm = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if norm == 0.0:  # pragma: no cover - defensive; callers never build a zero vector
        raise ValueError("cannot normalise a zero vector")
    return (v[0] / norm, v[1] / norm, v[2] / norm)


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _angle_between(a: Vector, b: Vector) -> float:
    # atan2(|a x b|, a.b) rather than acos(a.b): acos loses catastrophic precision for
    # near-parallel vectors, which is exactly the regime the min-separation check cares
    # about most (two countries almost on top of each other).
    cross = _cross(a, b)
    sin_len = math.sqrt(cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2)
    return math.atan2(sin_len, _dot(a, b))


def central_angle(a: Position, b: Position) -> float:
    """Great-circle angle between two positions, in radians [0, pi]."""
    return _angle_between(a.unit_vector(), b.unit_vector())


def distance_km(a: Position, b: Position) -> float:
    """Great-circle distance in abstract kilometres (v2 spec §1)."""
    return central_angle(a, b) * config.WORLD_RADIUS_KM


def region_centers(count: int) -> list[Vector]:
    """`count` near-evenly spaced points on the sphere (Fibonacci lattice).

    Deterministic and generic in `count`, which beats a hand-written table of platonic
    solids: it needs no special-casing per region count and stays near-optimally spaced
    for any of them. The per-seed rotation is applied later (generate_layout) so this
    stays a pure, cacheable function of `count` alone.
    """
    if count < 1:
        raise ValueError(f"region count must be >= 1, got {count}")
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    centers: list[Vector] = []
    for i in range(count):
        z = 1.0 - (2.0 * i + 1.0) / count
        radius = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden_angle * i
        centers.append((radius * math.cos(theta), radius * math.sin(theta), z))
    return centers


def min_center_separation(centers: list[Vector]) -> float:
    """Smallest angle between any two region centers. Single-region worlds have no pair,
    so they get the whole hemisphere (pi) -- the cap then covers most of the sphere,
    which is the correct degenerate behaviour."""
    if len(centers) < 2:
        return math.pi
    return min(_angle_between(a, b) for a, b in itertools.combinations(centers, 2))


def cap_radius(region_count: int) -> float:
    """Angular radius of the spherical cap each region's countries are sampled inside."""
    return config.REGION_CAP_FACTOR * min_center_separation(region_centers(region_count))


def countries_per_region(region_count: int, country_count: int) -> int:
    """Countries are assigned round-robin, so the busiest region holds the ceiling."""
    return math.ceil(country_count / region_count)


def min_country_separation(region_count: int, country_count: int) -> float:
    """The no-overlap invariant: no two countries may be closer than this angle.

    Scaled by 1/sqrt(countries_per_region) so the packing fraction inside a cap stays
    constant (~6.5%) no matter how crowded the world is -- rejection sampling therefore
    never jams. See docs/design-decisions.md ("M9 — Spatial foundation") for the
    measured convergence data.
    """
    if country_count < 1:
        raise ValueError(f"country count must be >= 1, got {country_count}")
    per_region = countries_per_region(region_count, country_count)
    return config.COUNTRY_SEPARATION_FACTOR * cap_radius(region_count) / math.sqrt(per_region)


def land_connected(region_a: str, region_b: str) -> bool:
    """Same landmass => reachable by land (rail). Cross-region => sea or air only (§1).

    Takes region strings rather than Country objects to keep this module a leaf -- see
    the module docstring's LAYERING note.
    """
    return region_a == region_b


@dataclass(frozen=True)
class Placement:
    """A country's immutable genesis capital placement. M15 conquest transfers
    Country.territories markers while keeping this distance anchor stable."""

    region: str
    position: Position


def _rotation_matrix(rng: Rng) -> tuple[Vector, Vector, Vector]:
    """A seed-derived rotation, so two seeds don't put their continents in the same place.

    Composed from three Euler angles. This is NOT uniform over SO(3) -- it doesn't need to
    be. It only needs to be deterministic and varied; a uniform quaternion would cost extra
    RNG draws for a property nothing here reads.
    """
    a = rng.uniform(0.0, 2.0 * math.pi)
    b = rng.uniform(0.0, 2.0 * math.pi)
    c = rng.uniform(0.0, 2.0 * math.pi)
    ca, sa = math.cos(a), math.sin(a)
    cb, sb = math.cos(b), math.sin(b)
    cc, sc = math.cos(c), math.sin(c)
    return (
        (cb * cc, cc * sa * sb - ca * sc, ca * cc * sb + sa * sc),
        (cb * sc, ca * cc + sa * sb * sc, -cc * sa + ca * sb * sc),
        (-sb, cb * sa, ca * cb),
    )


def _apply(matrix: tuple[Vector, Vector, Vector], v: Vector) -> Vector:
    return (_dot(matrix[0], v), _dot(matrix[1], v), _dot(matrix[2], v))


def rotated_region_centers(rng: Rng, region_count: int) -> list[Vector]:
    """Region centers for this world. Consumes exactly 3 RNG draws (the rotation).

    Public because tests and (later) the bridge need to reproduce the same centers; call
    it with a fresh Rng on the same seed to get the same answer.
    """
    matrix = _rotation_matrix(rng)
    return [_apply(matrix, c) for c in region_centers(region_count)]


def _cap_basis(center: Vector) -> tuple[Vector, Vector]:
    """Two unit vectors orthogonal to `center` and to each other -- the cap's local axes.

    The helper is chosen to be far from parallel with `center`, since crossing two nearly
    parallel vectors yields a near-zero, direction-garbage result.
    """
    helper: Vector = (0.0, 0.0, 1.0) if abs(center[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _normalise(_cross(helper, center))
    v = _cross(center, u)
    return u, v


def _sample_in_cap(rng: Rng, center: Vector, radius: float) -> Vector:
    """A point drawn uniformly BY AREA inside the cap. Consumes exactly 2 RNG draws.

    Sampling z uniformly in [cos(radius), 1] (rather than sampling the angle uniformly)
    is what makes it area-uniform: Archimedes' hat-box theorem says equal z-bands on a
    sphere have equal area. Sampling the angle instead would bunch countries at the pole.
    """
    u, v = _cap_basis(center)
    z = rng.uniform(math.cos(radius), 1.0)
    theta = rng.uniform(0.0, 2.0 * math.pi)
    ring = math.sqrt(max(0.0, 1.0 - z * z))
    return (
        z * center[0] + ring * (math.cos(theta) * u[0] + math.sin(theta) * v[0]),
        z * center[1] + ring * (math.cos(theta) * u[1] + math.sin(theta) * v[1]),
        z * center[2] + ring * (math.cos(theta) * u[2] + math.sin(theta) * v[2]),
    )


def generate_layout(rng: Rng, country_count: int, region_count: int) -> list[Placement]:
    """Place `country_count` countries across `region_count` regions, no two overlapping.

    Countries are assigned to regions round-robin (balanced by construction), then
    rejection-sampled inside their region's cap until they clear min_country_separation
    from every country already placed.

    RNG DRAW ORDER (pinned -- changing it changes every seed's world): 3 draws for the
    rotation, then per country a variable number of 2-draw position attempts. The variable
    count is fine for determinism (the same seed replays the same attempts) but it does
    mean adding a country changes every subsequent country's position.

    On attempt exhaustion the best candidate so far is taken rather than looping forever.
    MEASURED: never reached -- worst observed was 6 of 64 attempts across 1000 worlds. If
    this path ever fires, the invariant test in tests/unit/test_space.py fails loudly
    rather than silently shipping a stacked globe.
    """
    if not 1 <= region_count <= len(config.REGION_NAMES):
        raise ValueError(
            f"region_count must be 1..{len(config.REGION_NAMES)} (the named regions), "
            f"got {region_count}"
        )
    if country_count < 1:
        raise ValueError(f"country_count must be >= 1, got {country_count}")
    centers = rotated_region_centers(rng, region_count)
    radius = cap_radius(region_count)
    required = min_country_separation(region_count, country_count)

    placements: list[Placement] = []
    placed_vectors: list[Vector] = []
    for index in range(country_count):
        region_index = index % region_count
        center = centers[region_index]
        best: Vector | None = None
        best_clearance = -1.0
        for _ in range(config.POSITION_SAMPLE_ATTEMPTS):
            candidate = _sample_in_cap(rng, center, radius)
            clearance = min(
                (_angle_between(candidate, other) for other in placed_vectors),
                default=math.pi,
            )
            if clearance > best_clearance:
                best_clearance = clearance
                best = candidate
            if clearance >= required:
                break
        assert best is not None  # POSITION_SAMPLE_ATTEMPTS >= 1
        placed_vectors.append(best)
        placements.append(
            Placement(region=config.REGION_NAMES[region_index], position=_from_vector(best))
        )
    return placements


def region_angle(position: Position, center: Vector) -> float:
    """Angle from a region center to a position -- i.e. how deep inside its cap a country
    sits. Public because tests and (M16) the globe renderer both need it."""
    return _angle_between(position.unit_vector(), center)


def place_near(
    rng: Rng,
    anchor: Position,
    occupied: list[Position],
    required: float,
    radius: float,
) -> Position | None:
    """A position inside the cap of `radius` around `anchor` that clears `required` from
    every position in `occupied`, or None if POSITION_SAMPLE_ATTEMPTS draws all fail.

    Used when a country is born mid-history (secession). Unlike genesis layout there is no
    best-effort fallback: returning a crowded position would break the no-overlap invariant
    the globe relies on, so the caller must treat None as "no room" and not create the
    country. Consumes 2 RNG draws per attempt, stopping at the first success.
    """
    center = anchor.unit_vector()
    others = [p.unit_vector() for p in occupied]
    for _ in range(config.POSITION_SAMPLE_ATTEMPTS):
        candidate = _sample_in_cap(rng, center, radius)
        if all(_angle_between(candidate, other) >= required for other in others):
            return _from_vector(candidate)
    return None
