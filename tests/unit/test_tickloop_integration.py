"""End-to-end check of the real, wired-together tickloop.SYSTEMS (M2.1-M2.7), not a
monkeypatched fixture list. §6.2's formulas have no probabilistic branches, so M2's
nine systems are entirely RNG-free -- this is the first point where fork-zero-diff is
meaningful against the REAL economy, not just a hand-written no-RNG fixture (M1.4).
"""

import pytest

from meddler.engine.events import Event, LedgerEntry
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _money_supply(world) -> dict[str, int]:
    """Total of each currency across ALL pools that hold it. A currency == a country code
    (the ledger convention): country C's own treasury/households/corporates PLUS the
    `fx:<C>` bookkeeping desk (M12's bilateral trade parks the cross-currency leg there).
    Excluding the fx desk would make a conserved transfer look like an unbacked leak."""
    supply = {c.code: sum(c.pools.values()) for c in world.countries}
    for pool_name, balance in world.fx_pools.items():
        code = pool_name.split(":", 1)[1]  # "fx:<code>" -> "<code>"
        if code in supply:
            supply[code] += balance
    return supply


def test_full_pipeline_runs_many_ticks_without_error():
    settings = WorldSettings(starting_country_count=8, snapshot_interval=50)
    world = generate_world(1337, settings)
    tl = Timeline(seed=1337, world=world, snapshots={}, rng=Rng(1337))
    for _ in range(500):
        tl.advance()
    assert tl.world.tick == 500


def test_full_pipeline_conserves_money_per_currency_except_explicit_mint_burn():
    settings = WorldSettings(starting_country_count=4, snapshot_interval=50)
    world = generate_world(5, settings)
    supply_before = _money_supply(world)
    tl = Timeline(seed=5, world=world, snapshots={}, rng=Rng(5))
    for _ in range(200):
        tl.advance()
    supply_after = _money_supply(tl.world)

    net_mint_burn: dict[str, int] = {c.code: 0 for c in tl.world.countries}
    for event in tl.world.log:
        for entry in event.ledger:
            if entry.kind == "mint" and entry.dst_pool is not None:
                code = entry.dst_pool.split(".")[0]
                if code in net_mint_burn:
                    net_mint_burn[code] += entry.amount
            elif entry.kind == "burn" and entry.src_pool is not None:
                code = entry.src_pool.split(".")[0]
                if code in net_mint_burn:
                    net_mint_burn[code] -= entry.amount

    for code in supply_before:
        assert supply_after[code] - supply_before[code] == net_mint_burn[code]


def test_full_pipeline_world_at_matches_continuous_run():
    settings = WorldSettings(starting_country_count=4, snapshot_interval=10)
    world = generate_world(19, settings)
    tl = Timeline(seed=19, world=world, snapshots={}, rng=Rng(19))
    for _ in range(123):
        tl.advance()

    reconstructed = tl.world_at(123)
    live_by_code = {c.code: c for c in tl.world.countries}
    for c in reconstructed.countries:
        live = live_by_code[c.code]
        assert c.pools == live.pools
        assert c.gdp_tick == pytest.approx(live.gdp_tick)
        assert c.inflation == pytest.approx(live.inflation)
        assert c.stability == pytest.approx(live.stability)
        assert c.exchange_rate == pytest.approx(live.exchange_rate)


def test_full_pipeline_fork_zero_diff_against_real_rng_free_systems():
    """M2's systems consume no RNG at all (§6.2 has no probabilistic branches), so
    this is the first meaningful fork-zero-diff test against the real engine, not a
    hand-written fixture."""
    settings = WorldSettings(starting_country_count=4, snapshot_interval=10)
    world = generate_world(41, settings)
    tl = Timeline(seed=41, world=world, snapshots={}, rng=Rng(41))
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=20, intervention=None)
    fork_tl = mv.forks[fork_id]

    for _ in range(50):
        tl.advance()
        fork_tl.advance()
        assert _money_supply(fork_tl.world) == _money_supply(tl.world)


def test_full_pipeline_fork_with_intervention_diverges():
    settings = WorldSettings(starting_country_count=4, snapshot_interval=10)
    world = generate_world(41, settings)
    tl = Timeline(seed=41, world=world, snapshots={}, rng=Rng(41))
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    target = tl.world.countries[0].code

    intervention = Event(
        id=-1,
        tick=20,
        kind="TEST_INTERVENTION_MINT",
        country=target,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=True,
        payload={},
        ledger=(
            LedgerEntry(
                kind="mint",
                src_pool=None,
                dst_pool=f"{target}.treasury",
                amount=5_000_000,
                currency=target,
            ),
        ),
        stat_deltas=(),
        severity=1,
    )
    fork_id = mv.fork(at_tick=20, intervention=intervention)
    fork_tl = mv.forks[fork_id]

    for _ in range(50):
        tl.advance()
        fork_tl.advance()

    assert _money_supply(fork_tl.world) != _money_supply(tl.world)
