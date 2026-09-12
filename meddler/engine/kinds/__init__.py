"""Built-in event catalog. PROPOSAL §4.7 / §6.6 (full catalog as of M7.1).

Importing this package registers every built-in EventSpec (one submodule per
EventCategory, plus interventions) into engine.registry.EVENT_REGISTRY as a side effect,
then runs validate_all() so any registry inconsistency (a missing child_kind, a bad
probability, an inverted delay range) fails LOUDLY at import time.

HISTORY (M3.1 -> M7.1). M3.1 registered a core subset of 15 kinds + interventions + their
direct connective children, with onward edges into unregistered §6.6 kinds deliberately
TRIMMED so validate_all()'s closure check would pass without the full catalog -- notably
CAPITAL_FLIGHT's back-edge to CURRENCY_SLIDE, dropped specifically to keep the graph
acyclic (that edge is the only cycle in §6.6.2: CURRENCY_SLIDE -> INFLATION_CRISIS ->
UNREST/CAPITAL_FLIGHT -> CURRENCY_SLIDE), which made the M3.1-era clip_count == 0 by
construction. M7.1 registers the REST of §6.6 (every remaining natural/economic/
political/military/diplomatic/infrastructure/social kind, plus §6.7.4's infrastructure
interventions) and RESTORES every trimmed edge -- each call site notes where. The graph
is no longer acyclic: cascades can now genuinely reach MAX_DEPTH and clip. That's expected
and is the real input to M7.4's tuning pass (see docs/progress.md's "M7.1 landmines"),
not a regression.

Kinds gated behind a default-off setting (allow_nukes) or with genuinely sparse organic
triggers are flagged low_frequency_ok=True so the M7.1 coverage test (every kind fires at
least once across 20 seeds x 5000 ticks) doesn't require the unreachable.

Several §6.6 table entries describe a TEMPORARY per-country probability modifier (e.g.
COUP_RISK_UP "raises coup roll probability for 30 ticks", PRESS_SUPPRESSION "SCANDAL p
halved") or a fire-time STRUCTURAL choice (e.g. INFRASTRUCTURE_RESTORED's "target class").
Neither has an engine mechanism yet (no expiring-buff system, no per-firing parameter
passing beyond the static EventSpec model) -- each such kind is registered with a
declarative approximation and a comment at its call site, not hacked into an ad hoc
special case. This mirrors every prior milestone's documented-deferral pattern.
"""

from __future__ import annotations

from meddler.engine.kinds import (  # noqa: F401  (imported for registration side effects)
    commodities,
    diplomatic,
    economy,
    infrastructure,
    interventions,
    logistics,
    military,
    natural,
    politics,
    social,
)
from meddler.engine.registry import validate_all

validate_all()
