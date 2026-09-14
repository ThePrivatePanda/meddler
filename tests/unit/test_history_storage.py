"""Regression coverage for disk-backed, single-copy deterministic history."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from meddler.bridge.server import new_session


def test_periodic_snapshots_share_one_history_store_without_hydrated_events() -> None:
    session = new_session(1337)
    timeline = session.multiverse.prime
    for _ in range(100):
        timeline.advance()

    live_log = timeline.world.log
    snapshot_logs = [snapshot.log for snapshot in timeline.snapshots.values()]
    assert len(snapshot_logs) == 3  # genesis, t50, t100
    assert {log.database_path for log in snapshot_logs} == {live_log.database_path}
    # Pinned counts, not claims: they are this seed's event totals at the snapshot
    # boundaries, here so a behaviour change gets noticed. The assertions around them --
    # one shared database, no hydrated events, a bounded live cache -- are the subject.
    assert [len(log) for log in snapshot_logs] == [0, 5863, 12045]
    assert all(log.resident_event_count == 0 for log in snapshot_logs)
    assert live_log.resident_event_count <= live_log.CACHE_LIMIT

    path = live_log.database_path
    session.close()
    assert not any(os.path.exists(candidate) for candidate in (path, f"{path}-wal", f"{path}-shm"))


def test_seed_1337_history_rss_is_bounded_at_200_ticks() -> None:
    script = r"""
import gc
import json
import os
from meddler.bridge.server import new_session

page = os.sysconf('SC_PAGE_SIZE')
session = new_session(1337)
timeline = session.multiverse.prime
for _ in range(200):
    timeline.advance()
gc.collect()
with open('/proc/self/statm', encoding='ascii') as stream:
    rss = int(stream.read().split()[1]) * page
print(json.dumps({
    'rssMiB': rss / 1024 / 1024,
    'events': len(timeline.world.log),
    'snapshots': len(timeline.snapshots),
    'residentEvents': timeline.world.log.resident_event_count,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    measurement = json.loads(completed.stdout)
    # A pinned count, as above. The three assertions below it are the architectural
    # claims: 5 snapshots, the resident-event cache bound, and RSS under the 140 MiB
    # ceiling. Re-pinning the count after a behaviour change leaves them untouched.
    assert measurement["events"] == 24347
    assert measurement["snapshots"] == 5
    assert measurement["residentEvents"] <= 512
    # Verified pre-migration baseline was 268.4 MiB at t200. Keep generous CI headroom
    # while still catching cumulative Event copies returning to periodic snapshots.
    assert measurement["rssMiB"] < 140.0
