"""Military event kinds. PROPOSAL §6.6.4. EventCategory.MILITARY.

DEVIATION (documented, deliberate, ongoing): the full catalog's exogenous root is
WAR_SPARK (p=0.002, "eligible only while some relation < -60"), spawning WAR_DECLARED.
Two things keep this from being the ONLY war trigger even after M7.1 registers WAR_SPARK:
(a) `Condition` (engine/registry.py) compares a COUNTRY stat, not a World.relations pair --
there is no clean way to express "some relation < -60" through it without a structural
extension, so WAR_SPARK is registered WITHOUT that gate (a documented simplification, not
an oversight); (b) organically, nothing pushes relations that negative in the first place
-- RelationsSystem only decays TOWARD 0, and no system worsens a relation absent an actual
war. So WAR_DECLARED keeps its M3.1 unconditional low-p exogenous roll (still needed for
war cadence) ALONGSIDE the new WAR_SPARK -> WAR_DECLARED (target="worst_relation") path,
which does at least aim any WAR_SPARK-triggered war at the most hostile available pair.

At_war_with/relations structural mutation for WAR_DECLARED/PEACE (so wars are actually
"live" -- occupation-eligible, visible in contract `wars`/`war` fields) is bundled into
M7.3 alongside conquest/absorption, since ANNEXATION's own accept criteria need a real war
to annex out of. See docs/progress.md.
"""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, register

# OCCUPATION_BEGIN (§6.8, §6.6.4): PoliticsSystem (M3.2) rolls this when stability==0 AND
# at war AND relation < -80. Structural effects (status=OCCUPIED, occupied_by) are handled
# by a structural.py hook registered in systems/politics.py, not here (this module is
# metadata/consequences only).
register(
    EventSpec(
        kind="OCCUPATION_BEGIN",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,  # primary = occupied, secondary = occupier
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -2.0},  # further collapse under occupation
        consequences=[
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=0.8, delay_min=5, delay_max=20, target="same"
            ),
            # M7.1 restores this edge (RESISTANCE_MOVEMENT wasn't registered in M3.1).
            ConsequenceRule(
                "RESISTANCE_MOVEMENT", base_p=0.5, delay_min=20, delay_max=60, target="same"
            ),
        ],
        tags=frozenset({"war", "military"}),
    )
)

register(
    EventSpec(
        kind="WAR_DECLARED",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=True,
        exogenous_base_p=0.002,
        is_intervention=False,
        stat_deltas={"stability": -3.0},
        consequences=[
            # "TREASURY_DRAIN p=1.0 (both)": fan out to primary (same) and secondary (foe).
            ConsequenceRule("TREASURY_DRAIN", base_p=1.0, delay_min=1, delay_max=4, target="same"),
            ConsequenceRule("TREASURY_DRAIN", base_p=1.0, delay_min=1, delay_max=4, target="foe"),
            ConsequenceRule(
                "REFUGEE_CRISIS", base_p=0.4, delay_min=10, delay_max=30, target="same"
            ),
        ],
        tags=frozenset({"war", "military"}),
    )
)

register(
    EventSpec(
        kind="STRIKE",
        severity=1,
        category=EventCategory.MILITARY,
        targets=2,  # primary = target, secondary = attacker
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # Dynamic distance-scaled attrition is applied by systems/war.py's structural
        # handler and payload-recorded for replay; static EventSpec deltas cannot express it.
        tags=frozenset({"war", "military"}),
    )
)

register(
    EventSpec(
        kind="WAR_SPARK",
        severity=1,
        category=EventCategory.MILITARY,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.002,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "WAR_DECLARED", base_p=0.85, delay_min=1, delay_max=4, target="worst_relation"
            ),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,  # no relation gate (module docstring); kept sparse by p alone
    )
)

register(
    EventSpec(
        kind="ANNEXATION",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,  # primary = annexed, secondary = annexer
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # Structural (country dissolved, stats merged into annexer) -- see M7.3's
        # structural.py handler in systems/politics.py. No declarative stat_deltas here:
        # the annexed country ceases to exist, so a per-country delta is meaningless.
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,  # rare terminal event, gated behind a long occupation
    )
)

