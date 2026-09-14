"""Political event kinds. PROPOSAL §6.6.3 (core subset + connective children).
EventCategory.POLITICS.

SCANDAL is the only exogenous root here. ELECTION is calendar-driven (election_due_tick),
fired by PoliticsSystem in M3.2 -- registered with is_exogenous=False so it never rolls
organically this milestone. Several effects here exceed the declarative EventSpec model
and are marked TODO for M3.2/M5.1 (structural leader replacement, country creation).
"""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import Condition, ConsequenceRule, EventSpec, register

register(
    EventSpec(
        kind="SCANDAL",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.004,
        is_intervention=False,
        stat_deltas={"stability": -2.0},
        consequences=[
            ConsequenceRule("STABILITY_DROP", base_p=0.7, delay_min=2, delay_max=8, target="same"),
            ConsequenceRule(
                "ELECTION_UPSET", base_p=0.3, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="ELECTION",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,  # calendar-driven via election_due_tick (PoliticsSystem, M3.2)
        exogenous_base_p=0.0,
        is_intervention=False,
        # M3.1 registered a fixed-p=0.5 LEADER_CHANGE consequence here as a placeholder.
        # M3.2's PoliticsSystem replaces it: it decides the incumbent-changes outcome itself
        # (§6.6.3's literal "p=varies (incumbent traits, stability)" -- not expressible as a
        # single ConsequenceRule.base_p) and, on change, fires LEADER_CHANGE immediately as
        # ELECTION's direct child rather than a delayed generic consequence. No consequences
        # list here; see engine/systems/politics.py.
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="SECESSION",
        severity=2,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # TODO(M3.2/M5.1): SECESSION "creates a new country" and splits population between
        # parent and breakaway -- structural World.countries work outside the EventSpec model
        # (§5.1 dynamic country registry). Only the declarative stability hit is applied here;
        # country creation is deliberately NOT hacked in. See task scope note.
        stat_deltas={"stability": -5.0},
        # Fire-time gate (§6.3: SECESSION follows CIVIL_WAR_RISK only if stability < 20).
        conditions=[Condition(stat="stability", op="<", value=20.0, target="primary")],
        tags=frozenset({"politics", "war"}),
    )
)

register(
    EventSpec(
        kind="STABILITY_DROP",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -4.0},
        # May trip the threshold bridge's UNREST crossing next tick (§6.3); no direct edge.
        tags=frozenset({"politics", "social"}),
    )
)

register(
    EventSpec(
        kind="ELECTION_UPSET",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("LEADER_CHANGE", base_p=0.9, delay_min=1, delay_max=2, target="same"),
        ],
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="LEADER_CHANGE",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # Leader replacement + trait re-bias is handled structurally by
        # systems/politics.py's LEADER_CHANGE handler (M3.2), not here.
        stat_deltas={"stability": -1.0},
        consequences=[
            # M7.1 restores this edge (POLICY_SHIFT wasn't registered in M3.1).
            ConsequenceRule("POLICY_SHIFT", base_p=0.8, delay_min=1, delay_max=5, target="same"),
        ],
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="COUP",
        severity=2,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -6.0},
        consequences=[
            ConsequenceRule("LEADER_CHANGE", base_p=1.0, delay_min=1, delay_max=2, target="same"),
            ConsequenceRule("CRACKDOWN", base_p=0.6, delay_min=1, delay_max=4, target="same"),
        ],
        tags=frozenset({"politics", "war"}),
    )
)

register(
    EventSpec(
        kind="CRACKDOWN",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"civil_rights": -5.0, "press_freedom": -5.0},
        consequences=[
            ConsequenceRule("STABILITY_DROP", base_p=0.3, delay_min=5, delay_max=15, target="same"),
            # M7.1 restores this edge (PRESS_SUPPRESSION wasn't registered in M3.1).
            ConsequenceRule(
                "PRESS_SUPPRESSION", base_p=0.4, delay_min=2, delay_max=8, target="same"
            ),
        ],
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="CIVIL_WAR_RISK",
        severity=2,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,  # also emitted by threshold bridge (stability < 15), §6.3
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -3.0},
        consequences=[
            # SECESSION's own fire-time condition (stability < 20) gates whether it lands.
            ConsequenceRule("SECESSION", base_p=0.5, delay_min=20, delay_max=60, target="same"),
            # Extends PROPOSAL §6.6.3, whose only REVOLUTION parent is FAMINE. That made
            # revolution a food event: a world whose trade relief works never reaches
            # grain_stock 0 (relief engages at 15 days of need, above both famine lines),
            # so political collapse -- stability under 15 -- could split a country or
            # topple a ruler but never overturn the order. Threshold-emitted, this fires at
            # depth 1, so the spawn chance is 0.3 x cascade_decay.
            #
            # Fire-time gate on this edge only: a country that calmed back out of unrest
            # during the delay does not overturn its order. Set at the UNREST line rather
            # than SECESSION's 20, and kept off the REVOLUTION spec so the FAMINE edge stays
            # ungated as §6.6.3 has it (a starving nation revolts wherever stability sits;
            # seed 7's TAB sat at 27.5 for over 1,000 ticks with no food). Also makes the
            # temperament reset (systems/politics.py) on this path a move from collapse.
            ConsequenceRule(
                "REVOLUTION",
                base_p=0.3,
                delay_min=10,
                delay_max=40,
                target="same",
                fire_conditions=[Condition(stat="stability", op="<", value=35.0, target="primary")],
            ),
        ],
        tags=frozenset({"politics", "war"}),
    )
)

