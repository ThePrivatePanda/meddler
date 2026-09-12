"""RelationsSystem: passive decay + threshold RELATION_SHIFT events. PROPOSAL §6.1
slot 8, §5.2.

Only passive decay is implemented here -- event-driven relation changes (WAR_DECLARED
sets -90, ALLIANCE sets +70, etc.) are M3+ EventSpec stat_deltas applied by
ExogenousSystem/ConsequenceSystem, not this system's job. No `armed`-dict hysteresis
here (unlike M2.7's country-stat threshold bridge): decay is monotonic toward zero, so
each threshold is crossed at most once as a value settles, absent some other force
(war/alliance events, M3+) pushing it back out -- nothing in M2 does that yet.
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.events import Event, StatDelta
from meddler.engine.model import World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_relation_delta


def _crossed_threshold(old: float, new: float) -> float | None:
    for threshold in config.RELATION_SHIFT_THRESHOLDS:
        if (old - threshold) * (new - threshold) < 0:
            return threshold
    return None


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for pair in sorted(world.relations.keys()):
        value = world.relations[pair]
        if value == 0.0:
            continue
        # A relationship needs two countries that still exist. Letting a pair decay after
        # one side was annexed kept producing RELATION_SHIFT headlines about a nation that
        # had been absorbed -- "a cautious thaw between Numoania and Pituadia", 89 ticks
        # after Numoania ceased to be.
        if not (world.country(pair[0]).in_world and world.country(pair[1]).in_world):
            continue

        # Proportional decay (new = value * (1 - rate)) can never cross zero for
        # 0 < rate < 1, so no overshoot guard is needed -- it asymptotically approaches
        # zero but only an event (M3+) can actually zero it out or flip its sign.
        decay = world.settings.relation_decay_rate * abs(value)
        new_value = value - decay if value > 0 else value + decay

        stat_deltas: list[StatDelta] = []
        apply_relation_delta(world, stat_deltas, pair[0], pair[1], new_value - value)

        event = world.log.append(
            tick=world.tick,
            kind="RELATION_DECAY",
            country=pair[0],
            country2=pair[1],
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={"relation": new_value},
            stat_deltas=tuple(stat_deltas),
            severity=0,
        )
        events.append(event)

        crossed = _crossed_threshold(value, new_value)
        if crossed is not None:
            shift_event = world.log.append(
                tick=world.tick,
                kind="RELATION_SHIFT",
                country=pair[0],
                country2=pair[1],
                parent_id=None,
                depth=0,
                is_intervention=False,
                payload={"relation": new_value, "threshold": crossed},
                severity=0,
            )
            events.append(shift_event)
    return events
