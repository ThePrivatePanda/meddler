"""Economic event kinds. PROPOSAL §6.6.2. EventCategory.ECONOMY.

M3.1 registered a core subset plus connective children with several onward edges
TRIMMED (unregistered §6.6 targets) to keep the graph acyclic -- notably CAPITAL_FLIGHT's
back-edge to CURRENCY_SLIDE, which is the only cycle in the full §6.6.2 graph
(CURRENCY_SLIDE -> INFLATION_CRISIS -> UNREST/CAPITAL_FLIGHT -> CURRENCY_SLIDE). M7.1
registers the full table and RESTORES every trimmed edge (each call site below notes
where). Restoring the CAPITAL_FLIGHT cycle means cascades can now genuinely reach
MAX_DEPTH and clip -- expected, and the reason M7.4's tuning pass treats clip_count as a
real signal instead of a trivially-zero formality (docs/progress.md).

gdp_tick is recomputed every tick by ProductionSystem, so a raw gdp_tick stat_delta
would be overwritten immediately; persistent economic effects are modelled via
innovation_mult / stability / inflation instead. Amounts are v1 tuning placeholders.

DEBT_CRISIS and INFLATION_CRISIS are ALSO emitted directly by the M2 threshold bridge
(systems/thresholds.py), which appends them without going through this cascade path --
so their consequences below only fire when the kind is reached AS A CONSEQUENCE, not
from a threshold crossing. Bridging thresholds into the schedule queue is future work.
"""

from __future__ import annotations

from meddler.engine.model import EventCategory
from meddler.engine.registry import ConsequenceRule, EventSpec, PoolTransfer, register

