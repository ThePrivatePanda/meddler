"""InflationSystem: update inflation from minting/shortage pressure.
PROPOSAL §6.1 slot 6, §6.2.

"Money supply minted this tick" is read as every mint-kind ledger entry recorded by
any earlier system this tick (ProductionSystem's gdp_tick mint, TradeSystem's
export-proceeds mint) landing in this country's own pools -- there is no other
bookkeeping of "how much was minted" than the ledger itself. Verified empirically
(tests/unit/test_inflation.py) that this keeps inflation in a legible, non-runaway
band over 300 ticks rather than spiraling; see docs/design-decisions.md.

Decay ("decays 2% of itself per tick toward 2% baseline") is read as proportional
pull toward the baseline: inflation -= DECAY_RATE * (inflation - baseline).
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.events import Event, StatDelta
from meddler.engine.model import World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_country_stat


def _minted_this_tick(world: World, code: str) -> int:
    return world.log.minted_amount(tick=world.tick, country=code)


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in world.living_countries():
        money_supply = sum(country.pools.values())
        minted = _minted_this_tick(world, country.code)
        pct_minted = (minted / money_supply * 100) if money_supply > 0 else 0.0
        # STOCK-based, not flow-based (2026-07-22 pacing pass, matching systems/stability.py's
        # identical fix). `grain_output < grain_need` flagged every structural food IMPORTER --
        # a country perfectly well fed through trade -- as "in shortage" and added
        # INFLATION_SHORTAGE_BUMP (0.3) EVERY tick; against the 0.02 decay that pulls inflation
        # toward ~17%, so mean inflation ran ~9% (baseline 2%) and 44% of country-ticks sat
        # above the crisis line. That single flow/stock confusion was the biggest driver of
        # both INFLATION_CRISIS and the stability flood. The bump now fires only on a genuine
        # food shortage (the FAMINE_WARNING line).
        shortage = country.grain_stock < country.grain_need * config.FAMINE_WARNING_DAYS

        new_inflation = country.inflation
        new_inflation += config.INFLATION_PER_PCT_MINTED * pct_minted
        if shortage:
            new_inflation += config.INFLATION_SHORTAGE_BUMP
        new_inflation -= config.INFLATION_DECAY_RATE * (
            country.inflation - config.INFLATION_BASELINE
        )

        stat_deltas: list[StatDelta] = []
        apply_country_stat(
            world, stat_deltas, country.code, "inflation", new_inflation - country.inflation
        )

        event = world.log.append(
            tick=world.tick,
            kind="INFLATION_UPDATE",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={"inflation": new_inflation, "minted": minted, "pct_minted": pct_minted},
            stat_deltas=tuple(stat_deltas),
            severity=0,
        )
        events.append(event)
    return events
