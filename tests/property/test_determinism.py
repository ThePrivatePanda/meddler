"""PROPOSAL §8.2: the most important test file in the project.

M1.4 ships with no real systems (those land in M2/M3), so these tests inject
fixture systems to exercise the timeline machinery meaningfully:

- `_deterministic_system` consumes no RNG at all; useful for isolating fork/snapshot
  machinery bugs (aliasing, a hidden module-level `random` call, unsorted dict/set
  iteration) from RNG semantics entirely.
- `_rng_system` consumes RNG, exercising same-seed reproducibility, snapshot+replay
  reconstruction, restart's "deliberately different future" guarantee, and -- since
  the "Fork RNG semantics resolved" decision in docs/design-decisions.md -- that a
  no-intervention fork mirrors prime's exact dice (not an independent reseed).
- `_stat_drift_system` mutates a Country stat with no ledger entry, exercising the
  stat_deltas replay path (see docs/design-decisions.md, "Stat deltas...").
"""

from __future__ import annotations

import pytest

from meddler.engine import tickloop
from meddler.engine.events import Event, LedgerEntry, StatDelta
from meddler.engine.ledger import Ledger, apply_to_world
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_country_stat
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _deterministic_system(world: World, rng: Rng) -> list[Event]:
    events = []
    for country in sorted(world.countries, key=lambda c: c.code):
        entries: list[LedgerEntry] = []
        Ledger.mint(entries, f"{country.code}.treasury", 10, country.code)
        event = world.log.append(
            tick=world.tick,
            kind="TEST_MINT",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={"amount": 10},
            ledger=tuple(entries),
            severity=0,
        )
        for entry in event.ledger:
            apply_to_world(world, entry)
        events.append(event)
    return events


def _rng_system(world: World, rng: Rng) -> list[Event]:
    events = []
    for country in sorted(world.countries, key=lambda c: c.code):
        if rng.roll(0.5):
            amount = rng.randint(1, 100)
            entries: list[LedgerEntry] = []
            Ledger.mint(entries, f"{country.code}.treasury", amount, country.code)
            event = world.log.append(
                tick=world.tick,
                kind="TEST_RNG_MINT",
                country=country.code,
                country2=None,
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={"amount": amount},
                ledger=tuple(entries),
                severity=0,
            )
            for entry in event.ledger:
                apply_to_world(world, entry)
            events.append(event)
    return events


def _stat_drift_system(world: World, rng: Rng) -> list[Event]:
    """Continuous per-tick ambient drift with NO ledger entry -- exercises the
    stat_deltas replay path (§6.2-style formulas: stability, inflation, etc. mutate
    every tick with no discrete narrative event). Regression test for the gap a
    review pass caught: world_at() originally replayed only event.ledger, leaving every
    non-money field frozen at the nearest snapshot."""
    events = []
    for country in sorted(world.countries, key=lambda c: c.code):
        deltas: list[StatDelta] = []
        apply_country_stat(world, deltas, country.code, "stability", -1.0)
        event = world.log.append(
            tick=world.tick,
            kind="TEST_STABILITY_DRIFT",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={},
            stat_deltas=tuple(deltas),
            severity=0,
        )
        events.append(event)
    return events


def _make_timeline(seed: int, snapshot_interval: int = 10) -> Timeline:
    settings = WorldSettings(starting_country_count=4, snapshot_interval=snapshot_interval)
    world = generate_world(seed, settings)
    return Timeline(seed=seed, world=world, snapshots={}, rng=Rng(seed))


def _pools_snapshot(world: World) -> dict[str, dict[str, int]]:
    return {c.code: dict(c.pools) for c in world.countries}


def _log_snapshot(world: World) -> list[tuple[int, str, str | None, dict]]:
    return [(e.tick, e.kind, e.country, dict(e.payload)) for e in world.log]


def _full_log_snapshot(world: World) -> list[tuple]:
    """A byte-identity view of the log that includes the fields an RNG-consumption-order
    bug actually corrupts -- depth, parent_id, country2, severity, and the stat/ledger
    deltas -- not just (tick, kind, country, payload). Used to guard the M3.1 cascade
    systems, whose ordering bugs the M3.1 statistical test cannot see (they only surface
    at M3.3's golden master, two milestones later)."""
    return [
        (
            e.tick,
            e.kind,
            e.country,
            e.country2,
            e.parent_id,
            e.depth,
            e.severity,
            e.is_intervention,
            dict(e.payload),
            e.ledger,
            e.stat_deltas,
        )
        for e in world.log
    ]


