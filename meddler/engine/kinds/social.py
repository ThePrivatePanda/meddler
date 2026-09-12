"""Social event kinds. PROPOSAL §6.6.6. EventCategory.SOCIAL.

UNREST, FAMINE_WARNING, and FAMINE are also emitted directly by the M2 threshold bridge
(systems/thresholds.py); as with the economic crises, their consequences below fire only
when the kind is reached AS A CONSEQUENCE, not from a threshold crossing (bridging the two
is future work). None of these are exogenous roots.
"""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, register

register(
    EventSpec(
        kind="UNREST",
        severity=2,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,  # threshold bridge (stability < 35) + consequence, §6.3
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -3.0},
        consequences=[
            # §6.6.6 "p=varies"; fixed placeholders for M3.1.
            ConsequenceRule("COUP", base_p=0.15, delay_min=5, delay_max=20, target="same"),
            ConsequenceRule("CRACKDOWN", base_p=0.2, delay_min=3, delay_max=8, target="same"),
            ConsequenceRule(
                "CIVIL_WAR_RISK", base_p=0.1, delay_min=20, delay_max=60, target="same"
            ),
        ],
        tags=frozenset({"social"}),
    )
)

register(
    EventSpec(
        kind="FAMINE_WARNING",
        severity=2,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,  # threshold bridge (grain_stock < 10 days) + consequence, §6.3
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -2.0},
        consequences=[
            ConsequenceRule("UNREST", base_p=0.5, delay_min=10, delay_max=30, target="same"),
            ConsequenceRule(
                "POPULATION_LOSS", base_p=0.3, delay_min=15, delay_max=40, target="same"
            ),
        ],
        tags=frozenset({"social"}),
    )
)

register(
    EventSpec(
        kind="POPULATION_LOSS",
        severity=2,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"population": -0.3},
        tags=frozenset({"social"}),
    )
)

# --- M7.1 additions: FAMINE (also threshold-emitted, thresholds.py) + the rest of
# §6.6.6's table ---

register(
    EventSpec(
        kind="FAMINE",
        severity=2,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,  # threshold bridge (grain_stock == 0) + consequence, §6.3
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -5.0},
        consequences=[
            ConsequenceRule(
                "POPULATION_LOSS", base_p=1.0, delay_min=1, delay_max=5, target="same"
            ),
            ConsequenceRule("REVOLUTION", base_p=0.4, delay_min=10, delay_max=30, target="same"),
        ],
        tags=frozenset({"social"}),
    )
)

register(
    EventSpec(
        kind="STABILITY_UP",
        severity=0,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": 3.0},
        tags=frozenset({"social"}),
    )
)

register(
    EventSpec(
        kind="EDUCATION_DECLINE",
        severity=1,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"education": -5.0},
        consequences=[
            ConsequenceRule(
                "LABOUR_SHORTAGE", base_p=0.2, delay_min=60, delay_max=180, target="same"
            ),
        ],
        # "INNOVATION exogenous p reduced" -- deferred per-country probability-modifier
        # class (see politics.py's COUP_RISK_UP note); education already scales
        # INNOVATION per §6.2's own formula, so the direct stat hit above carries most
        # of the intended effect even without a separate modifier.
        tags=frozenset({"social"}),
    )
)

register(
    EventSpec(
        kind="EDUCATION_REFORM",
        severity=1,
        category=EventCategory.SOCIAL,
        targets=1,
        # No consequence parent fires this in the §6.6 catalog; wired as a small
        # exogenous root (same footing as SPORTS_VICTORY/INNOVATION) so it's actually
        # reachable rather than dead-registered. Found via M7.1's own coverage test.
        is_exogenous=True,
        exogenous_base_p=0.0025,
        is_intervention=False,
        stat_deltas={"education": 5.0},
        tags=frozenset({"social"}),
    )
)

register(
    EventSpec(
        kind="BRAIN_DRAIN",
        severity=1,
        category=EventCategory.SOCIAL,
        targets=1,
        # Same "no organic root" gap as EDUCATION_REFORM above; made exogenous rather
        # than low_frequency_ok since EDUCATION_DECLINE only reaches the catalog
        # through this kind's consequence.
        is_exogenous=True,
        exogenous_base_p=0.002,
        is_intervention=False,
        stat_deltas={"innovation_mult": -0.01},
        consequences=[
            ConsequenceRule(
                "EDUCATION_DECLINE", base_p=0.5, delay_min=20, delay_max=60, target="same"
            ),
            ConsequenceRule(
                "GDP_TICK_REDUCTION", base_p=0.4, delay_min=10, delay_max=30, target="same"
            ),
        ],
        tags=frozenset({"social", "economy"}),
    )
)

register(
    EventSpec(
        kind="CULTURAL_RENAISSANCE",
        severity=1,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": 1.0},
        consequences=[
            ConsequenceRule("STABILITY_UP", base_p=0.7, delay_min=5, delay_max=15, target="same"),
            ConsequenceRule(
                "IMMIGRATION_WAVE", base_p=0.3, delay_min=30, delay_max=80, target="same"
            ),
        ],
        tags=frozenset({"social"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="EPIDEMIC_FEAR",
        severity=1,
        category=EventCategory.SOCIAL,
        targets=1,
        # No consequence parent fires this; made exogenous like EDUCATION_REFORM above.
        is_exogenous=True,
        exogenous_base_p=0.002,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "GDP_TICK_REDUCTION", base_p=0.6, delay_min=3, delay_max=10, target="same"
            ),
        ],
        tags=frozenset({"social"}),
    )
)

register(
    EventSpec(
        kind="SPORTS_VICTORY",
        severity=0,
        category=EventCategory.SOCIAL,
        targets=1,
        is_exogenous=True,
        # No probability given anywhere in §6.6 (a pure-flavor kind); a small, sparse
        # roll keeps it a rare morale bump rather than a no-op registration.
        exogenous_base_p=0.0015,
        is_intervention=False,
        consequences=[
            ConsequenceRule("STABILITY_UP", base_p=0.5, delay_min=1, delay_max=3, target="same"),
        ],
        tags=frozenset({"social"}),
        low_frequency_ok=True,
    )
)
