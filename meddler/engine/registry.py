"""Global event-kind registry and its declarative types. PROPOSAL §4.7.

M3.1 replaces the M1 `Any`-typed skeleton with the real frozen dataclasses
(`EventSpec`, `ConsequenceRule`, `Condition`, `PoolTransfer`) transcribed from §4.7,
and fills in `validate_all()`'s cross-checks (every referenced `child_kind` is
registered, every probability is in [0, 1], every delay range is sane).

`PoolTransfer` has no literal body in §4.7 (it appears only as a type annotation on
`EventSpec.pool_transfers`); its shape here is a documented M3.1 design choice -- a
country-templated ledger movement, resolved to a concrete LedgerEntry at fire time.
See docs/design-decisions.md.

Specs are registered at import time from engine/kinds/*.py. This module must NOT import
engine.kinds (kinds -> registry only), or import would cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from meddler.engine.model import EventCategory, WorldSettings

# --- Declarative types (PROPOSAL §4.7, verbatim field names/types/literals) ---


@dataclass(frozen=True)
class Condition:
    """A precondition on world state. Evaluated at schedule time (exogenous rolls) and
    again at fire time (consequences); a fire-time failure drops the event silently
    without consuming a queue slot (§4.7)."""

    stat: str  # e.g. "stability", "treasury", "asset.satellites.count"
    op: Literal["<", ">", "<=", ">=", "==", "!="]
    value: float
    target: Literal["primary", "secondary", "any", "all"]


# World rules (§6.9, WorldSettings) that switch a whole KIND off. `allow_nukes` gates a
# tagged FAMILY and lives in systems/exogenous.py's tag check; these rules each name ONE
# outcome ("can a country split?", "can a country be taken?"), so a kind map states what
# they mean without inventing a tag per rule. Consulted by every path that can produce an
# event: the consequence scheduler, the exogenous roll, and god mode.
WORLD_RULE_GATES: dict[str, str] = {
    "SECESSION": "allow_secession",
    "INTERVENE_SECEDE": "allow_secession",
    "OCCUPATION_BEGIN": "allow_conquest",
    "ANNEXATION": "allow_conquest",
}


def world_rule_allows(kind: str, settings: WorldSettings) -> bool:
    """False when a world rule the viewer switched off forbids this kind outright."""
    rule = WORLD_RULE_GATES.get(kind)
    return rule is None or bool(getattr(settings, rule))


@dataclass(frozen=True)
class PoolTransfer:
    """One money movement a spec causes, as a country-templated ledger entry (§4.5/§4.7).

    kind mirrors LedgerEntry: "transfer" (src+dst), "mint" (dst only), "burn" (src only).
    Pool names and currency are templates: the tokens "{primary}" and "{secondary}" are
    substituted with the firing event's country codes at fire time, e.g.
    src_pool="{primary}.treasury", currency="{primary}". amount is positive minor units.

    `gdp_ticks`, when set, replaces the fixed amount with that many ticks of the primary
    country's current output (`gdp_tick`, itself in minor units). Fixed amounts are the
    same for a country with a 10bn treasury and one with 1bn; a scaled amount means the
    same thing everywhere.
    """

    kind: Literal["transfer", "mint", "burn"]
    src_pool: str | None
    dst_pool: str | None
    amount: int
    currency: str
    gdp_ticks: float | None = None


@dataclass(frozen=True)
class ConsequenceRule:
    """One authored cause->effect edge on an EventSpec (§4.7). base_p is pre-decay
    (§6.4.1); delay is drawn uniformly in [delay_min, delay_max] (§6.4.2)."""

    child_kind: str
    base_p: float  # before cascade decay (§6.4.1)
    delay_min: int  # ticks (§6.4.2)
    delay_max: int
    # "worst_relation" is an M7.1 addition beyond §4.7's literal listing: WAR_SPARK's
    # own table entry ("against the worst-relation eligible pair", §6.6.4) has no other
    # way to be expressed -- none of the other four literals can select "the specific
    # country primary's relation is most hostile toward". See cascade.py's
    # _resolve_target for the resolution (deterministic, no RNG: sorts by relation value).
    target: Literal["same", "foe", "ally", "random", "all_at_war", "worst_relation"]
    payload_template: dict[str, float | int | str] = field(default_factory=dict)
    conditions: list[Condition] = field(default_factory=list)


@dataclass(frozen=True)
class EventSpec:
    """Declarative descriptor for one event kind -- the unit of extension (§4.7)."""

    kind: str  # unique string key, e.g. "DROUGHT"
    severity: int  # 0 info / 1 notable / 2 crisis
    category: EventCategory  # enum grouping for UI filtering (one per kinds/ file)
    targets: int  # 1 = one country; 2 = ordered (primary, secondary) pair
    is_exogenous: bool  # can fire as a root event with no parent
    exogenous_base_p: float  # per-tick probability if is_exogenous (0 if not)
    is_intervention: bool  # god-mode kind; never rolls organically
    stat_deltas: dict[str, float] = field(default_factory=dict)  # Country field -> delta
    pool_transfers: list[PoolTransfer] = field(default_factory=list)
    consequences: list[ConsequenceRule] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    tags: frozenset[str] = frozenset()
    low_frequency_ok: bool = False  # exempt from the M7.1 statistical-coverage test


# --- Registry storage ---

EVENT_REGISTRY: dict[str, EventSpec] = {}


def register(spec: EventSpec) -> None:
    """Add a spec; raise if the kind key is already registered."""
    if spec.kind in EVENT_REGISTRY:
        raise ValueError(f"event kind already registered: {spec.kind}")
    EVENT_REGISTRY[spec.kind] = spec


def _check_prob(p: float, where: str) -> None:
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"probability out of [0,1]: {p} ({where})")


def validate_all() -> None:
    """Cross-check registry consistency (PROPOSAL §4.7):

    - registry key matches spec.kind (bookkeeping invariant);
    - every child_kind referenced by any consequence is itself registered (closure);
    - every probability (exogenous_base_p, each rule's base_p) is in [0, 1];
    - every consequence delay range is sane (0 <= delay_min <= delay_max).
    """
    for kind, spec in EVENT_REGISTRY.items():
        if spec.kind != kind:
            raise ValueError(f"registry key {kind!r} does not match spec.kind {spec.kind!r}")
        _check_prob(spec.exogenous_base_p, f"{kind}.exogenous_base_p")
        for rule in spec.consequences:
            if rule.child_kind not in EVENT_REGISTRY:
                raise ValueError(
                    f"{kind} references unregistered child_kind {rule.child_kind!r}"
                )
            _check_prob(rule.base_p, f"{kind}->{rule.child_kind}.base_p")
            if not (0 <= rule.delay_min <= rule.delay_max):
                raise ValueError(
                    f"bad delay range for {kind}->{rule.child_kind}: "
                    f"[{rule.delay_min}, {rule.delay_max}]"
                )
