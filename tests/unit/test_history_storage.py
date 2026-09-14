"""Regression coverage for disk-backed, single-copy deterministic history."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from meddler.bridge.server import new_session
from meddler.engine.events import EventLog


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


_OPEN_STORE = r"""
import os
import signal
import sys
from meddler.engine.events import EventLog

log = EventLog()
print(log.database_path, flush=True)
if sys.argv[1] == "kill":
    os.kill(os.getpid(), signal.SIGKILL)
sys.stdin.readline()
"""


def _store_files(path: str) -> list[str]:
    candidates = (path, f"{path}-wal", f"{path}-shm")
    return [candidate for candidate in candidates if os.path.exists(candidate)]


def test_new_store_removes_stores_orphaned_by_killed_processes_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finalizer cannot run under SIGKILL, so a killed run's store must be reclaimable by
    the next one -- while a store whose process is still running is never touched."""
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    killed = subprocess.run(
        [sys.executable, "-c", _OPEN_STORE, "kill"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert killed.returncode == -signal.SIGKILL
    orphan = killed.stdout.strip()
    assert os.path.dirname(orphan) == str(tmp_path)
    assert len(_store_files(orphan)) == 3

    live = subprocess.Popen(
        [sys.executable, "-c", _OPEN_STORE, "wait"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert live.stdout is not None
        live_path = live.stdout.readline().strip()
        assert len(_store_files(live_path)) == 3

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        log = EventLog()
        try:
            assert os.path.dirname(log.database_path) == str(tmp_path)
            assert _store_files(orphan) == []
            assert len(_store_files(live_path)) == 3
            assert len(_store_files(log.database_path)) == 3
        finally:
            log.close()
    finally:
        # Let the live child exit normally so its own finalizer removes its store.
        assert live.stdin is not None
        live.stdin.close()
        try:
            live.wait(timeout=60)
        except subprocess.TimeoutExpired:
            live.kill()
            live.wait(timeout=60)
