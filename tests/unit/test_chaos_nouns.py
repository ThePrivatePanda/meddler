"""Every kind INTERVENE_CHAOS can resolve into has a written noun phrase for its headline.

Chaos picks from all exogenous kinds, and the headline falls back to the bare kind name for
anything missing from `_CHAOS_NOUN`, so a newly exogenous kind reads "the dice come up
brain drain" until someone notices.
"""

from __future__ import annotations

from meddler.engine import cascade, tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.god import EVENT_REGISTRY
from meddler.text.headlines import _CHAOS_NOUN


def test_every_exogenous_kind_has_a_chaos_noun() -> None:
    exogenous = {kind for kind, spec in EVENT_REGISTRY.items() if spec.is_exogenous}
    assert exogenous
    assert sorted(exogenous - _CHAOS_NOUN.keys()) == []
