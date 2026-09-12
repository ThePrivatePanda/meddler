"""Infrastructure event kinds. PROPOSAL §6.6.5. EventCategory.INFRASTRUCTURE.

All kinds here fire from condition thresholds (§6.7.3, wired in InfrastructureSystem by
M7.2) or as ordinary cascade consequences; none are exogenous. M3.1 registered only
INFRASTRUCTURE_DAMAGE, with a stability-only declarative approximation (the real §6.7.2
effect -- a per-asset-class condition delta -- needed cascade.py's "infra_all" stat_deltas
key, added in M7.1/M7.2; see cascade.py's _apply_spec_effects). M7.1 adds the rest of the
table, now that engine/assets.py + the infra_all wiring exist to give them real effects.
"""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, register

register(
    EventSpec(
        kind="INFRASTRUCTURE_DAMAGE",
        severity=1,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "(condition delta on all asset classes, §6.7.2)" -- see cascade.py's
        # _apply_spec_effects for how "infra_all" routes through apply_infra_condition.
        stat_deltas={"infra_all": -0.08, "stability": -1.0},
        tags=frozenset({"infra"}),
    )
)

register(
    EventSpec(
        kind="INFRASTRUCTURE_RESTORED",
        severity=0,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "(positive condition delta on target class)" -- v1 applies it to all classes
        # uniformly rather than a fire-time-selected single class (no registered kind
        # needs single-class targeting yet, e.g. via payload; deferred, not hacked in).
        stat_deltas={"infra_all": 0.15},
        tags=frozenset({"infra"}),
        # Only parent is INTERVENE_INFRASTRUCTURE_BOOST (is_intervention=True), so this
        # can never fire organically -- same categorical reasoning as the is_intervention
        # exclusion in test_catalog_coverage.py, one hop removed.
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="SATELLITE_FAILURE",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "COMMS_BLACKOUT", base_p=0.8, delay_min=3, delay_max=8, target="same"
            ),
        ],
        tags=frozenset({"infra"}),
    )
)

register(
    EventSpec(
        kind="COMMS_BLACKOUT",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"press_freedom": -10.0},  # §6.7.1: comms condition x press_freedom
        consequences=[
            ConsequenceRule(
                "MEDIA_SUPPRESSION", base_p=0.9, delay_min=3, delay_max=10, target="same"
            ),
            ConsequenceRule(
                "COUP_RISK_UP", base_p=0.3, delay_min=5, delay_max=20, target="same"
            ),
        ],
        tags=frozenset({"infra", "politics"}),
    )
)

register(
    EventSpec(
        kind="MEDIA_SUPPRESSION",
        severity=1,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"press_freedom": -8.0},
        consequences=[
            ConsequenceRule("UNREST", base_p=0.5, delay_min=10, delay_max=35, target="same"),
        ],
        # "scandal exogenous p halved" for this country -- a per-country probability
        # modifier with no engine mechanism yet (same class of gap as PRESS_SUPPRESSION,
        # EDUCATION_DECLINE/REFORM; see politics.py). Deferred, documented, not hacked in.
        tags=frozenset({"infra", "politics"}),
    )
)

register(
    EventSpec(
        kind="NAVAL_LOSS",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "SUPPLY_DISRUPTION", base_p=0.7, delay_min=2, delay_max=6, target="same"
            ),
        ],
        tags=frozenset({"infra", "war"}),
    )
)

register(
    EventSpec(
        kind="PORT_CLOSURE",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("TRADE_HALT", base_p=0.9, delay_min=1, delay_max=3, target="same"),
        ],
        tags=frozenset({"infra", "economy"}),
    )
)

register(
    EventSpec(
        kind="SUPPLY_DISRUPTION",
        severity=1,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("PRICE_SPIKE", base_p=0.8, delay_min=2, delay_max=8, target="same"),
        ],
        tags=frozenset({"infra", "economy"}),
    )
)

register(
    EventSpec(
        kind="RAIL_COLLAPSE",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "GRAIN_TRANSPORT_FAILURE", base_p=0.9, delay_min=2, delay_max=6, target="same"
            ),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.5, delay_min=2, delay_max=5, target="same"),
        ],
        tags=frozenset({"infra"}),
    )
)

register(
    EventSpec(
        kind="GRAIN_TRANSPORT_FAILURE",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"grain_stock": -2.0},
        consequences=[
            ConsequenceRule(
                "FAMINE_WARNING", base_p=0.8, delay_min=3, delay_max=10, target="same"
            ),
        ],
        tags=frozenset({"infra", "economy"}),
    )
)

register(
    EventSpec(
        kind="POWER_OUTAGE",
        severity=2,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "INDUSTRY_SHUTDOWN", base_p=0.8, delay_min=1, delay_max=4, target="same"
            ),
            ConsequenceRule("UNREST", base_p=0.3, delay_min=10, delay_max=25, target="same"),
        ],
        tags=frozenset({"infra"}),
    )
)

register(
    EventSpec(
        kind="INDUSTRY_SHUTDOWN",
        severity=1,
        category=EventCategory.INFRASTRUCTURE,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "GDP_TICK_REDUCTION", base_p=0.9, delay_min=1, delay_max=3, target="same"
            ),
            ConsequenceRule(
                "UNEMPLOYMENT_SPIKE", base_p=0.5, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"infra", "economy"}),
    )
)
