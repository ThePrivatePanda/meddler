"""M4.2: world_at structural replay + restart's country-pruning contract.
PROPOSAL §3.4 (scrubbing), §3.7 (restart un-happens late-born countries), §4.4.

The headline regression this file guards: before M4.2, Timeline.world_at replayed
event.ledger/event.stat_deltas but never structural.py's effects (LEADER_CHANGE's leader
swap, OCCUPATION_BEGIN's status/occupied_by) -- a scrubbed-to-the-past world would show
the WRONG leader/status even for ticks strictly after the change. See
engine/structural.py's REPLAY_EFFECTS and docs/design-decisions.md ("M3.2 politics") for
the full story.
"""

from __future__ import annotations

from meddler.engine import cascade, config, tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import CountryStatus, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


def _two_country_timeline(seed: int = 1, snapshot_interval: int = 10) -> Timeline:
    settings = WorldSettings(starting_country_count=2, snapshot_interval=snapshot_interval)
    world = generate_world(seed, settings)
    return Timeline(seed=seed, world=world, snapshots={}, rng=Rng(seed))


def test_world_at_reconstructs_leader_change_before_at_and_after_the_event_tick():
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    original_name = tl.world.country(code).leader.name

    for _ in range(5):
        tl.advance()

    # Fire LEADER_CHANGE directly on the live world (deterministic Rng so the successor
    # name/traits are reproducible) rather than relying on a real coup/election roll.
    event = cascade.emit_event(
        tl.world,
        Rng(999),
        kind="LEADER_CHANGE",
        primary=code,
        secondary=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
    )
    new_name = tl.world.country(code).leader.name
    assert new_name != original_name  # sanity: the live handler actually changed it

    for _ in range(5):
        tl.advance()  # crosses a snapshot boundary past the change

    before = tl.world_at(event.tick - 1)
    at_event = tl.world_at(event.tick)
    after = tl.world_at(tl.world.tick)

    assert before.country(code).leader.name == original_name
    assert at_event.country(code).leader.name == new_name
    assert after.country(code).leader.name == new_name
    assert after.country(code).leader.name == tl.world.country(code).leader.name
    assert after.country(code).leader.traits == tl.world.country(code).leader.traits


def test_world_at_reconstructs_occupation_status_and_occupied_by():
    tl = _two_country_timeline()
    occupied_code = tl.world.countries[0].code
    occupier_code = tl.world.countries[1].code

    for _ in range(5):
        tl.advance()

    event = cascade.emit_event(
        tl.world,
        Rng(7),
        kind="OCCUPATION_BEGIN",
        primary=occupied_code,
        secondary=occupier_code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
    )

    for _ in range(5):
        tl.advance()

    before = tl.world_at(event.tick - 1)
    at_event = tl.world_at(event.tick)

    assert before.country(occupied_code).status == CountryStatus.ACTIVE
    assert before.country(occupied_code).occupied_by is None
    assert at_event.country(occupied_code).status == CountryStatus.OCCUPIED
    assert at_event.country(occupied_code).occupied_by == occupier_code
    assert at_event.country(occupied_code).status == tl.world.country(occupied_code).status
    assert at_event.country(occupied_code).occupied_by == tl.world.country(
        occupied_code
    ).occupied_by
    assert before.country(occupied_code).occupation_start_tick is None
    assert at_event.country(occupied_code).occupation_start_tick == event.tick
    assert (
        at_event.country(occupied_code).occupation_start_tick
        == tl.world.country(occupied_code).occupation_start_tick
    )


def test_scrub_to_tick_matches_world_at():
    """§3.4: scrubbing reads Timeline.world_at(t) -- this is that contract, spelled out
    as its own test rather than only exercised incidentally by other files."""
    tl = _two_country_timeline()
    for _ in range(37):
        tl.advance()
    scrubbed = tl.world_at(37)
    assert scrubbed.tick == tl.world.tick == 37
    assert [c.code for c in scrubbed.countries] == [c.code for c in tl.world.countries]


