"""FiscalSystem: collect taxes. PROPOSAL §6.1 slot 4, §6.2.

Only a tax formula is specced ("treasury += tax_rate * gdp_tick, from corporates +
households proportionally"). "Subsidies" (M2.4's task title) has no corresponding
formula anywhere in PROPOSAL -- there is no WorldSettings/Government subsidy
mechanism in this simplified v1 model -- and "treasury drift" is just this system's
observable effect on treasury, not a separate mechanic. Both left out of scope until
(if ever) specified; see docs/design-decisions.md.

"Proportionally" is read as mirroring ProductionSystem's wage split
(config.WAGE_SHARE_OF_GDP): tax is drawn from households' and corporates' this-tick
income shares in the same ratio wages were just paid in, rather than introducing an
undefined second ratio.

tax_rate is fraction-scale (0.15 = 15%) -- see config.py's STARTING_TAX_RATE comment.
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.events import Event, LedgerEntry
from meddler.engine.ledger import Ledger, apply_to_world, round_money
from meddler.engine.model import World
from meddler.engine.rng import Rng


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in sorted(world.countries, key=lambda c: c.code):
        total_tax = round_money(country.tax_rate * country.gdp_tick)
        if total_tax <= 0:
            continue

        household_tax = round_money(total_tax * config.WAGE_SHARE_OF_GDP)
        corporate_tax = total_tax - household_tax  # remainder: avoids independent-rounding drift

        ledger: list[LedgerEntry] = []
        if household_tax > 0:
            Ledger.transfer(
                ledger,
                f"{country.code}.households",
                f"{country.code}.treasury",
                household_tax,
                country.code,
            )
        if corporate_tax > 0:
            Ledger.transfer(
                ledger,
                f"{country.code}.corporates",
                f"{country.code}.treasury",
                corporate_tax,
                country.code,
            )

        event = world.log.append(
            tick=world.tick,
            kind="TAX_COLLECTION",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={
                "total_tax": total_tax,
                "household_tax": household_tax,
                "corporate_tax": corporate_tax,
            },
            ledger=tuple(ledger),
            severity=0,
        )
        for entry in event.ledger:
            apply_to_world(world, entry)
        events.append(event)
    return events
