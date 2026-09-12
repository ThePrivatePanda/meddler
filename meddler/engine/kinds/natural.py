"""Natural / exogenous event kinds. PROPOSAL §6.6.1. EventCategory.NATURAL.

Probabilities, delays, and consequence edges are transcribed from the §6.6.1 table.
stat_deltas and any pool amounts are v1 tuning placeholders (§6.6 gives severities and
chains, not magnitudes) -- see docs/design-decisions.md. M3.3 implemented the core
subset (DROUGHT/EARTHQUAKE/METEOR/PLAGUE); M7.1 adds the rest of the table.
"""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, register

register(
    EventSpec(
        kind="DROUGHT",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.006,
        is_intervention=False,
        stat_deltas={"grain_stock": -5.0, "grain_output": -2.0},
        consequences=[
            ConsequenceRule("PRICE_SPIKE", base_p=0.95, delay_min=2, delay_max=8, target="same"),
        ],
        tags=frozenset({"natural", "weather"}),
    )
)

register(
    EventSpec(
        kind="EARTHQUAKE",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.002,
        is_intervention=False,
        stat_deltas={"stability": -3.0},
        consequences=[
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=0.9, delay_min=0, delay_max=2, target="same"
            ),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.8, delay_min=2, delay_max=5, target="same"),
        ],
        tags=frozenset({"natural", "disaster"}),
    )
)

register(
    EventSpec(
        kind="METEOR",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.0002,
        is_intervention=False,
        stat_deltas={"stability": -5.0, "population": -0.1},
        consequences=[
            ConsequenceRule("PRICE_SPIKE", base_p=1.0, delay_min=3, delay_max=6, target="same"),
            ConsequenceRule("UNREST", base_p=0.7, delay_min=8, delay_max=20, target="same"),
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=1.0, delay_min=0, delay_max=1, target="same"
            ),
        ],
        tags=frozenset({"natural", "disaster"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="PLAGUE",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.002,
        is_intervention=False,
        stat_deltas={"health": -10.0, "population": -0.2, "stability": -2.0},
        consequences=[
            ConsequenceRule(
                "LABOUR_SHORTAGE", base_p=0.7, delay_min=8, delay_max=25, target="same"
            ),
            ConsequenceRule("UNREST", base_p=0.6, delay_min=10, delay_max=30, target="same"),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.6, delay_min=4, delay_max=10, target="same"),
        ],
        tags=frozenset({"natural", "health"}),
    )
)

register(
    EventSpec(
        kind="FLOOD",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.003,
        is_intervention=False,
        stat_deltas={"grain_stock": -3.0},
        consequences=[
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=0.8, delay_min=1, delay_max=4, target="same"
            ),
            ConsequenceRule("GRAIN_LOSS", base_p=0.7, delay_min=1, delay_max=3, target="same"),
        ],
        tags=frozenset({"natural", "weather"}),
    )
)

register(
    EventSpec(
        kind="WILDFIRE",
        severity=1,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.003,
        is_intervention=False,
        stat_deltas={"grain_stock": -1.0},
        consequences=[
            ConsequenceRule("GRAIN_LOSS", base_p=0.6, delay_min=1, delay_max=4, target="same"),
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=0.4, delay_min=1, delay_max=3, target="same"
            ),
        ],
        tags=frozenset({"natural", "weather"}),
    )
)

register(
    EventSpec(
        kind="COLD_SNAP",
        severity=1,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.004,
        is_intervention=False,
        stat_deltas={"grain_stock": -1.0},
        consequences=[
            ConsequenceRule("GRAIN_LOSS", base_p=0.5, delay_min=1, delay_max=5, target="same"),
            ConsequenceRule("POWER_OUTAGE", base_p=0.3, delay_min=2, delay_max=6, target="same"),
        ],
        tags=frozenset({"natural", "weather"}),
    )
)

register(
    EventSpec(
        kind="LOCUST_SWARM",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.001,
        is_intervention=False,
        stat_deltas={"grain_output": -3.0},
        consequences=[
            ConsequenceRule("GRAIN_LOSS", base_p=0.95, delay_min=1, delay_max=4, target="same"),
            ConsequenceRule(
                "FAMINE_WARNING", base_p=0.5, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"natural", "weather"}),
        low_frequency_ok=True,  # p=0.001/tick: sparse but not gated, borderline coverage
    )
)

register(
    EventSpec(
        kind="VOLCANIC_ERUPTION",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.0005,
        is_intervention=False,
        stat_deltas={"stability": -4.0},
        consequences=[
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=1.0, delay_min=0, delay_max=2, target="same"
            ),
            ConsequenceRule("GRAIN_LOSS", base_p=0.8, delay_min=2, delay_max=6, target="same"),
            ConsequenceRule(
                "POPULATION_LOSS", base_p=0.5, delay_min=0, delay_max=2, target="same"
            ),
        ],
        tags=frozenset({"natural", "disaster"}),
        low_frequency_ok=True,  # p=0.0005/tick, like METEOR
    )
)
