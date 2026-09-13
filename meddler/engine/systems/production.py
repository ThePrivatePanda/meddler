"""ProductionSystem: grain + GDP output. PROPOSAL §6.1 slot 1, §6.2.

gdp_tick output is newly-created economic value, not moved from an existing pool, so
it is minted (not transferred) into corporates -- the only reading consistent with
Invariant A (per-currency conservation changes only via explicit mint/burn, §4.5).
Wages are then a real transfer, corporates -> households.
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.events import Event, LedgerEntry, StatDelta
from meddler.engine.ledger import Ledger, apply_to_world, round_money
from meddler.engine.model import World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_country_stat


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in world.living_countries():
        new_gdp_tick = country.base_gdp * (0.5 + country.stability / 200) * country.innovation_mult

        ledger: list[LedgerEntry] = []
        minted = round_money(new_gdp_tick)
        Ledger.mint(ledger, f"{country.code}.corporates", minted, country.code)

        wages = round_money(minted * config.WAGE_SHARE_OF_GDP)
        Ledger.transfer(
            ledger, f"{country.code}.corporates", f"{country.code}.households", wages, country.code
        )

        stat_deltas: list[StatDelta] = []
        apply_country_stat(
            world, stat_deltas, country.code, "gdp_tick", new_gdp_tick - country.gdp_tick
        )

        event = world.log.append(
            tick=world.tick,
            kind="PRODUCTION",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={"gdp_tick": new_gdp_tick, "minted": minted},
            ledger=tuple(ledger),
            stat_deltas=tuple(stat_deltas),
            severity=0,
        )
        for entry in event.ledger:
            apply_to_world(world, entry)
        events.append(event)
    return events