def test_exogenous_and_consequence_systems_are_deterministic() -> None:
    """The M3.1 guardrail the milestone's own statistical test lacks: run the SAME seed
    through the REAL wired SYSTEMS list (exogenous slot 10 + consequence slot 11 included,
    not a monkeypatched fixture) twice and assert byte-identical event logs. drama_multiplier
    is cranked so exogenous roots and their scheduled cascades genuinely fire inside the
    window -- otherwise this would trivially pass on ticks that never touched the new code."""
    from meddler.engine import tickloop as real_tickloop

    def run() -> World:
        settings = WorldSettings(starting_country_count=4, drama_multiplier=6.0)
        world = generate_world(2024, settings)
        rng = Rng(2024)
        for _ in range(400):
            real_tickloop.tick(world, rng)
        return world

    world_a = run()
    world_b = run()

    # The run must actually exercise the new systems, or determinism here is meaningless.
    assert any(e.depth >= 1 and e.parent_id is not None for e in world_a.log), (
        "no scheduled consequence fired -- test window did not exercise ConsequenceSystem"
    )
    assert any(
        e.kind in {"DROUGHT", "EARTHQUAKE", "METEOR", "PLAGUE", "SCANDAL", "WAR_DECLARED"}
        for e in world_a.log
    ), "no exogenous root fired -- test window did not exercise ExogenousSystem"

    assert _full_log_snapshot(world_a) == _full_log_snapshot(world_b)
    assert _pools_snapshot(world_a) == _pools_snapshot(world_b)


def test_same_seed_produces_identical_event_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])
    tl1 = _make_timeline(1337)
    tl2 = _make_timeline(1337)
    for _ in range(100):
        tl1.advance()
        tl2.advance()
    assert _log_snapshot(tl1.world) == _log_snapshot(tl2.world)
    assert _pools_snapshot(tl1.world) == _pools_snapshot(tl2.world)


def test_world_at_matches_continuous_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])
    tl = _make_timeline(1337)
    for _ in range(500):
        tl.advance()
    reconstructed = tl.world_at(500)
    assert reconstructed.tick == tl.world.tick == 500
    assert _pools_snapshot(reconstructed) == _pools_snapshot(tl.world)
    assert _log_snapshot(reconstructed) == _log_snapshot(tl.world)


def test_world_at_mid_interval_matches_continuous_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # 237 falls between snapshot_interval=10 boundaries, forcing real replay.
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])
    tl = _make_timeline(7, snapshot_interval=10)
    for _ in range(300):
        tl.advance()
    reconstructed = tl.world_at(237)

    tl_continuous = _make_timeline(7, snapshot_interval=10)
    for _ in range(237):
        tl_continuous.advance()

    assert reconstructed.tick == 237
    assert _pools_snapshot(reconstructed) == _pools_snapshot(tl_continuous.world)
    assert _log_snapshot(reconstructed) == _log_snapshot(tl_continuous.world)


def test_world_at_reconstructs_stat_deltas_not_just_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without stat_deltas replay, world_at(237) would show stability reduced by only
    230 (the nearest snapshot <= 237 at interval 10), not 237 -- a 7-tick drift."""
    monkeypatch.setattr(tickloop, "SYSTEMS", [_stat_drift_system])
    tl = _make_timeline(7, snapshot_interval=10)
    for _ in range(300):
        tl.advance()
    reconstructed = tl.world_at(237)

    tl_continuous = _make_timeline(7, snapshot_interval=10)
    for _ in range(237):
        tl_continuous.advance()

    assert reconstructed.tick == 237
    live_stability = {c.code: c.stability for c in tl_continuous.world.countries}
    reconstructed_stability = {c.code: c.stability for c in reconstructed.countries}
    assert reconstructed_stability == live_stability
    # Sanity: confirms 237 ticks of drift actually happened, not e.g. 0 or 230.
    for code, value in live_stability.items():
        genesis_value = next(c.stability for c in tl.snapshots[0].countries if c.code == code)
        assert genesis_value - value == pytest.approx(237.0)


def test_fork_zero_diff_without_intervention_deterministic_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_deterministic_system])
    tl = _make_timeline(1337)
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=20, intervention=None)
    fork_tl = mv.forks[fork_id]

    for _ in range(30):
        tl.advance()
        fork_tl.advance()
        assert _pools_snapshot(fork_tl.world) == _pools_snapshot(tl.world)


def test_fork_zero_diff_without_intervention_under_real_randomness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of mirroring prime's Rng state (docs/design-decisions.md, "Fork RNG
    semantics resolved"): a no-intervention fork must track prime bit-for-bit even
    when the system genuinely consumes randomness, not just for RNG-free systems."""
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])
    tl = _make_timeline(1337)
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=20, intervention=None)
    fork_tl = mv.forks[fork_id]

    for _ in range(30):
        tl.advance()
        fork_tl.advance()
        assert _pools_snapshot(fork_tl.world) == _pools_snapshot(tl.world)
        assert _log_snapshot(fork_tl.world) == _log_snapshot(tl.world)