# --- M7.1 additions: the rest of §6.6.3's table ---

register(
    EventSpec(
        kind="POLICY_SHIFT",
        severity=0,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "(biases future system rolls)" -- no per-country probability-modifier
        # mechanism exists yet (same deferred class as COUP_RISK_UP/PRESS_SUPPRESSION/
        # EDUCATION_DECLINE/REFORM); a small stability nudge stands in for now.
        stat_deltas={"stability": 0.5},
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="COUP_RISK_UP",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "(raises PoliticsSystem coup roll probability for 30 ticks)" -- temporary
        # per-country probability modifiers need state PoliticsSystem doesn't track yet
        # (a expiring-buff mechanism, not just a Country field). Declarative
        # approximation: a direct, immediate, smaller stability hit instead of a
        # elevated ongoing coup risk. Deferred, documented, not hacked in.
        stat_deltas={"stability": -1.0},
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="REVOLUTION",
        severity=2,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -8.0},
        consequences=[
            ConsequenceRule("LEADER_CHANGE", base_p=1.0, delay_min=0, delay_max=2, target="same"),
            ConsequenceRule(
                "GOVT_TYPE_CHANGE", base_p=0.6, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"politics", "social"}),
    )
)

register(
    EventSpec(
        kind="GOVT_TYPE_CHANGE",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # Actually swapping Country.govt_type (and its leader-title implications,
        # worldgen._LEADER_TITLES) is structural, in the same family as LEADER_CHANGE's
        # own structural handler -- not wired this milestone (no organic path drives
        # REVOLUTION often enough for it to matter yet); declarative stability
        # settling-in effect stands in for now.
        stat_deltas={"stability": 1.0},
        consequences=[
            ConsequenceRule("POLICY_SHIFT", base_p=1.0, delay_min=1, delay_max=3, target="same"),
        ],
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="REFERENDUM",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "outcome determines POLICY_SHIFT or SECESSION" -- outcome selection needs a
        # runtime branch the declarative ConsequenceRule model expresses as two
        # independent rolls rather than a true either/or; both edges registered, each
        # with its own probability, rather than picking one path in code.
        consequences=[
            ConsequenceRule("POLICY_SHIFT", base_p=0.6, delay_min=1, delay_max=5, target="same"),
            ConsequenceRule(
                "SECESSION", base_p=0.15, delay_min=5, delay_max=20, target="same"
            ),
        ],
        tags=frozenset({"politics"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="PROPAGANDA_CAMPAIGN",
        severity=0,
        category=EventCategory.POLITICS,
        targets=1,
        # No consequence parent fires this; made exogenous (like SPORTS_VICTORY) so
        # it's actually reachable. Found via M7.1's own coverage test.
        is_exogenous=True,
        exogenous_base_p=0.0025,
        is_intervention=False,
        # "STABILITY_DROP prevented for 30 ticks" -- same deferred temporary-modifier
        # class as COUP_RISK_UP/POLICY_SHIFT; a one-shot stability nudge stands in.
        stat_deltas={"stability": 1.0},
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="ASSASSINATION",
        severity=2,
        category=EventCategory.POLITICS,
        targets=1,
        # Its only parent, RESISTANCE_MOVEMENT, is a depth-1 child of the rare
        # OCCUPATION_BEGIN, so the 0.2 edge lands at 0.14 and fired well under once per
        # 5000-tick seed. Also a small-p exogenous root, like PROPAGANDA_CAMPAIGN, so it is
        # actually reachable. Measured over 3 seeds x 2000 ticks: 12 against 25 coups at drama
        # 1.0, 3 against 26 at the shipped 0.4. Found via the catalog coverage test.
        is_exogenous=True,
        exogenous_base_p=0.0015,
        is_intervention=False,
        # Only in an unstable country: some country is below this on most ticks, so the gate
        # makes the target plausible and the p keeps it sparse. Re-checked at fire time for
        # the resistance edge too, the same gate SECESSION uses.
        conditions=[Condition(stat="stability", op="<", value=20.0, target="primary")],
        stat_deltas={"stability": -4.0},
        consequences=[
            ConsequenceRule("LEADER_CHANGE", base_p=1.0, delay_min=1, delay_max=2, target="same"),
            ConsequenceRule("UNREST", base_p=0.8, delay_min=3, delay_max=10, target="same"),
        ],
        tags=frozenset({"politics", "war"}),
    )
)

register(
    EventSpec(
        kind="PRESS_SUPPRESSION",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"press_freedom": -6.0},
        # "SCANDAL exogenous p halved for this country" -- deferred per-country
        # probability-modifier class, see COUP_RISK_UP's note above.
        tags=frozenset({"politics"}),
    )
)

register(
    EventSpec(
        kind="CIVIL_RIGHTS_REFORM",
        severity=1,
        category=EventCategory.POLITICS,
        targets=1,
        # No consequence parent fires this; made exogenous so it's actually reachable.
        # Found via M7.1's own coverage test.
        is_exogenous=True,
        exogenous_base_p=0.002,
        is_intervention=False,
        stat_deltas={"civil_rights": 6.0},
        consequences=[
            ConsequenceRule("STABILITY_UP", base_p=0.7, delay_min=5, delay_max=15, target="same"),
            ConsequenceRule("GDP_BOOM", base_p=0.2, delay_min=20, delay_max=50, target="same"),
        ],
        tags=frozenset({"politics"}),
    )
)
