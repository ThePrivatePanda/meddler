"""Snapshots, scrubbing, forks, and restart. PROPOSAL §4.4, §5, §3.5, §3.7."""

from __future__ import annotations

import copy
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from meddler.engine import settings as world_settings
from meddler.engine import structural
from meddler.engine.events import Event, EventLog, ScheduleEntry
from meddler.engine.ledger import apply_to_world
from meddler.engine.model import World
from meddler.engine.rng import Rng, derive_seed
from meddler.engine.stats import replay_stat_delta
from meddler.engine.tickloop import tick

FORK_SLOTS = ("B", "C", "D")
CountryStats = dict[str, dict[str, float]]


def _aux_state(world: World) -> dict[str, Any]:
    """End-of-tick state that no event records, so replaying events cannot rebuild it:
    the consequence queue and its counters, threshold hysteresis arms, and election dates.
    None of it is displayed, but a fork taken from a reconstructed world runs on it -- a
    stale queue would fire (or skip) consequences prime never did."""
    return {
        "schedule": [asdict(entry) for entry in world.schedule],
        "schedule_seq": world.schedule_seq,
        "clip_count": world.clip_count,
        "armed": {c.code: dict(c.armed) for c in world.countries},
        "election_due": {c.code: c.election_due_tick for c in world.countries},
    }


def _apply_aux_state(world: World, state: dict[str, Any]) -> None:
    world.schedule = [ScheduleEntry(**entry) for entry in state["schedule"]]
    world.schedule_seq = int(state["schedule_seq"])
    world.clip_count = int(state["clip_count"])
    armed = state["armed"]
    election_due = state["election_due"]
    for country in world.countries:
        if country.code in armed:
            country.armed = {str(k): bool(v) for k, v in armed[country.code].items()}
        if country.code in election_due:
            due = election_due[country.code]
            country.election_due_tick = None if due is None else int(due)


def _record_stats(world: World) -> CountryStats:
    """Record exact display statistics without affecting simulation behavior."""
    return {
        c.code: {
            "stability": c.stability,
            "inflation": c.inflation,
            "gdp": c.gdp_tick,
            "fx": c.exchange_rate,
            "treasury": float(c.pools.get("treasury", 0)),
        }
        for c in world.countries
    }


class RngStateHistory(Mapping[int, tuple[Any, ...]]):
    """SQLite-backed exact PRNG states with a tiny bounded read cache."""

    CACHE_LIMIT = 8

    def __init__(self, log: EventLog) -> None:
        self._log = log
        self._cache: OrderedDict[int, tuple[Any, ...]] = OrderedDict()

    def __getitem__(self, tick: int) -> tuple[Any, ...]:
        if tick in self._cache:
            self._cache.move_to_end(tick)
            return self._cache[tick]
        state = self._log.rng_state(tick)
        self._cache[tick] = state
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)
        return state

    def __setitem__(self, tick: int, state: tuple[Any, ...]) -> None:
        self._log.record_rng_state(tick, state)
        self._cache[tick] = state
        self._cache.move_to_end(tick)
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)

    def __iter__(self) -> Iterator[int]:
        return iter(self._log.rng_ticks())

    def __len__(self) -> int:
        return len(self._log.rng_ticks())

    def truncate_after(self, tick: int) -> None:
        self._log.truncate_tick_history_after(tick)
        self._cache = OrderedDict(
            (recorded_tick, state)
            for recorded_tick, state in self._cache.items()
            if recorded_tick <= tick
        )


class StatHistory(Mapping[int, CountryStats]):
    """SQLite-backed country observations with bounded point hydration."""

    CACHE_LIMIT = 256

    def __init__(self, log: EventLog) -> None:
        self._log = log
        self._cache: OrderedDict[int, CountryStats] = OrderedDict()

    def __getitem__(self, tick: int) -> CountryStats:
        if tick in self._cache:
            self._cache.move_to_end(tick)
            return self._cache[tick]
        stats = self._log.stats_at(tick)
        self._cache[tick] = stats
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)
        return stats

    def __setitem__(self, tick: int, stats: CountryStats) -> None:
        self._log.record_country_stats(tick, stats)
        self._cache[tick] = stats
        self._cache.move_to_end(tick)
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)

    def __iter__(self) -> Iterator[int]:
        return iter(self._log.stat_ticks())

    def __len__(self) -> int:
        return len(self._log.stat_ticks())

    def truncate_after(self, tick: int) -> None:
        self._log.truncate_tick_history_after(tick)
        self._cache = OrderedDict(
            (recorded_tick, stats)
            for recorded_tick, stats in self._cache.items()
            if recorded_tick <= tick
        )


