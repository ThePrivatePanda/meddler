import pytest

from meddler.engine import config, tickloop
from meddler.engine.ledger import round_money
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import production
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def test_production_gdp_formula_exact():
    world = _two_country_world()
    rng = Rng(1)
    country = world.countries[0]
    base_gdp, stability, innovation_mult = (
        country.base_gdp,
        country.stability,
        country.innovation_mult,
    )
    before_corporates = country.pools["corporates"]
    before_households = country.pools["households"]

    production.run(world, rng)

    expected_gdp_tick = base_gdp * (0.5 + stability / 200) * innovation_mult
    updated = world.country(country.code)
    assert updated.gdp_tick == pytest.approx(expected_gdp_tick)

    minted = round_money(expected_gdp_tick)
    wages = round_money(minted * config.WAGE_SHARE_OF_GDP)
    assert updated.pools["corporates"] == before_corporates + minted - wages
    assert updated.pools["households"] == before_households + wages


def test_production_events_are_root_ambient():
    world = _two_country_world()
    rng = Rng(1)
    events = production.run(world, rng)
    assert len(events) == len(world.countries)
    for event in events:
        assert event.parent_id is None
        assert event.depth == 0
        assert event.is_intervention is False
        assert event.severity == 0
        assert event.kind == "PRODUCTION"


def test_production_processes_countries_in_sorted_order():
    world = _two_country_world()
    rng = Rng(1)
    events = production.run(world, rng)
    codes = [e.country for e in events]
    assert codes == sorted(codes)


def test_world_at_reconstructs_production_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real-system version of the M1.4 stat-delta regression test (a review
    recommendation): every field ProductionSystem mutates must survive snapshot+replay,
    not just the ones a fixture happened to touch."""
    monkeypatch.setattr(tickloop, "SYSTEMS", [production.run])
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    world = generate_world(11, settings)
    tl = Timeline(seed=11, world=world, snapshots={}, rng=Rng(11))
    for _ in range(37):
        tl.advance()

    reconstructed = tl.world_at(37)

    live_by_code = {c.code: c for c in tl.world.countries}
    for c in reconstructed.countries:
        live = live_by_code[c.code]
        assert c.gdp_tick == pytest.approx(live.gdp_tick)
        assert c.pools == live.pools
