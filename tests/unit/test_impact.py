"""Multi-cause event DAG and deduplicated impact analysis regressions."""

from meddler.engine.events import CauseLink, EventLog, StatDelta
from meddler.engine.impact import analyze, ancestor_ids, component_ids, descendant_ids
from meddler.engine.trace import trace


def _event(
    log: EventLog,
    *,
    tick: int,
    parent: int | None = None,
    causes: tuple[CauseLink, ...] = (),
    delta: float = 0.0,
    severity: int = 1,
):
    return log.append(
        tick=tick,
        kind="TEST",
        country="AAA",
        country2=None,
        parent_id=parent,
        depth=0 if parent is None else 1,
        is_intervention=False,
        stat_deltas=(StatDelta("AAA", "stability", delta, 50.0, 50.0 + delta),),
        severity=severity,
        causes=causes,
    )


def _dag() -> EventLog:
    log = EventLog()
    _event(log, tick=0, delta=1.0)  # 0 root
    _event(log, tick=1, parent=0, delta=2.0)  # 1 branch A
    _event(log, tick=2, causes=(CauseLink(0, "context", "shared root"),), delta=3.0)  # 2 B
    _event(
        log,
        tick=3,
        parent=1,
        causes=(CauseLink(2, "contributor", "converging branch"),),
        delta=4.0,
    )  # 3 shared descendant
    _event(log, tick=20, parent=3, delta=5.0)  # 4
    return log


def test_parent_ids_are_canonical_and_severity_is_independent_of_cause_count():
    log = EventLog()
    root_a = _event(log, tick=0, severity=0)
    root_b = _event(log, tick=0, severity=2)
    result = _event(
        log,
        tick=1,
        parent=root_b.id,
        causes=(
            CauseLink(root_b.id, "context", "duplicate legacy parent"),
            CauseLink(root_a.id, "contributor", "additional cause"),
        ),
        severity=1,
    )

    assert result.parent_ids == (root_a.id, root_b.id)
    assert [link.role for link in result.causal_links()] == ["contributor", "trigger"]
    assert result.severity == 1


def test_ancestors_descendants_and_component_are_deduplicated():
    log = _dag()
    assert ancestor_ids(log, 3) == (0, 1, 2)
    assert descendant_ids(log, 0) == (1, 2, 3, 4)
    assert component_ids(log, 3) == (0, 1, 2, 3, 4)


def test_horizon_excludes_late_descendants_without_losing_shared_nodes():
    log = _dag()
    assert descendant_ids(log, 0, horizon=5) == (1, 2, 3)
    impact = analyze(log, 0, horizon=5)
    assert [event.id for event in impact.descendants] == [1, 2, 3]
    assert impact.cumulative_totals[0].delta == 10.0
    assert impact.cumulative_totals[0].event_ids == (0, 1, 2, 3)


def test_shared_descendant_effect_counts_once_in_cumulative_total():
    impact = analyze(_dag(), 0)
    total = impact.cumulative_totals[0]
    assert total.delta == 15.0
    assert total.event_ids == (0, 1, 2, 3, 4)
    assert len([record for record in impact.downstream_effects if record.event_id == 3]) == 1


def test_trace_emits_shared_node_once_and_exposes_all_parent_ids():
    nodes = trace(_dag(), 3)
    ids = [node["id"] for node in nodes]
    assert set(ids) == {0, 1, 2, 3, 4}
    assert len(ids) == len(set(ids))
    shared = next(node for node in nodes if node["id"] == 3)
    assert shared["parent_ids"] == [1, 2]


def test_generic_effect_preserves_exact_before_and_after():
    event = _event(EventLog(), tick=0, delta=-7.5)
    effect = event.effects[0]
    assert effect.effect_type == "stat"
    assert effect.target == "AAA"
    assert effect.metric == "stability"
    assert effect.delta == -7.5
    assert effect.before == 50.0
    assert effect.after == 42.5
