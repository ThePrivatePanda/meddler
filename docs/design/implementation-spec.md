# Meddler — Implementation Spec (the v1 build plan)

**What this is:** the task-by-task plan that took `PROPOSAL.md` from design to a working v1
(milestones M0–M8). `PROPOSAL.md` is the *what and why* and stays the source of truth for
behavior; this document only sequences it into small, verifiable steps and pins down the exact
names, signatures, and commands. It is kept as a record of how the build was organized. The
system as it exists today is described in [`../architecture.md`](../architecture.md), and later
work (the v2 track, M9–M16) is in [`v2-simulation-driven-world.md`](v2-simulation-driven-world.md).

**If this plan and `PROPOSAL.md` disagree, one of them is stale.** Resolve it explicitly rather
than picking whichever is convenient.

The target: the **zero-player world simulator** described in `PROPOSAL.md`. That means a pure
Python engine (`meddler/engine/`), a headline text layer (`meddler/text/`), a WebSocket bridge
(`meddler/bridge/`) that lights up the web frontend in `web/`, and a CLI. When this plan was
written, the frontend already existed and ran on an in-browser mock. The job was to build the
real engine and the bridge that feeds it.

---

## 0. Working rules

The rules are strict on purpose. Determinism bugs are silent: plausible-looking code can pass a
casual read and still produce a different event log on the next run.

1. **One task at a time, in the order listed.** Tasks are labelled `M0.1`, `M1.2`, and so on.
   A task doesn't start until the previous one's verify command passes. Don't combine tasks to
   save time.
2. **Every task ends with a copy-paste _Verify_ command that must pass.** Run it and keep the
   real output. If it fails, the task isn't done. Never edit a test to make it pass unless the
   task says to change that test.
3. **Don't invent behavior.** Every formula, constant, probability, and threshold is already in
   `PROPOSAL.md` at the section this plan cites (e.g. "§6.2"). Copy it exactly. If a number is
   not specified anywhere, raise it instead of guessing.
4. **Don't add anything not asked for.** No extra config options, no extra event kinds, no
   drive-by refactors of other files, no dependencies. Every task lists the files it may touch.
5. **Match the names here exactly.** Class, method, and field names, message `type`/`cmd`
   strings, and file paths are load-bearing: tests and the frontend contract depend on them.
6. **When a task is finished, tick its box in `PROPOSAL.md` §10.** If a formula had to change
   because it was clearly wrong, record it under "tuning candidates" in
   `docs/design-decisions.md`. Never change a specced number silently.
7. **After three genuine attempts on the same failure, stop** and write down what was tried,
   the exact error output, and what is missing. Thrashing costs more than asking.

### The determinism creed (memorize; it governs the whole engine)

The engine's #1 correctness property: **same seed ⇒ byte-identical event log, forever, on every
machine.** A CI golden-master test enforces it. These rules make it true (full statement in
`PROPOSAL.md` §4.3):

- There is **exactly one** RNG stream, an instance of our `Rng` class, threaded explicitly as a
  function argument through `tick()`. **Never** call `random.random()`, `random.*` at module
  level, `time.time()`, `time.*`, `datetime.now()`, `uuid4()`, `os.urandom()`, or `id()`-based
  logic anywhere in `meddler/engine/` or `meddler/text/`.
- **Never iterate a `dict` or `set` in a way that affects RNG draws or event order.** Iterate
  `sorted(...)` keys, or iterate the ordered `World.countries` list. Countries live in a list in
  a fixed order.
- Event IDs are **sequential integers** from the event log, never UUIDs.
- Headline template choice is `event.id % len(templates)` — it never consumes RNG.
- Money lives in **integer minor units** (like cents). No floats in money pools, ever.

If you are ever unsure whether something breaks determinism, assume it does and route it through
`Rng` or `sorted()`.

---

## 1. Environment setup (do this once, before M0.1)

```bash
cd meddler          # repository root
python3 --version   # must be 3.11 or newer (PROPOSAL §7)
python3 -m venv .venv
source .venv/bin/activate
```

You will create `pyproject.toml` in M0.1; after that, `pip install -e ".[dev]"` installs the
package and dev tools. Runtime deps: **`websockets`** only (bridge). Dev deps: `pytest`,
`hypothesis`, `ruff`, `mypy`, `import-linter`, `pytest-cov`. The web frontend has **no** build
step and **no** dependencies — never add an npm/package.json step for it.

---

## 2. Global conventions (apply to every Python file you write)

