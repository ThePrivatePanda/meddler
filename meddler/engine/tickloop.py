"""The core tick loop. PROPOSAL §4.2/§6.1."""

from __future__ import annotations

from typing import Callable

import meddler.engine.kinds  # noqa: F401  -- populates EVENT_REGISTRY + validate_all() at import
from meddler.engine.events import Event
from meddler.engine.model import World
from meddler.engine.rng import Rng
from meddler.engine.systems import commodity_production, consequence, diplomacy, exogenous, fiscal
from meddler.engine.systems import fx, infrastructure, inflation, logistics, politics, production
from meddler.engine.systems import relations as relations_system
from meddler.engine.systems import secession  # noqa: F401 -- registers INTERVENE_SECEDE handlers
from meddler.engine.systems import stability, tariffs, thresholds, trade, war

System = Callable[[World, Rng], list[Event]]

# Ordered per PROPOSAL §6.1: production, commodity_production (M10, v2 spec §3 -- slotted
# between production and trade so trade reads this tick's commodity surpluses/deficits and
# folds the production balance into stock), trade (M12 bilateral matching -> M13 dispatches
# in-flight shipments), logistics (M13, v2 spec §5 -- advances shipments: interdiction +
# arrival, right after trade so this tick's dispatches, transit>=1, cannot arrive same tick),
# fx, fiscal, infrastructure, inflation, stability, relations, [thresholds -- §6.3, no
# dedicated slot, see thresholds.py], politics, diplomacy, war (M15: coalition-front
# strikes + resource-war roots), exogenous, consequence.
SYSTEMS: list[System] = [
    production.run,
    commodity_production.run,
    trade.run,
    logistics.run,
    fx.run,
    fiscal.run,
    infrastructure.run,
    inflation.run,
    stability.run,
    relations_system.run,
    thresholds.run,
    politics.run,
    diplomacy.run,
    tariffs.run,
    war.run,
    exogenous.run,
    consequence.run,
]


def tick(world: World, rng: Rng) -> tuple[World, list[Event]]:
    """Advance exactly one tick. Deterministic: same (world, rng state) -> same
    result (§4.2). Mutates `world` in place and returns it together with every
    event emitted this tick, in system order."""
    world.tick += 1
    events: list[Event] = []
    for system in SYSTEMS:
        events.extend(system(world, rng))
    return world, events
