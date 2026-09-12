"""World settings are deterministic timeline state, not transient bridge preferences."""

from __future__ import annotations

import json

import pytest

from meddler.bridge.server import new_session
from meddler.engine.settings import SETTINGS_CHANGED


def test_settings_change_is_recorded_and_visible_at_the_same_tick() -> None:
    session = new_session(101)
    timeline = session.multiverse.prime

    changed = timeline.update_settings({"drama_multiplier": 2.5})

    assert changed == ("drama_multiplier",)
    assert timeline.world.settings.drama_multiplier == 2.5
    assert timeline.world.log[-1].kind == SETTINGS_CHANGED
    assert json.loads(str(timeline.world.log[-1].payload["changes"])) == {
        "drama_multiplier": 2.5
    }
    assert timeline.world_at(0).settings.drama_multiplier == 2.5
    session.close()


def test_scrub_and_restart_observe_settings_at_their_historical_ticks() -> None:
    session = new_session(102)
    timeline = session.multiverse.prime
    timeline.advance()
    before_tick = timeline.world.tick
    timeline.advance()
    change_tick = timeline.world.tick
    timeline.update_settings(
        {
            "drama_multiplier": 2.0,
            "allow_nukes": True,
            "starting_stability_range": [35.0, 90.0],
        }
    )
    timeline.advance()

    assert timeline.world_at(before_tick).settings.drama_multiplier == 0.4
    changed_world = timeline.world_at(change_tick)
    assert changed_world.settings.drama_multiplier == 2.0
    assert changed_world.settings.allow_nukes is True
    assert changed_world.settings.starting_stability_range == (35.0, 90.0)

    session.multiverse.restart(before_tick)
    assert session.multiverse.prime.world.settings.drama_multiplier == 0.4
    assert session.multiverse.prime.world.settings.allow_nukes is False
    assert all(event.kind != SETTINGS_CHANGED for event in session.multiverse.prime.world.log)
    session.close()


def test_settings_validation_rejects_wrong_types_without_recording_an_event() -> None:
    session = new_session(103)
    timeline = session.multiverse.prime
    before = len(timeline.world.log)

    with pytest.raises(TypeError, match="drama_multiplier must be numeric"):
        timeline.update_settings({"drama_multiplier": "maximum"})

    assert len(timeline.world.log) == before
    assert timeline.world.settings.drama_multiplier == 0.4
    session.close()


def test_identical_settings_commands_produce_identical_event_history() -> None:
    sessions = [new_session(104), new_session(104)]
    try:
        for session in sessions:
            timeline = session.multiverse.prime
            for _ in range(3):
                timeline.advance()
            timeline.update_settings(
                {
                    "allow_nukes": True,
                    "drama_multiplier": 1.7,
                    "starting_stability_range": [35.0, 90.0],
                }
            )
            timeline.advance()

        serialized = []
        for session in sessions:
            serialized.append(
                [
                    (
                        event.id,
                        event.tick,
                        event.kind,
                        event.payload,
                        event.parent_ids,
                    )
                    for event in session.multiverse.prime.world.log
                ]
            )
        assert serialized[0] == serialized[1]
    finally:
        for session in sessions:
            session.close()


def test_forks_inherit_settings_represented_at_their_fork_tick() -> None:
    session = new_session(105)
    timeline = session.multiverse.prime
    timeline.advance()
    before_tick = timeline.world.tick
    timeline.advance()
    change_tick = timeline.world.tick
    timeline.update_settings({"drama_multiplier": 2.25})

    old_fork_id = session.multiverse.fork(before_tick, intervention=None)
    changed_fork_id = session.multiverse.fork(change_tick, intervention=None)

    assert session.multiverse.forks[old_fork_id].world.settings.drama_multiplier == 0.4
    assert session.multiverse.forks[changed_fork_id].world.settings.drama_multiplier == 2.25
    session.close()