- **Layering (hard wall, enforced by CI in M0.4).** Dependencies point *down* only:
  ```
  bridge/  → may import engine/, text/
  text/    → may import engine/ (types only)
  engine/  → imports NOTHING above it. No websockets. No file I/O. No printing. No sockets.
             No wall-clock. Save/load helpers take an open file object as an argument.
  ```
  If you find yourself wanting to `import websockets` inside `engine/`, you are in the wrong
  layer — stop.
- **Everything is a `@dataclass`.** No Pydantic. Frozen dataclasses for immutable value objects
  (`Event`, `EventSpec`, `LedgerEntry`); mutable dataclasses for live state (`World`, `Country`).
- **Type-hint everything.** `mypy` runs (strict on `engine/`). No bare `dict`/`list` — write
  `dict[str, int]`, `list[Event]`.
- **All magic numbers live in `engine/config.py`** with a one-line comment each. Systems read
  tunables from `world.settings` (a `WorldSettings` instance) when the key appears in §6.9,
  otherwise from `config.py`. Never hardcode a formula constant inline.
- **Comments state constraints, not narration.** Do not write `# increment counter`.
- **Run `ruff check . && ruff format . && mypy meddler/engine` before declaring any task done.**
- **The r4 forward-compatibility rules (`PROPOSAL.md` §12.3) bind every task from M3 onward.**
  They change code *shape*, never behavior: every mutation has an owning event carrying its
  delta; trade/market logic is parameterized on `(supply, need, stock, ...)`, not `grain_`
  literals; asset count/condition math goes through `engine/assets.py` helpers; intervention
  kinds are never enumerated by name outside the registry; `Event.payload` and queue entries
  are open dicts; threshold parent-attribution lives in one module; save/load ignores unknown
  keys. Do NOT build anything from §12.1–§12.2 — those are post-v1. If a skeleton below and
  §12.3 conflict on shape, follow §12.3 and note it in `docs/design-decisions.md`.

---

## 3. The build, task by task

Milestones and tasks mirror `PROPOSAL.md` §9 exactly. Each task below gives: **Goal**, **Files**,
**Interface** (exact signatures/skeletons — fill the bodies from the cited PROPOSAL section),
**Test**, **Verify** (a command whose output you must paste), and **Done when**.

---

## M0 — Scaffolding

### M0.1 — Package skeleton and entry point

**Goal:** `pip install -e .` works and `meddler --version` prints.

**Files:** `pyproject.toml`, `meddler/__init__.py` (exists), `meddler/cli.py`, and empty
`__init__.py` in `meddler/engine/`, `meddler/text/`, `meddler/bridge/`, `meddler/engine/kinds/`,
`meddler/engine/systems/`.

**Interface — `pyproject.toml`** (minimum viable; deps per §7):

```toml
[project]
name = "meddler"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["websockets>=12"]

[project.optional-dependencies]
dev = ["pytest", "hypothesis", "ruff", "mypy", "import-linter", "pytest-cov"]

[project.scripts]
meddler = "meddler.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.11"
```

**Interface — `meddler/cli.py`** (stub the subcommands now; they get bodies in later tasks):

```python
import argparse

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meddler")
    parser.add_argument("--version", action="version", version="meddler 0.1.0")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="headless simulation to stdout")
    run.add_argument("--headless", action="store_true")
    run.add_argument("--seed", type=int, default=1337)
    run.add_argument("--ticks", type=int, default=1000)
    run.add_argument("--trace", type=int, default=None)

    serve = sub.add_parser("serve", help="start the websocket bridge")
    serve.add_argument("--seed", type=int, default=1337)

    args = parser.parse_args(argv)
    # M3.3 fills `run`; M6.1 fills `serve`.
    raise SystemExit(0)
```

**Verify:**
```bash
pip install -e ".[dev]" && meddler --version
```
Expected: prints `meddler 0.1.0`.

**Done when:** the command above prints the version; all `__init__.py` files exist.

---

### M0.2 — Docs move and README stub

**Goal:** preserve the origin story, give a new-project README.

**Files:** move `README.md` → `docs/original-vision.md`; write a new short `README.md`
(quickstart: `meddler serve --seed 1337` then open `web/index.html`; link to `PROPOSAL.md`).

> ⚠️ **Hold this until the move is confirmed.** The current `README.md` is the original
> vision document, and moving it is awkward to undo. Leave M0.2 for last; nothing else in the
> build depends on it.

**Verify:** `ls docs/original-vision.md && head -5 README.md`.

**Done when:** links in the new README resolve; original content is intact at its new path.

---

### M0.3 — CI

**Goal:** GitHub Actions runs lint + typecheck + tests on Python 3.11 and 3.12.

**Files:** `.github/workflows/ci.yml`.

