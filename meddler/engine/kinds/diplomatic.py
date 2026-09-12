"""Diplomatic event kinds. PROPOSAL §6.6.4. EventCategory.DIPLOMATIC."""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, register

register(
    EventSpec(
        kind="PEACE",
        severity=1,
        category=EventCategory.DIPLOMATIC,
        targets=1,
        is_exogenous=False,  # arises via consequence/intervention/politics.py's war-
        # weariness roll (M7.4), not an unconditional organic root
        exogenous_base_p=0.0,
        is_intervention=False,
        # Clearing at_war_with is the _make_peace structural handler (systems/
        # politics.py), which fires for every PEACE event regardless of source
        # (god_peace, cascade consequence, or M7.4's organic war-weariness roll).
        stat_deltas={"stability": 2.0},
        consequences=[
            ConsequenceRule(
                "GDP_TICK_RESTORATION", base_p=0.6, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"diplomatic", "war"}),
    )
)

register(
    EventSpec(
        kind="REFUGEE_CRISIS",
        severity=2,
        category=EventCategory.DIPLOMATIC,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -2.0},
        consequences=[
            ConsequenceRule("UNREST", base_p=0.5, delay_min=10, delay_max=30, target="same"),
        ],
        tags=frozenset({"diplomatic", "social"}),
    )
)

register(
    EventSpec(
        kind="RELATION_SHIFT",
        severity=0,
        category=EventCategory.DIPLOMATIC,
        targets=2,
        is_exogenous=False,  # also emitted directly by systems/relations.py, §5.2
        exogenous_base_p=0.0,
        is_intervention=False,
        # "(relation delta / threshold-crossing record; no chain)" -- systems/
        # relations.py already applies the actual relation delta via apply_relation_delta
        # when it appends this directly; when reached AS A CONSEQUENCE (e.g. from
        # ALLIANCE/ALLIANCE_BROKEN/ARMS_DEAL/NUCLEAR_TEST below) it is a pure record with
        # no further stat_deltas of its own, matching the table's "no chain".
        tags=frozenset({"diplomatic"}),
    )
)

register(
    EventSpec(
        kind="ALLIANCE",
        severity=1,
        category=EventCategory.DIPLOMATIC,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # §5.2: "Alliance: relation set to +70 on ALLIANCE; decays from there." A direct
        # set (not a delta) needs a value it can compare against, which stat_deltas can't
        # express for World.relations -- left as a RELATION_SHIFT consequence instead,
        # matching the table's own "-> RELATION_SHIFT p=1.0" edge; the +70 magnitude
        # itself is intervention territory (INTERVENE_ALLIANCE, still declarative-only,
        # M5.1 deferred).
        consequences=[
            ConsequenceRule(
                "RELATION_SHIFT", base_p=1.0, delay_min=0, delay_max=1, target="foe"
            ),
        ],
        tags=frozenset({"diplomatic"}),
        # M11: ALLIANCE now fires organically from systems/diplomacy.py's bloc formation
        # (measured: 19/20 seeds over 5000 ticks), so it is no longer low-frequency-exempt.
    )
)

register(
    EventSpec(
        kind="ALLIANCE_BROKEN",
        severity=1,
        category=EventCategory.DIPLOMATIC,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "RELATION_SHIFT", base_p=1.0, delay_min=0, delay_max=1, target="foe"
            ),
        ],
        tags=frozenset({"diplomatic"}),
        # M11: ALLIANCE_BROKEN fires organically from systems/diplomacy.py's strain path
        # (measured: 19/20 seeds over 5000 ticks), so it is no longer low-frequency-exempt.
    )
)
