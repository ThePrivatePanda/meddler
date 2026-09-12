"""Logistics event kinds (M13, v2 spec §5). EventCategory.ECONOMY.

SHIPMENT_LOST is the only registered kind here: it carries the destination's stability
hit and removes the convoy. It is not what the feed prints, though -- one line per sinking
reads as a shipping manifest, so systems/logistics.py folds the sinkings into a periodic
per-destination CONVOY_LOSSES report and that is the visible kind. CONVOY_LOSSES has no
spec effects and no consequences of its own, so like FAMINE and RELATION_SHIFT it is
appended directly rather than registered here.

The high-frequency bookkeeping lifecycle events SHIPMENT_DISPATCHED and SHIPMENT_ARRIVED
are likewise emitted directly by systems/logistics.py, with their own structural handlers.

SHIPMENT_LOST is reached only via LogisticsSystem's interdiction roll on a sea shipment in
a war zone -- there is no exogenous root and no consequence parent, so it is flagged
low_frequency_ok (it needs an active war AND a sea lane AND the roll, sparse by design).
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.model import EventCategory
from meddler.engine.registry import EventSpec, register

register(
    EventSpec(
        kind="SHIPMENT_LOST",
        severity=1,
        category=EventCategory.ECONOMY,
        targets=1,  # primary = the importer (dest) who loses the expected goods
        is_exogenous=False,
        exogenous_base_p=0.0,
        is_intervention=False,
        # The structural handler (systems/logistics.py) removes the shipment; the economic
        # loss (the importer's already-paid money, stranded in the fx desk) is implicit. The
        # declarative bite is the destination's stability -- expected relief that never came.
        stat_deltas={"stability": config.SHIPMENT_LOST_STABILITY_HIT},
        tags=frozenset({"economy", "war"}),
        low_frequency_ok=True,
    )
)