**Interface:** a matrix job on `[3.11, 3.12]` that runs
`pip install -e ".[dev]"`, `ruff check .`, `mypy meddler/engine`, `pytest`.

**Verify:** `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`
(valid YAML) — and it goes green on the first push that has tests.

**Done when:** the workflow file is valid and green on push.

---

### M0.4 — Import-linter layering contract

**Goal:** CI fails if `engine/` imports `bridge/` or `text/`, or `text/` imports `bridge/`.

**Files:** `.importlinter` (or `[tool.importlinter]` in `pyproject.toml`).

**Interface:** a `layers` contract, top-to-bottom: `meddler.bridge`, `meddler.text`,
`meddler.engine`.

**Verify:**
```bash
lint-imports
```
Then prove it works: on a throwaway branch, add `import meddler.bridge` inside an `engine/` file
and confirm `lint-imports` fails; revert.

**Done when:** `lint-imports` passes clean, and a deliberate bad import makes it fail.

---

## M1 — Deterministic core

This milestone is the foundation everything else stands on. Get determinism right here or every
later test is unreliable. Code skeletons in M1 are given nearly complete because they are the
determinism-critical primitives — copy them closely.

### M1.1 — RNG, config, events, registry skeleton

**Goal:** the deterministic primitives exist and are tested.

**Files:** `engine/rng.py`, `engine/config.py`, `engine/events.py`, `engine/registry.py`.

**Interface — `engine/rng.py`** (the ONLY source of randomness in the whole engine):

```python
from __future__ import annotations
import random

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

    def choice(self, seq: list):   # seq MUST be an ordered list, never a set/dict
        return self._r.choice(seq)

    def sub(self, seed_key) -> "Rng":
        """Derive a child stream deterministically (forks/restart, PROPOSAL §4.3.5/§4.3.6)."""
        return Rng(seed=hash((self._seed, seed_key)))
```

> Note: fork/restart reseeding uses the exact `hash((base_seed, fork_tick, "fork", fork_id))` /
> `hash((base_seed, restart_tick, "genesis"))` recipes in §4.3.5–§4.3.6. Compute those seeds where
> forks/restart are created (M1.4), passing the tuple to `Rng(seed=hash(...))`.

**Interface — `engine/config.py`:** module-level constants, each commented. Start with the ones
M1 needs and grow it per task. Example:

```python
SNAPSHOT_INTERVAL_DEFAULT = 50   # ticks between full world snapshots (PROPOSAL §4.4)
MAX_DEPTH_DEFAULT = 8            # cascade depth cap (PROPOSAL §6.4.3)
CASCADE_DECAY_DEFAULT = 0.70    # p_spawn = base_p * decay^depth (PROPOSAL §6.4.1)
```

**Interface — `engine/events.py`:** the `Event` and `LedgerEntry` frozen dataclasses **exactly**
as `PROPOSAL.md` §4.6 and §4.5 specify (`id, tick, kind, country, country2, parent_id, depth,
is_intervention, payload, ledger, severity`), plus:

```python
class EventLog:
    """Append-only authoritative history. Assigns sequential ids."""
    def __init__(self) -> None:
        self._events: list[Event] = []
    def append(self, **fields) -> Event:
        """Create an Event with the next sequential id and store it. Returns it."""
        ...
    def __getitem__(self, event_id: int) -> Event: ...
    def __len__(self) -> int: ...
    def truncate_after(self, tick: int) -> None:  # used by restart (M1.4)
        ...
```

**Interface — `engine/registry.py`:** skeleton only (specs come in M3.1):

```python
EVENT_REGISTRY: dict[str, "EventSpec"] = {}

def register(spec: "EventSpec") -> None:
    """Add a spec; raise if the kind key is already registered."""
    ...

def validate_all() -> None:
    """Cross-check registry consistency (child_kind exists, p in [0,1], sane delays). PROPOSAL §4.7."""
    ...
```
(`EventSpec` itself is defined in M3.1; keep `registry.py` importable now with a `TYPE_CHECKING`
guard or a forward ref.)

**Test — `tests/unit/test_rng.py`, `tests/unit/test_events.py`:**
- Two `Rng(42)` instances produce identical sequences of `roll/randint/uniform` outputs.
- `Rng(42)` and `Rng(43)` differ.
- `EventLog` assigns ids `0,1,2,...` in append order; `truncate_after(t)` drops later events.

**Verify:**
```bash
pytest tests/unit/test_rng.py tests/unit/test_events.py -q
```

**Done when:** those pass; `mypy meddler/engine` clean.

---

### M1.2 — World model and the Ledger (with invariants)

**Goal:** the full state model exists; money is provably conserved.

