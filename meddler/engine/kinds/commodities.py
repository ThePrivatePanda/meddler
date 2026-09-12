"""Commodity shortage event kinds (M10, v2 spec §3). EventCategory.ECONOMY.

Fired by systems/thresholds.py's hysteresis `_check`, mirroring FAMINE_WARNING/FAMINE's
existing pattern exactly -- food already owns those two kinds, so this module covers the
five NON-food buckets only (energy, raw_materials, manufactured, consumer, high_tech).

Each kind targets durable stats (stability, innovation_mult), never a recomputed output --
gdp_tick/commodity output are recomputed every tick (kinds/economy.py's documented trap;
CommodityProductionSystem's design constraint 4/5), so a stat_delta on either would be
silently erased or bypassed. The bite is modelled the same way economy.py already models
recomputed-field effects (see GDP_TICK_REDUCTION's innovation_mult stand-in).

Consequence edges below are chosen to be causally honest against the REAL production
chain in config.COMMODITY_INPUTS: manufactured needs raw_materials + energy, consumer
needs manufactured. So MATERIALS_SHORTAGE/ENERGY_SHORTAGE feed MANUFACTURING_SLUMP, and
MANUFACTURING_SLUMP feeds CONSUMER_SHORTAGE -- the same one-hop chain the production
system itself enforces, now visible in the narrative layer too.
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, register

register(
    EventSpec(
        kind="ENERGY_SHORTAGE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,  # emitted by the threshold bridge, like FAMINE/DEBT_CRISIS
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": config.COMMODITY_SHORTAGE_STABILITY_HIT},
        consequences=[
            ConsequenceRule(
                "MANUFACTURING_SLUMP", base_p=0.5, delay_min=5, delay_max=15, target="same"
            ),
            ConsequenceRule("PRICE_SPIKE", base_p=0.4, delay_min=3, delay_max=10, target="same"),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="MATERIALS_SHORTAGE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": config.COMMODITY_SHORTAGE_STABILITY_HIT},
        consequences=[
            ConsequenceRule(
                "MANUFACTURING_SLUMP", base_p=0.5, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="MANUFACTURING_SLUMP",
        severity=2,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # innovation_mult, not a raw manufactured-output delta: output is recomputed every
        # tick from gdp_tick/innovation_mult/education (design constraint 5), so a delta on
        # output itself would be overwritten before any system read it.
        stat_deltas={
            "stability": config.COMMODITY_SHORTAGE_STABILITY_HIT,
            "innovation_mult": config.COMMODITY_SHORTAGE_GDP_INNOVATION_HIT,
        },
        consequences=[
            # Causally honest: config.COMMODITY_INPUTS["consumer"] = {"manufactured": 0.4}
            # -- a manufacturing slump is the real one-hop cause of a consumer shortage.
            ConsequenceRule(
                "CONSUMER_SHORTAGE", base_p=0.5, delay_min=5, delay_max=15, target="same"
            ),
            ConsequenceRule(
                "UNEMPLOYMENT_SPIKE", base_p=0.4, delay_min=5, delay_max=20, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="CONSUMER_SHORTAGE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": config.COMMODITY_SHORTAGE_STABILITY_HIT},
        consequences=[
            ConsequenceRule("UNREST", base_p=0.4, delay_min=10, delay_max=30, target="same"),
        ],
        tags=frozenset({"economy", "social"}),
    )
)

register(
    EventSpec(
        kind="TECH_STAGNATION",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"innovation_mult": config.COMMODITY_SHORTAGE_GDP_INNOVATION_HIT},
        consequences=[
            ConsequenceRule(
                "GDP_TICK_REDUCTION", base_p=0.4, delay_min=10, delay_max=30, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)
