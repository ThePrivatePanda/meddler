"""Per-connection bridge state. docs/frontend-contract.md.

Server-runtime session state.

The engine Multiverse and SQLite history belong to ``meddler serve`` for the process
lifetime. Browser WebSockets attach and detach as views; focus, scrub, speed, forks, and
settings therefore survive reloads. True persistence across process restarts remains a
separate save/load concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from meddler.engine.model import WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.timeline import Multiverse, Timeline
from meddler.engine.worldgen import generate_world


@dataclass
class ForkMeta:
    label: str
    fork_tick: int  # the PRIME tick this fork branched from


@dataclass
class Session:
    seed: int
    multiverse: Multiverse
    running: bool = False
    tps: float = 1.0
    focused_fork_id: str | None = None  # one of Multiverse.forks' keys, or None
    scrub_tick: int | None = None  # None = live; else a read-only worldAt view (prime only)
    pre_scrub_running: bool = False  # restored by resumeLive
    fork_meta: dict[str, ForkMeta] = field(default_factory=dict)

    def focused_fork(self) -> Timeline | None:
        if self.focused_fork_id is None:
            return None
        return self.multiverse.forks.get(self.focused_fork_id)

    def close(self) -> None:
        """Close and remove this session's shared ephemeral SQLite history store."""
        self.multiverse.prime.world.log.close()


def new_session(seed: int, settings: WorldSettings | None = None) -> Session:
    """Genesis: the world a runtime starts from, and the world `newWorld` replaces it with.

    Forks share prime's SQLite store, so every timeline in a session is born here."""
    world = generate_world(seed, settings if settings is not None else WorldSettings())
    timeline = Timeline(seed=seed, world=world, snapshots={}, rng=Rng(seed))
    return Session(seed=seed, multiverse=Multiverse(prime=timeline))
