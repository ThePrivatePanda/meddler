"""M7.1 acceptance: the full §6.6 catalog actually fires. PROPOSAL §6.6, implementation-
spec.md's M7.1 verify: "a 5000-tick x 20-seed headless run produces >=1 of every kind not
flagged low_frequency_ok."

Runs the 20 seeds in parallel via ProcessPoolExecutor, same rationale as
tests/unit/test_cascade.py's own docstring (a single seed's 5000-tick log is large; this
keeps wall-clock down and each worker's memory flat). Cached per-process like
test_cascade.py so multiple assertions share one run.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

import pytest

from meddler.engine import tickloop
from meddler.engine.model import WorldSettings
from meddler.engine.registry import EVENT_REGISTRY
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world

# Heavy multi-seed run; excluded from the quick local subset via -m "not slow".
pytestmark = pytest.mark.slow

TICKS = 5000
SEEDS = 20
COUNTRIES = 4


def _run_seed(seed: int) -> Counter:
    # drama_multiplier=1.0 (NOT the calm shipped default of 0.4, set in the 2026-07-22 pacing
    # pass). This test measures REACHABILITY -- "does the catalog have any dead kinds?" -- which
    # is a property of the mechanics at normal drama, the regime the test was written for. The
    # calm default is a viewing-experience choice, not a claim that a rare kind can never fire;
    # conflating the two would make lowering the default drama look like a broken catalog. Deep
    # consequence chains (e.g. PORT_CLOSURE/TRADE_HALT off military/embargo roots) need full
    # drama to surface within the seed/tick budget.
    settings = WorldSettings(starting_country_count=COUNTRIES, drama_multiplier=1.0)
    world = generate_world(seed, settings)
    rng = Rng(seed)
    for _ in range(TICKS):
        tickloop.tick(world, rng)
    return Counter(e.kind for e in world.log)


@dataclass
class _Coverage:
    kind_counts: Counter


_CACHE: _Coverage | None = None


def _coverage() -> _Coverage:
    """Runs all SEEDS in parallel, printing one progress line per seed as it finishes
    (this run is heavy enough on a contended host that silent multi-minute waits are
    unhelpful -- flush eagerly since pytest -q captures stdout by default and only
    releases it on failure/with -s, but -v/CI logs still benefit from a visible trail)."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    start = time.monotonic()
    total: Counter = Counter()
    done = 0
    with ProcessPoolExecutor() as pool:
        futures = {pool.submit(_run_seed, seed): seed for seed in range(SEEDS)}
        for future in as_completed(futures):
            seed = futures[future]
            total.update(future.result())
            done += 1
            elapsed = time.monotonic() - start
            print(
                f"[test_catalog_coverage] seed {seed} done ({done}/{SEEDS}), "
                f"{elapsed:.0f}s elapsed",
                file=sys.stderr,
                flush=True,
            )
    _CACHE = _Coverage(kind_counts=total)
    return _CACHE


def test_every_non_low_frequency_kind_fires_at_least_once():
    """Interventions (is_intervention=True) are excluded: they only ever fire via
    god-mode calls (bridge/god.py), never organically from a headless tickloop run --
    that's categorical, not a rarity the low_frequency_ok flag is meant to paper over."""
    counts = _coverage().kind_counts
    required = sorted(
        k
        for k, spec in EVENT_REGISTRY.items()
        if not spec.low_frequency_ok and not spec.is_intervention
    )
    missing = [k for k in required if counts.get(k, 0) == 0]
    assert not missing, f"kinds never fired across {SEEDS} seeds x {TICKS} ticks: {missing}"


def test_low_frequency_kinds_are_a_real_minority():
    """Sanity check on the low_frequency_ok flag itself: it should mark genuinely rare/
    gated kinds, not become an escape hatch for a large chunk of the catalog."""
    total = len(EVENT_REGISTRY)
    flagged = sum(1 for spec in EVENT_REGISTRY.values() if spec.low_frequency_ok)
    assert flagged < total * 0.4, (
        f"{flagged}/{total} kinds flagged low_frequency_ok -- coverage test may be too weak"
    )