def test_restart_prunes_countries_born_after_the_restart_tick():
    """§3.7: "Countries born after tick un-happen." No currently-implemented system
    creates countries at runtime yet (SECESSION's structural creation is deferred, see
    docs/design-decisions.md), so this scripts an "impossible" born_at_tick directly onto
    the snapshot restart(5) will actually reconstruct from -- the only way to exercise
    the prune path in isolation before real country-creation lands."""
    tl = _two_country_timeline()
    for _ in range(10):
        tl.advance()

    late_code = tl.snapshots[0].countries[0].code
    tl.snapshots[0].countries[0].born_at_tick = 999

    mv = Multiverse(prime=tl)
    mv.restart(5)

    codes = {c.code for c in mv.prime.world.countries}
    assert late_code not in codes
    assert mv.prime.world.tick == 5


def test_restart_keeps_countries_born_at_or_before_the_restart_tick():
    tl = _two_country_timeline()
    for _ in range(10):
        tl.advance()
    original_codes = {c.code for c in tl.world.countries}

    mv = Multiverse(prime=tl)
    mv.restart(5)

    assert {c.code for c in mv.prime.world.countries} == original_codes


def test_world_at_tolerates_hand_built_events_with_no_recorded_structural_payload():
    """Replay handlers must no-op (not raise) on events with no recorded outcome -- e.g.
    a hand-built LEADER_CHANGE event from a test/older save that predates payload
    recording."""
    tl = _two_country_timeline()
    code = tl.world.countries[0].code
    for _ in range(3):
        tl.advance()
    tl.world.log.append(
        tick=tl.world.tick,
        kind="LEADER_CHANGE",
        country=code,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},  # no new_leader_name/new_leader_traits recorded
        severity=1,
    )
    for _ in range(3):
        tl.advance()
    reconstructed = tl.world_at(tl.world.tick)  # must not raise
    assert reconstructed.country(code) is not None


def _bloc_fingerprint(world) -> tuple:
    """M11: the diplomatic state world_at must reconstruct exactly -- bloc membership,
    embargo lanes, and relations. If any structural/replay handler is wrong, scrubbing
    silently shows the wrong alliances."""
    return (
        sorted((bl.id, tuple(sorted(bl.members)), bl.formed_at_tick) for bl in world.blocs),
        sorted(world.embargoes),
        sorted((k, round(v, 6)) for k, v in world.relations.items()),
    )


def test_world_at_reconstructs_bloc_and_embargo_state_exactly() -> None:
    settings = WorldSettings(starting_country_count=8, snapshot_interval=50)
    world = generate_world(1337, settings)
    tl = Timeline(seed=1337, world=world, snapshots={}, rng=Rng(1337))
    live_fingerprints: dict[int, tuple] = {}
    for _ in range(300):
        tl.advance()
        live_fingerprints[tl.world.tick] = _bloc_fingerprint(tl.world)
    # seed 1337 forms a persistent bloc by tick ~6, so these ticks exercise real bloc state
    for at in (50, 137, 200, 299):
        reconstructed = tl.world_at(at)
        assert _bloc_fingerprint(reconstructed) == live_fingerprints[at], f"divergence at t{at}"


def _trade_fingerprint(world) -> tuple:
    """M12/M13: the fiscal + inventory + in-flight state world_at must reconstruct exactly.
    Ledger legs (treasury + fx: pools) and commodity-stock stat_deltas ride the generic
    replay paths; the in-flight Shipment objects ride SHIPMENT_DISPATCHED/ARRIVED/LOST
    structural REPLAY handlers (systems/logistics.py). This is the test that would catch a
    money leak, a stock divergence, or a shipment the replay path failed to reconstruct."""
    return (
        sorted((c.code, tuple(sorted(c.pools.items()))) for c in world.countries),
        sorted(world.fx_pools.items()),
        sorted(
            (c.code, name, round(c.commodity_stock[name], 6))
            for c in world.countries
            for name in sorted(c.commodity_stock)
        ),
        world.shipment_seq,
        sorted(
            (s.id, s.origin, s.dest, s.commodity, round(s.qty, 6), s.carrier, s.arrive_tick)
            for s in world.shipments
        ),
    )


