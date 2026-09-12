"""M7.3: bridge/adapter.py excludes ANNEXED/DISSOLVED countries from frames. contract §6/§9.

Pure-function tests -- adapter.py's helpers take a World and return dicts, no WebSocket
needed (contrast test_handshake.py's end-to-end protocol tests).
"""

from __future__ import annotations

from meddler.bridge import adapter
from meddler.engine import tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import CountryStatus, WorldSettings
from meddler.engine.worldgen import generate_world


def test_annexed_country_excluded_from_stats_and_leaders_and_active_list():
    world = generate_world(1, WorldSettings(starting_country_count=3))
    annexed_code = world.countries[0].code
    world.countries[0].status = CountryStatus.ANNEXED

    active_codes = {c.code for c in adapter.active_countries(world)}
    assert annexed_code not in active_codes
    assert annexed_code not in adapter.all_stats(world)
    assert annexed_code not in adapter.all_leaders(world)


def test_dissolved_country_excluded_too():
    world = generate_world(1, WorldSettings(starting_country_count=3))
    dissolved_code = world.countries[0].code
    world.countries[0].status = CountryStatus.DISSOLVED

    assert dissolved_code not in adapter.all_stats(world)


def test_active_and_occupied_countries_still_included():
    world = generate_world(1, WorldSettings(starting_country_count=3))
    world.countries[1].status = CountryStatus.OCCUPIED
    world.countries[1].occupied_by = world.countries[2].code

    active_codes = {c.code for c in adapter.active_countries(world)}
    assert world.countries[0].code in active_codes
    assert world.countries[1].code in active_codes  # occupied, still shown
    assert world.countries[2].code in active_codes


def test_hello_message_omits_annexed_country():
    world = generate_world(1, WorldSettings(starting_country_count=3))
    annexed_code = world.countries[0].code
    world.countries[0].status = CountryStatus.ANNEXED

    from meddler.bridge.server import new_session

    session = new_session(seed=1)
    session.multiverse.prime.world = world
    hello = adapter.hello_message(session)

    codes = {c["code"] for c in hello["countries"]}
    assert annexed_code not in codes


def test_event_json_adds_dag_causes_and_normalized_effects_compatibly():
    from meddler.engine.events import CauseLink, StatDelta

    world = generate_world(2, WorldSettings(starting_country_count=2))
    country = world.countries[0].code
    root = world.log.append(
        tick=0,
        kind="FAMINE_WARNING",
        country=country,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"grain_stock": 0.0},
    )
    event = world.log.append(
        tick=1,
        kind="UNREST",
        country=country,
        country2=None,
        parent_id=root.id,
        depth=1,
        is_intervention=False,
        stat_deltas=(StatDelta(country, "stability", -2.0, 50.0, 48.0),),
        causes=(CauseLink(root.id, "contributor", "also contributed"),),
    )

    payload = adapter.event_to_json(event, world)
    assert payload is not None
    assert payload["parentId"] == root.id
    assert payload["parentIds"] == [root.id]
    assert payload["causes"] == [
        {"eventId": root.id, "role": "trigger", "detail": "direct consequence"}
    ]
    assert payload["effects"][0]["before"] == 50.0
    assert payload["effects"][0]["after"] == 48.0


def test_event_impact_prime_explicitly_reports_no_counterfactual_baseline():
    from meddler.bridge import commands
    from meddler.bridge.server import new_session

    session = new_session(seed=3)
    country = session.multiverse.prime.world.countries[0].code
    event = session.multiverse.prime.world.log.append(
        tick=0,
        kind="FAMINE_WARNING",
        country=country,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"grain_stock": 0.0},
    )

    message = commands.handle(
        session, {"cmd": "eventImpact", "eventId": event.id, "tl": "A", "horizon": 10}
    )[0]
    assert message["type"] == "eventImpact"
    assert message["immediateEffects"] == []
    assert message["downstreamEffects"] == []
    assert message["cumulativeTotals"] == []
    assert message["horizonDiff"]["available"] is False
    assert "baseline" in message["horizonDiff"]["reason"]


def test_event_impact_uses_only_an_aligned_fork_for_valid_horizon_diff():
    from meddler.bridge import commands
    from meddler.bridge.server import new_session
    from meddler.bridge.session import ForkMeta

    session = new_session(seed=4)
    session.multiverse.prime.advance()
    fork_id = session.multiverse.fork(at_tick=1, intervention=None)
    session.fork_meta[fork_id] = ForkMeta(label="test fork", fork_tick=1)
    fork = session.multiverse.forks[fork_id]
    country = fork.world.countries[0].code
    event = fork.world.log.append(
        tick=1,
        kind="FAMINE_WARNING",
        country=country,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=True,
        payload={"grain_stock": 0.0},
    )

    message = commands.handle(
        session,
        {"cmd": "eventImpact", "eventId": event.id, "tl": fork_id, "horizon": 0},
    )[0]
    horizon = message["horizonDiff"]
    assert horizon["available"] is True
    assert horizon["basis"] == "alignedFork"
    assert "not organic-event suppression" in horizon["scope"]
    assert isinstance(horizon["effects"], list)