**Files:** `engine/model.py`, `engine/ledger.py`, `tests/property/test_ledger.py`.

**Interface — `engine/model.py`:** transcribe these dataclasses **field-for-field** from the
PROPOSAL — do not abbreviate, do not rename fields, do not add fields:
- `Country` — full stat block, `PROPOSAL.md` §5.1.
- `AssetClass` and `InfrastructureBlock` — §6.7.1.
- `WorldSettings` — every key in §6.9's three tables with the listed default.
- Enums `CountryStatus`, `GovtType`, `Leader`, `EventCategory` — as referenced in §5.1/§4.7.
- `World`:
  ```python
  @dataclass
  class World:
      tick: int
      countries: list[Country]                    # ordered; fixed iteration order
      relations: dict[tuple[str, str], float]     # key = sorted code pair, value in [-100,100]
      settings: WorldSettings
      log: EventLog
      # schedule queue + snapshots may live here or on Timeline (M1.4); keep countries a LIST
      def country(self, code: str) -> Country: ...  # lookup by code
  ```

**Interface — `engine/ledger.py`** (money is integer minor units; §4.5):

```python
def round_money(x: float) -> int:
    """The ONE money-rounding helper. Banker's rounding to integer minor units. §4.5."""
    ...

class Ledger:
    """Every money movement goes through here so Invariants A/B hold. Entries live on the event."""
    @staticmethod
    def transfer(entries: list, src_pool: str, dst_pool: str, amount: int, currency: str) -> None: ...
    @staticmethod
    def mint(entries: list, pool: str, amount: int, currency: str) -> None: ...
    @staticmethod
    def burn(entries: list, pool: str, amount: int, currency: str) -> None: ...
```
Pool names are `"<CODE>.treasury"`, `"<CODE>.households"`, `"<CODE>.corporates"`, and
`"fx:<pair>"` per §4.5. `transfer` appends one `LedgerEntry`; `mint`/`burn` append a
mint/burn-typed entry. The system that calls these also applies the delta to `world` pools
atomically (same tick, same code path).

**Test — `tests/property/test_ledger.py`** (Hypothesis, §8.3):
- **Invariant A (conservation):** over a random sequence of `transfer`s (no mint/burn), the sum
  of all pools in each currency is unchanged.
- **Invariant B (reconstruction):** replaying all ledger entries from a zero state reproduces the
  final pools exactly.
- Pools are always `int` (never float).

**Verify:**
```bash
pytest tests/property/test_ledger.py -q
```

**Done when:** both invariants pass over Hypothesis's generated cases; pools typed `int`.

---

### M1.3 — Worldgen

**Goal:** `worldgen(seed, settings)` deterministically builds the starting world.

**Files:** `engine/worldgen.py`, `text/names.py`, `tests/unit/test_worldgen.py`.

**Interface:**
```python
# engine/worldgen.py
def generate_world(seed: int, settings: WorldSettings) -> World:
    """Build the genesis world per PROPOSAL §5.3. Pure function of (seed, settings)."""
    ...
```
Follow §5.3 exactly: `settings.starting_country_count` countries (default 8); each gets a
syllable-grammar name (`text/names.py`), a unique 3-letter code, a currency (name+symbol from a
curated list in `names.py`), a `Leader` with 2 traits from the fixed trait list, stats sampled
from §6.9's worldgen ranges, one staple "grain", an `InfrastructureBlock` at condition 0.7–0.9,
and `rival_pairs` seeded negative relations. **All sampling goes through one `Rng(seed)`.**

**Test:**
- Same seed ⇒ deeply equal `World` (compare fields, or a canonical dict dump).
- Different seed ⇒ different names/stats.
- Names match a pronounceability regex (alternating consonant/vowel clusters, §M1.3 accept).
- Exactly `starting_country_count` countries; codes unique; relations keys are sorted pairs.

**Verify:**
```bash
pytest tests/unit/test_worldgen.py -q
```

**Done when:** determinism test passes; names look pronounceable.

---

### M1.4 — Tick loop, snapshots, timeline, forks, restart

**Goal:** the tick scaffold runs with no-op systems; time travel and forking work.

**Files:** `engine/tickloop.py`, `engine/timeline.py`, `tests/property/test_determinism.py`.

**Interface — `engine/tickloop.py`:**
```python
SYSTEMS = [ ... ]  # ordered list of system callables, PROPOSAL §6.1 — no-ops for now, filled M2/M3

def tick(world: World, rng: Rng) -> tuple[World, list[Event]]:
    """Advance exactly one tick. Pure: same (world, rng state) -> same result. §4.2.
    Runs SYSTEMS in fixed order; each returns events already applied to state."""
    ...
```

