import pytest

from meddler.engine import config, tickloop
from meddler.engine.ledger import round_money
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import fiscal
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def test_fiscal_tax_formula_exact_and_split_by_wage_share():
    world = _two_country_world()
    country = world.countries[0]
    before_treasury = country.pools["treasury"]
    before_households = country.pools["households"]
    before_corporates = country.pools["corporates"]

    fiscal.run(world, Rng(1))

    total_tax = round_money(country.tax_rate * country.gdp_tick)
    household_tax = round_money(total_tax * config.WAGE_SHARE_OF_GDP)
    corporate_tax = total_tax - household_tax

    updated = world.country(country.code)
    assert updated.pools["treasury"] == before_treasury + total_tax
    assert updated.pools["households"] == before_households - household_tax
    assert updated.pools["corporates"] == before_corporates - corporate_tax


def test_fiscal_tax_parts_sum_exactly_to_total_no_rounding_drift():
    world = _two_country_world()
    events = fiscal.run(world, Rng(1))
    for event in events:
        assert (
            event.payload["household_tax"] + event.payload["corporate_tax"]
            == event.payload["total_tax"]
        )


def test_fiscal_skips_zero_tax():
    world = _two_country_world()
    for c in world.countries:
        c.tax_rate = 0.0
    events = fiscal.run(world, Rng(1))
    assert events == []


def test_fiscal_events_are_root_ambient():
    world = _two_country_world()
    events = fiscal.run(world, Rng(1))
    for event in events:
        assert event.kind == "TAX_COLLECTION"
        assert event.parent_id is None
        assert event.depth == 0
        assert event.is_intervention is False


def test_world_at_reconstructs_fiscal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [fiscal.run])
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    world = generate_world(9, settings)
    tl = Timeline(seed=9, world=world, snapshots={}, rng=Rng(9))
    for _ in range(37):
        tl.advance()

    reconstructed = tl.world_at(37)
    live_by_code = {c.code: c for c in tl.world.countries}
    for c in reconstructed.countries:
        assert c.pools == live_by_code[c.code].pools