register(
    EventSpec(
        kind="LIBERATION_WAR",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -2.0},  # "treats as WAR_DECLARED" -- same aggressor hit
        consequences=[
            ConsequenceRule("OCCUPATION_END", base_p=0.5, delay_min=5, delay_max=20, target="same"),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="OCCUPATION_END",
        severity=1,
        category=EventCategory.MILITARY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("STABILITY_UP", base_p=0.8, delay_min=5, delay_max=15, target="same"),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="NAVAL_BLOCKADE",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("PORT_CLOSURE", base_p=0.9, delay_min=1, delay_max=3, target="foe"),
            ConsequenceRule("PRICE_SPIKE", base_p=0.7, delay_min=3, delay_max=8, target="foe"),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="NAVAL_BATTLE",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("NAVAL_LOSS", base_p=0.6, delay_min=0, delay_max=2, target="same"),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.8, delay_min=1, delay_max=3, target="same"),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="ARMS_DEAL",
        severity=0,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "treasury transfer" -- no fixed amount specced; a real cross-country
        # pool_transfer needs both codes at fire time (§4.5), which PoolTransfer's
        # {primary}/{secondary} templating already supports, but no magnitude is given
        # anywhere in §6.6 -- left as a stat-free relation nudge only. See
        # docs/design-decisions.md.
        consequences=[
            ConsequenceRule("RELATION_SHIFT", base_p=0.5, delay_min=0, delay_max=1, target="same"),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="PROXY_WAR",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,  # primary = funder, secondary = funded side
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("TREASURY_DRAIN", base_p=1.0, delay_min=1, delay_max=4, target="same"),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="CEASEFIRE",
        severity=1,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "holds for a tick window; can break into WAR_DECLARED again" -- no explicit
        # p/d given for the break; modelled as a modest-probability relapse.
        consequences=[
            ConsequenceRule("WAR_DECLARED", base_p=0.2, delay_min=10, delay_max=40, target="foe"),
        ],
        tags=frozenset({"war", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="TREATY",
        severity=1,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("ALLIANCE", base_p=0.4, delay_min=5, delay_max=20, target="foe"),
            ConsequenceRule("TRADE_BOOST", base_p=0.5, delay_min=5, delay_max=15, target="foe"),
        ],
        tags=frozenset({"diplomatic", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="TRADE_BOOST",
        severity=0,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # "FX and GDP positive drift for both parties" -- primary only (declarative
        # stat_deltas apply to primary; the "foe" side would need a second EventSpec
        # firing or a fan-out target this milestone doesn't add, see module docstring).
        stat_deltas={"innovation_mult": 0.01, "exchange_rate": -0.005},
        tags=frozenset({"diplomatic", "military"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="RESISTANCE_MOVEMENT",
        severity=1,
        category=EventCategory.MILITARY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule("UNREST", base_p=0.6, delay_min=10, delay_max=30, target="same"),
            ConsequenceRule(
                "ASSASSINATION", base_p=0.2, delay_min=20, delay_max=60, target="same"
            ),
        ],
        tags=frozenset({"war", "military"}),
    )
)

register(
    EventSpec(
        kind="NUCLEAR_TEST",
        severity=2,
        category=EventCategory.MILITARY,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.0003,
        is_intervention=False,
        consequences=[
            # "(all countries: -20 each)": true world-wide fan-out has no target enum
            # value (module docstring's simplification note applies here too) -- a
            # single random other country takes the relation hit instead. Irrelevant to
            # default-settings cadence either way: allow_nukes defaults False.
            ConsequenceRule(
                "RELATION_SHIFT", base_p=1.0, delay_min=0, delay_max=1, target="random"
            ),
        ],
        tags=frozenset({"war", "military", "nuclear"}),
        low_frequency_ok=True,  # gated behind allow_nukes=False by default
    )
)

register(
    EventSpec(
        kind="NUCLEAR_STRIKE",
        severity=2,
        category=EventCategory.MILITARY,
        targets=2,
        is_exogenous=False,  # requires an active war to target; no organic root
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"population": -5.0, "stability": -20.0},
        consequences=[
            ConsequenceRule(
                "POPULATION_LOSS", base_p=1.0, delay_min=0, delay_max=1, target="foe"
            ),
            ConsequenceRule(
                "INFRASTRUCTURE_DAMAGE", base_p=1.0, delay_min=0, delay_max=1, target="foe"
            ),
        ],
        tags=frozenset({"war", "military", "nuclear"}),
        low_frequency_ok=True,  # gated behind allow_nukes=False by default; no firing path yet
    )
)
