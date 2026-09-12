import pytest

from meddler.engine import config, tickloop
from meddler.engine.events import LedgerEntry
from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import fiscal, fx, inflation, production, stability, trade
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _two_country_world():
    settings = WorldSettings(starting_country_count=2)
    return generate_world(1, settings)


def _mint_now(world, code, amount):
    world.log.append(
        tick=world.tick,
        kind="TEST_PRIOR_MINT",
        country=code,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
        ledger=(
            LedgerEntry(
                kind="mint",
                src_pool=None,
                dst_pool=f"{code}.corporates",
                amount=amount,
                currency=code,
            ),
        ),
        severity=0,
    )


def test_inflation_formula_exact_no_shortage():
    world = _two_country_world()
    country = world.countries[0]
    country.grain_output = country.grain_need  # no shortage
    money_supply_before = sum(country.pools.values())
    inflation_before = country.inflation
    minted = 1000
    _mint_now(world, country.code, minted)

    inflation.run(world, Rng(1))

    pct_minted = minted / money_supply_before * 100
    expected = (
        inflation_before
        + config.INFLATION_PER_PCT_MINTED * pct_minted
        - config.INFLATION_DECAY_RATE * (inflation_before - config.INFLATION_BASELINE)
    )
    updated = world.country(country.code)
    assert updated.inflation == pytest.approx(expected)


def test_inflation_shortage_adds_flat_bump():
    world = _two_country_world()
    a = world.countries[0]
    b = world.countries[1]
    # Equalise inflation so the decay terms match and the only difference is the bump.
    a.inflation = b.inflation = config.INFLATION_BASELINE
    # Shortage is STOCK-based (2026-07-22 fix): a is well stocked (no bump), b's food stock is
    # below the FAMINE_WARNING line (bump). grain_output is irrelevant to the shortage flag now.
    a.grain_stock = a.grain_need * config.FAMINE_WARNING_DAYS * 2
    b.grain_stock = 0.0
    a_inflation_before = a.inflation
    b_inflation_before = b.inflation

    inflation.run(world, Rng(1))

    a_delta = world.country(a.code).inflation - a_inflation_before
    b_delta = world.country(b.code).inflation - b_inflation_before
    assert b_delta - a_delta == pytest.approx(config.INFLATION_SHORTAGE_BUMP)


def test_inflation_decays_toward_baseline_with_no_minting_no_shortage():
    world = _two_country_world()
    country = world.countries[0]
    country.grain_output = country.grain_need
    country.inflation = 10.0
    before = country.inflation

    inflation.run(world, Rng(1))

    updated = world.country(country.code)
    expected = before - config.INFLATION_DECAY_RATE * (before - config.INFLATION_BASELINE)
    assert updated.inflation == pytest.approx(expected)
    assert updated.inflation < before  # pulled down toward the 2% baseline


def test_inflation_events_are_root_ambient():
    world = _two_country_world()
    events = inflation.run(world, Rng(1))
    for event in events:
        assert event.kind == "INFLATION_UPDATE"
        assert event.parent_id is None
        assert event.depth == 0


def test_stability_penalizes_high_inflation_shortage_and_war():
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = config.STABILITY_HIGH_INFLATION_THRESHOLD + 1
    # A food shortage is now a STOCK condition (2026-07-22 pacing fix): drive grain_stock
    # below the FAMINE_WARNING line, not merely grain_output below grain_need (a country can
    # import to cover a flow deficit and still be well stocked, hence not penalised).
    country.grain_stock = 0.0
    country.at_war_with = ["XXX"]
    before = country.stability

    stability.run(world, Rng(1))

    updated = world.country(country.code)
    # At war the target is 0, so the reversion term is itself a drain; inflation and
    # shortage are additive displacements on top of it.
    expected_delta = (
        config.STABILITY_REVERSION_RATE * (0.0 - before)
        - config.STABILITY_WAR_PENALTY
        - config.STABILITY_HIGH_INFLATION_PENALTY
        - config.STABILITY_SHORTAGE_PENALTY
    )
    assert updated.stability == pytest.approx(max(0.0, min(100.0, before + expected_delta)))


def test_stability_reverts_toward_the_target_from_below():
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = 2.0
    country.grain_output = country.grain_need
    country.at_war_with = []
    target = stability.target_stability(country)
    country.stability = target - 20.0
    before = country.stability

    stability.run(world, Rng(1))

    updated = world.country(country.code)
    assert updated.stability > before  # a country below its equilibrium climbs
    assert updated.stability == pytest.approx(
        before + config.STABILITY_REVERSION_RATE * (target - before)
    )


def test_stability_reverts_toward_the_target_from_above():
    """The half the old flat bonus could not do: a calm country ABOVE its equilibrium
    settles back down to it instead of ratcheting into the clamp at 100."""
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = 2.0
    country.grain_output = country.grain_need
    country.at_war_with = []
    target = stability.target_stability(country)
    country.stability = target + 20.0
    before = country.stability

    stability.run(world, Rng(1))

    assert world.country(country.code).stability < before


