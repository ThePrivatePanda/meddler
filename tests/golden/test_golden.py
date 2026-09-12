"""Golden master (§8.1): `meddler run --headless --seed 1337 --ticks 1000` must equal
tests/golden/seed1337_1000t.txt byte-for-byte. This is the determinism backstop -- any
change to worldgen, systems, RNG order, or templates that shifts output fails here.

Regenerate the golden file ONLY when a task explicitly changes simulation behavior
(implementation-spec M3.3), then eyeball the diff.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

from meddler.cli import main

GOLDEN_PATH = Path(__file__).parent / "seed1337_1000t.txt"


def _capture_run() -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        main(["run", "--headless", "--seed", "1337", "--ticks", "1000"])
    return buffer.getvalue()


def test_golden_master_matches() -> None:
    expected = GOLDEN_PATH.read_text(encoding="utf-8")
    actual = _capture_run()
    assert actual == expected, "headless output drifted from the committed golden master"


def test_golden_run_is_deterministic() -> None:
    # Two in-process runs must be byte-identical (no RNG/wall-clock leak in text/ or cli).
    assert _capture_run() == _capture_run()
