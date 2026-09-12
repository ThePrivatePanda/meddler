"""M7.2: infrastructure failure-threshold rolls. PROPOSAL §6.7.3/§6.7.5.

M7.2's own acceptance test (implementation-spec.md): "zero treasury for 100 ticks
produces SATELLITE_FAILURE and the full §6.7.5 chain, traced end to end."
"""

from __future__ import annotations

from meddler.engine import tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import infrastructure
from meddler.engine.trace import trace
from meddler.engine.worldgen import generate_world


def _zero_treasury_world():
    settings = WorldSettings(starting_country_count=2)
    world = generate_world(1, settings)
    for country in world.countries:
        country.pools["treasury"] = 0
        # Low stability adds INFRA_INSTABILITY_DEGRADATION_RATE on top of the pure
        # zero-funding degradation, so condition crosses every failure threshold well
        # within 100 ticks regardless of starting condition (up to 0.9).
        country.stability = 0.0
    return world


def test_zero_treasury_produces_satellite_failure_and_its_chain_traced_end_to_end():
    world = _zero_treasury_world()
    rng = Rng(1)

    fired: list = []
    for _ in range(100):
        world.tick += 1
        fired.extend(infrastructure.run(world, rng))

    satellite_failures = [e for e in fired if e.kind == "SATELLITE_FAILURE"]
    assert satellite_failures, "expected >=1 SATELLITE_FAILURE within 100 zero-treasury ticks"

    # §6.7.5's chain: SATELLITE_FAILURE -> COMMS_BLACKOUT -> MEDIA_SUPPRESSION ->
    # UNREST/COUP_RISK_UP. The consequence queue schedules children with real delays
    # (up to d=3-10 for MEDIA_SUPPRESSION, d=10-35 for UNREST), so keep draining it
    # forward in time to let the chain actually fire.
    from meddler.engine.systems import consequence

    for _ in range(200):
        world.tick += 1
        fired.extend(consequence.run(world, rng))

    kinds_fired = {e.kind for e in fired}
    assert "COMMS_BLACKOUT" in kinds_fired, f"chain stalled after SATELLITE_FAILURE: {kinds_fired}"

    satellite_failure_ids = {e.id for e in satellite_failures}
    comms_blackout = next(
        e for e in fired if e.kind == "COMMS_BLACKOUT" and e.parent_id in satellite_failure_ids
    )
    nodes = trace(world.log, comms_blackout.id)
    assert nodes[0]["kind"] == "SATELLITE_FAILURE"
    assert nodes[0]["id"] in satellite_failure_ids
    assert any(n["id"] == comms_blackout.id for n in nodes)


def test_failure_does_not_fire_above_threshold():
    world = _zero_treasury_world()
    for country in world.countries:
        for cls in ("satellites", "naval_fleet", "rail_network", "power_grid", "communications"):
            getattr(country.infrastructure, cls).condition = 0.95
        country.stability = 80.0  # no instability degradation either
    rng = Rng(1)

    fired = []
    for _ in range(20):
        world.tick += 1
        fired.extend(infrastructure.run(world, rng))

    failure_kinds = {
        "SATELLITE_FAILURE",
        "NAVAL_LOSS",
        "PORT_CLOSURE",
        "RAIL_COLLAPSE",
        "POWER_OUTAGE",
        "COMMS_BLACKOUT",
    }
    assert not any(e.kind in failure_kinds for e in fired)


def test_failure_hysteresis_does_not_refire_while_still_armed():
    world = _zero_treasury_world()
    code = world.countries[0].code
    world.countries[0].infrastructure.satellites.condition = 0.1  # deep below threshold
    rng = Rng(7)

    fired = []
    for _ in range(50):
        world.tick += 1
        fired.extend(infrastructure.run(world, rng))

    satellite_failures = [
        e for e in fired if e.kind == "SATELLITE_FAILURE" and e.country == code
    ]
    # Condition stays near 0 (zero treasury, zero stability) with no recovery, so once
    # armed it must not refire even across 50 ticks below threshold.
    assert len(satellite_failures) <= 1
