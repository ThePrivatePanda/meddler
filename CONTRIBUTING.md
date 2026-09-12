# Contributing to Meddler

Thanks for your interest. Bug reports, reproducible weird worlds, and focused pull requests are
all welcome. Before investing in a large change, please open an issue to talk it through: the
engine has a few hard guarantees (below) that shape what a good change looks like.

Meddler is free software released under the [GNU Affero General Public License, version 3 or
later](LICENSE). By contributing you agree that your contribution is licensed under
AGPL-3.0-or-later.

You also grant Parth Mittal a perpetual, worldwide, irrevocable right to license your contribution
under other terms, including commercially. You keep the copyright in your own work, and nothing
here narrows the AGPL rights everyone else receives — this exists only so the project can be
offered under a separate licence in future without tracking down every past contributor. If you
would rather not grant it, say so in the pull request and we will find another way.

## Development setup

You need Python 3.11 or newer and, for the JavaScript syntax check, Node.js 20 or newer.

```sh
git clone https://github.com/ThePrivatePanda/meddler.git
cd meddler
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
meddler serve --open               # UI + engine on http://127.0.0.1:7677/
```

With an editable install the server serves `web/` straight from your checkout, so frontend edits
show up on a browser reload. To serve a different copy of the frontend, set `MEDDLER_WEB_DIR`.

## Running the tests

The full suite includes a few long multi-seed simulations (the event-catalog coverage and
cascade tests run thousands of ticks across many seeds). They are marked `slow`.

```sh
pytest -m "not slow"                # fast local loop: everything except the slow tests
pytest tests/unit/test_trade.py     # just the area you touched
pytest                              # the full suite, the same as CI
pytest -n auto                      # the full suite across all cores (pytest-xdist)
```

CI runs everything except the slow tests on Python 3.11, 3.12 and 3.13, and the slow tests once
in a separate job. If you only
changed one system, running that system's tests plus the golden master locally is usually enough;
CI will catch the rest.

Also run the static checks before opening a pull request:

```sh
ruff check .
mypy meddler/engine meddler/bridge meddler/cli.py   # the engine is type-checked strictly
lint-imports                                        # engine <- text <- bridge layering
for f in web/*.js; do node --check "$f"; done
```

## Determinism and the golden master

Determinism is the engine's core promise: the same seed must produce a byte-identical event log
on every run. Scrubbing, forking and replay all depend on it. CI checks the golden master on
Linux with Python 3.11, 3.12 and 3.13. (World generation uses the platform's trig functions, so
another OS's math library could in principle differ in the last bit; see
[`docs/design-decisions.md`](docs/design-decisions.md).)

- All randomness goes through the engine's seeded RNG (`meddler/engine/rng.py`). Never use
  `random`, `time`, `uuid`, hashing of objects, or iteration order of sets in anything that can
  influence the simulation.
- Every state change must be recorded as a ledger or stat delta on an event, so a past tick can be
  reconstructed without re-running systems.
- `tests/golden/seed1337_1000t.txt` pins the output of
  `meddler run --headless --seed 1337 --ticks 1000`. CI also checks it against an installed wheel
  under different hash seeds.

If your change is **not** meant to alter simulation behavior (a refactor, UI work, tooling), the
golden test must stay green without touching the file. If it goes red, you have changed behavior.

If your change **is** meant to alter behavior, regenerate the golden file in the same pull
request and explain why in the description:

```sh
meddler run --headless --seed 1337 --ticks 1000 > tests/golden/seed1337_1000t.txt
```

Before committing it, run the command twice and confirm the outputs are identical, then read the
diff: headlines should still be coherent, with no `None`, `null` or unrendered template tokens.

## Architecture rules

Three layers, and imports only point downward (enforced by `lint-imports`):

- `meddler/engine/`: the deterministic simulation. Imports nothing from the other layers.
- `meddler/text/`: headline templates. May import the engine.
- `meddler/bridge/`: translates engine state to the WebSocket protocol and owns the wall clock.

`meddler run --headless` must never import the bridge. The browser speaks only the JSON protocol
in [`docs/frontend-contract.md`](docs/frontend-contract.md) and never reaches into the engine.
[`docs/architecture.md`](docs/architecture.md) is the longer tour of how the pieces fit together.

## Working on the web UI

Everything in `web/` follows [`docs/design-guide.md`](docs/design-guide.md). It is binding: color
is semantic, never decorative; serif type is for the world, sans for chrome, mono for numbers;
glass surfaces only float above the world; motion only signals a change of state. The frontend
has no build step, no dependencies and no CDN, and must work from the origin that serves it.

For UI changes, include a screenshot or short clip in the pull request, and check the browser
console for errors. `scripts/capture/` can regenerate the README screenshots and GIF (see
[`scripts/capture/README.md`](scripts/capture/README.md)).

## Commits and pull requests

- Keep commits small and focused, one logical change each.
- Write the subject in the imperative mood, 72 characters or fewer ("Fix fork diff when a
  country secedes", not "fixed stuff"). Use the body to explain why, not what.
- Keep pull requests to one topic, describe how you tested them, and say explicitly if the golden
  master changed.
- Don't weaken or skip tests to make them pass. A failing test is telling you something; if you
  believe it is wrong, say so in the pull request.

## Reporting bugs

A good report includes the seed, the tick, the steps to reproduce, and what you expected. Since
the simulation is deterministic, a seed and a list of actions usually reproduce a bug exactly.
For security issues, see [SECURITY.md](SECURITY.md) instead.
