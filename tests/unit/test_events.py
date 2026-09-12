from meddler.engine.events import EventLog


def _append(log: EventLog, tick: int, kind: str = "TEST", parent_id: int | None = None) -> int:
    event = log.append(
        tick=tick,
        kind=kind,
        country="ELB",
        country2=None,
        parent_id=parent_id,
        depth=0 if parent_id is None else 1,
        is_intervention=False,
    )
    return event.id


def test_ids_are_sequential_in_append_order():
    log = EventLog()
    ids = [_append(log, tick=t) for t in range(5)]
    assert ids == [0, 1, 2, 3, 4]
    assert len(log) == 5


def test_getitem_returns_matching_event():
    log = EventLog()
    _append(log, tick=0, kind="DROUGHT")
    second_id = _append(log, tick=1, kind="PRICE_SPIKE", parent_id=0)
    event = log[second_id]
    assert event.kind == "PRICE_SPIKE"
    assert event.parent_id == 0
    assert event.depth == 1


def test_truncate_after_drops_later_events_only():
    log = EventLog()
    for t in range(10):
        _append(log, tick=t)
    log.truncate_after(4)
    assert len(log) == 5
    assert all(e.tick <= 4 for e in log)


def test_default_payload_and_ledger_are_empty():
    log = EventLog()
    event = log.append(
        tick=0,
        kind="TEST",
        country=None,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
    )
    assert event.payload == {}
    assert event.ledger == ()


def test_finalized_payload_and_effects_survive_cache_eviction():
    from meddler.engine.events import record_structural_effect

    log = EventLog()
    event = log.append(
        tick=0,
        kind="STRUCTURAL",
        country="ELB",
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
    )
    event.payload["result"] = "recorded"
    record_structural_effect(event, target="ELB", metric="leader", after="Ada")
    for index in range(EventLog.CACHE_LIMIT + 8):
        _append(log, tick=index + 1)
    list(log)  # finalizes, streams, and evicts the oldest hydrated object

    restored = log[0]
    assert restored.payload == {"result": "recorded"}
    assert [(effect.target, effect.metric, effect.after) for effect in restored.effects] == [
        ("ELB", "leader", "Ada")
    ]
    assert log.resident_event_count <= EventLog.CACHE_LIMIT


def test_deepcopy_is_a_compact_immutable_boundary_on_the_same_database():
    import copy

    log = EventLog()
    for tick in range(5):
        _append(log, tick)
    snapshot = copy.deepcopy(log)
    _append(log, 5)

    assert snapshot.database_path == log.database_path
    assert len(snapshot) == 5
    assert [event.id for event in snapshot] == [0, 1, 2, 3, 4]
    assert len(log) == 6


def test_branch_shares_prefix_and_isolates_its_suffix():
    import copy

    prime = EventLog()
    for tick in range(3):
        _append(prime, tick)
    fork = copy.deepcopy(prime).branch()
    fork_id = _append(fork, 3, kind="FORK_ONLY")
    prime_id = _append(prime, 3, kind="PRIME_ONLY")

    assert fork.database_path == prime.database_path
    assert fork_id == prime_id == 3
    assert [event.kind for event in fork] == ["TEST", "TEST", "TEST", "FORK_ONLY"]
    assert [event.kind for event in prime] == ["TEST", "TEST", "TEST", "PRIME_ONLY"]


def test_country_interval_query_uses_effect_targets_and_reports_exact_total():
    from meddler.engine.events import record_structural_effect

    log = EventLog()
    direct = log.append(
        tick=2,
        kind="DIRECT",
        country="ELB",
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        severity=1,
    )
    affected = log.append(
        tick=3,
        kind="AFFECTED",
        country="OTHER",
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        severity=2,
    )
    record_structural_effect(affected, target="ELB.infrastructure", metric="condition", delta=-0.2)
    log.append(
        tick=3,
        kind="UNRELATED",
        country="OTHER",
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
    )

    events, total = log.country_events("ELB", 1, 3, limit=1)
    assert total == 2
    assert [event.id for event in events] == [affected.id]
    assert direct.id != affected.id


def test_indexed_recent_kind_interval_and_tail_queries_preserve_order_and_branch_view():
    import copy

    prime = EventLog()
    _append(prime, tick=0, kind="KEEP")
    _append(prime, tick=1, kind="IGNORE")
    _append(prime, tick=2, kind="KEEP")
    fork = copy.deepcopy(prime).branch()
    _append(prime, tick=3, kind="PRIME_ONLY")
    _append(fork, tick=3, kind="KEEP")

    assert [event.id for event in prime.recent_events_of_kinds(("KEEP",), limit=2)] == [
        0,
        2,
    ]
    assert [event.id for event in fork.recent_events_of_kinds(("KEEP",), limit=2)] == [
        2,
        3,
    ]
    assert [
        event.id for event in fork.events_of_kinds_between(("KEEP",), 0, 3)
    ] == [2, 3]
    assert [event.kind for event in fork.tail(2)] == ["KEEP", "KEEP"]
    assert [event.kind for event in prime.tail(2)] == ["KEEP", "PRIME_ONLY"]
