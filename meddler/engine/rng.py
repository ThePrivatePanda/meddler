"""The single deterministic random stream. PROPOSAL §4.3.

engine/ and text/ must never call random.random(), time.time(), uuid4(),
os.urandom(), or any other nondeterministic source. Every draw in the
simulation goes through an Rng instance threaded explicitly through tick().
"""

from __future__ import annotations

import hashlib
import random
from typing import Any


def derive_seed(*parts: Any) -> int:
    """Deterministic integer hash, stable across processes and machines.

    CPython salts str/bytes hashing per process (PYTHONHASHSEED) unless it is
    pinned, so a tuple containing strings fed to the builtin hash() produces a
    different int on every run. That would silently break fork/restart reseed
    reproducibility (PROPOSAL §4.3.5/§4.3.6) the moment any fork/restart output
    is compared across processes or machines (success criterion §2.3.2) — this
    routes seed derivation through SHA-256 instead so it is process-independent.
    Deviation from the literal `hash((...))` skeleton in the implementation
    spec; recorded in docs/design-decisions.md.
    """
    digest = hashlib.sha256(repr(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class Rng:
    """The single deterministic random stream. Thread one instance explicitly through tick()."""

    def __init__(self, seed: int) -> None:
        self._r = random.Random(seed)
        self._seed = seed

    def roll(self, p: float) -> bool:
        """True with probability p. Consumes one draw."""
        return self._r.random() < p

    def uniform(self, lo: float, hi: float) -> float:
        return self._r.uniform(lo, hi)

    def randint(self, lo: int, hi: int) -> int:
        """Inclusive on both ends (like random.randint)."""
        return self._r.randint(lo, hi)

    def choice(self, seq: list[Any]) -> Any:
        """seq MUST be an ordered list, never a set/dict."""
        return self._r.choice(seq)

    def sub(self, seed_key: Any) -> "Rng":
        """Derive a child stream deterministically. General-purpose; restart uses
        derive_seed() directly at the call site to match PROPOSAL §4.3.6's exact
        flat-tuple recipe. Fork no longer reseeds -- see get_state()/from_state()
        and docs/design-decisions.md ("Fork RNG semantics resolved")."""
        return Rng(seed=derive_seed(self._seed, seed_key))

    def get_state(self) -> tuple[Any, ...]:
        """Snapshot the exact internal PRNG state. Used to mirror prime's dice when
        forking (§4.3.5, resolved in docs/design-decisions.md) -- NOT a seed, the full
        Mersenne Twister state, so a clone continues the identical draw sequence."""
        return self._r.getstate()

    @classmethod
    def from_state(cls, seed: int, state: tuple[Any, ...]) -> "Rng":
        """Reconstruct an independent Rng whose next draw continues exactly from a
        prior get_state() snapshot. `seed` is retained only for bookkeeping (e.g. a
        later .sub() call); it does not reseed the stream -- `state` fully determines
        future draws."""
        rng = cls(seed=seed)
        rng._r.setstate(state)
        return rng