**Interface — `engine/timeline.py`:**
```python
@dataclass
class Timeline:
    seed: int
    world: World
    snapshots: dict[int, World]     # tick -> deep-copied World, every snapshot_interval ticks
    rng: Rng
    def advance(self) -> list[Event]: ...          # calls tick(), snapshots on interval
    def world_at(self, tick: int) -> World:        # nearest snapshot <= tick, then replay events §4.4
        ...

class Multiverse:
    """Prime timeline + up to 3 forks (B/C/D). PROPOSAL §4.4, §5, §3.5."""
    def fork(self, at_tick: int, intervention: Event | None) -> str: ...   # returns fork id, max 3
    def adopt_fork(self, fork_id: str) -> None: ...   # promote to prime, dissolve siblings
    def drop_fork(self, fork_id: str) -> None: ...
    def restart(self, tick: int) -> None:             # §3.7/§4.4: truncate + reseed(§4.3.6); refused if forks exist
        ...
```
Snapshot/replay must **not** re-run systems (replay applies stored deltas only, so it needs no
RNG and cannot diverge — §4.4). Fork reseed uses §4.3.5's recipe; restart reseed uses §4.3.6's.

**Test — `tests/property/test_determinism.py`** (this is the most important test file in the
project; §8.2):
- Two fresh runs, same seed, N ticks in-process ⇒ identical event logs.
- `world_at(500)` == the world from a continuous run to tick 500 (snapshot+replay correctness).
- **Fork with `intervention=None` diffs to zero at every tick** (the strongest determinism test).
- `restart(T)` truncates history after T and produces a deterministic (deliberately *different*,
  per §4.3.6) future; re-running the same restart reproduces it.

**Verify:**
```bash
pytest tests/property/test_determinism.py -q
```

**Done when:** all four hold. If fork-zero-diff fails, you have a hidden RNG or dict-ordering
leak — hunt it now, do not proceed to M2.

---

## M2 — Systems: economy, infrastructure, relations

One system per file in `engine/systems/`, run in the fixed order of §6.1. Each system is a
callable `def run(world: World, rng: Rng) -> list[Event]` that mutates `world` and returns the
events it emitted (with correct `parent_id`/`depth`). **Implement every formula exactly as
written in §6.2** — tune only via `config.py`. Add each system to `tickloop.SYSTEMS` in its §6.1
slot.

Do these one task = one system-or-pair, tests each:

- **M2.1 `production.py`** — grain + GDP output (§6.2 `gdp_tick` formula, wages transfer).
- **M2.2 `trade.py`** — match surplus→deficit, move grain and money through the ledger.
- **M2.3 `fx.py`** — update exchange rates from trade balances (§6.2 FX drift `tanh` formula).
- **M2.4 `fiscal.py`** — taxes, subsidies, treasury drift.
- **M2.5 `inflation.py` + `stability.py`** — the inflation and stability update formulas (§6.2).
- **M2.6 `infrastructure.py` + `relations.py`** — maintenance funding/degradation/recovery
  (§6.7.2 formulas) and relation decay + threshold `RELATION_SHIFT` (§5.2).
- **M2.7 threshold bridge** (§6.3): each stat crossing emits its event with hysteresis via the
  country's `armed` dict. Parent = the causing system's event where one exists, else root.

**Interface (every system):**
```python
# engine/systems/production.py
def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in world.countries:          # ordered list — deterministic
        ...                                  # apply §6.2 formula; append events via world.log
    return events
```

**Test (each task):** a hand-built minimal world ("two countries, one in drought") asserting the
exact numeric outcome of one tick; plus the M1.2 ledger invariants still pass; plus each emitted
event has the correct `parent_id` and `depth`.

**Verify (per task):**
```bash
pytest tests/unit/test_<system>.py tests/property/test_ledger.py -q
```

**Done when (M2.7):** a forced doom-spiral (mint → inflation → unrest) produces the full
parent-linked event chain, verified by a test that walks `parent_id` from the `UNREST` back to
the `MINT`.

---

## M3 — Drama (registry-driven events)

### M3.1 — Registry types + exogenous + consequence systems + core catalog

**Goal:** events fire from data, cascade correctly, and respect the depth cap.

**Files:** finish `engine/registry.py`; add `engine/systems/exogenous.py`,
`engine/systems/consequence.py`; add `engine/kinds/*.py` for the **core subset only** (the build
order list in §6.6); `tests/unit/test_cascade.py`.

**Interface:** define `EventSpec`, `ConsequenceRule`, `Condition`, `PoolTransfer`, `EventCategory`
**exactly** as §4.7 gives them (all frozen dataclasses). Then:

```python
# engine/systems/exogenous.py — §6.1 slot 10
def run(world: World, rng: Rng) -> list[Event]:
    """For each is_exogenous spec (sorted by kind), roll exogenous_base_p * drama_multiplier,
    honoring enabled/disabled tags (§6.9). Emit root events (parent_id=None, depth=0)."""
    ...

# engine/systems/consequence.py — §6.1 slot 11
def run(world: World, rng: Rng) -> list[Event]:
    """Fire due entries from the schedule queue (ordered by (fire_tick, schedule_seq), §4.3.7).
    For each: look up EventSpec, check conditions at fire time (drop silently if failed),
    apply stat_deltas + pool_transfers, emit the event, schedule its children with
    p_spawn = base_p * cascade_decay^parent.depth (§6.4.1), delay = rng.randint(dmin,dmax) (§6.4.2),
    respecting MAX_DEPTH (§6.4.3: clip, set parent payload 'cascade_clipped'=True)."""
    ...
```
The schedule queue is a list of `(fire_tick, schedule_seq, child_kind, parent_id, payload)`
ordered per §4.3.7; `schedule_seq` is a monotonic counter assigned at schedule time. Per
§12.3.5, `payload` (on events and queue entries) is an open dict — never validate against a
closed key set; validation covers types/ranges of keys a spec *does* define, not key presence. Register the
core-subset specs (DROUGHT, EARTHQUAKE, METEOR, PLAGUE, SCANDAL, ELECTION, UNREST, SECESSION,
DEBT_CRISIS, INFLATION_CRISIS, GOLDEN_AGE, INNOVATION, FAMINE_WARNING, WAR_DECLARED, PEACE, plus
the intervention kinds) with the probabilities/consequences from the §6.6 tables.

**Test:** over 2000 ticks × 10 seeds — ≥1 war and ≥3 crises occur; no event has `depth > max_depth`;
the clip counter stays under `CLIP_BUDGET_PER_1000_TICKS`; `registry.validate_all()` passes.

**Verify:**
```bash
pytest tests/unit/test_cascade.py -q
```

**Done when:** cascade decay, delays, and the depth cap all behave; validation passes.

### M3.2 — PoliticsSystem

**Goal:** elections, coups, leader changes, conquest rolls.

**Files:** `engine/systems/politics.py` (§6.1 slot 9), `tests/unit/test_politics.py`.

**Interface:** `def run(world, rng) -> list[Event]` — calendar elections (~every 90 ticks via
`election_due_tick`), coup rolls, leader change with trait re-bias, and the conquest/absorption
rolls of §6.8 (`OCCUPATION_BEGIN` when stability 0 + at war + relation < −80).

**Test:** a scripted stability-0 + active-war world triggers `OCCUPATION_BEGIN`.

**Verify:** `pytest tests/unit/test_politics.py -q`.

### M3.3 — Headlines, names, golden master, headless CLI

**Goal:** every core event renders prose; the golden file is locked in; `meddler run` works.

**Files:** `text/headlines.py`, finish `text/names.py`, finish `run` in `cli.py`,
`tests/property/test_templates.py`, `tests/golden/test_golden.py`,
`tests/golden/seed1337_1000t.txt`.

**Interface — `text/headlines.py`:**
```python
TEMPLATES: dict[str, list[str]] = {
    "UNREST": [
        "Bread riots erupt in {country} as inflation hits {inflation:.0f}% — {leader_title} {leader} blames {scapegoat}.",
        ...  # >= 4 per kind (§6.5)
    ],
    ...
}

def render(event: Event, world: World) -> str:
    """Pick TEMPLATES[event.kind][event.id % len(templates)] (deterministic, §4.3.4);
    fill slots from event.payload + country/leader state; flavor lists chosen by hashing event.id.
    Severity-2 events get an all-caps lead-in word. No RNG, no network, no LLM."""
    ...
```
`meddler run --headless --seed 42 --ticks 1000` prints one headline per line
(`t0412 [ELB] Bread riots erupt…`); `--trace <id>` prints an ancestry tree as text. **This path
must not import `bridge/` or `web/`.**

**Test:**
- **Template coverage (§8.4):** every registered kind has ≥ 4 templates, and every template
  renders against a synthetic payload without `KeyError`.
- **Golden master (§8.1):** `meddler run --headless --seed 1337 --ticks 1000` equals
  `tests/golden/seed1337_1000t.txt` byte-for-byte. Generate the file once, eyeball it, commit it.

**Verify:**
```bash
pytest tests/property/test_templates.py tests/golden/test_golden.py -q
meddler run --headless --seed 42 --ticks 20
```