def test_world_at_reconstructs_trade_pools_stock_and_fx_exactly() -> None:
    # The full tick loop drives real bilateral trade + in-flight logistics every tick
    # (M12/M13): conserved cross-currency transfers through fx: pools, per-country stock, and
    # a live set of in-flight Shipment objects created/removed mid-tick.
    settings = WorldSettings(starting_country_count=8, snapshot_interval=50)
    world = generate_world(1337, settings)
    tl = Timeline(seed=1337, world=world, snapshots={}, rng=Rng(1337))
    live_fingerprints: dict[int, tuple] = {}
    for _ in range(300):
        tl.advance()
        live_fingerprints[tl.world.tick] = _trade_fingerprint(tl.world)
    for at in (50, 137, 200, 299):
        reconstructed = tl.world_at(at)
        assert _trade_fingerprint(reconstructed) == live_fingerprints[at], f"divergence at t{at}"


def _asset_fingerprint(world) -> tuple:
    """M14: everything the asset couplings read. Condition rides `infra:` StatDeltas through
    the generic replay path -- so this is the assertion that M14 genuinely added NO new
    replay surface, rather than the assumption that it didn't."""
    return _trade_fingerprint(world) + (
        sorted(
            (c.code, cls, round(getattr(c.infrastructure, cls).condition, 9))
            for c in world.countries
            for cls in config.INFRA_ASSET_CLASSES
        ),
        sorted(
            (c.code, name, round(c.commodity_output[name], 6))
            for c in world.countries
            for name in sorted(c.commodity_output)
        ),
    )


def test_world_at_reconstructs_a_metered_partially_filled_world() -> None:
    """M14's load-bearing determinism proof.

    Freight metering, the grid production multiplier, satellite reach and comms friction are
    all pure functions of asset CONDITION, and the per-tick freight budgets are locals that
    never touch World -- so no new structural/replay handler pair should be needed. That claim
    only means something if a world where the couplings actually BITE reconstructs exactly:
    fleets are cut to a fraction of demand (so fills are metered and the remainder re-matches
    next tick) and every quality asset is degraded (so the multiplier, reach, spread and
    routing friction are all off their no-op values).
    """
    settings = WorldSettings(starting_country_count=8, snapshot_interval=50)
    world = generate_world(1337, settings)
    for c in world.countries:
        for cls in ("naval_fleet", "rail_network", "air_fleet"):
            asset = getattr(c.infrastructure, cls)
            asset.count = 1  # a single unit: metering binds constantly
            asset.condition = 0.3
        c.infrastructure.power_grid.condition = 0.35
        c.infrastructure.satellites.condition = 0.3
        c.infrastructure.communications.condition = 0.25
    tl = Timeline(seed=1337, world=world, snapshots={}, rng=Rng(1337))
    live_fingerprints: dict[int, tuple] = {}
    for _ in range(300):
        tl.advance()
        live_fingerprints[tl.world.tick] = _asset_fingerprint(tl.world)

    # The metering must actually have bitten, or this test proves nothing.
    dispatched = [e for e in tl.world.log if e.kind == "SHIPMENT_DISPATCHED"]
    assert dispatched, "no trade at all -- the fixture is not exercising the coupled paths"
    cap = 1 * 0.3 * config.CARRIER_CAPACITY_PER_UNIT["sea"]
    assert any(
        e.payload["carrier"] == "sea" and e.payload["qty"] >= cap - 1e-9 for e in dispatched
    ), "no shipment hit a fleet budget -- metering never bound, so nothing was proven"

    for at in (50, 137, 200, 299):
        reconstructed = tl.world_at(at)
        assert _asset_fingerprint(reconstructed) == live_fingerprints[at], f"divergence at t{at}"
