"""INTERVENE_SECEDE creates a real country, conserves money, keeps the no-overlap
invariant, and survives scrubbing, forking, adoption, and restart."""

from __future__ import annotations

from collections import defaultdict

from meddler.engine import god, space, tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import CountryStatus, World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _timeline(seed: int = 1337, countries: int = 6, snapshot_interval: int = 10) -> Timeline:
    settings = WorldSettings(starting_country_count=countries, snapshot_interval=snapshot_interval)
    return Timeline(seed=seed, world=generate_world(seed, settings), snapshots={}, rng=Rng(seed))


def _largest(world: World) -> str:
    return max(world.countries, key=lambda c: (c.population, c.code)).code


def _currency_totals(world: World) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for c in world.countries:
        totals[c.code] += sum(c.pools.values())
    for pool, amount in world.fx_pools.items():
        totals[pool[len("fx:") :]] += amount
    return dict(totals)


def _fingerprint(world: World) -> tuple:
    return (
        tuple(
            (
                c.code,
                c.name,
                c.status,
                round(c.population, 9),
                round(c.base_gdp, 6),
                tuple(sorted(c.pools.items())),
                tuple((t.position.lat, t.position.lon, t.region) for t in c.territories),
                tuple(sorted((k, round(v, 9)) for k, v in c.commodity_stock.items())),
                tuple(
                    getattr(c.infrastructure, cls).count
                    for cls in ("satellites", "naval_fleet", "air_fleet", "rail_network", "power_grid", "communications")
                ),
                c.leader.name,
            )
            for c in world.countries
        ),
        sorted((k, round(v, 6)) for k, v in world.relations.items()),
        sorted(world.fx_pools.items()),
    )


def test_secede_creates_a_real_country() -> None:
    tl = _timeline()
    parent_code = _largest(tl.world)
    parent = tl.world.country(parent_code)
    before_pop = parent.population
    before_count = len(tl.world.countries)

    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_SECEDE", country=parent_code)

    assert event.payload["seceded"] == 1
    assert len(tl.world.countries) == before_count + 1
    child = tl.world.country(str(event.payload["new_country_code"]))
    assert child.parent_code == parent_code
    assert child.status == CountryStatus.ACTIVE
    assert child.born_at_tick == tl.world.tick
    assert child.name == event.payload["new_country_name"]
    assert abs(child.population + parent.population - before_pop) < 1e-9
    assert child.territories and child.territories[0].position == child.position
    assert all(v > 0 for v in child.pools.values())
    assert tl.world.relations[tuple(sorted((child.code, parent_code)))] < 0


def test_secede_conserves_every_currency_including_fx_desks() -> None:
    tl = _timeline()
    before = _currency_totals(tl.world)
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_SECEDE", country=_largest(tl.world))
    after = _currency_totals(tl.world)
    child = str(event.payload["new_country_code"])
    assert after.pop(child) == 0  # the desk issued exactly what the new pools received
    assert after == before


def test_secede_keeps_the_no_overlap_invariant() -> None:
    tl = _timeline()
    for _ in range(3):
        god.intervene(tl.world, tl.rng, kind="INTERVENE_SECEDE", country=_largest(tl.world))
    live = [c for c in tl.world.countries if c.status != CountryStatus.ANNEXED]
    required = space.min_country_separation(tl.world.settings.region_count, len(live))
    for i, a in enumerate(live):
        for b in live[i + 1 :]:
            assert space.central_angle(a.position, b.position) >= required - 1e-12


def test_secede_fizzles_when_the_roster_is_full() -> None:
    tl = _timeline()
    tl.world.settings.max_countries = len(tl.world.countries)
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_SECEDE", country=_largest(tl.world))
    assert event.payload["seceded"] == 0
    assert len(tl.world.countries) == tl.world.settings.max_countries