def test_snapshot_world_objects_project_the_supplied_historical_world_not_live_prime():
    from copy import deepcopy

    from meddler.bridge.server import new_session
    from meddler.engine.model import Shipment, Territory
    from meddler.engine.space import Position

    session = new_session(seed=23)
    live = session.multiverse.prime.world
    origin, dest = live.countries[:2]
    live.tick = 9
    live.shipments = [
        Shipment(
            id="SHIP99",
            origin=origin.code,
            dest=dest.code,
            commodity="food",
            qty=2.0,
            carrier="sea",
            buyer_pool="households",
            base_cost=10,
            tariff_duty=0,
            cost=10,
            proceeds=9,
            dispatch_event_id=0,
            depart_tick=8,
            arrive_tick=12,
            relief=False,
        )
    ]
    historical = deepcopy(live)
    historical.tick = 3
    historical.shipments = []
    historical.countries[0].territories = [Territory(Position(11.0, 22.0), "Historical")]

    message = adapter.snapshot_message(session, historical, live=False, scrubbed=True)

    objects = message["worldObjects"]
    assert objects["tick"] == 3
    assert objects["shipments"] == []
    assert objects["countries"][origin.code]["territories"] == [
        {"position": {"lat": 11.0, "lon": 22.0}, "region": "Historical"}
    ]
    assert live.shipments[0].id == "SHIP99"


def test_country_added_includes_the_new_countrys_world_object():
    world = generate_world(29, WorldSettings(starting_country_count=3))
    country = world.countries[1]

    message = adapter.country_added_message(world, country.code, world.countries[0].code)

    assert message["worldObject"]["code"] == country.code
    assert message["worldObject"]["position"] == {
        "lat": country.position.lat,
        "lon": country.position.lon,
    }


def test_annals_impact_ranks_complete_dag_without_double_counting_shared_descendants():
    from meddler.bridge import commands
    from meddler.bridge.server import new_session
    from meddler.engine.events import CauseLink, StatDelta

    session = new_session(seed=41)
    world = session.multiverse.prime.world
    a, b = world.countries[:2]
    root = world.log.append(
        tick=0,
        kind="FAMINE_WARNING",
        country=a.code,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"grain_stock": 0.0},
    )
    child_a = world.log.append(
        tick=1,
        kind="UNREST",
        country=a.code,
        country2=None,
        parent_id=root.id,
        depth=1,
        is_intervention=False,
        stat_deltas=(StatDelta(a.code, "stability", -2.0, 50.0, 48.0),),
    )
    child_b = world.log.append(
        tick=1,
        kind="UNREST",
        country=b.code,
        country2=None,
        parent_id=root.id,
        depth=1,
        is_intervention=False,
    )
    shared = world.log.append(
        tick=2,
        kind="SCANDAL",
        country=a.code,
        country2=b.code,
        parent_id=child_a.id,
        depth=2,
        is_intervention=False,
        causes=(CauseLink(child_b.id, "contributor", "converging cause"),),
        stat_deltas=(StatDelta(a.code, "stability", -1.0, 48.0, 47.0),),
    )
    world.tick = 2

    message = commands.handle(
        session,
        {"cmd": "annalsImpact", "tl": "A", "sortBy": "descendants", "limit": 10},
    )[0]

    assert message["type"] == "annalsImpact"
    assert message["tl"] == "A"
    assert message["sortBy"] == "descendants"
    leader = message["leaders"][0]
    assert leader["event"]["id"] == root.id
    assert leader["directChildren"] == 2
    assert leader["descendants"] == 3
    assert leader["generations"] == 2
    assert leader["affectedCountries"] == sorted([a.code, b.code])
    assert leader["recordedEffects"] == 2
    assert [item["id"] for item in leader["children"]] == [child_a.id, child_b.id]
    assert shared.id not in {item["event"]["id"] for item in message["leaders"]}


def test_annals_impact_normalizes_unknown_sort_and_bounds_limit():
    world = generate_world(42, WorldSettings(starting_country_count=2))
    message = adapter.annals_impact_message(
        world,
        timeline_id="A",
        sort_by="not-a-sort",
        limit=1000,
    )
    assert message["sortBy"] == "descendants"
    assert len(message["leaders"]) <= 100


def test_recent_events_uses_indexed_kind_tail_without_iterating_full_log(monkeypatch):
    world = generate_world(12, WorldSettings(starting_country_count=2))
    country = world.countries[0].code
    event = world.log.append(
        tick=1,
        kind="FAMINE_WARNING",
        country=country,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"grain_stock": 0.0},
    )

    def fail_iteration(_log):
        raise AssertionError("recent event payload streamed the full history")

    monkeypatch.setattr(type(world.log), "__iter__", fail_iteration)

    payload = adapter.recent_events(world)

    assert [item["id"] for item in payload] == [event.id]