**Done when:** coverage passes; the golden file is committed and the test is green.

> From here on, **never regenerate the golden file** unless a task explicitly changes simulation
> behavior — then eyeball the diff and mention it in the commit.

---

## M4 — Signature capabilities: causal + temporal navigation

### M4.1 — Trace
**Files:** `engine/trace.py`, `tests/unit/test_trace.py`.
**Interface:** `def trace(log: EventLog, event_id: int) -> list[dict]` — walk ancestry up to the
root and descendants down, DFS order, each node carrying a tree-depth `d` for indentation (the
`trace` message shape, contract §3). Clipped cascades carry the ∞ marker.
**Test:** a doom-spiral trace returns the full DFS tree with correct depths.
**Verify:** `pytest tests/unit/test_trace.py -q`.

### M4.2 — Scrubbing, restart, annals query
**Files:** `engine/annals.py`, wire `world_at`/`restart` from M1.4 into query form,
`tests/unit/test_scrub.py`.
**Interface:** scrub reads `Timeline.world_at(t)`; `restart(t)` truncates + reseeds + prunes
countries born after `t`; `annals(world, country=None) -> dict` returns `{eras, wars, records,
majorEvents}` derived from the real event archive (§3.7).
**Test:** scrub to `t` matches `world_at(t)`; restart prunes late-born countries.
**Verify:** `pytest tests/unit/test_scrub.py -q`.

---

## M5 — Signature capabilities: counterfactual + god mode

### M5.1 — God mode
**Files:** `engine/god.py`, intervention specs already in the registry (`is_intervention=True`),
`tests/unit/test_god.py`.
**Interface:** interventions are injected as root events from the registry; `god_edit`,
`god_relation`, `god_peace` emit synthetic root events (`GOD_EDIT`, `GOD_RELATION_SHIFT`, `PEACE`
with `is_intervention=True`) carrying before/after in payload — **unless** `silent_god_edits` is
set (§3.5), then no record is written. Per §12.3.4, `god.py` takes a `kind` string and looks it
up in `EVENT_REGISTRY` — no per-kind functions, no hardcoded kind lists (r4's raw-injection tier
reuses this exact path).
**Test:** an intervention appears with `is_intervention=True` and its consequences trace back to
it; a silent god edit leaves no event.
**Verify:** `pytest tests/unit/test_god.py -q`.

### M5.2 — Fork + compare
**Files:** finish `Multiverse` diff, `tests/property/test_fork.py`.
**Interface:** `Multiverse` holds up to 3 forks; `adopt_fork` dissolves siblings; a per-country
`diff(a, b) -> dict` powers the ΔWORLD strip.
**Test:** fork with no intervention ⇒ zero diff at every tick; fork with intervention ⇒ nonzero
diff; adopt promotes B and drops C/D.
**Verify:** `pytest tests/property/test_fork.py -q`.

---

## M6 — The web bridge (light up the real engine)

The frontend already speaks `docs/frontend-contract.md`. Your job: make the Python bridge emit
those exact messages and accept those exact commands. **Read `docs/frontend-contract.md` in full
before starting** — the `type`/`cmd` strings and field names are load-bearing and must match
byte-for-byte, including the fork-focus and settings additions.

- **M6.1 `bridge/server.py`** — asyncio WebSocket server at `ws://127.0.0.1:7677/ws`, one client
  per socket, JSON text frames. `meddler serve --seed N` starts it (fill `cli.py`'s `serve`).
  *Verify:* a browser (or a test client) connects and receives `hello`.
- **M6.2 `bridge/adapter.py`** — translate engine ticks → `frame`, plus `hello`, `status`,
  `snapshot`, `trace`, `forkStarted`, `countryAdded`, `toast`, and the fork-focus messages. Cover
  every server→client type in contract §3/§6/§7/§9. **No simulation logic in `bridge/`** — pure
  translation. *Verify:* `web/index.html` against the real engine (mock disabled) shows a
  live-running world.
- **M6.3 `bridge/commands.py`** — every client→server command in contract §4/§6/§7/§9, plus
  `updateSettings` (§6.9; also add it to the contract as §10). *Verify:* pause, intervene, fork,
  adopt, godEdit, restart, countryDetail, updateSettings all work with no mock fallback.
- **M6.4 settings round-trip** — `updateSettings` → `settingsAck` (full current settings;
  restart-required keys flagged). *Verify (headless):* toggling `allow_secession` off prevents
  SECESSION over the next 500 ticks.

**Interface (adapter shape):** convert `Event` (engine, snake_case: `parent_id`,
`is_intervention`) to the contract's JSON (`parentId`, `intervention`, `headline` — rendered via
`text.render`). Convert money from integer minor units for display per contract §2. Per
§12.3.4/§12.3.8, `hello.interventions` and any settings-key lists are *derived* (iterate
`EVENT_REGISTRY` / `WorldSettings` fields) — the adapter contains no hardcoded kind or key
lists.

