"""God-mode interventions do what their names promise, and scrubbing/forking reproduces it.

The organic WAR_DECLARED / PEACE / ALLIANCE / EMBARGO kinds have been structural since
M7.3/M11. Their intervention twins now run the SAME live and replay handlers, so a god-mode
war is a real war (at_war_with, relation -90) and `Timeline.world_at` rebuilds it exactly.

Commands run BETWEEN ticks, stamped with the current tick. The replay tests below also pin
the history boundary those commands expose: an event appended after a snapshot was taken at
the same tick must still be replayed when reconstructing that tick or any later one.
"""

from __future__ import annotations

import pytest

from meddler.engine import god, tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _timeline(seed: int = 1337, countries: int = 6, snapshot_interval: int = 10) -> Timeline:
    settings = WorldSettings(starting_country_count=countries, snapshot_interval=snapshot_interval)
    world = generate_world(seed, settings)
    return Timeline(seed=seed, world=world, snapshots={}, rng=Rng(seed))


def _pair_not_at_war(world: World) -> tuple[str, str]:
    codes = sorted(c.code for c in world.countries)
    for i, a in enumerate(codes):
        for b in codes[i + 1 :]:
            if b not in world.country(a).at_war_with:
                return a, b
    raise AssertionError("every pair is already at war")


def _pair_without_blocs(world: World) -> tuple[str, str]:
    free = sorted(c.code for c in world.countries if world.bloc_of(c.code) is None)
    assert len(free) >= 2, "fixture needs two bloc-less countries"
    return free[0], free[1]


def _fingerprint(world: World) -> tuple:
    return (
        sorted((c.code, tuple(sorted(c.at_war_with))) for c in world.countries),
        sorted((bl.id, tuple(sorted(bl.members)), bl.formed_at_tick) for bl in world.blocs),
        sorted(world.embargoes),
        sorted((k, round(v, 6)) for k, v in world.relations.items()),
        sorted((c.code, tuple(sorted(c.pools.items()))) for c in world.countries),
        sorted((c.code, round(c.stability, 9)) for c in world.countries),
    )


# ---- live effects ---------------------------------------------------------------------


def test_intervene_war_starts_a_structural_war() -> None:
    tl = _timeline()
    a, b = _pair_not_at_war(tl.world)
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_WAR", country=a, country2=b)
    assert b in tl.world.country(a).at_war_with
    assert a in tl.world.country(b).at_war_with
    assert tl.world.relations[tuple(sorted((a, b)))] == -90.0
    assert event.payload["relation_after"] == -90.0


def test_intervene_peace_ends_every_war_of_the_target() -> None:
    tl = _timeline()
    a, b = _pair_not_at_war(tl.world)
    god.intervene(tl.world, tl.rng, kind="INTERVENE_WAR", country=a, country2=b)
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_PEACE", country=a)
    assert tl.world.country(a).at_war_with == []
    assert a not in tl.world.country(b).at_war_with
    assert b in str(event.payload["ended_wars_with"]).split(",")


def test_intervene_alliance_forms_a_bloc() -> None:
    tl = _timeline()
    a, b = _pair_without_blocs(tl.world)
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_ALLIANCE", country=a, country2=b)
    bloc = tl.world.bloc_of(a)
    assert bloc is not None and b in bloc.members
    assert event.payload["bloc_id"] == bloc.id


def test_intervene_embargo_blocks_the_lane() -> None:
    tl = _timeline()
    a, b = sorted(c.code for c in tl.world.countries)[:2]
    god.intervene(tl.world, tl.rng, kind="INTERVENE_EMBARGO", country=a, country2=b)
    assert (a, b) in tl.world.embargoes


# ---- replay exactness -------------------------------------------------------------------