register(
    EventSpec(
        kind="INNOVATION",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.003,
        is_intervention=False,
        stat_deltas={"innovation_mult": 0.05},
        consequences=[
            ConsequenceRule("GDP_BOOM", base_p=0.8, delay_min=6, delay_max=15, target="same"),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="GOLDEN_AGE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.0008,
        is_intervention=False,
        stat_deltas={"stability": 5.0, "innovation_mult": 0.03},
        consequences=[
            ConsequenceRule("GDP_BOOM", base_p=0.9, delay_min=10, delay_max=20, target="same"),
            ConsequenceRule(
                "IMMIGRATION_WAVE", base_p=0.4, delay_min=30, delay_max=80, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="DEBT_CRISIS",
        severity=2,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,  # emitted by threshold bridge (treasury < 0), §6.3
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -3.0},
        consequences=[
            ConsequenceRule("MINT", base_p=0.8, delay_min=2, delay_max=5, target="same"),
            ConsequenceRule("CREDIT_FREEZE", base_p=0.5, delay_min=5, delay_max=15, target="same"),
        ],
        tags=frozenset({"economy"}),
        # M7.4 finding, not a wiring gap: starting treasury is gdp_tick *
        # STARTING_TREASURY_TICKS (billions in practice) while the only drain,
        # TREASURY_DRAIN, burns a flat 500_000/event -- confirmed via a 20-seed x
        # 5000-tick sample that treasury never gets within orders of magnitude of 0
        # even with 20+ wars firing per seed. Genuinely unreachable at current fiscal
        # scale, not merely rare; fixing it means rebalancing income/expense
        # magnitudes across the whole economy, out of scope here. See docs/progress.md.
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="INFLATION_CRISIS",
        severity=2,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,  # emitted by threshold bridge (inflation > 8%), §6.3
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -2.0},
        consequences=[
            ConsequenceRule("UNREST", base_p=0.7, delay_min=10, delay_max=30, target="same"),
            ConsequenceRule(
                "CAPITAL_FLIGHT", base_p=0.3, delay_min=15, delay_max=40, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="PRICE_SPIKE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"inflation": 0.5},
        consequences=[
            ConsequenceRule(
                "CURRENCY_SLIDE", base_p=0.75, delay_min=3, delay_max=10, target="same"
            ),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.5, delay_min=2, delay_max=5, target="same"),
            ConsequenceRule(
                "FAMINE_WARNING", base_p=0.3, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="CURRENCY_SLIDE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"exchange_rate": 0.02, "inflation": 0.3},
        consequences=[
            ConsequenceRule(
                "INFLATION_CRISIS", base_p=0.6, delay_min=5, delay_max=15, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="TREASURY_DRAIN",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        pool_transfers=[
            PoolTransfer(
                kind="burn", src_pool="{primary}.treasury", dst_pool=None,
                amount=500_000, currency="{primary}",
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="MINT",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"inflation": 0.2},
        pool_transfers=[
            PoolTransfer(
                kind="mint", src_pool=None, dst_pool="{primary}.treasury",
                amount=1_000_000, currency="{primary}",
            ),
        ],
        consequences=[
            ConsequenceRule("CURRENCY_SLIDE", base_p=0.9, delay_min=3, delay_max=8, target="same"),
        ],
        tags=frozenset({"economy"}),
        # Only parent is DEBT_CRISIS, which is itself low_frequency_ok (see its own
        # comment) -- inherits the same unreachability, not a separate gap.
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="CREDIT_FREEZE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -1.0},
        consequences=[
            # M7.1 restores the two edges M3.1 trimmed (RECESSION/COMPANY_COLLAPSE
            # weren't registered yet).
            ConsequenceRule("RECESSION", base_p=0.5, delay_min=10, delay_max=30, target="same"),
            ConsequenceRule(
                "COMPANY_COLLAPSE", base_p=0.4, delay_min=15, delay_max=40, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="CAPITAL_FLIGHT",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"exchange_rate": 0.03},
        consequences=[
            # M7.1 restores §6.6.2's CURRENCY_SLIDE edge, deliberately dropped by M3.1
            # to keep the graph acyclic (CURRENCY_SLIDE -> INFLATION_CRISIS -> UNREST/
            # CAPITAL_FLIGHT -> CURRENCY_SLIDE is a real cycle). Restoring it is correct
            # per the full catalog; cascades can now legitimately hit MAX_DEPTH and clip
            # -- that's the M7.4 tuning signal flagged in review, not a bug. See
            # docs/progress.md's M7.1 landmines.
            ConsequenceRule(
                "CURRENCY_SLIDE", base_p=0.8, delay_min=2, delay_max=8, target="same"
            ),
            ConsequenceRule("CREDIT_FREEZE", base_p=0.4, delay_min=5, delay_max=15, target="same"),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="GDP_BOOM",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"innovation_mult": 0.02},
        consequences=[
            ConsequenceRule(
                "IMMIGRATION_WAVE", base_p=0.2, delay_min=20, delay_max=60, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="IMMIGRATION_WAVE",
        severity=0,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"population": 0.1},
        tags=frozenset({"economy", "social"}),
    )
)

register(
    EventSpec(
        kind="LABOUR_SHORTAGE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"innovation_mult": -0.01},
        consequences=[
            # M7.1 restores both edges M3.1 trimmed.
            ConsequenceRule(
                "GDP_TICK_REDUCTION", base_p=0.8, delay_min=3, delay_max=10, target="same"
            ),
            ConsequenceRule(
                "IMMIGRATION_INCENTIVE", base_p=0.3, delay_min=20, delay_max=60, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="GDP_TICK_RESTORATION",
        severity=0,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # §6.6.2: "removes a prior reduction". M3.1 tracks no structural reduction ledger,
        # so this is modelled as a small stability recovery. See docs/design-decisions.md.
        stat_deltas={"stability": 1.0},
        tags=frozenset({"economy"}),
    )
)

# --- M7.1 additions: the rest of §6.6.2's table ---

register(
    EventSpec(
        kind="BOOM_BUST",
        severity=2,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=True,
        exogenous_base_p=0.001,
        is_intervention=False,
        stat_deltas={"innovation_mult": 0.03},  # the "boom" half; RECESSION is the bust
        consequences=[
            ConsequenceRule("RECESSION", base_p=0.9, delay_min=20, delay_max=60, target="same"),
        ],
        tags=frozenset({"economy"}),
        low_frequency_ok=True,  # p=0.001/tick
    )
)

register(
    EventSpec(
        kind="GDP_TICK_REDUCTION",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # gdp_tick itself is recomputed every tick by ProductionSystem (module docstring)
        # -- modelled as a persistent innovation_mult hit, symmetric with GDP_TICK_
        # RESTORATION's stability nudge and GDP_BOOM's innovation_mult bump.
        stat_deltas={"innovation_mult": -0.02},
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="RECESSION",
        severity=2,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"innovation_mult": -0.03, "stability": -2.0},
        consequences=[
            ConsequenceRule(
                "UNEMPLOYMENT_SPIKE", base_p=0.8, delay_min=5, delay_max=20, target="same"
            ),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.6, delay_min=3, delay_max=10, target="same"),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="COMPANY_COLLAPSE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"innovation_mult": -0.01},
        consequences=[
            ConsequenceRule(
                "UNEMPLOYMENT_SPIKE", base_p=0.9, delay_min=2, delay_max=8, target="same"
            ),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.3, delay_min=3, delay_max=10, target="same"),
        ],
        tags=frozenset({"economy"}),
        # Sole parent CREDIT_FREEZE is itself 4 cascade levels deep (PRICE_SPIKE/
        # DROUGHT -> CURRENCY_SLIDE -> INFLATION_CRISIS -> CAPITAL_FLIGHT ->
        # CREDIT_FREEZE -> here); confirmed via a 20-seed x 5000-tick run that
        # CREDIT_FREEZE itself does fire but the chain rarely completes its final hop
        # inside the run window. Genuinely rare by depth, not unwired.
        low_frequency_ok=True,
    )
)

register(
    EventSpec(
        kind="UNEMPLOYMENT_SPIKE",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"stability": -1.5},
        consequences=[
            ConsequenceRule("UNREST", base_p=0.4, delay_min=15, delay_max=35, target="same"),
        ],
        tags=frozenset({"economy", "social"}),
    )
)

register(
    EventSpec(
        kind="GRAIN_LOSS",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"grain_stock": -4.0},
        consequences=[
            ConsequenceRule("PRICE_SPIKE", base_p=0.8, delay_min=2, delay_max=8, target="same"),
            ConsequenceRule(
                "FAMINE_WARNING", base_p=0.4, delay_min=8, delay_max=20, target="same"
            ),
        ],
        tags=frozenset({"economy", "natural"}),
    )
)

register(
    EventSpec(
        kind="TRADE_HALT",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        stat_deltas={"exchange_rate": 0.02},
        consequences=[
            ConsequenceRule("PRICE_SPIKE", base_p=0.7, delay_min=3, delay_max=8, target="same"),
            ConsequenceRule("TREASURY_DRAIN", base_p=0.5, delay_min=2, delay_max=6, target="same"),
        ],
        tags=frozenset({"economy"}),
    )
)

register(
    EventSpec(
        kind="EMBARGO",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # No organic root fires EMBARGO in v1 (§6.6.2 lists no exogenous/consequence
        # source for it); reachable only via INTERVENE_EMBARGO's future structural
        # wiring (M5.1 deferred, docs/progress.md). Registered now so the catalog and
        # its "(target country)" consequences below are complete and validate_all()-clean.
        # low_frequency_ok is categorical here, not statistical: this kind cannot fire
        # organically at all pending that deferred intervention wiring.
        low_frequency_ok=True,
        consequences=[
            # "(target country)": the embargoed side is country2 on a two-target parent
            # -- cascade.py's "foe" resolution returns exactly that. Both onward edges
            # apply to the TARGET, not the embargoing country.
            ConsequenceRule("TRADE_HALT", base_p=0.9, delay_min=1, delay_max=3, target="foe"),
            ConsequenceRule(
                "CURRENCY_SLIDE", base_p=0.5, delay_min=5, delay_max=12, target="foe"
            ),
        ],
        tags=frozenset({"economy", "diplomatic"}),
    )
)

register(
    EventSpec(
        kind="TARIFF_IMPOSED",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=2,  # primary importer imposes duty on secondary exporter
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        tags=frozenset({"economy", "diplomatic", "trade", "policy"}),
    )
)

register(
    EventSpec(
        kind="TARIFF_REPEALED",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=2,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        tags=frozenset({"economy", "diplomatic", "trade", "policy"}),
    )
)

register(
    EventSpec(
        kind="IMMIGRATION_INCENTIVE",
        severity=0,
        category=EventCategory.ECONOMY,
        targets=1,
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        consequences=[
            ConsequenceRule(
                "IMMIGRATION_WAVE", base_p=0.5, delay_min=30, delay_max=90, target="same"
            ),
        ],
        tags=frozenset({"economy"}),
    )
)