**Test — `tests/bridge/test_handshake.py`:** spin up `server.py`, connect a test WebSocket
client, assert the `hello`/`status`/`snapshot`/`frame` handshake and that each contract command
produces its specified reply. **No browser needed** (§8.7).

**Verify (per task):**
```bash
pytest tests/bridge/ -q
```

**Done when (M6):** the full UI runs against the real engine with no mock fallback (success
criterion §2.3.1).

---

## M7 — Deep simulation (the full catalog)

- **M7.1** — populate `engine/kinds/` with **every** kind in §6.6 (natural, economy, politics,
  military/diplomatic, infrastructure, social), each with ≥ 4 headline templates. *Verify:*
  `registry.validate_all()` passes; a 5000-tick × 20-seed headless run produces ≥ 1 of every kind
  not flagged `low_frequency_ok`.
- **M7.2** — infrastructure failure chains (§6.7.3/§6.7.5). *Verify:* zero treasury for 100 ticks
  produces `SATELLITE_FAILURE` and the full §6.7.5 chain, traced end to end. Per §12.3.3, the
  condition-threshold checks read via `engine/assets.py` helpers (e.g.
  `effective_capacity(asset_class)`), never inline `count * condition`.
- **M7.3** — conquest/absorption (§6.8): `OCCUPATION_BEGIN` → `ANNEXATION`/`LIBERATION`. *Verify:*
  the scripted scenario triggers the full chain; the frontend prunes the annexed country.
- **M7.4** — tuning pass: 30 seeds × 5000 ticks; drama cadence ≥ 1 severity-2 event / 80 ticks;
  infra chains fire in ≥ 80% of runs; ≥ 1 country absorbed/dissolved in ≥ 40% of runs; no
  permanent single-country equilibrium. Document changes in `design-decisions.md`; regenerate the
  golden file (this is one of the rare sanctioned regenerations).

**Verify (coverage script):**
```bash
python -m meddler.cli run --headless --seed 1 --ticks 5000 | wc -l   # sanity
pytest tests/ -q
```

---

## M8 — Ship it

- **M8.1** — write `docs/design-decisions.md` (why tick-based, legibility>realism, determinism
  rules, ledger design, why the registry, why a bridge) and `docs/demo.md` (60-second scripted
  demo).
- **M8.2** — settings overlay + Annals polish on the frontend against the real engine (the only
  remaining UI work). Follow `docs/design-guide.md` — it is **binding** for anything visual.
- **M8.3** — final `README.md`: GIF of the UI, quickstart, feature tour, architecture sketch,
  origin-story link. *Verify:* a stranger can run it from the README alone.

---

## 4. Definition of done for the whole project (from `PROPOSAL.md` §2.3)

1. `pip install -e . && meddler serve --seed 1337`, open `web/index.html` → full UI on the real
   engine, no mock fallback.
2. Same seed ⇒ byte-identical event log across runs and machines (golden master green in CI).
3. Every headline traceable to a root cause in ≤ 1 keypress.
4. Fork + diff works (up to 3 forks, adopt promotes one).
5. Test suite (unit + property + golden + bridge) green in CI.
6. README has a GIF, quickstart, and design-decisions section.

## 5. Quick reference — where each thing is specified in PROPOSAL.md

| You need… | PROPOSAL section |
|---|---|
| Determinism rules | §4.3 |
| Event schema | §4.6 |
| Ledger / money invariants | §4.5 |
| Event registry / EventSpec / ConsequenceRule / Condition | §4.7 |
| Country stat block | §5.1 |
| Relations matrix | §5.2 |
| Worldgen | §5.3 |
| System order (fixed) | §6.1 |
| Stat formulas | §6.2 |
| Drama thresholds | §6.3 |
| Cascade model (decay/delay/depth cap) | §6.4 |
| Headline generation | §6.5 |
| Full event catalog (all kinds + probabilities) | §6.6 |
| Infrastructure assets + failure chains | §6.7 |
| Conquest/absorption | §6.8 |
| WorldSettings (all keys) | §6.9 |
| Repo layout | §7 |
| Testing strategy | §8 |
| r4 roadmap (post-v1) + binding forward-compat rules | §12 (§12.3 binds v1 code shape) |
| Frontend message protocol | `docs/frontend-contract.md` |
| Visual/UX rules | `docs/design-guide.md` |
</content>
</invoke>
