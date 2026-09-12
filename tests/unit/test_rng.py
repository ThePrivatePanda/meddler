import os
import subprocess
import sys

from meddler.engine.rng import Rng


def _draw_sequence(rng: Rng) -> list[object]:
    return [
        rng.roll(0.5),
        rng.uniform(0.0, 100.0),
        rng.randint(0, 1000),
        rng.choice(["a", "b", "c", "d"]),
    ]


def test_same_seed_produces_identical_sequence():
    a = Rng(42)
    b = Rng(42)
    assert _draw_sequence(a) == _draw_sequence(b)


def test_different_seed_diverges():
    a = Rng(42)
    b = Rng(43)
    assert _draw_sequence(a) != _draw_sequence(b)


def test_sub_is_deterministic_for_same_key():
    a = Rng(42).sub("fork-1")
    b = Rng(42).sub("fork-1")
    assert _draw_sequence(a) == _draw_sequence(b)


def test_sub_diverges_for_different_keys():
    a = Rng(42).sub("fork-1")
    b = Rng(42).sub("fork-2")
    assert _draw_sequence(a) != _draw_sequence(b)


def test_sub_matches_tuple_style_recipe():
    # PROPOSAL §4.3.5/§4.3.6 fork/restart recipes pass a composite key.
    a = Rng(1337).sub((371, "fork", "B"))
    b = Rng(1337).sub((371, "fork", "B"))
    assert _draw_sequence(a) == _draw_sequence(b)


def test_sub_is_stable_across_process_hash_seeds():
    # Regression test for the PYTHONHASHSEED trap: builtin hash() on a tuple
    # containing a string is salted per-process unless pinned, which would
    # silently break fork/restart reseed reproducibility across machines.
    code = (
        "from meddler.engine.rng import Rng; "
        "r = Rng(1337).sub((371, 'fork', 'B')); "
        "print(r.randint(0, 10**9))"
    )
    env_random = {**os.environ, "PYTHONHASHSEED": "random"}
    env_zero = {**os.environ, "PYTHONHASHSEED": "0"}
    out1 = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env_random, check=True
    )
    out2 = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env_zero, check=True
    )
    assert out1.stdout.strip() == out2.stdout.strip()