@dataclass
class Timeline:
    seed: int
    world: World
    snapshots: dict[int, World]
    rng: Rng
    rng_states: RngStateHistory = field(init=False)
    stat_history: StatHistory = field(init=False)
    # Log length when the timeline last recorded its own state. Commands (god mode, fork
    # interventions) append events between ticks; a length change at the next advance
    # means the world moved without a tick, so it is snapshotted before ticking.
    _recorded_boundary: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.rng_states = RngStateHistory(self.world.log)
        self.stat_history = StatHistory(self.world.log)
        # EventLog.__deepcopy__ stores only an immutable SQLite branch boundary, not events.
        if self.world.tick not in self.snapshots:
            self.snapshots[self.world.tick] = copy.deepcopy(self.world)
        self.rng_states[self.world.tick] = self.rng.get_state()
        self.stat_history[self.world.tick] = _record_stats(self.world)
        self._record_aux()

    def _record_aux(self) -> None:
        self._recorded_boundary = len(self.world.log)
        self.world.log.record_aux_state(
            self.world.tick, self._recorded_boundary, _aux_state(self.world)
        )

    def update_settings(self, incoming: object) -> tuple[str, ...]:
        """Record settings as deterministic timeline state and snapshot the command tick."""
        with self.world.log.transaction():
            changed = world_settings.record_changes(self.world, self.rng, incoming)
        if changed:
            # Commands run between ticks. Replacing/creating this boundary avoids a stale
            # same-tick snapshot hiding the structural event from world_at(tick).
            self.snapshots[self.world.tick] = copy.deepcopy(self.world)
        return changed

    def advance(self) -> list[Event]:
        """Run one atomic tick and snapshot compact state at the configured interval."""
        if len(self.world.log) != self._recorded_boundary:
            # A command changed the world since the last tick; keep that state exactly.
            self.snapshots[self.world.tick] = copy.deepcopy(self.world)
        with self.world.log.transaction():
            _, events = tick(self.world, self.rng)
            self.rng_states[self.world.tick] = self.rng.get_state()
            self.stat_history[self.world.tick] = _record_stats(self.world)
            self._record_aux()
        if self.world.tick % self.world.settings.snapshot_interval == 0:
            self.snapshots[self.world.tick] = copy.deepcopy(self.world)
        return events

    def world_at(self, at_tick: int) -> World:
        """Reconstruct state from the nearest compact snapshot and persisted event range.

        Replay starts at the snapshot's event BOUNDARY (the log length when it was taken),
        not at the tick after it: god-mode commands run between ticks and are stamped with
        the current tick, so an intervention can land after a same-tick snapshot. Starting
        from the next tick silently dropped it (a fork's first snapshot is always taken just
        before its intervention is applied)."""
        if at_tick == self.world.tick:
            # The live world is exact, including anything a command did since the last tick.
            reconstructed = copy.deepcopy(self.world)
            reconstructed.log = self.world.log.view_through_tick(at_tick)
            return reconstructed
        candidates = [snapshot_tick for snapshot_tick in self.snapshots if snapshot_tick <= at_tick]
        if not candidates:
            raise ValueError(f"no snapshot at or before tick {at_tick}")
        nearest = max(candidates)
        snapshot = self.snapshots[nearest]
        reconstructed = copy.deepcopy(snapshot)
        for event in self.world.log.events_from_id_through_tick(len(snapshot.log), at_tick):
            for entry in event.ledger:
                apply_to_world(reconstructed, entry)
            for delta in event.stat_deltas:
                replay_stat_delta(reconstructed, delta)
            structural.replay(reconstructed, event)
        if nearest < at_tick:
            # Events rebuild everything they record; the rest was recorded at end of tick.
            # A command at a later tick than the snapshot would have forced a snapshot of
            # its own at the next advance, so this record is never older than the events.
            aux = self.world.log.aux_state(at_tick)
            if aux is not None:
                _apply_aux_state(reconstructed, aux[1])
        reconstructed.tick = at_tick
        reconstructed.log = self.world.log.view_through_tick(at_tick)
        return reconstructed


