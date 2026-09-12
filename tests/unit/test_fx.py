from math import tanh

import pytest

from meddler.engine import config, tickloop
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import fx
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def test_fx_deficit_country_currency_weakens():
    world = _two_country_world()
    country = world.countries[0]
    country.grain_need = 100.0
    country.grain_output = 80.0  # deficit
    country.inflation = 0.0  # isolate the trade term
    before_rate = country.exchange_rate

    fx.run(world, Rng(1))

    trade_balance_norm = (100.0 - 80.0) / 100.0
    expected_rate = before_rate * (1 + config.FX_TRADE_DRIFT_COEFFICIENT * tanh(trade_balance_norm))
    updated = world.country(country.code)
    assert updated.exchange_rate == pytest.approx(expected_rate)
    assert updated.exchange_rate > before_rate  # weakens (more local units per veri)


def test_fx_surplus_country_currency_strengthens():
    world = _two_country_world()
    country = world.countries[1]
    country.grain_need = 80.0
    country.grain_output = 100.0  # surplus
    country.inflation = 0.0
    before_rate = country.exchange_rate

    fx.run(world, Rng(1))

    trade_balance_norm = (80.0 - 100.0) / 80.0
    expected_rate = before_rate * (1 + config.FX_TRADE_DRIFT_COEFFICIENT * tanh(trade_balance_norm))
    updated = world.country(country.code)
    assert updated.exchange_rate == pytest.approx(expected_rate)
    assert updated.exchange_rate < before_rate  # strengthens


def test_fx_higher_inflation_weakens_currency_further():
    world = _two_country_world()
    country = world.countries[0]
    country.grain_output = country.grain_need  # isolate the inflation term
    country.inflation = 10.0
    before_rate = country.exchange_rate

    fx.run(world, Rng(1))

    expected_rate = before_rate * (1 + config.FX_INFLATION_DRIFT_COEFFICIENT * (10.0 / 100))
    updated = world.country(country.code)
    assert updated.exchange_rate == pytest.approx(expected_rate)
    assert updated.exchange_rate > before_rate


def test_fx_events_are_root_ambient_one_per_country():
    world = _two_country_world()
    events = fx.run(world, Rng(1))
    assert len(events) == len(world.countries)
    for event in events:
        assert event.kind == "FX_DRIFT"
        assert event.parent_id is None
        assert event.depth == 0


def test_world_at_reconstructs_fx_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [fx.run])
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    world = generate_world(5, settings)
    world.countries[0].grain_output = world.countries[0].grain_need - 4
    tl = Timeline(seed=5, world=world, snapshots={}, rng=Rng(5))
    for _ in range(37):
        tl.advance()

    reconstructed = tl.world_at(37)
    live_by_code = {c.code: c for c in tl.world.countries}
    for c in reconstructed.countries:
        assert c.exchange_rate == pytest.approx(live_by_code[c.code].exchange_rate)


def test_a_country_whose_population_reached_zero_does_not_crash_the_tick():
    """A nation can lose its whole population -- occupation drains it, and nothing in the
    engine ever sets CountryStatus.DISSOLVED, so it stays ACTIVE and keeps being simulated.
    Its food need scales with population, so it reaches zero too, and dividing by it took
    the whole tick down at t777 of seed 1337 on the first run after war drain was rewritten.

    Zero need means no food imbalance -- the same reading systems/trade.py and
    systems/war.py already take of a zero need. Without the guard this raises
    ZeroDivisionError rather than failing an assertion.
    """
    world = generate_world(1337, WorldSettings(starting_country_count=3))
    dead = world.countries[0]
    dead.population = 0.0
    for name in list(dead.commodity_need):
        dead.commodity_need[name] = 0.0
        dead.commodity_output[name] = 0.0
    before = dead.exchange_rate

    events = fx.run(world, Rng(1))

    assert len(events) == len(world.countries)  # it is still simulated, not skipped
    # No food imbalance, so the only drift left is the inflation term -- never a crash.
    expected = before * (
        1 + config.FX_INFLATION_DRIFT_COEFFICIENT * (dead.inflation / 100) +
        config.FX_TRADE_DRIFT_COEFFICIENT * tanh(0.0)
    )
    assert dead.exchange_rate == pytest.approx(expected)
