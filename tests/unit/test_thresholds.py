import pytest

from meddler.engine import config, tickloop
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import inflation, stability, thresholds
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def test_inflation_crisis_fires_once_then_hysteresis_suppresses_repeats():
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = config.INFLATION_CRISIS_THRESHOLD + 1

    first = thresholds.run(world, Rng(1))
    second = thresholds.run(world, Rng(1))  # inflation still high, should NOT re-fire

    assert any(e.kind == "INFLATION_CRISIS" and e.country == country.code for e in first)
    assert not any(e.kind == "INFLATION_CRISIS" and e.country == country.code for e in second)


def test_inflation_crisis_rearms_after_recovering_past_margin():
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = config.INFLATION_CRISIS_THRESHOLD + 1
    thresholds.run(world, Rng(1))  # fires, arms

    country.inflation = 0.0  # well below threshold - margin
    thresholds.run(world, Rng(1))  # recovers, disarms

    country.inflation = config.INFLATION_CRISIS_THRESHOLD + 1
    refired = thresholds.run(world, Rng(1))
    assert any(e.kind == "INFLATION_CRISIS" and e.country == country.code for e in refired)


def test_unrest_and_civil_war_risk_thresholds():
    world = _two_country_world()
    country = world.countries[0]
    country.stability = config.UNREST_THRESHOLD - 1
    events = thresholds.run(world, Rng(1))
    assert any(e.kind == "UNREST" and e.country == country.code for e in events)
    assert not any(e.kind == "CIVIL_WAR_RISK" for e in events)

    country.armed.clear()
    country.stability = config.CIVIL_WAR_RISK_THRESHOLD - 1
    events2 = thresholds.run(world, Rng(1))
    assert any(e.kind == "UNREST" for e in events2)
    assert any(e.kind == "CIVIL_WAR_RISK" for e in events2)


def test_famine_warning_and_famine_thresholds():
    world = _two_country_world()
    country = world.countries[0]
    country.grain_stock = country.grain_need * (config.FAMINE_WARNING_DAYS - 1)
    events = thresholds.run(world, Rng(1))
    assert any(e.kind == "FAMINE_WARNING" and e.country == country.code for e in events)
    assert not any(e.kind == "FAMINE" for e in events)

    country.armed.clear()
    country.grain_stock = 0.0
    events2 = thresholds.run(world, Rng(1))
    assert any(e.kind == "FAMINE" for e in events2)
    for e in events2:
        if e.kind in ("FAMINE_WARNING", "FAMINE"):
            assert e.parent_id is None


def test_debt_crisis_threshold():
    world = _two_country_world()
    country = world.countries[0]
    country.pools["treasury"] = -1
    events = thresholds.run(world, Rng(1))
    event = next(e for e in events if e.kind == "DEBT_CRISIS" and e.country == country.code)
    assert event.parent_id is None


def test_inflation_crisis_parent_is_same_tick_inflation_update_event():
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = config.INFLATION_CRISIS_THRESHOLD + 5
    country.grain_output = country.grain_need  # no shortage bump, isolate the test

    inflation_events = inflation.run(world, Rng(1))
    threshold_events = thresholds.run(world, Rng(1))

    inflation_event = next(e for e in inflation_events if e.country == country.code)
    crisis_event = next(e for e in threshold_events if e.kind == "INFLATION_CRISIS")

    assert crisis_event.parent_id == inflation_event.id
    assert crisis_event.depth == inflation_event.depth + 1


def test_unrest_parent_is_same_tick_stability_update_event():
    world = _two_country_world()
    country = world.countries[0]
    # StabilitySystem's own drift has to cross the line for the parent link to exist at all.
    # Since stability became mean-reverting, a country sitting just above the line is also
    # being pulled UP toward its target, so an inflation penalty alone no longer drags it
    # across -- the country has to be ABOVE its own equilibrium as well as inflating.
    country.stability = config.UNREST_THRESHOLD + 0.05
    country.base_stability = config.UNREST_THRESHOLD - 15.0
    country.inflation = config.STABILITY_HIGH_INFLATION_THRESHOLD + 1
    country.grain_output = country.grain_need

    stability_events = stability.run(world, Rng(1))
    threshold_events = thresholds.run(world, Rng(1))

    stability_event = next(e for e in stability_events if e.country == country.code)
    unrest_event = next(e for e in threshold_events if e.kind == "UNREST")

    assert unrest_event.parent_id == stability_event.id
    assert unrest_event.depth == stability_event.depth + 1


def test_thresholds_events_are_severity_2():
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = config.INFLATION_CRISIS_THRESHOLD + 1
    events = thresholds.run(world, Rng(1))
    for e in events:
        assert e.severity == 2
        assert e.is_intervention is False


def test_world_at_reconstructs_threshold_armed_state_is_not_needed_for_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """armed is explicitly NOT serialised/replayed (§5.1) -- reconstructed on load by
    re-deriving from the live stat values, not from the event log. This test confirms
    replay still reproduces the emitted events themselves (which DO matter for
    history), independent of armed's in-memory-only status."""
    monkeypatch.setattr(tickloop, "SYSTEMS", [inflation.run, thresholds.run])
    settings = WorldSettings(starting_country_count=2, snapshot_interval=10)
    world = generate_world(31, settings)
    world.countries[0].inflation = config.INFLATION_CRISIS_THRESHOLD + 3
    tl = Timeline(seed=31, world=world, snapshots={}, rng=Rng(31))
    for _ in range(15):
        tl.advance()

    reconstructed = tl.world_at(15)
    live_kinds = [(e.tick, e.kind, e.country) for e in tl.world.log]
    reconstructed_kinds = [(e.tick, e.kind, e.country) for e in reconstructed.log]
    assert live_kinds == reconstructed_kinds


def test_unrest_records_multiple_recent_stability_contributors_without_severity_change():
    from meddler.engine.events import StatDelta

    world = _two_country_world()
    country = world.countries[0]
    country.stability = config.UNREST_THRESHOLD - 1
    contributors = []
    for tick, delta in ((0, -3.0), (1, -4.0)):
        world.tick = tick
        contributors.append(
            world.log.append(
                tick=tick,
                kind="TEST_STABILITY_LOSS",
                country=country.code,
                country2=None,
                parent_id=None,
                depth=0,
                is_intervention=False,
                stat_deltas=(StatDelta(country.code, "stability", delta, 50.0, 50.0 + delta),),
            )
        )
    world.tick = 2

    unrest = next(event for event in thresholds.run(world, Rng(1)) if event.kind == "UNREST")
    assert unrest.parent_id is None
    assert unrest.parent_ids == tuple(event.id for event in contributors)
    assert all(link.role == "contributor" for link in unrest.causes)
    assert unrest.severity == 2