def test_the_new_country_lives_through_the_tick_loop() -> None:
    tl = _timeline()
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_SECEDE", country=_largest(tl.world))
    child = str(event.payload["new_country_code"])
    for _ in range(60):
        tl.advance()
    country = tl.world.country(child)
    assert country.population > 0
    assert any(
        s.origin == child or s.dest == child for s in tl.world.shipments
    ) or any(e.country == child for e in tl.world.log.events_between(1, tl.world.tick) if e.kind == "SHIPMENT_DISPATCHED")


def test_world_at_rebuilds_the_secession_on_a_snapshot_tick() -> None:
    tl = _timeline()
    for _ in range(20):
        tl.advance()
    at = tl.world.tick
    assert at in tl.snapshots
    god.intervene(tl.world, tl.rng, kind="INTERVENE_SECEDE", country=_largest(tl.world))
    live = {at: _fingerprint(tl.world)}
    for _ in range(25):
        tl.advance()
        live[tl.world.tick] = _fingerprint(tl.world)
    before = _fingerprint(tl.world_at(at - 1))
    assert len(before[0]) == len(live[at][0]) - 1
    for tick in (at, at + 1, at + 10, at + 25):
        assert _fingerprint(tl.world_at(tick)) == live[tick], f"divergence at t{tick}"


def test_secession_in_a_fork_replays_and_survives_adoption_and_restart() -> None:
    tl = _timeline()
    for _ in range(30):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=24, intervention=None)
    fork = mv.forks[fork_id]
    event = god.intervene(fork.world, fork.rng, kind="INTERVENE_SECEDE", country=_largest(fork.world))
    child = str(event.payload["new_country_code"])
    live = {24: _fingerprint(fork.world)}
    for _ in range(12):
        fork.advance()
        live[fork.world.tick] = _fingerprint(fork.world)
    for tick in (24, 30, 36):
        assert _fingerprint(fork.world_at(tick)) == live[tick], f"fork divergence at t{tick}"

    mv.adopt_fork(fork_id)
    assert child in {c.code for c in mv.prime.world.countries}
    assert child not in {c.code for c in mv.prime.world_at(20).countries}
    mv.restart(30)
    assert child in {c.code for c in mv.prime.world.countries}


def _parent_fingerprint(world: World, code: str) -> tuple:
    c = world.country(code)
    return (
        round(c.population, 12),
        round(c.base_gdp, 12),
        round(c.gdp_tick, 12),
        tuple(sorted((k, round(v, 12)) for k, v in c.commodity_output.items())),
        tuple(sorted((k, round(v, 12)) for k, v in c.commodity_stock.items())),
        tuple(
            getattr(c.infrastructure, cls).count
            for cls in ("satellites", "naval_fleet", "air_fleet", "rail_network", "power_grid", "communications")
        ),
        tuple((t.position.lat, t.position.lon, t.region) for t in c.territories),
    )


def test_the_parent_shrinks_identically_live_and_on_replay() -> None:
    """The parent is shrunk by what the child took, not restored to a recorded absolute --
    so every field the split divides must reconstruct bit-for-bit. Deliberately run off a
    snapshot tick, so world_at has to REPLAY the parent forward to the secession rather than
    copy it: an absolute would hide a discrepancy that relative subtraction exposes."""
    tl = _timeline()
    for _ in range(23):  # snapshot_interval is 10, so this tick has no snapshot of its own
        tl.advance()
    at = tl.world.tick
    assert at not in tl.snapshots
    parent = _largest(tl.world)

    god.intervene(tl.world, tl.rng, kind="INTERVENE_SECEDE", country=parent)
    live = {at: _parent_fingerprint(tl.world, parent)}
    for _ in range(20):
        tl.advance()
        live[tl.world.tick] = _parent_fingerprint(tl.world, parent)

    for tick in (at, at + 1, at + 7, at + 20):
        assert _parent_fingerprint(tl.world_at(tick), parent) == live[tick], f"t{tick}"
