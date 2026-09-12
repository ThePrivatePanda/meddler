"""M4.2: engine/annals.py. PROPOSAL §3.7."""

from __future__ import annotations

from meddler.engine.annals import annals
from meddler.engine.events import EventLog
from meddler.engine.model import WorldSettings
from meddler.engine.worldgen import generate_world


def _world_with_log(log: EventLog):
    world = generate_world(1, WorldSettings(starting_country_count=3))
    world.log = log
    return world


def _scripted_log() -> tuple[EventLog, dict[str, int]]:
    log = EventLog()
    ids: dict[str, int] = {}

    ids["drought"] = log.append(
        tick=10, kind="DROUGHT", country="AAA", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
    ).id
    ids["war"] = log.append(
        tick=20, kind="WAR_DECLARED", country="AAA", country2="BBB",
        parent_id=None, depth=0, is_intervention=False, severity=2,
    ).id
    ids["peace"] = log.append(
        tick=50, kind="PEACE", country="AAA", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=1,
    ).id
    ids["inflation1"] = log.append(
        tick=60, kind="INFLATION_CRISIS", country="AAA", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
        payload={"inflation": 9.5},
    ).id
    ids["inflation2"] = log.append(
        tick=70, kind="INFLATION_CRISIS", country="CCC", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
        payload={"inflation": 14.2},
    ).id
    ids["coup1"] = log.append(
        tick=80, kind="COUP", country="AAA", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
    ).id
    ids["coup2"] = log.append(
        tick=90, kind="COUP", country="AAA", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
    ).id
    ids["ambient"] = log.append(
        tick=95, kind="PRODUCTION", country="AAA", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=0,
    ).id
    return log, ids


def test_major_events_excludes_severity_zero_and_sorts_by_tick():
    log, ids = _scripted_log()
    world = _world_with_log(log)
    result = annals(world)
    kinds = [e["kind"] for e in result["majorEvents"]]
    assert "PRODUCTION" not in kinds  # severity 0, ambient
    ticks = [e["tick"] for e in result["majorEvents"]]
    assert ticks == sorted(ticks)


def test_major_events_filtered_by_country():
    log, ids = _scripted_log()
    world = _world_with_log(log)
    result = annals(world, country="CCC")
    assert [e["id"] for e in result["majorEvents"]] == [ids["inflation2"]]


def test_war_derivation_finds_peace_and_computes_toll():
    log, ids = _scripted_log()
    world = _world_with_log(log)
    result = annals(world)
    wars = result["wars"]
    assert len(wars) == 1
    war = wars[0]
    assert war["aggressor"] == "AAA"
    assert war["defender"] == "BBB"
    assert war["start_tick"] == 20
    assert war["end_tick"] == 50
    assert war["toll"] == 30
    assert war["outcome"] == "peace"


def test_war_without_peace_is_ongoing():
    log = EventLog()
    log.append(
        tick=5, kind="WAR_DECLARED", country="AAA", country2="BBB",
        parent_id=None, depth=0, is_intervention=False, severity=2,
    )
    world = _world_with_log(log)
    result = annals(world)
    assert len(result["wars"]) == 1
    assert result["wars"][0]["end_tick"] is None
    assert result["wars"][0]["toll"] is None
    assert result["wars"][0]["outcome"] == "ongoing"


def test_records_worst_inflation_and_most_coups():
    log, ids = _scripted_log()
    world = _world_with_log(log)
    result = annals(world)
    records = result["records"]

    assert records["worst_inflation"]["country"] == "CCC"
    assert records["worst_inflation"]["value"] == 14.2

    assert records["most_coups"]["country"] == "AAA"
    assert records["most_coups"]["count"] == 2

    assert records["longest_war"]["toll"] == 30


def test_records_omit_keys_with_no_supporting_events():
    log = EventLog()
    log.append(
        tick=1, kind="DROUGHT", country="AAA", country2=None,
        parent_id=None, depth=0, is_intervention=False, severity=2,
    )
    world = _world_with_log(log)
    records = annals(world)["records"]
    assert "worst_inflation" not in records
    assert "most_coups" not in records
    assert "longest_war" not in records


def test_eras_partition_on_severity_two_boundaries():
    log, ids = _scripted_log()
    world = _world_with_log(log)
    eras = annals(world)["eras"]
    # Every severity-2 event closes its own era (usually a 1-event era); PEACE
    # (severity 1) accumulates into the NEXT severity-2 event's era instead of closing
    # one of its own.
    assert len(eras) == 6
    assert eras[0] == {
        "start_tick": 10,
        "end_tick": 10,
        "event_count": 1,
        "dominant_kind": "DROUGHT",
    }
    assert eras[1] == {
        "start_tick": 10,
        "end_tick": 20,
        "event_count": 1,
        "dominant_kind": "WAR_DECLARED",
    }
    # PEACE(50) + inflation1(60): 2 events, both closed out by inflation1's severity 2.
    assert eras[2] == {
        "start_tick": 20,
        "end_tick": 60,
        "event_count": 2,
        "dominant_kind": "PEACE",
    }


def test_empty_log_yields_empty_annals():
    world = _world_with_log(EventLog())
    result = annals(world)
    assert result == {"eras": [], "wars": [], "records": {}, "majorEvents": []}
