"""God-mode intervention kinds. PROPOSAL §4.7 ("Interventions are EventSpecs too").

is_intervention=True, is_exogenous=False, exogenous_base_p=0 -- they never roll
organically; the god-mode palette (M5.1) is generated from these specs at runtime. The
kinds, target counts, and ordering mirror the shipped frontend prototype exactly
(web/engine.js INTERVENTIONS, ~lines 58-71) so the M6 bridge lines up 1:1. Category is
assigned by effect (the §6.6 catalog does not group interventions).

Specs here carry the declarative part (stat_deltas, pool_transfers, consequences). Kinds
whose effect is structural -- war, peace, alliance, embargo -- run the organic kinds' shared
structural and replay handlers, registered in systems/politics.py; god.intervene is the
invocation path.

INTERVENE_CHAOS is documented to resolve, at god-mode invocation time (M5.1), to a random
registered *exogenous* spec which is then fired as a root event -- e.g.
`random_exogenous = rng.choice(sorted(k for k,s in EVENT_REGISTRY.items() if s.is_exogenous))`.
It is registered here as metadata only; no organic firing path exists this milestone.
"""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, PoolTransfer, register

register(
    EventSpec(
        kind="INTERVENE_DROUGHT",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"grain_stock": -5.0},
        consequences=[
            ConsequenceRule("PRICE_SPIKE", base_p=0.95, delay_min=2, delay_max=8, target="same"),
        ],
        tags=frozenset({"natural", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_ASSASSINATE",
        severity=2,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"stability": -5.0},
        consequences=[
            ConsequenceRule("LEADER_CHANGE", base_p=1.0, delay_min=1, delay_max=2, target="same"),
        ],
        tags=frozenset({"politics", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_MINT",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # Twenty ticks of output: roughly doubles a genesis treasury (seeded at
        # STARTING_TREASURY_TICKS = 20) and is ~half the money supply (treasury +
        # households + corporates = 40 ticks at genesis). InflationSystem only prices mints
        # made before it runs in a tick, and interventions land between ticks, so the price
        # effect is applied directly: 0.05pp per 1% of money supply minted
        # (INFLATION_PER_PCT_MINTED) x ~50% = +2.5pp. The old fixed 1,000,000 minor units
        # was about 0.01% of a typical treasury -- a mint nobody could see.
        stat_deltas={"inflation": 2.5},
        pool_transfers=[
            PoolTransfer(
                kind="mint", src_pool=None, dst_pool="{primary}.treasury",
                amount=0, currency="{primary}", gdp_ticks=20.0,
            ),
        ],
        consequences=[
            ConsequenceRule("CURRENCY_SLIDE", base_p=0.9, delay_min=3, delay_max=8, target="same"),
        ],
        tags=frozenset({"economy", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_TAXCUT",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"tax_rate": -0.03, "stability": 2.0},
        tags=frozenset({"economy", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_PLAGUE",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"health": -10.0, "population": -0.2},
        consequences=[
            ConsequenceRule(
                "LABOUR_SHORTAGE", base_p=0.7, delay_min=8, delay_max=25, target="same"
            ),
        ],
        tags=frozenset({"natural", "health", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_QUAKE",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"stability": -3.0},
        consequences=[
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=0.9, delay_min=0, delay_max=2, target="same"
            ),
        ],
        tags=frozenset({"natural", "disaster", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_METEOR",
        severity=2,
        category=EventCategory.NATURAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"stability": -5.0, "population": -0.1},
        consequences=[
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=1.0, delay_min=0, delay_max=1, target="same"
            ),
        ],
        tags=frozenset({"natural", "disaster", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_SECEDE",
        severity=2,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # The new country itself is created by systems/secession.py's structural handler;
        # the parent's stability hit is declarative.
        stat_deltas={"stability": -5.0},
        tags=frozenset({"politics", "war", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_GOLDEN",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"stability": 5.0, "innovation_mult": 0.03},
        consequences=[
            ConsequenceRule("GDP_BOOM", base_p=0.9, delay_min=10, delay_max=20, target="same"),
        ],
        tags=frozenset({"economy", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_PEACE",
        severity=1,
        category=EventCategory.DIPLOMATIC,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # Ends every war of the target through PEACE's shared structural handler.
        stat_deltas={"stability": 2.0},
        tags=frozenset({"diplomatic", "war", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_WAR",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # The war itself (at_war_with, relation -90) is WAR_DECLARED's shared structural
        # handler; the aggressor's stability hit is declarative.
        stat_deltas={"stability": -3.0},
        tags=frozenset({"war", "military", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_ALLIANCE",
        severity=1,
        category=EventCategory.DIPLOMATIC,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # Bloc formation and the +70 relation are ALLIANCE's shared structural handler.
        tags=frozenset({"diplomatic", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_EMBARGO",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # The blocked lane is EMBARGO's shared structural handler (trade consults it).
        tags=frozenset({"economy", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_INNOVATE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        stat_deltas={"innovation_mult": 0.05},
        consequences=[
            ConsequenceRule("GDP_BOOM", base_p=0.8, delay_min=6, delay_max=15, target="same"),
        ],
        tags=frozenset({"economy", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_CHAOS",
        severity=1,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # Resolves to a random registered exogenous root at invocation time (M5.1); see
        # module docstring. Metadata only this milestone.
        tags=frozenset({"intervention", "chaos"}),
    )
)

# --- M7.1 additions: §6.7.4's infrastructure-targeted interventions ---

register(
    EventSpec(
        kind="INTERVENE_DESTROY_SATELLITE",
        severity=1,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # "satellites.count -= 1": a structural handler in systems/infrastructure.py
        # removes one unit, then SATELLITE_FAILURE fires unconditionally as the table
        # specifies.
        consequences=[
            ConsequenceRule(
                "SATELLITE_FAILURE", base_p=1.0, delay_min=0, delay_max=1, target="same"
            ),
        ],
        tags=frozenset({"infra", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_NAVAL_BLOCKADE",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # "sets target naval routes to condition = 0.1": the blockaded (second) country's
        # naval_fleet is capped at config.INTERVENTION_DISABLED_CONDITION by a structural
        # handler, which cuts its sea freight through the M14 coupling; PORT_CLOSURE then
        # fires on the target unconditionally.
        consequences=[
            ConsequenceRule("PORT_CLOSURE", base_p=1.0, delay_min=0, delay_max=1, target="foe"),
        ],
        tags=frozenset({"infra", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_BLACKOUT",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # communications is capped at config.INTERVENTION_DISABLED_CONDITION by a structural
        # handler (systems/infrastructure.py), so coordination and routing degrade for real.
        consequences=[
            ConsequenceRule(
                "COMMS_BLACKOUT", base_p=1.0, delay_min=0, delay_max=1, target="same"
            ),
        ],
        tags=frozenset({"infra", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_INFRASTRUCTURE_BOOST",
        severity=1,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # Every asset class is repaired to at least config.INTERVENTION_RESTORED_CONDITION
        # by a structural handler, then INFRASTRUCTURE_RESTORED fires.
        consequences=[
            ConsequenceRule(
                "INFRASTRUCTURE_RESTORED", base_p=1.0, delay_min=0, delay_max=1, target="same"
            ),
        ],
        tags=frozenset({"infra", "intervention"}),
    )
)

register(
    EventSpec(
        kind="INTERVENE_POWER_GRID_FAILURE",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=True,
        # power_grid is capped at config.INTERVENTION_DISABLED_CONDITION by a structural
        # handler, which cuts grid-fed production through the M14 coupling.
        consequences=[
            ConsequenceRule("POWER_OUTAGE", base_p=1.0, delay_min=0, delay_max=1, target="same"),
        ],
        tags=frozenset({"infra", "intervention"}),
    )
)