def test_fork_with_intervention_diverges_via_world_state_not_dice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Divergence must be attributable to the intervention (§3.5), not to different
    randomness: the fork's Rng still mirrors prime, but the intervention changes the
    fork's starting money, which a downstream RNG-driven system (draws still
    identical between the two) then acts on differently."""
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])
    tl = _make_timeline(1337)
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
        payload={"amount": 999},
        ledger=(
            LedgerEntry(
                kind="mint",
                src_pool=None,
                dst_pool=f"{target}.treasury",
                amount=999,
                currency=target,
            ),
        ),
        stat_deltas=(),
        severity=1,
    )
    fork_id = mv.fork(at_tick=20, intervention=intervention)
    fork_tl = mv.forks[fork_id]

    assert _pools_snapshot(fork_tl.world) != _pools_snapshot(tl.world_at(20))

    for _ in range(30):
        tl.advance()
        fork_tl.advance()

    assert _pools_snapshot(fork_tl.world) != _pools_snapshot(tl.world)


def test_fork_at_scrubbed_past_tick_mirrors_prime_historical_dice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forking is not limited to prime's current live tick (§3.5: "An intervention at
    a past tick... forks the timeline"). The fork must mirror prime's dice AS THEY
    WERE at that historical tick, not prime's current (already-advanced-past-it) Rng."""
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])
    tl = _make_timeline(1337)
    for _ in range(50):
        tl.advance()  # prime's live tick is now 50, well past the fork point below

    mv = Multiverse(prime=tl)
    fork_id = mv.fork(at_tick=15, intervention=None)
    fork_tl = mv.forks[fork_id]
    assert fork_tl.world.tick == 15

    tl_continuous = _make_timeline(1337)
    for _ in range(15):
        tl_continuous.advance()

    for _ in range(10):
        fork_tl.advance()
        tl_continuous.advance()
        assert _pools_snapshot(fork_tl.world) == _pools_snapshot(tl_continuous.world)


def test_two_no_intervention_forks_from_same_tick_are_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])
    tl = _make_timeline(1337)
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    b = mv.fork(at_tick=20, intervention=None)
    c = mv.fork(at_tick=20, intervention=None)

    for _ in range(10):
        mv.forks[b].advance()
        mv.forks[c].advance()

    assert _pools_snapshot(mv.forks[b].world) == _pools_snapshot(mv.forks[c].world)


def test_fork_raises_beyond_three_concurrent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_deterministic_system])
    tl = _make_timeline(1337)
    for _ in range(10):
        tl.advance()
    mv = Multiverse(prime=tl)
    mv.fork(at_tick=10, intervention=None)
    mv.fork(at_tick=10, intervention=None)
    mv.fork(at_tick=10, intervention=None)
    with pytest.raises(ValueError):
        mv.fork(at_tick=10, intervention=None)


def test_adopt_fork_promotes_and_dissolves_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_deterministic_system])
    tl = _make_timeline(1337)
    for _ in range(10):
        tl.advance()
    mv = Multiverse(prime=tl)
    b = mv.fork(at_tick=10, intervention=None)
    mv.fork(at_tick=10, intervention=None)  # c
    promoted_world = mv.forks[b].world

    mv.adopt_fork(b)

    assert mv.prime.world is promoted_world
    assert mv.forks == {}


def test_drop_fork_removes_only_that_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_deterministic_system])
    tl = _make_timeline(1337)
    for _ in range(10):
        tl.advance()
    mv = Multiverse(prime=tl)
    b = mv.fork(at_tick=10, intervention=None)
    c = mv.fork(at_tick=10, intervention=None)

    mv.drop_fork(b)

    assert b not in mv.forks
    assert c in mv.forks


def test_restart_refused_while_forks_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_deterministic_system])
    tl = _make_timeline(1337)
    for _ in range(20):
        tl.advance()
    mv = Multiverse(prime=tl)
    mv.fork(at_tick=20, intervention=None)
    with pytest.raises(ValueError):
        mv.restart(10)


def test_restart_truncates_and_produces_deterministic_different_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [_rng_system])

    def run_restart_scenario() -> tuple[list, list]:
        tl = _make_timeline(1337)
        for _ in range(50):
            tl.advance()
        original_future = [e for e in _log_snapshot(tl.world) if e[0] > 30]

        mv = Multiverse(prime=tl)
        mv.restart(30)
        assert all(e.tick <= 30 for e in mv.prime.world.log)
        for _ in range(20):
            mv.prime.advance()
        restarted_future = [e for e in _log_snapshot(mv.prime.world) if e[0] > 30]
        return original_future, restarted_future

    original_future_a, restarted_future_a = run_restart_scenario()
    assert restarted_future_a != original_future_a  # deliberately different (§4.3.6)

    # Re-running the identical scenario from scratch must reproduce the same restart.
    _, restarted_future_b = run_restart_scenario()
    assert restarted_future_a == restarted_future_b
