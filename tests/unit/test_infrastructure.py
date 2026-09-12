import pytest

from meddler.engine import assets, config, tickloop
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import infrastructure
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def test_zero_treasury_world_degrades_condition_over_time():
    """M2.6's own accept criterion."""
    world = _two_country_world()
    for c in world.countries:
        c.pools["treasury"] = 0
    rng = Rng(1)

    before = {
        cls: getattr(world.countries[0].infrastructure, cls).condition
        for cls in config.INFRA_ASSET_CLASSES
    }
    for _ in range(20):
        infrastructure.run(world, rng)
    after = {
        cls: getattr(world.countries[0].infrastructure, cls).condition
        for cls in config.INFRA_ASSET_CLASSES
    }

    for cls in config.INFRA_ASSET_CLASSES:
        assert after[cls] < before[cls]


def test_fully_funded_world_recovers_condition():
    world = _two_country_world()
    country = world.countries[0]
    country.pools["treasury"] = 10**15  # effectively unlimited
    country.stability = 100.0  # zero instability degradation
    for cls in config.INFRA_ASSET_CLASSES:
        getattr(country.infrastructure, cls).condition = 0.5
    rng = Rng(1)

    infrastructure.run(world, rng)

    for cls in config.INFRA_ASSET_CLASSES:
        asset = getattr(world.country(country.code).infrastructure, cls)
        assert asset.condition == pytest.approx(0.5 + config.INFRA_RECOVERY_RATE)


def test_maintenance_formula_exact_partial_funding():
    world = _two_country_world()
    country = world.countries[0]
    country.stability = 100.0  # isolate the maintenance-ratio term
    maintenance_cost = assets.upkeep_cost(country)
    # set treasury so only half of maintenance is affordable
    country.pools["treasury"] = round((maintenance_cost / 2) / config.INFRA_MAINTENANCE_SHARE_CAP)
    before = {
        cls: getattr(country.infrastructure, cls).condition for cls in config.INFRA_ASSET_CLASSES
    }

    infrastructure.run(world, Rng(1))

    expected_ratio = 0.5
    for cls in config.INFRA_ASSET_CLASSES:
        expected_delta = (
            -(1 - expected_ratio) * config.INFRA_DEGRADATION_RATE
            + expected_ratio * config.INFRA_RECOVERY_RATE
        )
        updated = getattr(world.country(country.code).infrastructure, cls)
        assert updated.condition == pytest.approx(before[cls] + expected_delta, abs=1e-6)


def test_maintenance_payment_transfers_treasury_to_corporates():
    world = _two_country_world()
    country = world.countries[0]
    country.pools["treasury"] = 10**15
    before_treasury = country.pools["treasury"]
    before_corporates = country.pools["corporates"]

    events = infrastructure.run(world, Rng(1))

    event = next(e for e in events if e.country == country.code)
    paid = event.payload["paid"]
    updated = world.country(country.code)
    assert updated.pools["treasury"] == before_treasury - paid
    assert updated.pools["corporates"] == before_corporates + paid


def test_condition_clamped_to_zero_and_one():
    world = _two_country_world()
    country = world.countries[0]
    country.pools["treasury"] = 0
    country.stability = 0.0
    for cls in config.INFRA_ASSET_CLASSES:
        getattr(country.infrastructure, cls).condition = 0.0001

    infrastructure.run(world, Rng(1))

    for cls in config.INFRA_ASSET_CLASSES:
        asset = getattr(world.country(country.code).infrastructure, cls)
        assert asset.condition == 0.0


def test_infrastructure_events_are_root_ambient():
    world = _two_country_world()
    events = infrastructure.run(world, Rng(1))
    assert len(events) == len(world.countries)
    for event in events:
        assert event.kind == "INFRASTRUCTURE_MAINTENANCE"
        assert event.parent_id is None
        assert event.depth == 0


def test_world_at_reconstructs_infrastructure_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [infrastructure.run])
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    world = generate_world(13, settings)
    world.countries[0].pools["treasury"] = 0  # force real degradation, not equilibrium
    tl = Timeline(seed=13, world=world, snapshots={}, rng=Rng(13))
    for _ in range(37):
        tl.advance()

    reconstructed = tl.world_at(37)
    live_by_code = {c.code: c for c in tl.world.countries}
    for c in reconstructed.countries:
        live = live_by_code[c.code]
        for cls in config.INFRA_ASSET_CLASSES:
            r_asset = getattr(c.infrastructure, cls)
            l_asset = getattr(live.infrastructure, cls)
            assert r_asset.condition == pytest.approx(l_asset.condition)
        assert c.pools == live.pools
