"""M4.1: engine/trace.py. PROPOSAL §3.3/§4.6."""

from __future__ import annotations

import pytest

from meddler.engine.events import EventLog
from meddler.engine.trace import trace


def _doom_spiral_log() -> tuple[EventLog, dict[str, int]]:
    """Mirrors the PROPOSAL §3.3 mock trace exactly:

        DROUGHT(d0)
          -> PRICE_SPIKE(d1)
               -> CURRENCY_SLIDE(d2) -> UNREST(d3)
               -> TREASURY_DRAIN(d2)   [sibling branch, not on UNREST's ancestor chain]
    """
    log = EventLog()
    ids: dict[str, int] = {}

    drought = log.append(
        tick=371, kind="DROUGHT", country="KRV", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
    )
    ids["DROUGHT"] = drought.id

    price_spike = log.append(
        tick=395, kind="PRICE_SPIKE", country="ELB", country2=None,
        parent_id=drought.id, depth=1, is_intervention=False, severity=1,
    )
    ids["PRICE_SPIKE"] = price_spike.id

    currency_slide = log.append(
        tick=408, kind="CURRENCY_SLIDE", country="ELB", country2=None,
        parent_id=price_spike.id, depth=2, is_intervention=False, severity=1,
    )
    ids["CURRENCY_SLIDE"] = currency_slide.id

    unrest = log.append(
        tick=412, kind="UNREST", country="ELB", country2=None,
        parent_id=currency_slide.id, depth=3, is_intervention=False, severity=2,
    )
    ids["UNREST"] = unrest.id

    treasury_drain = log.append(
        tick=401, kind="TREASURY_DRAIN", country="KRV", country2=None,
        parent_id=price_spike.id, depth=2, is_intervention=False, severity=1,
    )
    ids["TREASURY_DRAIN"] = treasury_drain.id

    return log, ids


def test_doom_spiral_trace_returns_full_dfs_tree_with_correct_depths():
    log, ids = _doom_spiral_log()
    nodes = trace(log, ids["UNREST"])

    assert [n["kind"] for n in nodes] == [
        "DROUGHT",
        "PRICE_SPIKE",
        "CURRENCY_SLIDE",
        "UNREST",
        "TREASURY_DRAIN",
    ]
    assert [n["d"] for n in nodes] == [0, 1, 2, 3, 2]
    assert nodes[0]["parent_id"] is None
    assert nodes[3]["id"] == ids["UNREST"]


def test_trace_from_any_node_returns_the_same_whole_tree():
    """Trace means "this event's whole causal component," not "this event's own
    ancestors/descendants" -- TREASURY_DRAIN is a sibling branch, not an ancestor or
    descendant of UNREST, yet it appears when tracing FROM UNREST (and vice versa)."""
    log, ids = _doom_spiral_log()

    from_unrest = trace(log, ids["UNREST"])
    from_treasury_drain = trace(log, ids["TREASURY_DRAIN"])
    from_root = trace(log, ids["DROUGHT"])

    assert from_unrest == from_treasury_drain == from_root


def test_trace_root_has_no_parent_and_depth_zero():
    log, ids = _doom_spiral_log()
    nodes = trace(log, ids["DROUGHT"])
    root_node = nodes[0]
    assert root_node["parent_id"] is None
    assert root_node["d"] == 0


def test_cascade_clipped_marker_surfaces_on_the_node():
    log = EventLog()
    root = log.append(
        tick=1, kind="DROUGHT", country="KRV", country2=None,
        parent_id=None, depth=8, is_intervention=False, severity=2,
        payload={"cascade_clipped": True},
    )
    nodes = trace(log, root.id)
    assert nodes[0]["cascade_clipped"] is True


def test_node_without_clip_marker_defaults_false():
    log = EventLog()
    root = log.append(
        tick=1, kind="DROUGHT", country="KRV", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
    )
    nodes = trace(log, root.id)
    assert nodes[0]["cascade_clipped"] is False


def test_intervention_flag_surfaces_on_the_node():
    log = EventLog()
    root = log.append(
        tick=1, kind="INTERVENE_DROUGHT", country="KRV", country2=None,
        parent_id=None, depth=0, is_intervention=True, severity=2,
    )
    nodes = trace(log, root.id)
    assert nodes[0]["is_intervention"] is True


def test_children_ordered_deterministically_by_id():
    """Multiple children of the same parent: DFS visits them (and their subtrees) in id
    order, i.e. emission/schedule order (§4.3.2/§4.3.7)."""
    log = EventLog()
    root = log.append(
        tick=1, kind="PRICE_SPIKE", country="ELB", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=1,
    )
    first_child = log.append(
        tick=2, kind="TREASURY_DRAIN", country="ELB", country2=None,
        parent_id=root.id, depth=1, is_intervention=False, severity=1,
    )
    second_child = log.append(
        tick=3, kind="CURRENCY_SLIDE", country="ELB", country2=None,
        parent_id=root.id, depth=1, is_intervention=False, severity=1,
    )

    nodes = trace(log, root.id)
    assert [n["id"] for n in nodes] == [root.id, first_child.id, second_child.id]


def test_out_of_range_event_id_raises():
    log = EventLog()
    log.append(
        tick=1, kind="DROUGHT", country="KRV", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
    )
    with pytest.raises(ValueError):
        trace(log, 999)
    with pytest.raises(ValueError):
        trace(log, -1)