@pytest.mark.parametrize("on_snapshot_tick", [False, True])
def test_world_at_reconstructs_structural_interventions(on_snapshot_tick: bool) -> None:
    """Interventions stamped with the current tick, including a tick that already has a
    snapshot, must be replayed by world_at at that tick and every later one."""
    tl = _timeline()
    for _ in range(20 if on_snapshot_tick else 17):
        tl.advance()
    at = tl.world.tick
    assert (at in tl.snapshots) is on_snapshot_tick

    a, b = _pair_not_at_war(tl.world)
    c, d = _pair_without_blocs(tl.world)
    god.intervene(tl.world, tl.rng, kind="INTERVENE_WAR", country=a, country2=b)
    god.intervene(tl.world, tl.rng, kind="INTERVENE_ALLIANCE", country=c, country2=d)
    god.intervene(tl.world, tl.rng, kind="INTERVENE_EMBARGO", country=a, country2=d)
    god.intervene(tl.world, tl.rng, kind="INTERVENE_MINT", country=c)
    live = {at: _fingerprint(tl.world)}
    for _ in range(25):
        tl.advance()
        live[tl.world.tick] = _fingerprint(tl.world)
    for tick in (at, at + 1, at + 3, at + 13, at + 25):
        assert _fingerprint(tl.world_at(tick)) == live[tick], f"divergence at t{tick}"


def test_god_relation_ending_a_war_survives_replay() -> None:
    tl = _timeline()
    for _ in range(12):
        tl.advance()
    a, b = _pair_not_at_war(tl.world)
    god.intervene(tl.world, tl.rng, kind="INTERVENE_WAR", country=a, country2=b)
    tl.advance()
    god.god_relation(tl.world, a, b, 10.0)  # crosses above -30: ends the war
    assert b not in tl.world.country(a).at_war_with
    at = tl.world.tick
    live = {at: _fingerprint(tl.world)}
    for _ in range(12):
        tl.advance()
        live[tl.world.tick] = _fingerprint(tl.world)
    for tick in (at, at + 5, at + 12):
        assert _fingerprint(tl.world_at(tick)) == live[tick], f"divergence at t{tick}"


def test_fork_intervention_replays_in_the_fork_and_after_adoption() -> None:
    """The bridge's intervene path: fork at a tick, then intervene on the fork's world.
    The fork's own history (and prime's, once adopted) must reconstruct the war, and the
    adopted timeline must still scrub back before the fork point."""
    tl = _timeline()
    for _ in range(30):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=25, intervention=None)
    fork = mv.forks[fork_id]
    a, b = _pair_not_at_war(fork.world)
    god.intervene(fork.world, fork.rng, kind="INTERVENE_WAR", country=a, country2=b)
    live = {25: _fingerprint(fork.world)}
    for _ in range(15):
        fork.advance()
        live[fork.world.tick] = _fingerprint(fork.world)
    for tick in (25, 26, 33, 40):
        assert _fingerprint(fork.world_at(tick)) == live[tick], f"fork divergence at t{tick}"

    before_fork = _fingerprint(tl.world_at(12))
    mv.adopt_fork(fork_id)
    assert _fingerprint(mv.prime.world_at(12)) == before_fork
    for tick in (25, 33, 40):
        assert _fingerprint(mv.prime.world_at(tick)) == live[tick], f"adopted divergence t{tick}"


# ---- asset-class and money interventions ------------------------------------------------


def _infra_fingerprint(world: World) -> tuple:
    return tuple(
        (
            c.code,
            tuple(
                (cls, getattr(c.infrastructure, cls).count, round(getattr(c.infrastructure, cls).condition, 9))
                for cls in ("satellites", "naval_fleet", "air_fleet", "rail_network", "power_grid", "communications")
            ),
            tuple(sorted(k for k, v in c.armed.items() if v)),
        )
        for c in sorted(world.countries, key=lambda c: c.code)
    )


