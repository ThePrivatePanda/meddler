"""Deterministic WorldSettings mutation and replay.

Settings changed while a simulation is running are timeline state, not browser preferences.
Each accepted change is therefore recorded as a structural event so scrub, fork, restart,
and future save/load reconstruction observe the same rules at the same ticks.
"""

from __future__ import annotations

import json
from dataclasses import fields

from meddler.engine import structural
from meddler.engine.events import Event
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng

SETTINGS_CHANGED = "SETTINGS_CHANGED"
_SETTING_NAMES = frozenset(field.name for field in fields(WorldSettings))


def _coerce(current: object, value: object, key: str) -> object:
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise TypeError(f"setting {key} must be boolean")
        return value
    if isinstance(current, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"setting {key} must be an integer")
        return value
    if isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"setting {key} must be numeric")
        return float(value)
    if isinstance(current, list):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise TypeError(f"setting {key} must be a list of strings")
        return list(value)
    if isinstance(current, tuple):
        if not isinstance(value, (list, tuple)) or len(value) != len(current):
            raise TypeError(f"setting {key} must contain {len(current)} values")
        converted: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise TypeError(f"setting {key} values must be numeric")
            converted.append(float(item))
        return tuple(converted)
    if not isinstance(value, type(current)):
        raise TypeError(f"setting {key} has an invalid type")
    return value


def normalize_changes(settings: WorldSettings, incoming: object) -> dict[str, object]:
    """Validate known partial updates; preserve the contract's unknown-key tolerance."""
    if not isinstance(incoming, dict):
        raise TypeError("settings must be an object")
    normalized: dict[str, object] = {}
    for key in sorted(key for key in incoming if isinstance(key, str)):
        if key not in _SETTING_NAMES:
            continue
        current = getattr(settings, key)
        value = _coerce(current, incoming[key], key)
        if value != current:
            normalized[key] = value
    return normalized


def _decode_changes(event: Event) -> dict[str, object]:
    raw = event.payload.get("changes")
    if not isinstance(raw, str):
        return {}
    decoded = json.loads(raw)
    return decoded if isinstance(decoded, dict) else {}


def _apply(world: World, event: Event) -> None:
    for key, value in _decode_changes(event).items():
        if key not in _SETTING_NAMES:
            continue
        current = getattr(world.settings, key)
        setattr(world.settings, key, _coerce(current, value, key))


def _apply_live(world: World, rng: Rng, event: Event) -> None:
    del rng
    _apply(world, event)


def _apply_replay(world: World, event: Event) -> None:
    _apply(world, event)


def record_changes(world: World, rng: Rng, incoming: object) -> tuple[str, ...]:
    """Record and apply one deterministic partial settings update."""
    changes = normalize_changes(world.settings, incoming)
    if not changes:
        return ()
    before = {key: getattr(world.settings, key) for key in changes}
    event = world.log.append(
        tick=world.tick,
        kind=SETTINGS_CHANGED,
        country=None,
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=True,
        payload={
            "before": json.dumps(before, sort_keys=True, separators=(",", ":")),
            "changes": json.dumps(changes, sort_keys=True, separators=(",", ":")),
        },
        severity=0,
    )
    structural.run(world, rng, event)
    return tuple(changes)


structural.register_structural(SETTINGS_CHANGED, _apply_live)
structural.register_replay(SETTINGS_CHANGED, _apply_replay)


__all__ = ["SETTINGS_CHANGED", "normalize_changes", "record_changes"]