def test_no_country_has_the_ceiling_as_its_equilibrium():
    """The flatness guarantee: whatever a country's record, its target stays under 100, so
    stability approaches its level asymptotically and the clamp is never an attractor."""
    world = _two_country_world()
    country = world.countries[0]
    country.at_war_with = []
    country.base_stability = 100.0
    for stat in ("civil_rights", "press_freedom", "education", "health"):
        setattr(country, stat, 100.0)
    assert stability.target_stability(country) <= config.STABILITY_TARGET_CEILING < 100.0


def test_a_country_at_war_has_no_equilibrium_and_drains():
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = 2.0
    country.grain_output = country.grain_need
    country.at_war_with = ["XXX"]
    assert stability.target_stability(country) == 0.0

    previous = country.stability
    for _ in range(5):
        stability.run(world, Rng(1))
        current = world.country(country.code).stability
        assert current < previous  # monotonic for the whole duration of the war
        previous = current


def test_a_war_drains_stability_all_the_way_to_zero():
    """A LIMIT, not a direction. Reversion alone is geometric and never arrives, which
    would leave OCCUPATION_BEGIN's `stability <= 0.0` gate unreachable and quietly delete
    conquest from the world. The additive war penalty is what makes the drain terminate."""
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = 2.0
    country.grain_output = country.grain_need
    country.stability = 100.0
    country.at_war_with = ["XXX"]

    for tick in range(1, 201):
        stability.run(world, Rng(1))
        if world.country(country.code).stability <= 0.0:
            assert tick < 150, f"war took {tick} ticks to reach the occupation gate"
            break
    else:
        raise AssertionError("a war never drained stability to the occupation gate")


def test_a_better_record_raises_the_target():
    world = _two_country_world()
    a, b = world.countries[0], world.countries[1]
    for country in (a, b):
        country.at_war_with = []
        country.base_stability = 60.0
    for stat in ("civil_rights", "press_freedom", "education", "health"):
        setattr(a, stat, getattr(a, stat) + 20.0)
    assert stability.target_stability(a) > stability.target_stability(b)


def test_persistent_shortage_parks_below_the_target_instead_of_dying():
    """A famine state hovers in crisis range rather than sliding to zero: the additive
    penalty balances the reversion pull at target - penalty/rate."""
    world = _two_country_world()
    country = world.countries[0]
    country.inflation = 2.0
    country.at_war_with = []
    country.grain_stock = 0.0
    target = stability.target_stability(country)
    expected = target - config.STABILITY_SHORTAGE_PENALTY / config.STABILITY_REVERSION_RATE
    country.stability = max(0.0, expected)

    stability.run(world, Rng(1))

    assert world.country(country.code).stability == pytest.approx(max(0.0, expected), abs=1e-9)


def test_stability_clamps_at_zero():
    world = _two_country_world()
    country = world.countries[0]
    country.stability = 0.1
    country.inflation = config.STABILITY_HIGH_INFLATION_THRESHOLD + 1
    country.grain_output = country.grain_need - 1
    country.at_war_with = ["XXX"]

    stability.run(world, Rng(1))

    assert world.country(country.code).stability == 0.0


def test_stability_stays_inside_the_clamp():
    world = _two_country_world()
    country = world.countries[0]
    country.stability = 99.95
    country.inflation = 2.0
    country.grain_output = country.grain_need
    country.at_war_with = []

    stability.run(world, Rng(1))

    # It can no longer REACH 100 by recovering -- above its target it falls -- but the
    # clamp still holds for any state a stat_delta could put it in.
    assert 0.0 <= world.country(country.code).stability <= 100.0
    assert world.country(country.code).stability < 99.95


def test_inflation_stays_in_legible_band_over_full_pipeline():
    """Empirical check (recommended in review) of the 'what counts as minted' reading:
    run the real economic pipeline for 300 ticks and confirm inflation settles into a
    bounded band rather than spiraling. If this ever fails after a legitimate formula
    change, that's the signal to narrow the minted-this-tick definition -- see
    docs/design-decisions.md."""
    settings = WorldSettings(starting_country_count=2, snapshot_interval=50)
    world = generate_world(77, settings)
    rng = Rng(77)
    systems = [production.run, trade.run, fx.run, fiscal.run, inflation.run, stability.run]
    for _ in range(300):
        world.tick += 1
        for system in systems:
            system(world, rng)

    for country in world.countries:
        assert -20.0 < country.inflation < 40.0
        assert 0.0 <= country.stability <= 100.0


def test_world_at_reconstructs_inflation_and_stability_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tickloop, "SYSTEMS", [production.run, inflation.run, stability.run])
    settings = WorldSettings(starting_country_count=3, snapshot_interval=10)
    world = generate_world(3, settings)
    tl = Timeline(seed=3, world=world, snapshots={}, rng=Rng(3))
    for _ in range(37):
        tl.advance()

    reconstructed = tl.world_at(37)
    live_by_code = {c.code: c for c in tl.world.countries}
    for c in reconstructed.countries:
        live = live_by_code[c.code]
        assert c.inflation == pytest.approx(live.inflation)
        assert c.stability == pytest.approx(live.stability)