@dataclass
class Multiverse:
    """Prime timeline + up to 3 forks with shared immutable SQLite history prefixes."""

    prime: Timeline
    forks: dict[str, Timeline] = field(default_factory=dict)

    def fork(self, at_tick: int, intervention: Event | None) -> str:
        fork_id = next((slot for slot in FORK_SLOTS if slot not in self.forks), None)
        if fork_id is None:
            raise ValueError("at most 3 concurrent forks (PROPOSAL §3.5/§4.4)")
        if at_tick not in self.prime.rng_states:
            raise ValueError(f"no recorded RNG state at tick {at_tick}")

        world = self.prime.world_at(at_tick)
        world.log = world.log.branch()
        if intervention is not None:
            with world.log.transaction():
                recorded = world.log.append(
                    tick=at_tick,
                    kind=intervention.kind,
                    country=intervention.country,
                    country2=intervention.country2,
                    parent_id=None,
                    depth=0,
                    is_intervention=True,
                    payload=dict(intervention.payload),
                    ledger=intervention.ledger,
                    stat_deltas=intervention.stat_deltas,
                    severity=intervention.severity,
                )
                for entry in recorded.ledger:
                    apply_to_world(world, entry)
                for delta in recorded.stat_deltas:
                    replay_stat_delta(world, delta)
                # An intervention's declarative half is its ledger and stat deltas; its
                # structural half (a war declared, a country seceded, an asset class
                # knocked out) lives in the recorded payload. Replay it too, or a fork
                # seeded with one keeps only half of what it did.
                structural.replay(world, recorded)

        fork_rng = Rng.from_state(seed=self.prime.seed, state=self.prime.rng_states[at_tick])
        # A fork shares prime's history up to its branch point, so it inherits prime's
        # earlier snapshots (immutable; world_at deep-copies before replaying). Without them
        # an adopted fork could not scrub back before the tick it branched from.
        inherited = {t: w for t, w in self.prime.snapshots.items() if t < at_tick}
        self.forks[fork_id] = Timeline(
            seed=self.prime.seed, world=world, snapshots=inherited, rng=fork_rng
        )
        return fork_id

    def adopt_fork(self, fork_id: str) -> None:
        promoted = self.forks.pop(fork_id)
        for sibling in self.forks.values():
            sibling.world.log.discard_writable_suffix()
        self.forks.clear()
        promoted.world.log.prune_inherited_futures()
        self.prime = promoted

    def drop_fork(self, fork_id: str) -> None:
        dropped = self.forks.pop(fork_id)
        dropped.world.log.discard_writable_suffix()

    def restart(self, at_tick: int) -> None:
        if self.forks:
            raise ValueError("restart refused while forks exist (PROPOSAL §3.7/§4.4)")

        restored = self.prime.world_at(at_tick)
        live_log = self.prime.world.log
        live_log.truncate_after(at_tick)
        self.prime.rng_states.truncate_after(at_tick)
        self.prime.stat_history.truncate_after(at_tick)
        restored.log = live_log
        restored.countries = [c for c in restored.countries if c.born_at_tick <= at_tick]
        self.prime.world = restored
        self.prime.snapshots = {t: w for t, w in self.prime.snapshots.items() if t <= at_tick}
        self.prime.snapshots[at_tick] = copy.deepcopy(restored)
        self.prime.rng = Rng(seed=derive_seed(self.prime.seed, at_tick, "genesis"))
        self.prime.rng_states[at_tick] = self.prime.rng.get_state()
        self.prime.stat_history[at_tick] = _record_stats(restored)
        self.prime._record_aux()
