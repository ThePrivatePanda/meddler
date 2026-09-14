"""Event schema and the append-only event log. PROPOSAL §4.5 / §4.6."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
import weakref
import zlib
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast


@dataclass(frozen=True)
class LedgerEntry:
    """One money movement caused by an event. PROPOSAL §4.5.

    kind="transfer": src_pool and dst_pool both set.
    kind="mint": src_pool is None, dst_pool is the pool credited.
    kind="burn": dst_pool is None, src_pool is the pool debited.
    amount is always positive, in integer minor units.
    """

    kind: Literal["transfer", "mint", "burn"]
    src_pool: str | None
    dst_pool: str | None
    amount: int
    currency: str


@dataclass(frozen=True)
class CauseLink:
    """One additional edge in the causal DAG.

    `Event.parent_id` remains the legacy/direct trigger edge for protocol compatibility.
    These links capture other triggers, state contributors, and enabling context. Severity
    belongs to the resulting event and is never derived from the number of links.
    """

    event_id: int
    role: Literal["trigger", "contributor", "context"] = "contributor"
    detail: str = ""


EffectValue = float | int | str | None


@dataclass(frozen=True)
class Effect:
    """A normalized, UI-facing record of one mutation caused by an Event."""

    effect_type: Literal["stat", "money", "structural"]
    target: str
    metric: str
    delta: float | int | None
    before: EffectValue = None
    after: EffectValue = None
    unit: str = ""


@dataclass(frozen=True)
class StatDelta:
    """One non-money stat mutation caused by an event. The Ledger's counterpart for
    everything that isn't a money pool (stability, inflation, gdp_tick, population,
    grain_*, infra condition, World.relations, ...). Not in PROPOSAL §4.6's literal
    Event listing; added so Timeline.world_at's replay can reconstruct these fields
    without re-running systems, matching §4.4's "replay applies stored ledger and stat
    deltas" for state PROPOSAL §6.2/§6.7.2/§5.2 mutate every tick. See
    docs/design-decisions.md ("Stat deltas: the Ledger pattern extended to non-money state").

    target="<CODE>": stat is a Country field name; delta is added to it.
    target="relation": stat is "<CODEA>:<CODEB>" (sorted pair); delta is added to
    World.relations[(CODEA, CODEB)].

    delta is always the ALREADY-CLAMPED actual change (new_value - old_value), never
    the raw pre-clamp formula output, so replay reproduces clamped results exactly
    without needing to know any stat's clamp range.
    """

    target: str
    stat: str
    delta: float
    before: float | None = None
    after: float | None = None


@dataclass(frozen=True)
class ScheduleEntry:
    """One pending consequence in ConsequenceSystem's schedule queue (§6.4.2/§4.3.7).

    Ordered by (fire_tick, schedule_seq); schedule_seq is a per-timeline monotonic
    counter assigned at schedule time, so ties on fire_tick resolve deterministically
    without ever comparing the payload dict.

    The §4.7 sketch describes the queue as a bare (fire_tick, schedule_seq, child_kind,
    parent_id, payload) tuple; this carries three extra resolved routing fields
    (parent_depth, primary, secondary) so firing needs no back-reference into the log.
    parent_depth is cached because child depth = parent_depth + 1 (§6.4.1) and the
    target country codes are resolved at schedule time (the "random" pick consumes RNG
    there, per the pinned draw order -- see systems/consequence.py). Documented deviation.
    """

    fire_tick: int
    schedule_seq: int
    child_kind: str
    parent_id: int
    parent_depth: int
    primary: str | None
    secondary: str | None
    payload: dict[str, float | int | str]


@dataclass(frozen=True)
class Event:
    """PROPOSAL §4.6. depth is capped at MAX_DEPTH; overflow sets
    payload["cascade_clipped"] = True on the parent (§6.4.3)."""

    id: int
    tick: int
    kind: str
    country: str | None
    country2: str | None
    parent_id: int | None
    depth: int
    is_intervention: bool
    payload: dict[str, float | int | str]
    ledger: tuple[LedgerEntry, ...]
    stat_deltas: tuple[StatDelta, ...]
    severity: int
    causes: tuple[CauseLink, ...] = ()
    # Structural handlers finalize these two mutable containers synchronously after append.
    effects: list[Effect] = field(default_factory=list)

    @property
    def parent_ids(self) -> tuple[int, ...]:
        """All causal predecessor ids, sorted and deduplicated for DAG traversal."""
        ids = {link.event_id for link in self.causes}
        if self.parent_id is not None:
            ids.add(self.parent_id)
        return tuple(sorted(ids))

    def causal_links(self) -> tuple[CauseLink, ...]:
        """Canonical typed links, including the legacy direct parent as a trigger."""
        links = {link.event_id: link for link in self.causes}
        if self.parent_id is not None:
            links[self.parent_id] = CauseLink(self.parent_id, "trigger", "direct consequence")
        return tuple(links[event_id] for event_id in sorted(links))


def _generic_effects(
    ledger: tuple[LedgerEntry, ...], stat_deltas: tuple[StatDelta, ...]
) -> list[Effect]:
    effects = [
        Effect(
            effect_type="stat",
            target=delta.target,
            metric=delta.stat,
            delta=delta.delta,
            before=delta.before,
            after=delta.after,
        )
        for delta in stat_deltas
    ]
    for entry in ledger:
        if entry.src_pool is not None:
            effects.append(
                Effect("money", entry.src_pool, "balance", -entry.amount, unit=entry.currency)
            )
        if entry.dst_pool is not None:
            effects.append(
                Effect("money", entry.dst_pool, "balance", entry.amount, unit=entry.currency)
            )
    return effects


def record_structural_effect(
    event: Event,
    *,
    target: str,
    metric: str,
    before: EffectValue = None,
    after: EffectValue = None,
    delta: float | int | None = None,
    unit: str = "",
) -> None:
    """Append one realized structural mutation after EventLog.append froze the event."""
    event.effects.append(Effect("structural", target, metric, delta, before, after, unit))


@dataclass(frozen=True)
class _Segment:
    branch: str
    start: int
    end: int


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _pack(value: object) -> bytes:
    raw = _json(value).encode("utf-8")
    compressed = zlib.compress(raw, level=1)
    return b"Z" + compressed if len(compressed) < len(raw) else b"J" + raw


def _unpack(value: object) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    raw = bytes(cast(bytes, value))
    if not raw:
        raise ValueError("empty history payload")
    data = zlib.decompress(raw[1:]) if raw[:1] == b"Z" else raw[1:]
    return json.loads(data.decode("utf-8"))


def _cleanup_store(connection: sqlite3.Connection, path: str) -> None:
    with contextlib.suppress(sqlite3.Error):
        connection.close()
    for candidate in (path, f"{path}-wal", f"{path}-shm"):
        with contextlib.suppress(OSError):
            os.remove(candidate)


#: Widest tick window read through the `events_tick` index rather than the primary key.
#: A segment's id bounds span its whole branch, so the unhinted plan walks every event in
#: the branch whatever the window; the tick index reads only the window, then sorts it by
#: id. Measured on seed 1337 at t1000 (116k events): a one-tick read went from 36 ms to
#: under 1 ms, a ten-tick read of three shipment kinds from ~40 ms to 1.9 ms, and the two plans tie
#: near 300 ticks, past which the sort makes the index slower (0.25 s -> 0.58 s over the
#: whole history).
_TICK_INDEX_MAX_SPAN = 100


def _tick_window_hint(start_tick: int, end_tick: int) -> str:
    """The index hint for a ``start_tick < tick <= end_tick`` read. Results are identical
    either way: both queries carry the same predicate and ``ORDER BY id``."""
    return "INDEXED BY events_tick" if end_tick - start_tick <= _TICK_INDEX_MAX_SPAN else ""


class _HistoryStore:
    """One file-backed SQLite database shared by prime, snapshots, and fork views."""

    def __init__(self) -> None:
        descriptor, path = tempfile.mkstemp(prefix="meddler-history-", suffix=".sqlite3")
        os.close(descriptor)
        self.path = path
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA cache_size=-4096")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.executescript(
            """
            CREATE TABLE events (
                branch TEXT NOT NULL,
                id INTEGER NOT NULL,
                tick INTEGER NOT NULL,
                kind TEXT NOT NULL,
                country TEXT,
                country2 TEXT,
                parent_id INTEGER,
                depth INTEGER NOT NULL,
                is_intervention INTEGER NOT NULL,
                payload BLOB NOT NULL,
                ledger BLOB NOT NULL,
                stat_deltas BLOB NOT NULL,
                severity INTEGER NOT NULL,
                causes BLOB NOT NULL,
                effects BLOB NOT NULL,
                PRIMARY KEY (branch, id)
            ) WITHOUT ROWID;
            CREATE INDEX events_tick ON events(branch, tick, id);
            CREATE INDEX events_kind ON events(branch, kind, id);
            CREATE TABLE event_countries (
                branch TEXT NOT NULL,
                id INTEGER NOT NULL,
                code TEXT NOT NULL,
                PRIMARY KEY (branch, id, code),
                FOREIGN KEY (branch, id) REFERENCES events(branch, id) ON DELETE CASCADE
            ) WITHOUT ROWID;
            CREATE INDEX event_countries_code
                ON event_countries(code, branch, id);
            CREATE TABLE causal_edges (
                branch TEXT NOT NULL,
                child_id INTEGER NOT NULL,
                parent_id INTEGER NOT NULL,
                PRIMARY KEY (branch, child_id, parent_id),
                FOREIGN KEY (branch, child_id) REFERENCES events(branch, id) ON DELETE CASCADE
            ) WITHOUT ROWID;
            CREATE INDEX causal_edges_parent
                ON causal_edges(branch, parent_id, child_id);
            CREATE TABLE event_effects (
                branch TEXT NOT NULL,
                id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                target TEXT NOT NULL,
                metric TEXT NOT NULL,
                delta REAL,
                PRIMARY KEY (branch, id, ordinal),
                FOREIGN KEY (branch, id) REFERENCES events(branch, id) ON DELETE CASCADE
            ) WITHOUT ROWID;
            CREATE INDEX event_effects_lookup
                ON event_effects(target, metric, branch, id);
            CREATE TABLE event_ledger (
                branch TEXT NOT NULL,
                id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                tick INTEGER NOT NULL,
                country TEXT,
                kind TEXT NOT NULL,
                amount INTEGER NOT NULL,
                PRIMARY KEY (branch, id, ordinal),
                FOREIGN KEY (branch, id) REFERENCES events(branch, id) ON DELETE CASCADE
            ) WITHOUT ROWID;
            CREATE INDEX event_ledger_tick_country
                ON event_ledger(branch, tick, country, kind, id);
            CREATE TABLE aux_states (
                branch TEXT NOT NULL,
                tick INTEGER NOT NULL,
                boundary INTEGER NOT NULL,
                state BLOB NOT NULL,
                PRIMARY KEY (branch, tick)
            ) WITHOUT ROWID;
            CREATE TABLE rng_states (
                branch TEXT NOT NULL,
                tick INTEGER NOT NULL,
                state BLOB NOT NULL,
                PRIMARY KEY (branch, tick)
            ) WITHOUT ROWID;
            CREATE TABLE country_stats (
                branch TEXT NOT NULL,
                tick INTEGER NOT NULL,
                code TEXT NOT NULL,
                stability REAL NOT NULL,
                inflation REAL NOT NULL,
                gdp REAL NOT NULL,
                fx REAL NOT NULL,
                treasury REAL NOT NULL,
                PRIMARY KEY (branch, tick, code)
            ) WITHOUT ROWID;
            CREATE INDEX country_stats_code_tick
                ON country_stats(branch, code, tick);
            """
        )
        self._branch_seq = 0
        self._transaction_depth = 0
        self._finalizer = weakref.finalize(self, _cleanup_store, self.connection, self.path)

    def new_branch(self) -> str:
        branch = f"b{self._branch_seq}"
        self._branch_seq += 1
        return branch

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        outermost = self._transaction_depth == 0
        if outermost:
            self.connection.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield
        except BaseException:
            self._transaction_depth -= 1
            if outermost:
                self.connection.execute("ROLLBACK")
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                self.connection.execute("COMMIT")

    def close(self) -> None:
        self._finalizer()


class EventLog:
    """SQLite-backed authoritative history with list-compatible deterministic ordering.

    Hydrated Event objects are held in a bounded LRU. Deep-copying a World copies only a
    read-only branch boundary, so periodic snapshots share each immutable event exactly once.
    Forks also share their immutable prefix and append a separate suffix in the same store.
    """

    CACHE_LIMIT = 512

    def __init__(self) -> None:
        self._store = _HistoryStore()
        self._segments: tuple[_Segment, ...] = ()
        self._branch: str | None = self._store.new_branch()
        self._branch_start = 0
        self._length = 0
        self._persisted_length = 0
        self._read_only = False
        self._cache: OrderedDict[int, Event] = OrderedDict()
        self._dirty_ids: set[int] = set()

    @classmethod
    def _from_view(
        cls,
        store: _HistoryStore,
        segments: tuple[_Segment, ...],
        *,
        branch: str | None,
        branch_start: int,
        length: int,
        read_only: bool,
    ) -> EventLog:
        log = cls.__new__(cls)
        log._store = store
        log._segments = segments
        log._branch = branch
        log._branch_start = branch_start
        log._length = length
        log._persisted_length = length
        log._read_only = read_only
        log._cache = OrderedDict()
        log._dirty_ids = set()
        return log

    def _all_segments(self) -> tuple[_Segment, ...]:
        if self._branch is None or self._length <= self._branch_start:
            return self._segments
        return self._segments + (_Segment(self._branch, self._branch_start, self._length),)

    def _segments_through(self, count: int) -> tuple[_Segment, ...]:
        if count < 0 or count > self._length:
            raise ValueError(f"event boundary outside log: {count}")
        selected: list[_Segment] = []
        for segment in self._all_segments():
            if segment.start >= count:
                break
            selected.append(_Segment(segment.branch, segment.start, min(segment.end, count)))
            if segment.end >= count:
                break
        return tuple(selected)

    def __deepcopy__(self, memo: dict[int, object]) -> EventLog:
        self.flush()
        copied = self._from_view(
            self._store,
            self._segments_through(self._length),
            branch=None,
            branch_start=self._length,
            length=self._length,
            read_only=True,
        )
        memo[id(self)] = copied
        return copied

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit one simulation operation atomically after mutable events are finalized."""
        if self._read_only:
            raise RuntimeError("cannot transact on a historical EventLog view")
        with self._store.transaction():
            yield
            self.flush()

    def branch(self) -> EventLog:
        """Return a writable branch sharing this view's immutable event prefix."""
        self.flush()
        return self._from_view(
            self._store,
            self._segments_through(self._length),
            branch=self._store.new_branch(),
            branch_start=self._length,
            length=self._length,
            read_only=False,
        )

    def view_through_tick(self, tick: int) -> EventLog:
        """Return a cheap read-only view containing exactly events through ``tick``."""
        self.flush()
        count = self._count_through_tick(tick)
        return self._from_view(
            self._store,
            self._segments_through(count),
            branch=None,
            branch_start=count,
            length=count,
            read_only=True,
        )

    def _count_through_tick(self, tick: int) -> int:
        count = 0
        for segment in self._all_segments():
            row = self._store.connection.execute(
                """SELECT COUNT(*) AS count FROM events
                   WHERE branch=? AND id>=? AND id<? AND tick<=?""",
                (segment.branch, segment.start, segment.end, tick),
            ).fetchone()
            if row is not None:
                count += int(row["count"])
        return count

    def append(
        self,
        *,
        tick: int,
        kind: str,
        country: str | None,
        country2: str | None,
        parent_id: int | None,
        depth: int,
        is_intervention: bool,
        payload: dict[str, float | int | str] | None = None,
        ledger: tuple[LedgerEntry, ...] = (),
        stat_deltas: tuple[StatDelta, ...] = (),
        severity: int = 0,
        causes: tuple[CauseLink, ...] = (),
    ) -> Event:
        """Create, persist, and return the next sequential event."""
        if self._read_only or self._branch is None:
            raise RuntimeError("cannot append to a historical EventLog view")
        next_id = self._length
        if parent_id is not None and (parent_id < 0 or parent_id >= next_id):
            raise ValueError(f"parent id must refer to an earlier event: {parent_id}")
        normalized: dict[int, CauseLink] = {}
        for link in causes:
            if link.event_id < 0 or link.event_id >= next_id:
                raise ValueError(f"cause id must refer to an earlier event: {link.event_id}")
            if link.event_id != parent_id:
                normalized[link.event_id] = link
        ordered_causes = tuple(normalized[event_id] for event_id in sorted(normalized))
        event = Event(
            id=next_id,
            tick=tick,
            kind=kind,
            country=country,
            country2=country2,
            parent_id=parent_id,
            depth=depth,
            is_intervention=is_intervention,
            payload=payload if payload is not None else {},
            ledger=ledger,
            stat_deltas=stat_deltas,
            severity=severity,
            causes=ordered_causes,
            effects=_generic_effects(ledger, stat_deltas),
        )
        self._length += 1
        self._dirty_ids.add(event.id)
        self._cache_event(event)
        return event

    def _event_values(self, event: Event) -> tuple[object, ...]:
        return (
            event.tick,
            event.kind,
            event.country,
            event.country2,
            event.parent_id,
            event.depth,
            int(event.is_intervention),
            _pack(event.payload),
            _pack([asdict(entry) for entry in event.ledger]),
            _pack([asdict(delta) for delta in event.stat_deltas]),
            event.severity,
            _pack([asdict(link) for link in event.causes]),
            _pack([asdict(effect) for effect in event.effects]),
        )

    @staticmethod
    def _country_codes(event: Event) -> set[str]:
        codes = {code for code in (event.country, event.country2) if code}
        for effect in event.effects:
            codes.add(effect.target.split(".", 1)[0])
        return codes

    def _insert_event(self, branch: str, event: Event) -> None:
        self._insert_events(branch, [event])

    def _insert_events(self, branch: str, events: list[Event]) -> None:
        if not events:
            return
        connection = self._store.connection
        connection.executemany(
            """INSERT INTO events
               (branch,id,tick,kind,country,country2,parent_id,depth,is_intervention,
                payload,ledger,stat_deltas,severity,causes,effects)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ((branch, event.id, *self._event_values(event)) for event in events),
        )
        country_rows = [
            (branch, event.id, code)
            for event in events
            for code in sorted(self._country_codes(event))
        ]
        edge_rows = [
            (branch, event.id, parent_id) for event in events for parent_id in event.parent_ids
        ]
        effect_rows = [
            (branch, event.id, ordinal, effect.target, effect.metric, effect.delta)
            for event in events
            for ordinal, effect in enumerate(event.effects)
        ]
        ledger_rows = [
            (branch, event.id, ordinal, event.tick, event.country, entry.kind, entry.amount)
            for event in events
            for ordinal, entry in enumerate(event.ledger)
        ]
        connection.executemany(
            "INSERT INTO event_countries(branch,id,code) VALUES (?,?,?)", country_rows
        )
        connection.executemany(
            "INSERT INTO causal_edges(branch,child_id,parent_id) VALUES (?,?,?)", edge_rows
        )
        connection.executemany(
            """INSERT INTO event_effects(branch,id,ordinal,target,metric,delta)
               VALUES (?,?,?,?,?,?)""",
            effect_rows,
        )
        connection.executemany(
            """INSERT INTO event_ledger
               (branch,id,ordinal,tick,country,kind,amount) VALUES (?,?,?,?,?,?,?)""",
            ledger_rows,
        )

    def _replace_indexes(self, branch: str, event: Event) -> None:
        connection = self._store.connection
        connection.execute(
            "DELETE FROM event_countries WHERE branch=? AND id=?", (branch, event.id)
        )
        connection.executemany(
            "INSERT INTO event_countries(branch,id,code) VALUES (?,?,?)",
            ((branch, event.id, code) for code in sorted(self._country_codes(event))),
        )
        connection.execute(
            "DELETE FROM causal_edges WHERE branch=? AND child_id=?", (branch, event.id)
        )
        connection.executemany(
            "INSERT INTO causal_edges(branch,child_id,parent_id) VALUES (?,?,?)",
            ((branch, event.id, parent_id) for parent_id in event.parent_ids),
        )
        connection.execute("DELETE FROM event_effects WHERE branch=? AND id=?", (branch, event.id))
        connection.executemany(
            """INSERT INTO event_effects(branch,id,ordinal,target,metric,delta)
               VALUES (?,?,?,?,?,?)""",
            (
                (branch, event.id, ordinal, effect.target, effect.metric, effect.delta)
                for ordinal, effect in enumerate(event.effects)
            ),
        )
        connection.execute("DELETE FROM event_ledger WHERE branch=? AND id=?", (branch, event.id))
        connection.executemany(
            """INSERT INTO event_ledger
               (branch,id,ordinal,tick,country,kind,amount) VALUES (?,?,?,?,?,?,?)""",
            (
                (
                    branch,
                    event.id,
                    ordinal,
                    event.tick,
                    event.country,
                    entry.kind,
                    entry.amount,
                )
                for ordinal, entry in enumerate(event.ledger)
            ),
        )

    def _update_event(self, branch: str, event: Event) -> None:
        self._store.connection.execute(
            """UPDATE events SET
               tick=?,kind=?,country=?,country2=?,parent_id=?,depth=?,is_intervention=?,
               payload=?,ledger=?,stat_deltas=?,severity=?,causes=?,effects=?
               WHERE branch=? AND id=?""",
            (*self._event_values(event), branch, event.id),
        )
        self._replace_indexes(branch, event)

    def flush(self) -> None:
        """Batch-persist events after synchronous payload/effect finalization."""
        if not self._dirty_ids or self._branch is None:
            return
        dirty_events = [
            self._cache[event_id] for event_id in sorted(self._dirty_ids) if event_id in self._cache
        ]
        new_events = [event for event in dirty_events if event.id >= self._persisted_length]
        existing_events = [event for event in dirty_events if event.id < self._persisted_length]
        self._insert_events(self._branch, new_events)
        for event in existing_events:
            self._update_event(self._branch, event)
        if new_events:
            self._persisted_length = max(self._persisted_length, new_events[-1].id + 1)
        self._dirty_ids.clear()
        self._trim_cache()

    def _trim_cache(self) -> None:
        if len(self._cache) <= self.CACHE_LIMIT:
            return
        for event_id in list(self._cache):
            if len(self._cache) <= self.CACHE_LIMIT:
                break
            if event_id not in self._dirty_ids:
                del self._cache[event_id]

    def _cache_event(self, event: Event) -> Event:
        self._cache[event.id] = event
        self._cache.move_to_end(event.id)
        self._trim_cache()
        return event

    @staticmethod
    def _decode_rows(raw: object) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], _unpack(raw))

    @classmethod
    def _hydrate(cls, row: sqlite3.Row) -> Event:
        ledger = tuple(
            LedgerEntry(
                kind=cast(Literal["transfer", "mint", "burn"], item["kind"]),
                src_pool=cast(str | None, item["src_pool"]),
                dst_pool=cast(str | None, item["dst_pool"]),
                amount=int(item["amount"]),
                currency=str(item["currency"]),
            )
            for item in cls._decode_rows(row["ledger"])
        )
        stat_deltas = tuple(
            StatDelta(
                target=str(item["target"]),
                stat=str(item["stat"]),
                delta=float(item["delta"]),
                before=cast(float | None, item.get("before")),
                after=cast(float | None, item.get("after")),
            )
            for item in cls._decode_rows(row["stat_deltas"])
        )
        causes = tuple(
            CauseLink(
                event_id=int(item["event_id"]),
                role=cast(Literal["trigger", "contributor", "context"], item["role"]),
                detail=str(item["detail"]),
            )
            for item in cls._decode_rows(row["causes"])
        )
        effects = [
            Effect(
                effect_type=cast(Literal["stat", "money", "structural"], item["effect_type"]),
                target=str(item["target"]),
                metric=str(item["metric"]),
                delta=cast(float | int | None, item.get("delta")),
                before=cast(EffectValue, item.get("before")),
                after=cast(EffectValue, item.get("after")),
                unit=str(item["unit"]),
            )
            for item in cls._decode_rows(row["effects"])
        ]
        return Event(
            id=int(row["id"]),
            tick=int(row["tick"]),
            kind=str(row["kind"]),
            country=cast(str | None, row["country"]),
            country2=cast(str | None, row["country2"]),
            parent_id=cast(int | None, row["parent_id"]),
            depth=int(row["depth"]),
            is_intervention=bool(row["is_intervention"]),
            payload=cast(dict[str, float | int | str], _unpack(row["payload"])),
            ledger=ledger,
            stat_deltas=stat_deltas,
            severity=int(row["severity"]),
            causes=causes,
            effects=effects,
        )

    def _segment_for(self, event_id: int) -> _Segment:
        for segment in self._all_segments():
            if segment.start <= event_id < segment.end:
                return segment
        raise IndexError(event_id)

    def __getitem__(self, event_id: int) -> Event:
        if event_id < 0:
            event_id += self._length
        if event_id < 0 or event_id >= self._length:
            raise IndexError(event_id)
        cached = self._cache.get(event_id)
        if cached is not None:
            self._cache.move_to_end(event_id)
            return cached
        segment = self._segment_for(event_id)
        row = self._store.connection.execute(
            "SELECT * FROM events WHERE branch=? AND id=?", (segment.branch, event_id)
        ).fetchone()
        if row is None:
            raise IndexError(event_id)
        return self._cache_event(self._hydrate(row))

    def __len__(self) -> int:
        return self._length

    def __iter__(self) -> Iterator[Event]:
        self.flush()
        for segment in self._all_segments():
            rows = self._store.connection.execute(
                "SELECT * FROM events WHERE branch=? AND id>=? AND id<? ORDER BY id",
                (segment.branch, segment.start, segment.end),
            )
            for row in rows:
                event_id = int(row["id"])
                cached = self._cache.get(event_id)
                yield cached if cached is not None else self._cache_event(self._hydrate(row))

    def __reversed__(self) -> Iterator[Event]:
        self.flush()
        for segment in reversed(self._all_segments()):
            rows = self._store.connection.execute(
                "SELECT * FROM events WHERE branch=? AND id>=? AND id<? ORDER BY id DESC",
                (segment.branch, segment.start, segment.end),
            )
            for row in rows:
                event_id = int(row["id"])
                cached = self._cache.get(event_id)
                yield cached if cached is not None else self._cache_event(self._hydrate(row))

    def truncate_after(self, tick: int) -> None:
        """Drop this writable branch's events strictly after ``tick`` and reuse their ids."""
        if self._read_only or self._branch is None:
            raise RuntimeError("cannot truncate a historical EventLog view")
        self.flush()
        count = self._count_through_tick(tick)
        if count < self._branch_start:
            raise ValueError("cannot truncate before this branch's immutable fork point")
        self._store.connection.execute(
            "DELETE FROM events WHERE branch=? AND id>=?", (self._branch, count)
        )
        self._length = count
        self._persisted_length = count
        self._cache = OrderedDict(
            (event_id, event) for event_id, event in self._cache.items() if event_id < count
        )
        self._dirty_ids = {event_id for event_id in self._dirty_ids if event_id < count}

    def extend(self, events: Iterable[Event]) -> None:
        """Append finalized preconstructed events with their existing sequential ids."""
        if self._read_only or self._branch is None:
            raise RuntimeError("cannot extend a historical EventLog view")
        with self.transaction():
            for event in events:
                if event.id != self._length:
                    raise ValueError(
                        f"extended event id {event.id} does not follow log length {self._length}"
                    )
                self._insert_event(self._branch, event)
                self._length += 1
                self._persisted_length = self._length
                self._cache_event(event)

    def latest_event(self, *, tick: int, country: str, kind: str) -> Event | None:
        """Return the newest matching event without hydrating unrelated history."""
        self.flush()
        newest_id: int | None = None
        for segment in self._all_segments():
            row = self._store.connection.execute(
                """SELECT id FROM events
                   WHERE branch=? AND id>=? AND id<? AND tick=? AND country=? AND kind=?
                   ORDER BY id DESC LIMIT 1""",
                (segment.branch, segment.start, segment.end, tick, country, kind),
            ).fetchone()
            if row is not None:
                candidate = int(row["id"])
                newest_id = candidate if newest_id is None else max(newest_id, candidate)
        return self[newest_id] if newest_id is not None else None

    def minted_amount(self, *, tick: int, country: str) -> int:
        """Sum mint ledger entries for one country's events at one tick in SQL."""
        self.flush()
        total = 0
        for segment in self._all_segments():
            row = self._store.connection.execute(
                """SELECT COALESCE(SUM(amount), 0) AS amount FROM event_ledger
                   WHERE branch=? AND id>=? AND id<?
                     AND tick=? AND country=? AND kind='mint'""",
                (segment.branch, segment.start, segment.end, tick, country),
            ).fetchone()
            if row is not None:
                total += int(row["amount"])
        return total

    def recent_effect_events(
        self,
        *,
        target: str,
        metrics: tuple[str, ...],
        start_tick: int | None,
        direction: int | None,
        limit: int,
        exclude_ids: frozenset[int] = frozenset(),
    ) -> tuple[Event, ...]:
        """Return newest distinct events matching normalized effects via SQLite indexes."""
        self.flush()
        if not metrics or limit <= 0:
            return ()
        placeholders = ",".join("?" for _ in metrics)
        candidates: set[int] = set()
        for segment in self._all_segments():
            clauses = [
                f"x.target=? AND x.metric IN ({placeholders})",
                "x.branch=?",
                "x.id>=?",
                "x.id<?",
            ]
            parameters: list[object] = [
                target,
                *metrics,
                segment.branch,
                segment.start,
                segment.end,
            ]
            if start_tick is not None:
                clauses.append("e.tick>=?")
                parameters.append(start_tick)
            if direction is not None:
                clauses.append("x.delta>0" if direction > 0 else "x.delta<0")
            # Written from event_effects so the planner starts at the (target, metric) index.
            # Starting from events, it walked that table's primary key newest-first and probed
            # each event for a matching effect: a lookup with no start_tick for a pair with few
            # matches read the whole history, 3 s at t362 of seed 1337 for 364 matching rows.
            # Same predicate and order, so the same ids come back. Not pinned with INDEXED BY:
            # measured at t1000, forcing it made two-metric threshold lookups ~30x slower.
            rows = self._store.connection.execute(
                "SELECT DISTINCT x.id FROM event_effects x "
                "JOIN events e ON e.branch=x.branch AND e.id=x.id WHERE "
                + " AND ".join(clauses)
                + " ORDER BY x.id DESC LIMIT ?",
                (*parameters, limit + len(exclude_ids)),
            )
            candidates.update(int(row["id"]) for row in rows)
        selected = [
            event_id for event_id in sorted(candidates, reverse=True) if event_id not in exclude_ids
        ]
        return tuple(self[event_id] for event_id in selected[:limit])

    def events_between(self, start_tick: int, end_tick: int) -> Iterator[Event]:
        """Stream events in deterministic id order for ``start_tick < tick <= end_tick``."""
        self.flush()
        hint = _tick_window_hint(start_tick, end_tick)
        for segment in self._all_segments():
            rows = self._store.connection.execute(
                f"""SELECT * FROM events {hint}
                   WHERE branch=? AND id>=? AND id<? AND tick>? AND tick<=?
                   ORDER BY id""",
                (segment.branch, segment.start, segment.end, start_tick, end_tick),
            )
            for row in rows:
                event_id = int(row["id"])
                cached = self._cache.get(event_id)
                yield cached if cached is not None else self._cache_event(self._hydrate(row))

    def events_from_id_through_tick(self, start_id: int, end_tick: int) -> Iterator[Event]:
        """Stream events with ``id >= start_id`` and ``tick <= end_tick`` in id order.

        Replay starts from a snapshot's event boundary rather than its tick: commands run
        between ticks and are stamped with the current tick, so an event can share a tick
        with a snapshot that was taken before it was appended."""
        self.flush()
        for segment in self._all_segments():
            if segment.end <= start_id:
                continue
            rows = self._store.connection.execute(
                """SELECT * FROM events
                   WHERE branch=? AND id>=? AND id<? AND tick<=?
                   ORDER BY id""",
                (segment.branch, max(segment.start, start_id), segment.end, end_tick),
            )
            for row in rows:
                event_id = int(row["id"])
                cached = self._cache.get(event_id)
                yield cached if cached is not None else self._cache_event(self._hydrate(row))

    def recent_events_of_kinds(
        self, kinds: tuple[str, ...], *, limit: int
    ) -> tuple[Event, ...]:
        """Return the newest matching events without hydrating unrelated history."""
        self.flush()
        if not kinds or limit <= 0:
            return ()
        placeholders = ",".join("?" for _ in kinds)
        candidates: set[int] = set()
        for segment in self._all_segments():
            rows = self._store.connection.execute(
                f"""SELECT id FROM events
                    WHERE branch=? AND id>=? AND id<?
                      AND kind IN ({placeholders})
                    ORDER BY id DESC LIMIT ?""",
                (segment.branch, segment.start, segment.end, *kinds, limit),
            )
            candidates.update(int(row["id"]) for row in rows)
        selected = sorted(candidates, reverse=True)[:limit]
        return tuple(self[event_id] for event_id in reversed(selected))

    def events_of_kinds_between(
        self,
        kinds: tuple[str, ...],
        start_tick: int,
        end_tick: int,
    ) -> tuple[Event, ...]:
        """Return matching events in id order for ``start_tick < tick <= end_tick``."""
        self.flush()
        if not kinds or end_tick <= start_tick:
            return ()
        placeholders = ",".join("?" for _ in kinds)
        hint = _tick_window_hint(start_tick, end_tick)
        events: list[Event] = []
        for segment in self._all_segments():
            rows = self._store.connection.execute(
                f"""SELECT * FROM events {hint}
                    WHERE branch=? AND id>=? AND id<? AND tick>? AND tick<=?
                      AND kind IN ({placeholders})
                    ORDER BY id""",
                (
                    segment.branch,
                    segment.start,
                    segment.end,
                    start_tick,
                    end_tick,
                    *kinds,
                ),
            )
            for row in rows:
                event_id = int(row["id"])
                cached = self._cache.get(event_id)
                events.append(
                    cached if cached is not None else self._cache_event(self._hydrate(row))
                )
        return tuple(events)

    def tail(self, limit: int) -> tuple[Event, ...]:
        """Return at most the final ``limit`` events in deterministic id order."""
        if limit <= 0:
            return ()
        start = max(0, self._length - limit)
        return tuple(self[event_id] for event_id in range(start, self._length))

    def country_events(
        self,
        code: str,
        start_tick: int,
        end_tick: int,
        *,
        limit: int,
    ) -> tuple[list[Event], int]:
        """Return strongest country-involving events in a tick interval and exact total."""
        self.flush()
        if limit < 0:
            raise ValueError("country event limit must be non-negative")
        selects: list[str] = []
        parameters: list[object] = []
        for segment in self._all_segments():
            selects.append(
                """SELECT e.* FROM events e
                   JOIN event_countries c ON c.branch=e.branch AND c.id=e.id
                   WHERE e.branch=? AND e.id>=? AND e.id<? AND c.code=?
                     AND e.tick>? AND e.tick<=?"""
            )
            parameters.extend(
                (segment.branch, segment.start, segment.end, code, start_tick, end_tick)
            )
        if not selects:
            return [], 0
        union = " UNION ALL ".join(selects)
        count_row = self._store.connection.execute(
            f"SELECT COUNT(*) AS count FROM ({union})", parameters
        ).fetchone()
        total = int(count_row["count"]) if count_row is not None else 0
        rows = self._store.connection.execute(
            f"SELECT * FROM ({union}) ORDER BY severity DESC, tick DESC, id DESC LIMIT ?",
            (*parameters, limit),
        )
        events: list[Event] = []
        for row in rows:
            event_id = int(row["id"])
            cached = self._cache.get(event_id)
            events.append(cached if cached is not None else self._cache_event(self._hydrate(row)))
        return events, total

    def children_of(self, event_id: int) -> tuple[int, ...]:
        """Return direct causal children visible in this branch view."""
        self.flush()
        children: list[int] = []
        for segment in self._all_segments():
            rows = self._store.connection.execute(
                """SELECT child_id FROM causal_edges
                   WHERE branch=? AND child_id>=? AND child_id<? AND parent_id=?
                   ORDER BY child_id""",
                (segment.branch, segment.start, segment.end, event_id),
            )
            children.extend(int(row["child_id"]) for row in rows)
        return tuple(children)

    def _history_branches(self) -> tuple[str, ...]:
        branches: list[str] = []
        for segment in self._segments:
            if segment.branch not in branches:
                branches.append(segment.branch)
        if self._branch is not None and self._branch not in branches:
            branches.append(self._branch)
        return tuple(branches)

    def record_rng_state(self, tick: int, state: tuple[Any, ...]) -> None:
        if self._read_only or self._branch is None:
            raise RuntimeError("cannot record RNG state on a historical EventLog view")
        self._store.connection.execute(
            "INSERT OR REPLACE INTO rng_states(branch,tick,state) VALUES (?,?,?)",
            (self._branch, tick, _pack(state)),
        )

    def record_aux_state(self, tick: int, boundary: int, state: dict[str, Any]) -> None:
        """Persist the end-of-tick state that no event records (see Timeline._aux_state)."""
        if self._read_only or self._branch is None:
            raise RuntimeError("cannot record auxiliary state on a historical EventLog view")
        self._store.connection.execute(
            "INSERT OR REPLACE INTO aux_states(branch,tick,boundary,state) VALUES (?,?,?,?)",
            (self._branch, tick, boundary, _pack(state)),
        )

    def aux_state(self, tick: int) -> tuple[int, dict[str, Any]] | None:
        """(event boundary, state) recorded at the end of ``tick``, newest branch first."""
        for branch in reversed(self._history_branches()):
            row = self._store.connection.execute(
                "SELECT boundary, state FROM aux_states WHERE branch=? AND tick=?", (branch, tick)
            ).fetchone()
            if row is not None:
                return int(row["boundary"]), cast(dict[str, Any], _unpack(row["state"]))
        return None

    @staticmethod
    def _tuple_state(value: object) -> object:
        if isinstance(value, list):
            return tuple(EventLog._tuple_state(item) for item in value)
        return value

    def rng_state(self, tick: int) -> tuple[Any, ...]:
        for branch in reversed(self._history_branches()):
            row = self._store.connection.execute(
                "SELECT state FROM rng_states WHERE branch=? AND tick=?", (branch, tick)
            ).fetchone()
            if row is not None:
                return cast(tuple[Any, ...], self._tuple_state(_unpack(row["state"])))
        raise KeyError(tick)

    def has_rng_state(self, tick: int) -> bool:
        try:
            self.rng_state(tick)
        except KeyError:
            return False
        return True

    def rng_ticks(self) -> tuple[int, ...]:
        ticks: set[int] = set()
        for branch in self._history_branches():
            rows = self._store.connection.execute(
                "SELECT tick FROM rng_states WHERE branch=? ORDER BY tick", (branch,)
            )
            ticks.update(int(row["tick"]) for row in rows)
        return tuple(sorted(ticks))

    def record_country_stats(self, tick: int, stats: dict[str, dict[str, float]]) -> None:
        if self._read_only or self._branch is None:
            raise RuntimeError("cannot record statistics on a historical EventLog view")
        connection = self._store.connection
        connection.execute("DELETE FROM country_stats WHERE branch=? AND tick=?", (self._branch, tick))
        connection.executemany(
            """INSERT INTO country_stats
               (branch,tick,code,stability,inflation,gdp,fx,treasury)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                (
                    self._branch,
                    tick,
                    code,
                    values["stability"],
                    values["inflation"],
                    values["gdp"],
                    values["fx"],
                    values["treasury"],
                )
                for code, values in sorted(stats.items())
            ),
        )

    def stat_ticks(self) -> tuple[int, ...]:
        ticks: set[int] = set()
        for branch in self._history_branches():
            rows = self._store.connection.execute(
                "SELECT DISTINCT tick FROM country_stats WHERE branch=? ORDER BY tick", (branch,)
            )
            ticks.update(int(row["tick"]) for row in rows)
        return tuple(sorted(ticks))

    def stats_at(self, tick: int) -> dict[str, dict[str, float]]:
        for branch in reversed(self._history_branches()):
            rows = list(
                self._store.connection.execute(
                    """SELECT code,stability,inflation,gdp,fx,treasury FROM country_stats
                       WHERE branch=? AND tick=? ORDER BY code""",
                    (branch, tick),
                )
            )
            if rows:
                return {
                    str(row["code"]): {
                        "stability": float(row["stability"]),
                        "inflation": float(row["inflation"]),
                        "gdp": float(row["gdp"]),
                        "fx": float(row["fx"]),
                        "treasury": float(row["treasury"]),
                    }
                    for row in rows
                }
        raise KeyError(tick)

    def truncate_tick_history_after(self, tick: int) -> None:
        if self._read_only or self._branch is None:
            raise RuntimeError("cannot truncate historical observations on a view")
        self._store.connection.execute(
            "DELETE FROM rng_states WHERE branch=? AND tick>?", (self._branch, tick)
        )
        self._store.connection.execute(
            "DELETE FROM aux_states WHERE branch=? AND tick>?", (self._branch, tick)
        )
        self._store.connection.execute(
            "DELETE FROM country_stats WHERE branch=? AND tick>?", (self._branch, tick)
        )

    @property
    def resident_event_count(self) -> int:
        """Number of hydrated Event objects retained by this view (test/observability hook)."""
        return len(self._cache)

    @property
    def database_path(self) -> str:
        """Backing file path (test/observability hook; lifecycle remains store-owned)."""
        return self._store.path

    def discard_writable_suffix(self) -> None:
        """Delete this branch's private rows when its timeline is discarded."""
        if self._branch is None:
            return
        self.flush()
        self._store.connection.execute("DELETE FROM events WHERE branch=?", (self._branch,))
        self._store.connection.execute("DELETE FROM rng_states WHERE branch=?", (self._branch,))
        self._store.connection.execute("DELETE FROM aux_states WHERE branch=?", (self._branch,))
        self._store.connection.execute("DELETE FROM country_stats WHERE branch=?", (self._branch,))
        self._cache.clear()
        self._dirty_ids.clear()
        self._length = self._branch_start
        self._persisted_length = self._branch_start

    def prune_inherited_futures(self) -> None:
        """Delete inaccessible parent suffixes after this branch is adopted.

        Call only after sibling timelines and the old parent timeline are no longer live.
        SQLite may retain free pages in the file, but subsequent history reuses them.
        """
        self.flush()
        for segment in self._segments:
            row = self._store.connection.execute(
                "SELECT MAX(tick) AS tick FROM events WHERE branch=? AND id<?",
                (segment.branch, segment.end),
            ).fetchone()
            end_tick = int(row["tick"]) if row is not None and row["tick"] is not None else -1
            self._store.connection.execute(
                "DELETE FROM rng_states WHERE branch=? AND tick>?", (segment.branch, end_tick)
            )
            self._store.connection.execute(
                "DELETE FROM aux_states WHERE branch=? AND tick>?", (segment.branch, end_tick)
            )
            self._store.connection.execute(
                "DELETE FROM country_stats WHERE branch=? AND tick>?", (segment.branch, end_tick)
            )
            self._store.connection.execute(
                "DELETE FROM events WHERE branch=? AND id>=?", (segment.branch, segment.end)
            )

    def close(self) -> None:
        """Close the shared SQLite store and remove its ephemeral files."""
        self._store.close()