def test_asset_interventions_hit_the_named_class() -> None:
    tl = _timeline()
    a, b = sorted(c.code for c in tl.world.countries)[:2]
    ca, cb = tl.world.country(a), tl.world.country(b)
    untouched = ca.infrastructure.rail_network.condition

    god.intervene(tl.world, tl.rng, kind="INTERVENE_NAVAL_BLOCKADE", country=a, country2=b)
    assert cb.infrastructure.naval_fleet.condition <= 0.1
    assert cb.armed["infra_failure:naval_fleet:PORT_CLOSURE"] is True
    # Only the outage the blockade itself announced is armed. naval_fleet crosses its
    # NAVAL_LOSS threshold too, but nothing has reported that, so arming it would silence a
    # wrecked fleet's losses until its condition recovered.
    assert "infra_failure:naval_fleet:NAVAL_LOSS" not in cb.armed

    god.intervene(tl.world, tl.rng, kind="INTERVENE_BLACKOUT", country=a)
    assert ca.infrastructure.communications.condition <= 0.1
    god.intervene(tl.world, tl.rng, kind="INTERVENE_POWER_GRID_FAILURE", country=a)
    assert ca.infrastructure.power_grid.condition <= 0.1
    assert ca.infrastructure.rail_network.condition == untouched

    satellites = ca.infrastructure.satellites.count
    god.intervene(tl.world, tl.rng, kind="INTERVENE_DESTROY_SATELLITE", country=a)
    assert ca.infrastructure.satellites.count == max(0, satellites - 1)

    god.intervene(tl.world, tl.rng, kind="INTERVENE_INFRASTRUCTURE_BOOST", country=a)
    for cls in ("satellites", "naval_fleet", "air_fleet", "rail_network", "power_grid", "communications"):
        assert getattr(ca.infrastructure, cls).condition >= 0.9


def test_intervene_mint_is_sized_to_the_economy() -> None:
    tl = _timeline()
    country = tl.world.countries[0]
    before = country.pools["treasury"]
    expected = round(country.gdp_tick * 20.0)
    event = god.intervene(tl.world, tl.rng, kind="INTERVENE_MINT", country=country.code)
    minted = sum(e.amount for e in event.ledger if e.kind == "mint")
    assert abs(minted - expected) <= 1
    assert country.pools["treasury"] - before == minted
    assert minted > before * 0.5  # a mint a treasury notices


def test_world_at_reconstructs_asset_interventions_on_a_snapshot_tick() -> None:
    tl = _timeline()
    for _ in range(20):
        tl.advance()
    at = tl.world.tick
    assert at in tl.snapshots
    a, b = sorted(c.code for c in tl.world.countries)[:2]
    for kind, second in (
        ("INTERVENE_NAVAL_BLOCKADE", b),
        ("INTERVENE_BLACKOUT", None),
        ("INTERVENE_POWER_GRID_FAILURE", None),
        ("INTERVENE_DESTROY_SATELLITE", None),
    ):
        god.intervene(tl.world, tl.rng, kind=kind, country=a, country2=second)
    god.intervene(tl.world, tl.rng, kind="INTERVENE_INFRASTRUCTURE_BOOST", country=b)
    live = {at: _infra_fingerprint(tl.world)}
    for _ in range(12):
        tl.advance()
        live[tl.world.tick] = _infra_fingerprint(tl.world)
    for tick in (at, at + 4, at + 12):
        assert _infra_fingerprint(tl.world_at(tick)) == live[tick], f"divergence at t{tick}"


def test_forking_on_an_intervention_carries_its_structural_half() -> None:
    """Multiverse.fork(intervention=...) seeds the fork with a recorded intervention. Its
    declarative half is the ledger and stat deltas; its structural half lives in the payload
    and needs replaying too, or the fork gets a war headline with no war behind it."""
    tl = _timeline()
    for _ in range(10):
        tl.advance()
    a, b = _pair_not_at_war(tl.world)

    # A real INTERVENE_WAR event, fired on a throwaway world of the same shape, is what the
    # caller hands fork() -- the fork re-appends it onto its own branch.
    scratch = _timeline()
    proto = god.intervene(scratch.world, scratch.rng, kind="INTERVENE_WAR", country=a, country2=b)
    assert proto is not None

    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=tl.world.tick, intervention=proto)
    forked = mv.forks[fork_id].world

    assert b in forked.country(a).at_war_with
    assert a in forked.country(b).at_war_with
    assert b not in tl.world.country(a).at_war_with  # prime is untouched by the fork
