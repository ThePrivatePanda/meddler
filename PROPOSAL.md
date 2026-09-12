# Meddler — Design Proposal & Execution Plan

**Status:** implemented. This is the original design record, kept because most of it still
describes the system accurately and the rest explains why it looks the way it does. For the
system as built, start with [`docs/architecture.md`](docs/architecture.md); every place the build
departed from this document is logged in [`docs/design-decisions.md`](docs/design-decisions.md).

**What changed after this document was written** (the sections below are left as designed):
- The v2 track (M9–M16, [`docs/design/v2-simulation-driven-world.md`](docs/design/v2-simulation-driven-world.md))
  made the world spatial and physical: positions and regions, six commodities, bilateral trade,
  in-flight shipments, alliances, strikes, and conquest economics. The §12 "r4 preview" was
  partly realized this way. Typed multi-cause links and the impact inspector are a first,
  unweighted step toward the §12.1.3 attribution ledger.
- §2.2 said "no database". Event history now lives in a per-run SQLite file so memory stays flat
  on long runs (see the 2026-07-28 entry in `docs/design-decisions.md`). It is still ephemeral:
  there is no save/load across process restarts.
- §2.2 said "no hosted deployment". The local server is still the primary way to run Meddler; a
  static in-browser build of the same engine (Pyodide) serves as the hosted demo.
- `meddler serve` hosts the frontend itself; nobody opens `web/index.html` from `file://`.

**Revision history:**
- **r1** — original proposal: Textual TUI, fixed 8 countries, ~25 event kinds in a closed enum.
- **r2 (2026-07-06)** — the primary v1 frontend became a **local web app**. The engine⇄frontend
  protocol is `docs/frontend-contract.md`; a working UX prototype on fake data lives in `web/`;
  all visual/UX work must follow `docs/design-guide.md` (binding tokens, semantics, and method).
  The prototype's v1.3 pass (contract §9) added the deep-time UX (Annals, checkpoints, restart).
- **r3 (2026-07-07)** — deep-simulation expansion: corrected cascade model, declarative event
  registry, dynamic country registry, infrastructure assets, relations system, user-configurable
  rules, and the WebSocket bridge. Integrated throughout — every section below is current; there
  are no trailing amendments to reconcile.
- **r3.1 (2026-07-08)** — added §12, the **r4 preview** (post-v1 deep-causation roadmap:
  flow-based infrastructure, a small commodity set, the causal attribution ledger, and the
  unbounded intervention surface) plus its **binding forward-compatibility rules** (§12.3) and
  short "r4 note" anchors at the sections they constrain. **v1 scope, formulas, milestones, and
  acceptance criteria are unchanged** — r3.1 changes *how* v1 code is shaped, never *what* it
  does.

**How it was built:** [`docs/design/implementation-spec.md`](docs/design/implementation-spec.md)
sequenced this document into small, ordered, individually verifiable tasks, each with exact
signatures and a pass/fail command. It points back here for every formula and table.
**Origin:** descoped from an earlier multiplayer concept for Meddler. The MMO stayed out of scope
for good: no players, game servers, Discord bots, or premium tiers.

---

## 1. One-paragraph pitch

Meddler is a **zero-player world simulator** — fictional countries (eight at
genesis; the roster grows by secession and shrinks by conquest) with economies, leaders,
grudges, infrastructure, and disasters, running on a deterministic tick loop — presented
through a local web app that works like a **time-travel debugger for history**. Every headline
in the world's news feed can be traced back through its chain of causes, the world can be
paused and rewound, and the user can intervene ("god mode": trigger a drought, assassinate a
leader), which **forks the timeline** (up to three concurrent forks) and shows a side-by-side
diff of the world that was going to happen versus the world they just created.

The novel UX claim, stated precisely: news/simulation UIs navigate **time** (scroll the feed,
scrub the chart). Meddler's primary navigation dimension is **causality** — you move through
the event graph (why did this happen? what did this cause?), and time scrubbing plus
counterfactual forking fall out of the same event-sourced architecture.

## 2. Product definition

### 2.1 What it is

- A Python package (`meddler`) with a pure, deterministic simulation core.
- A **local web frontend** (`web/` — plain HTML/JS/CSS, no build step, no dependencies)
  speaking to the engine over a loopback WebSocket **bridge** (`meddler serve`). The message
  protocol is `docs/frontend-contract.md`; the visual system is `docs/design-guide.md`. The
  prototype currently runs on an in-browser mock engine (`web/engine.js`); the bridge replaces
  the mock without changing the UI (contract §1).
- A headless CLI mode that streams headlines to stdout (for CI, demos, piping).

### 2.2 What it is NOT (permanent v1 non-goals)

- ❌ No real-world data, countries, or currencies. Fictional only. Realism is a tar pit.
- ❌ No multiplayer. No network I/O beyond the loopback WebSocket bridge (127.0.0.1 only).
- ❌ No hosted deployment; the web app runs from `file://` or a local static server.
- ❌ No database. State lives in memory; save files are plain files.
- ❌ No LLM calls at runtime. Headlines are template-generated, deterministically.
- ❌ No Textual TUI. r1 planned one; r2 replaced it with the web app. Do not build both —
  the headless CLI covers the terminal use case.

### 2.3 Success criteria (v1 "done")

1. `pip install -e . && meddler serve --seed 1337`, then open `web/index.html`: the full UI
   runs against the real engine — world running, headlines flowing, every keybinding working,
   no mock fallback.
2. Same seed ⇒ byte-identical event log across runs and machines (golden-master test in CI,
   via the headless CLI).
3. Every headline is traceable to a root cause in ≤ 1 keypress.
4. Fork + diff works: intervene, watch timelines diverge (up to 3 concurrent forks), view the
   diff, adopt a fork as the new prime.
5. Test suite: unit + property (invariants) + golden master + bridge integration, green in CI.
6. README has a GIF of the UI (record the browser), a quickstart, and a "design decisions"
   section.

## 3. UX design

### 3.1 Design principles

1. **Causality is the first-class navigation dimension.** Time is second. Space (the map) is
   third.
2. **Drama, not data.** The primary surface is prose headlines, not numbers. Numbers appear in
   the inspector when asked for.
3. **Legibility beats realism.** Every number on screen should be explainable by pointing at
   events. If a mechanic can't produce a headline, it's too subtle — cut it or amplify it.
4. **The pause button is sacred.** Nothing moves while paused. All navigation works while
   paused. Watching is a mode; investigating is a mode; the user always knows which they're in.
5. **Keyboard-first, command-palette driven.** No menus. Every action has a key or a `:command`.
6. **The web prototype is normative.** `web/` (on the mock engine) is the reference
   implementation of every interaction in this section; `docs/design-guide.md` is binding for
   anything visual.

### 3.2 Main screen ("Watch mode")

The pane grammar, schematically (the shipped rendering is the web prototype's layout — the
same three panes plus the orbital globe view):

```
┌ MEDDLER ────────────── seed 1337 ── tick 412 (Year 2, Day 47) ── ▶ 1 t/s ──┐
│ ┌─ WORLD ───────────────────┐ ┌─ CHRONICLE ────────────────────────────────┐│
│ │ ▲ Freedonia    $1.21 ↑    │ │ t412 ⚡ Bread riots erupt in Elbonia as    ││
│ │   stab ████████░░ 78%     │ │      inflation hits 11% — President Grum   ││
│ │   infl ██░░░░░░░░  2.1%   │ │      blames "foreign speculators".        ◀││
│ │ ▼ Elbonia      ₤0.44 ↓    │ │ t408 ▸ Elbonian crown slides 8% against    ││
│ │   stab ███░░░░░░░ 31% ⚠   │ │      the freed after trade deficit widens. ││
│ │   infl █████████░ 11.2% ⚠ │ │ t395 ▸ Grain prices spike across the       ││
│ │ ─ Kravonia     ₭2.03 ─    │ │      Eastern Reach; Elbonia imports triple.││
│ │   stab ██████░░░░ 64%     │ │ t371 ● DROUGHT strikes the Kravonian       ││
│ │ ... (5 more)              │ │      breadbasket. Harvest fails.           ││
│ └───────────────────────────┘ └────────────────────────────────────────────┘│
│ ┌─ INSPECTOR ──────────────────────────────────────────────────────────────┐│
│ │ (select a headline or country; press t to trace, enter to inspect)       ││
│ └──────────────────────────────────────────────────────────────────────────┘│
│ [space] pause  [t]race  [g]od mode  [f]ork  [←/→] scrub  [:] command  [?]  │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **WORLD pane:** one compact card per country: name, currency symbol + exchange rate with
  trend arrow, stability and inflation bars, ⚠ badges when a stat crosses its drama threshold
  (see §6.3). Selecting a country fills the INSPECTOR with its detail view. The country list is
  **dynamic** — secession adds cards at runtime, annexation retires them (§5.1).
- **CHRONICLE pane:** newest-first headline feed. Glyphs encode event class: `●` root/exogenous
  event, `▸` consequence (has a parent), `⚡` crisis-class consequence, `✦` intervention. The
  feed auto-follows while running; scrolling up auto-pauses follow (like `less +F`).
- **INSPECTOR pane:** context-sensitive: country detail (stat table + sparklines + active
  effects), or event detail (payload, ledger entries, parent/children links).
- **The globe** (web only): presentation-layer orbital view over protocol-v2 authoritative
  positions, territory ownership, routes, shipments, strikes, blocs, and aggregate assets.
  Territory outlines and satellite orbit paths are visual projections (contract §10).

### 3.3 Trace mode (the signature interaction)

Select any headline, press `t`. The inspector expands to show the event's **causal ancestry and
descendants** as a tree, root cause at top, selected event highlighted:

```
┌─ TRACE: "Bread riots erupt in Elbonia…" (t412, depth 3) ─────────────────────┐
│ ● t371 DROUGHT — Kravonian breadbasket harvest fails            [root cause] │
│ └─▸ t395 PRICE_SPIKE — grain +240% in Eastern Reach market                   │
│     ├─▸ t408 CURRENCY_SLIDE — Elbonian crown -8% (trade deficit)             │
│     │   └─▸ t412 UNREST — bread riots in Elbonia  ◀ you are here             │
│     └─▸ t401 TREASURY_DRAIN — Kravonia emergency grain subsidy, ₭2.1M        │
│ ↑/↓ navigate · enter jump feed to event · d show ledger deltas · esc back    │
└───────────────────────────────────────────────────────────────────────────────┘
```

Every event stores `parent_id` and `depth`, so this view is a cheap graph walk. This is the
descoped-but-real version of the original README's "butterfly engine stores event ids and
depth." Events whose cascade was clipped at the depth cap (§6.4.3) show an ∞ badge here.

### 3.4 Time scrubbing

`←`/`→` step ±1 tick, `Shift+←/→` ±25 ticks, `:goto 371` jumps. Scrubbing pauses the sim and
re-renders the world *as it was at that tick* (reconstructed from the nearest snapshot + event
replay, §4.4). A timeline ribbon appears above the footer while scrubbing, with tick marks at
crisis events and ◈ flags at checkpoints (§3.7). Pressing `space` at a past tick offers:
*resume from live*, *fork from here*, or *save a checkpoint here*.

### 3.5 God mode & forking (counterfactual play)

`g` opens the intervention palette — **generated at runtime from the event registry** (§4.7),
so adding an intervention is one `EventSpec`, zero UI changes. Kinds range from droughts,
assassinations, and minting through meteor strikes and splitting a country in two; some take
two nations (force a war, forge an alliance, embargo), some target infrastructure (§6.7.4).
Each injects a root event (`is_intervention=True`, rendered violet with the glyph `✦`).

An intervention at a past tick (or `f` at any tick) **forks the timeline**. Up to **three
concurrent forks** (`B`/`C`/`D`), all branching from prime, are switchable in a tab bar.
Compare mode splits the screen against the focused fork:

```
┌─ FORK @ t371 ── A: prime ──────────────┬── B: "no drought" (✦ intervention) ─┐
│ t412 ⚡ Bread riots erupt in Elbonia    │ t412 ▸ Elbonia posts record grain   │
│ t408 ▸ Elbonian crown slides 8%        │      surplus; crown steady          │
│ ...                                    │ ...                                 │
│ ΔWORLD @ t450:  Elbonia stab 31%→74%  infl 11.2%→3.0%   Kravonia gdp -12%→+2%│
└─────────────────────────────────────────────────────────────────────────────┘
```

The `ΔWORLD` strip shows per-country stat diffs at the current tick. Timelines share the seed
and RNG discipline, so **every divergence is attributable to the intervention** (fork reseed
rule §4.3.5) — that is the demo, the fun, and the interview story in one screen. A fork can be
discarded, or **adopted** as the new prime — its sibling forks dissolve.

**Direct stat edits** mutate the focused timeline in place, without forking: `godEdit`
(stability / inflation / gdp / population / treasury / grain / fx), `godRelation` (raising a
relation above −30 auto-ends a war), `godPeace`. Each emits a synthetic root event
(`GOD_EDIT`, `GOD_RELATION_SHIFT`, or `PEACE` with `is_intervention=True`) carrying the
before/after values in its payload, so subsequent organic consequences trace back to it. The
advanced setting `silent_god_edits` (§6.9) suppresses these event records; the frontend shows
a ⚠ "paper trail disabled" badge while it is on.

> **r4 note (§12.1.4, §12.3.4):** god mode grows into an unbounded intervention surface
> post-v1 (raw event injection, physics editing, multi-tick operations). v1 code must never
> enumerate intervention kinds by name outside the registry — palette, bridge catalog, and
> tests must iterate `EVENT_REGISTRY` filtered on `is_intervention`.

### 3.6 Headless mode

`meddler run --headless --seed 42 --ticks 1000` prints one headline per line
(`t0412 [ELB] Bread riots erupt…`), suitable for piping and used by the golden-master test.
`--trace <event_id>` prints an ancestry tree as text. No web/bridge import in this path.

### 3.7 Deep time: the Annals, checkpoints, restart

The prototype's v1.3 pass (contract §9) added the long-view UX. The real engine must back it:

- **The Annals** (`h`): the world's long record — named eras with epigraphs, wars of record
  with tolls and outcomes, records & superlatives that give raw numbers meaning, filterable by
  country and by kind; plus a ✦ ACTS OF GOD tab listing every intervention with its ripple
  count and a one-click trace. The prototype fabricates all of this client-side as set
  dressing; the real engine supplies a queryable event archive
  (`annals {country?}` → `{eras, wars, records, majorEvents}`) so eras and records derive from
  actual history.
- **Checkpoints** (`c`): client-side bookmarks `{id, tick, label, note}` drawn as ◈ flags on
  the ribbon — any tick, including a scrubbed past one. The only engine round-trip is restart;
  the engine may later persist them (`saveCheckpoint`/`listCheckpoints`), but the contract only
  requires `restart`.
- **Begin anew** (`restart {tick}`): rewinds prime to `tick`, truncates all later history and
  scheduled consequences, reseeds the RNG (§4.3.6 — the rewound world does not replay itself),
  and resumes live. Countries born after `tick` un-happen. Refused while forks exist.

### 3.8 The three control tiers

The user controls the simulation through three tiers, all available at runtime:

1. **Play controls** (always visible): pause / resume / speed / step / scrub / restart — the
   contract §4 and §9 commands.
2. **God mode** (`g`): the intervention palette and direct edits of §3.5.
3. **Advanced settings** (⚙ overlay): the `WorldSettings` keys of §6.9, grouped by category
   with inline descriptions — sliders for floats, toggles for bools, number inputs for ints,
   tag multiselect for event filtering. Sent via `updateSettings`; acked with the full current
   settings; keys marked *restart required* are flagged in the ack.

## 4. Architecture

### 4.1 Layer diagram (dependencies point down; NEVER up)

```
web/                 the frontend: plain JS, speaks docs/frontend-contract.md over WebSocket
meddler/bridge/    WebSocket server + protocol adapter                 (imports engine, text)
meddler/text/      headline templates, name generator                  (imports engine types only)
meddler/engine/    tick loop, systems, registry, events, ledger, forks (imports nothing above; NO I/O)
```

Hard rules: `engine/` must never import `websockets` or anything from `bridge/`
or `text/`. It does no printing, no file I/O (save/load helpers take file objects), no socket
I/O, no wall-clock reads. `bridge/` contains **no simulation logic** — it is purely translation
and transport. CI enforces the layering with an import-linter contract (task M0.4).

### 4.2 Core loop

```python
def tick(world: World, rng: Rng) -> tuple[World, list[Event]]:
    """Advance one tick. Pure: same (world, rng state) -> same result."""
```

Systems run in a **fixed order** each tick (order is part of the spec, §6.1). Each system
returns events; events are applied to state immediately by the system that emits them (state
changes and their event records are created together, atomically — see Ledger, §4.5).

1 tick = 1 simulated day. Default playback 1 tick/sec, speeds {0.5, 1, 2, 4} (UI-side timer;
the engine has no clock).

### 4.3 Determinism contract (binding rules for all engine code)

1. Exactly **one** RNG stream (`numpy` not required; use `random.Random(seed)` wrapped in our
   `Rng` class) threaded explicitly through `tick`. Never `random.random()` module-level, never
   `time.time()`, never `uuid4()`, never `os.urandom`.
2. Event IDs are sequential integers assigned by the event log, not UUIDs.
3. All dict/set iteration that feeds RNG consumption or event ordering must be over **sorted
   keys**. Countries are stored in a list in fixed order.
4. Headline template selection hashes the event ID (`event.id % len(templates)`), so replays and
   forks produce identical prose without consuming RNG.
5. Forks: timeline B gets `Rng(seed=hash((base_seed, fork_tick, "fork", fork_id)))`. Documented
   so diffs are reproducible.
6. Restart: after `restart {tick}` the prime timeline continues with
   `Rng(seed=hash((base_seed, restart_tick, "genesis")))` — deliberately fresh dice, documented
   so a save recipe (§4.4) replays identically.
7. The scheduled-consequence queue (§6.4.2) is ordered by `(fire_tick, schedule_seq)` where
   `schedule_seq` is a sequential counter assigned at schedule time; delay draws and consequence
   rolls consume the single RNG stream in that order.
8. Any violation is a bug of the highest severity. The golden-master CI test exists to catch it.

### 4.4 Event sourcing, snapshots, scrubbing, forks, restart, saves

- **Event log:** append-only, sequential `EventLog` semantics backed by a per-session SQLite file.
  Each event, causal edge, normalized country/effect index, exact RNG state, and country-stat sample
  is stored once; only a bounded hot-event/sample cache remains in Python memory.
- **Snapshots:** compact deep-copied `World` state every `snapshot_interval` ticks (default 50).
  A snapshot carries a lightweight immutable SQLite log boundary, not copied historical events.
  World state for tick *t* = nearest snapshot ≤ *t*, then replay indexed recorded events (replay
  applies stored ledger and stat deltas — it does NOT re-run systems, so it needs no RNG and cannot
  diverge).
- **Fork:** a writable SQLite branch sharing the prime timeline's immutable prefix, plus the
  intervention event and the prime's exact recorded RNG state at the fork tick. A `Multiverse`
  container holds prime plus up to **3** fork `Timeline` objects (`B`/`C`/`D`); adoption promotes
  one branch, reclaims sibling/private futures, and the UI reads all of them (contract §7).
- **Restart:** `restart {tick}` truncates the prime log and schedule queue after `tick`,
  restores the world via snapshot+replay, reseeds per §4.3.6, and resumes. Refused while forks
  exist. Checkpoints themselves are client-side bookmarks (§3.7).
- **Save/load** (`:save name`, `:load name`): a save is a *recipe*, tiny and diff-friendly —
  seed + settings (§6.9) + the ordered list of timeline-mutating commands (interventions, god
  edits, restarts, settings changes) with their ticks. Loading replays from tick 0. Format:
  JSON, versioned. *r4 note (§12.3.7):* loaders must ignore unknown keys rather than crash —
  r4 bumps the schema version and adds keys; v1 saves must load under r4 without a migration
  tool.

### 4.5 The Ledger (the invariant backbone)

Every mutation of a money pool goes through `Ledger.transfer(event, src_pool, dst_pool, amount,
currency)` or `Ledger.mint/burn(event, pool, amount, currency)`. Entries are stored **on the
event**. Rules:

- Pools per country: `treasury`, `households`, `corporates` (all in local currency), plus one
  `fx:<pair>` bookkeeping pool per conversion (conversions are recorded as burn X + mint Y at
  the logged rate — cross-currency totals are *not* conserved; per-currency totals are).
- **Invariant A (conservation):** for each currency, `sum(all pools)` changes only by explicit
  mint/burn entries.
- **Invariant B (reconstruction):** replaying all ledger entries from tick-0 state reproduces
  the current pools exactly. This single property test catches most economy bugs.
- Money is stored in integer minor units (no floats in pools). Rates/percentages are floats but
  every application rounds via one shared `round_money()` helper (banker's rounding).

> **r4 note (§12.1.3, §12.3.1):** the Ledger/StatDelta pattern — every mutation recorded on an
> owning event — is the substrate the post-v1 attribution ledger builds on. Never mutate any
> world state (money or stats) without an owning `Event` carrying the delta.

### 4.6 Event schema

```python
@dataclass(frozen=True)
class Event:
    id: int                  # sequential per timeline
    tick: int
    kind: str                # registry key (§4.7) — was a closed enum in r1
    country: str | None      # primary country code, e.g. "ELB"
    country2: str | None     # secondary code for two-target kinds, else None
    parent_id: int | None    # None => root cause (exogenous or intervention)
    depth: int               # 0 for roots, parent.depth+1 otherwise
    is_intervention: bool
    payload: dict[str, float | int | str]   # kind-specific; documented per kind
    ledger: tuple[LedgerEntry, ...]          # money deltas this event caused
    severity: int            # 0 info, 1 notable, 2 crisis (drives glyphs/thresholds)
```

`depth` is capped at `MAX_DEPTH = 8` (tunable, §6.9); a would-be consequence past the cap is
dropped and the parent's payload gains `"cascade_clipped": true` so the trace view can show an
∞ badge (§6.4.3).

### 4.7 The event registry (plugin-style event architecture)

r1 had ~25 event kinds in a closed enum. The full simulation needs hundreds. Hard-coding every
kind into a monolithic switch is unmaintainable, so the engine uses a **declarative event
registry** — each event kind is a self-contained descriptor that the core loop can execute
without knowing about.

```python
@dataclass(frozen=True)
class EventSpec:
    kind: str                          # unique string key, e.g. "SATELLITE_FAILURE"
    severity: int                      # 0 info · 1 notable · 2 crisis
    category: EventCategory            # enum grouping for UI filtering (one per kinds/ file)
    targets: int                       # 1 = one country; 2 = ordered pair (primary, secondary)
    is_exogenous: bool                 # can fire as a root event with no parent
    exogenous_base_p: float            # per-tick probability if is_exogenous (0 if not)
    is_intervention: bool              # god-mode kind; never rolls organically
    stat_deltas: dict[str, float]      # e.g. {"stability": -6, "gdp_mult": 0.95}
    pool_transfers: list[PoolTransfer] # ledger entries (may be empty)
    consequences: list[ConsequenceRule]
    conditions: list[Condition]        # preconditions that must hold for this event to fire
    tags: frozenset[str]               # free-form tags for settings filtering, e.g. "war","infra"
    low_frequency_ok: bool = False     # exempt from the M7.1 statistical-coverage test
```

Headline prose does **not** live on the spec — templates stay in `text/headlines.py`, keyed by
`kind` (§6.5), preserving the layer rule (§4.1: the engine is text-free). `registry.validate_all()`
checks the registry's internal consistency (every referenced `child_kind` exists, probabilities
∈ [0,1], delay ranges sane); startup code in `cli.py`/`bridge/` plus a property test (§8.4)
cross-check that every registered kind has ≥ 4 templates.

```python
@dataclass(frozen=True)
class ConsequenceRule:
    child_kind: str
    base_p: float                      # before cascade decay (§6.4.1)
    delay_min: int                     # ticks (§6.4.2)
    delay_max: int
    target: Literal["same", "foe", "ally", "random", "all_at_war"]
    payload_template: dict             # static values merged into child payload
    conditions: list[Condition]        # additional conditions beyond the depth cap
```

```python
@dataclass(frozen=True)
class Condition:
    stat: str                   # e.g. "stability", "treasury", "asset.satellites.count"
    op: Literal["<", ">", "<=", ">=", "==", "!="]
    value: float
    target: Literal["primary", "secondary", "any", "all"]
```

Conditions are evaluated against the current world state at schedule time (for exogenous rolls)
and again at fire time (for consequences). If a condition fails at fire time, the event is
silently dropped — it does not consume a queue slot.

`engine/registry.py` owns a global `EVENT_REGISTRY: dict[str, EventSpec]`. All built-in kinds
are registered at import time from `engine/kinds/` — one file per category (natural, economy,
politics, military, diplomatic, infrastructure, social). Adding a new kind = one `EventSpec` in
the appropriate file plus ≥ 4 headline templates in `text/headlines.py`. No changes to the core
loop are required: `ConsequenceSystem` iterates the schedule queue, looks up the `EventSpec`,
evaluates conditions, applies `stat_deltas` and `pool_transfers`, and schedules children. That
is the entire execution path — there is no special-case code per kind.

**Interventions are EventSpecs too** (`is_intervention=True`, `is_exogenous=False`). The god-mode
palette is generated at runtime from the registry, and the settings layer (§6.9) can
enable/disable individual kinds by tag or explicit allowlist — adding a new intervention is one
spec, no UI code changes.

> **r4 note (§12.3.5):** treat `Event.payload` and schedule-queue entries as open dicts — no
> code may whitelist payload keys or assume a closed set. r4 adds keys (e.g. `operation_id`,
> attribution metadata) to existing kinds without redefining them.

## 5. World model & generation

### 5.1 Dynamic country registry

The fixed-8-country assumption is dropped. `World.countries` is an ordered list that grows at
runtime via `SECESSION` events and shrinks (countries become `status=ANNEXED` or
`status=DISSOLVED`) via conquest and collapse (§6.8). The worldgen seed produces a starting set
(default 8, configurable §6.9); the registry just isn't closed after that. The frontend already
handles this (`countryAdded`, contract §6; pruning on restart, contract §9).

A country's full stat block:

```python
@dataclass
class Country:
    code: str                    # unique 3-letter, immutable
    name: str
    parent_code: str | None      # None for founding countries; set on secession
    status: CountryStatus        # ACTIVE | OCCUPIED | ANNEXED | DISSOLVED
    born_at_tick: int

    # economics
    population: float            # millions, live stat — changes from plague/meteor/secession
    gdp_tick: float
    grain_output: float
    grain_need: float
    grain_stock: float
    inflation: float             # %
    tax_rate: float              # %
    innovation_mult: float       # 1.0 baseline; buffed by INNOVATION events
    exchange_rate: float         # local per 1 veri
    pools: dict[str, int]        # "treasury","households","corporates" → minor units

    # social
    stability: float             # 0–100
    civil_rights: float          # 0–100; suppressed by crackdowns, raised by reform
    press_freedom: float         # 0–100; affects scandal visibility and comms blackouts
    education: float             # 0–100; affects innovation probability and leader quality
    health: float                # 0–100; affects labour productivity and plague severity

    # infrastructure (§6.7)
    infrastructure: InfrastructureBlock

    # political
    leader: Leader
    govt_type: GovtType          # DEMOCRACY | AUTOCRACY | MONARCHY | JUNTA | ANARCHY
    election_due_tick: int | None
    at_war_with: list[str]       # list of country codes
    occupied_by: str | None      # code of occupying country, or None

    # threshold hysteresis arms (not serialised to save files; reconstructed on load)
    armed: dict[str, bool]
```

### 5.2 Relations matrix

Stored on `World.relations: dict[tuple[str,str], float]` (always sorted pair as key,
value ∈ [-100, 100]). Updated by:

- Events that modify relations carry `relation_delta` in their `EventSpec.stat_deltas`
  (keyed `"relation:secondary"` for the pair); the system applies it.
- Passive decay: each tick, every value drifts toward 0 by `relation_decay_rate * abs(value)`
  (default 0.005/tick → half-life ~140 ticks). Old grudges fade; alliances cool.
- War: relation set to −90 on `WAR_DECLARED`; peace negotiation required to recover.
- Alliance: relation set to +70 on `ALLIANCE`; decays from there.

`RelationsSystem` (position 8 in the tick order, §6.1) handles decay and emits `RELATION_SHIFT`
events (severity 0) when a pair crosses a meaningful threshold (−60 → war-eligible, −30 →
rivals, 0 → neutral, +50 → allies).

### 5.3 Generation at seed

- **`starting_country_count` countries** (default 8), generated from seed: name
  (syllable-grammar generator, e.g. Freedonia, Elbonia, Kravonia, Zubrowka style), 3-letter
  code, currency (name, symbol from a curated list), leader (name + 2 personality traits from:
  `paranoid, reformist, corrupt, populist, technocrat, warhawk, frugal, flamboyant` — traits
  bias event probabilities and headline word choice), starting stats sampled from legible
  ranges (population 2–60M, gdp/capita tiers, stability and inflation ranges from §6.9's
  worldgen settings; defaults 40–85 and 1–6%).
- **Relations** seeded slightly negative between neighbours of similar size (`rival_pairs`
  seeded pairs, default 4 — rivalry is the narrative engine); grudges decay per §5.2.
- **One tradable staple, "grain"** (v1 has exactly one commodity; do not add more). Each
  country has `grain_output` and `grain_need`; surplus/deficit drives trade.
  *r4 note (§12.1.2, §12.3.2):* r4 adds two more commodities (fuel, goods). Keep
  market/trade logic in functions parameterized on `(supply, need, stock, ...)` rather than
  smearing `grain_` literals across systems — one commodity is a parameter value, not an
  architecture. The `grain_*` fields themselves stay exactly as specced.
- **Infrastructure genesis:** each country gets an `InfrastructureBlock` (§6.7) with per-class
  condition sampled from 0.7–0.9 and counts derived from its population tier.

## 6. Simulation design (exact v1 mechanics)

Implement these formulas as written; tune constants only via `engine/config.py`,
which centralizes every magic number with a comment. Values that appear in §6.9's settings
tables are read from `WorldSettings` at runtime; `config.py` holds their compile-time defaults.

### 6.1 System order (fixed, per tick)

1. `ProductionSystem` — grain + GDP output
2. `TradeSystem` — match surplus→deficit, move money/grain
3. `FxSystem` — update exchange rates from trade balances
4. `FiscalSystem` — collect taxes, pay subsidies, treasury drift
5. `InfrastructureSystem` — maintenance funding, condition degradation, failure rolls (§6.7)
6. `InflationSystem` — update inflation from minting/shortage pressure
7. `StabilitySystem` — update stability from inflation, shortage, war, leader traits
8. `RelationsSystem` — relation decay + threshold `RELATION_SHIFT` events (§5.2)
9. `PoliticsSystem` — elections (~every 90 ticks per country), coups, leader changes,
   occupation/annexation rolls (§6.8)
10. `ExogenousSystem` — rolls root events from the registry (respecting `drama_multiplier` and
    the enabled/disabled tag settings, §6.9)
11. `ConsequenceSystem` — fires due entries from the schedule queue per the cascade model (§6.4)

### 6.2 Country stats & toy formulas (legible on purpose)

Stats per country: see the `Country` block in §5.1.

- `gdp_tick = base_gdp * (0.5 + stability/200) * innovation_mult` → paid into `corporates`,
  then wages `0.6 * gdp_tick` corporates→households.
- Tax: `treasury += tax_rate * gdp_tick` (from corporates + households proportionally).
- Trade: deficit country buys grain at `price = base_price * (1 + shortage_ratio)²`; payment
  converts through FX (ledger burn/mint).
- FX drift: `exchange_rate *= 1 + 0.002 * tanh(trade_balance_norm) + drift_from_inflation`.
- Inflation: `+0.05pp` per 1% of money supply minted this tick; `+0.3pp` if grain shortage;
  decays 2% of itself per tick toward 2% baseline.
- Stability: `-0.4/tick` if inflation > 8%; `-1.0/tick` during shortage; `-0.3/tick` while at
  war; `-5` one-shot on scandal; `+0.1/tick` if inflation < 4% and no shortage; clamp 0–100.
- **Population** (millions, float) is a live stat: natural growth `+0.002%/tick`
  (≈ 0.7%/year); war attrition `-0.01%/tick` per ongoing war (large battles carry their own
  event deltas); famine `-0.05%/tick` while `grain_stock < 3 days`; plague/meteor/earthquake/
  secession apply their `EventSpec` deltas (secession splits population proportionally between
  rump and child). Population feeds back into GDP and unrest thresholds.
- **Social stats** bias probabilities rather than money: `education` scales the `INNOVATION`
  exogenous roll; `health` scales plague severity and labour productivity; `press_freedom`
  scales scandal visibility (comms blackouts suppress it, §6.7.3); `civil_rights` is moved by
  crackdowns/reforms and feeds stability drift.

### 6.3 Drama thresholds (stat → event bridge)

Threshold crossings emit events (with the *causing* system's event as parent where one exists,
else root): `inflation > 8%` → `INFLATION_CRISIS`; `stability < 35` → `UNREST`;
`stability < 15` → `CIVIL_WAR_RISK` (PoliticsSystem may then roll a coup; may cascade to
`SECESSION`); `grain_stock < 10 days` → `FAMINE_WARNING`; `grain_stock = 0` → `FAMINE`;
`treasury < 0` → `DEBT_CRISIS` (forces mint → inflation → the doom spiral is emergent,
traceable, and fun). Asset-condition thresholds roll infrastructure failures per §6.7.3.
Each threshold has hysteresis via the country's `armed` dict (re-arm only after recovering
past a margin) so the feed doesn't spam.

> **r4 note (§12.3.6):** keep the threshold bridge's parent-attribution logic ("which event
> caused this crossing") in one module, never copied into individual systems — it is exactly
> the code the r4 attribution ledger (§12.1.3) replaces with weighted multi-parent edges.

### 6.4 Cascade model

#### 6.4.1 Weight decay applies to spawn probability, never to severity

The `depth` field on an event governs how likely a consequence is to be *spawned*, not how
damaging it is if it fires. A satellite crashing because a treasury ran dry because of a war
is just as catastrophic as a satellite crashing from a direct intervention. The formula:

```
p_spawn = base_p * cascade_decay ^ parent.depth
```

`cascade_decay ∈ (0.5, 0.85)` (default `0.70`, tunable §6.9). At depth 5 with the default,
`p_spawn ≈ 0.168 * base_p` — rare enough to feel earned, but still consequential. Severity,
stat deltas, and ledger amounts are intrinsic to the event kind and are never modified by depth.

#### 6.4.2 Consequence timing

Every `ConsequenceRule` carries `delay_min` and `delay_max` (in ticks). The engine draws
`delay = rng.randint(delay_min, delay_max)` and schedules the consequence at `tick + delay`
(queue ordering per §4.3.7). This prevents same-tick spam and gives the world narrative
breathing room. Delays are tuned so chains feel causal but not instantaneous:

| Cause → Effect class | delay_min | delay_max |
|---|---|---|
| Physical shock → price response | 2 | 8 |
| Price/economic → currency/FX | 3 | 10 |
| Economic strain → social response | 10 | 35 |
| Social unrest → political action | 5 | 20 |
| Political change → policy effect | 1 | 5 |
| Infrastructure neglect → failure | 30 | 120 |
| War → economic damage | 1 | 6 |
| Health crisis → labour shortage | 8 | 25 |

All delay ranges live in `config.py` keyed by `(parent_kind, child_kind)` tuples, falling back
to a category-level default if no specific pair is defined.

#### 6.4.3 Depth cap and overflow

`MAX_DEPTH = 8` (tunable §6.9). A would-be child at depth 9 is dropped; the parent event's
payload gains `"cascade_clipped": true` so the trace view can show an ∞ badge. A per-timeline
counter tracks total clips; if it exceeds `CLIP_BUDGET_PER_1000_TICKS` (default 12),
`config.py` is likely under-tuned and a warning is logged to the design notes.

### 6.5 Headline generation (`text/`)

- Per event kind, ≥ 4 templates in a Python dict keyed by the kind string:
  `"Bread riots erupt in {country} as inflation hits {inflation:.0f}% — {leader_title} {leader} blames {scapegoat}."`
- Slot fillers come from the event payload + country/leader state; `scapegoat`-style flavor
  lists are selected by hashing `event.id` (determinism rule §4.3.4). Leader traits bias word
  choice (a `paranoid` leader blames spies; a `populist` blames elites).
- Severity 2 events render with an all-caps lead-in word. No LLMs, no network.
- Coverage is enforced: startup cross-check + property test (§4.7, §8.4).

### 6.6 Event catalog

The **starting catalog**, organised by category — each category is one file in `engine/kinds/`.
New kinds can be added at any time by adding one `EventSpec` (plus templates) without touching
the core loop. Shorthand: `→` means "can spawn as consequence"; `p=` is base probability before
cascade decay; `d=` is delay range in ticks.

**Build order:** M3.3 implements the core subset the mock already exercises (`DROUGHT`,
`EARTHQUAKE`, `METEOR`, `PLAGUE`, `SCANDAL`, `ELECTION`, `UNREST`, `SECESSION`, `DEBT_CRISIS`,
`INFLATION_CRISIS`, `GOLDEN_AGE`, `INNOVATION`, `FAMINE_WARNING`, `WAR_DECLARED`, `PEACE`, plus
the intervention kinds); M7.1 populates the rest.

#### 6.6.1 Natural / exogenous

| Kind | Sev | Root p/tick | → Consequences |
|---|---|---|---|
| `DROUGHT` | 2 | 0.006 | `PRICE_SPIKE` p=0.95 d=2-8 |
| `FLOOD` | 2 | 0.003 | `INFRASTRUCTURE_DAMAGE` p=0.8 d=1-4, `GRAIN_LOSS` p=0.7 d=1-3 |
| `EARTHQUAKE` | 2 | 0.002 | `INFRASTRUCTURE_DAMAGE` p=0.9 d=0-2, `TREASURY_DRAIN` p=0.8 d=2-5 |
| `METEOR` | 2 | 0.0002 | `PRICE_SPIKE` p=1.0 d=3-6, `UNREST` p=0.7 d=8-20, `INFRASTRUCTURE_DAMAGE` p=1.0 d=0-1 |
| `PLAGUE` | 2 | 0.002 | `LABOUR_SHORTAGE` p=0.7 d=8-25, `UNREST` p=0.6 d=10-30, `TREASURY_DRAIN` p=0.6 d=4-10 |
| `WILDFIRE` | 1 | 0.003 | `GRAIN_LOSS` p=0.6 d=1-4, `INFRASTRUCTURE_DAMAGE` p=0.4 d=1-3 |
| `COLD_SNAP` | 1 | 0.004 | `GRAIN_LOSS` p=0.5 d=1-5, `POWER_OUTAGE` p=0.3 d=2-6 |
| `LOCUST_SWARM` | 2 | 0.001 | `GRAIN_LOSS` p=0.95 d=1-4, `FAMINE_WARNING` p=0.5 d=5-15 |
| `VOLCANIC_ERUPTION` | 2 | 0.0005 | `INFRASTRUCTURE_DAMAGE` p=1.0 d=0-2, `GRAIN_LOSS` p=0.8 d=2-6, `POPULATION_LOSS` p=0.5 d=0-2 |

#### 6.6.2 Economic

Exogenous roots in this category: `INNOVATION` p=0.003/tick (scaled by `education`),
`GOLDEN_AGE` p=0.0008/tick, `BOOM_BUST` p=0.001/tick. Everything else fires only as a
consequence or via threshold (§6.3).

| Kind | Sev | → Consequences |
|---|---|---|
| `PRICE_SPIKE` | 1 | `CURRENCY_SLIDE` p=0.75 d=3-10, `TREASURY_DRAIN` p=0.5 d=2-5, `FAMINE_WARNING` p=0.3 d=5-15 |
| `CURRENCY_SLIDE` | 1 | `INFLATION_CRISIS` p=0.6 d=5-15 |
| `INFLATION_CRISIS` | 2 | `UNREST` p=0.7 d=10-30, `CAPITAL_FLIGHT` p=0.3 d=15-40 |
| `MINT` | 1 | `CURRENCY_SLIDE` p=0.9 d=3-8 |
| `DEBT_CRISIS` | 2 | `MINT` p=0.8 d=2-5, `CREDIT_FREEZE` p=0.5 d=5-15 |
| `TREASURY_DRAIN` | 1 | (none organic; threshold watches for `DEBT_CRISIS`) |
| `GDP_BOOM` | 1 | `IMMIGRATION_WAVE` p=0.2 d=20-60 |
| `GDP_TICK_REDUCTION` | 1 | (stat delta: lowers `gdp_tick`; recovery via `GDP_TICK_RESTORATION` or organic drift) |
| `GDP_TICK_RESTORATION` | 0 | (stat delta: removes a prior reduction) |
| `RECESSION` | 2 | `UNEMPLOYMENT_SPIKE` p=0.8 d=5-20, `TREASURY_DRAIN` p=0.6 d=3-10 |
| `CAPITAL_FLIGHT` | 1 | `CURRENCY_SLIDE` p=0.8 d=2-8, `CREDIT_FREEZE` p=0.4 d=5-15 |
| `CREDIT_FREEZE` | 1 | `RECESSION` p=0.5 d=10-30, `COMPANY_COLLAPSE` p=0.4 d=15-40 |
| `COMPANY_COLLAPSE` | 1 | `UNEMPLOYMENT_SPIKE` p=0.9 d=2-8, `TREASURY_DRAIN` p=0.3 d=3-10 |
| `UNEMPLOYMENT_SPIKE` | 1 | `UNREST` p=0.4 d=15-35 |
| `GRAIN_LOSS` | 1 | `PRICE_SPIKE` p=0.8 d=2-8, `FAMINE_WARNING` p=0.4 d=8-20 |
| `LABOUR_SHORTAGE` | 1 | `GDP_TICK_REDUCTION` p=0.8 d=3-10, `IMMIGRATION_INCENTIVE` p=0.3 d=20-60 |
| `TRADE_HALT` | 1 | `PRICE_SPIKE` p=0.7 d=3-8, `TREASURY_DRAIN` p=0.5 d=2-6 |
| `EMBARGO` | 1 | `TRADE_HALT` p=0.9 d=1-3 (target country), `CURRENCY_SLIDE` p=0.5 d=5-12 (target) |
| `BOOM_BUST` | 2 | `RECESSION` p=0.9 d=20-60 |
| `INNOVATION` | 1 | `GDP_BOOM` p=0.8 d=6-15 |
| `GOLDEN_AGE` | 1 | `GDP_BOOM` p=0.9 d=10-20, `IMMIGRATION_WAVE` p=0.4 d=30-80 |
| `IMMIGRATION_WAVE` | 0 | (population increase, no chain) |
| `IMMIGRATION_INCENTIVE` | 0 | `IMMIGRATION_WAVE` p=0.5 d=30-90 |

#### 6.6.3 Political

Exogenous roots: `SCANDAL` p=0.004/tick (scaled by the target's `press_freedom` — suppressed
media hides scandals, §6.7.3). `ELECTION` is calendar-driven (~every 90 ticks per country via
`election_due_tick`), not rolled.

| Kind | Sev | → Consequences |
|---|---|---|
| `SCANDAL` | 1 | `STABILITY_DROP` p=0.7 d=2-8, `ELECTION_UPSET` p=0.3 d=5-15 |
| `ELECTION` | 1 | `LEADER_CHANGE` p=varies (incumbent traits, stability) d=1-2 |
| `ELECTION_UPSET` | 1 | `LEADER_CHANGE` p=0.9 d=1-2 |
| `LEADER_CHANGE` | 1 | `POLICY_SHIFT` p=0.8 d=1-5 |
| `POLICY_SHIFT` | 0 | (biases future system rolls) |
| `COUP` | 2 | `LEADER_CHANGE` p=1.0 d=1-2, `CRACKDOWN` p=0.6 d=1-4 |
| `COUP_RISK_UP` | 1 | (raises `PoliticsSystem` coup roll probability for 30 ticks) |
| `CRACKDOWN` | 1 | `STABILITY_DROP` p=0.3 d=5-15, `PRESS_SUPPRESSION` p=0.4 d=2-8 |
| `CIVIL_WAR_RISK` | 2 | `SECESSION` p=0.5 d=20-60 (if stability < 20) |
| `SECESSION` | 2 | (creates new country; no further chain except relation drop) |
| `REVOLUTION` | 2 | `LEADER_CHANGE` p=1.0 d=0-2, `GOVT_TYPE_CHANGE` p=0.6 d=5-15 |
| `GOVT_TYPE_CHANGE` | 1 | `POLICY_SHIFT` p=1.0 d=1-3 |
| `REFERENDUM` | 1 | outcome determines `POLICY_SHIFT` or `SECESSION` |
| `PROPAGANDA_CAMPAIGN` | 0 | `STABILITY_DROP` prevented for 30 ticks |
| `ASSASSINATION` | 2 | `LEADER_CHANGE` p=1.0 d=1-2, `UNREST` p=0.8 d=3-10 |
| `PRESS_SUPPRESSION` | 1 | `SCANDAL` exogenous p halved for this country |
| `CIVIL_RIGHTS_REFORM` | 1 | `STABILITY_UP` p=0.7 d=5-15, `GDP_BOOM` p=0.2 d=20-50 |

#### 6.6.4 Military / diplomatic

Exogenous root: `WAR_SPARK` p=0.002/tick, eligible only while some relation < −60.

| Kind | Sev | → Consequences |
|---|---|---|
| `WAR_SPARK` | 1 | `WAR_DECLARED` p=0.85 d=1-4 (against the worst-relation eligible pair) |
| `WAR_DECLARED` | 2 | `TREASURY_DRAIN` p=1.0 d=1-4 (both), `REFUGEE_CRISIS` p=0.4 d=10-30 |
| `PEACE` | 1 | `GDP_TICK_RESTORATION` p=0.6 d=5-15 (both) |
| `ALLIANCE` | 1 | `RELATION_SHIFT` p=1.0 d=0-1 |
| `ALLIANCE_BROKEN` | 1 | `RELATION_SHIFT` p=1.0 d=0-1 |
| `RELATION_SHIFT` | 0 | (relation delta / threshold-crossing record; no chain) |
| `OCCUPATION_BEGIN` | 2 | `INFRASTRUCTURE_DAMAGE` p=0.8 d=5-20, `RESISTANCE_MOVEMENT` p=0.5 d=20-60 |
| `ANNEXATION` | 2 | (country dissolved; stats merged into annexer) |
| `LIBERATION_WAR` | 2 | treats as `WAR_DECLARED` with `OCCUPATION_END` as possible consequence |
| `OCCUPATION_END` | 1 | `STABILITY_UP` p=0.8 d=5-15 |
| `NAVAL_BLOCKADE` | 2 | `PORT_CLOSURE` p=0.9 d=1-3, `PRICE_SPIKE` p=0.7 d=3-8 (target) |
| `NAVAL_BATTLE` | 2 | `NAVAL_LOSS` p=0.6 d=0-2, `TREASURY_DRAIN` p=0.8 d=1-3 |
| `ARMS_DEAL` | 0 | treasury transfer; `RELATION_SHIFT` p=0.5 d=0-1 |
| `PROXY_WAR` | 2 | third country funds one side; `TREASURY_DRAIN` p=1.0 d=1-4 (funder) |
| `CEASEFIRE` | 1 | holds for a tick window; can break into `WAR_DECLARED` again |
| `TREATY` | 1 | `ALLIANCE` p=0.4 d=5-20, `TRADE_BOOST` p=0.5 d=5-15 |
| `TRADE_BOOST` | 0 | FX and GDP positive drift for both parties |
| `REFUGEE_CRISIS` | 2 | `UNREST` p=0.5 d=10-30 (receiving countries), population movement |
| `RESISTANCE_MOVEMENT` | 1 | `UNREST` p=0.6 d=10-30, `ASSASSINATION` p=0.2 d=20-60 |
| `NUCLEAR_TEST` | 2 | `RELATION_SHIFT` p=1.0 d=0-1 (all countries: −20 each); requires `allow_nukes=true` |
| `NUCLEAR_STRIKE` | 2 | catastrophic; requires `allow_nukes=true`; `POPULATION_LOSS`, `INFRASTRUCTURE_DAMAGE` both p=1.0 |

#### 6.6.5 Infrastructure

All kinds here fire from condition thresholds (§6.7.3) or as consequences; none are exogenous.

| Kind | Sev | → Consequences |
|---|---|---|
| `SATELLITE_FAILURE` | 2 | `COMMS_BLACKOUT` p=0.8 d=3-8 |
| `COMMS_BLACKOUT` | 2 | `MEDIA_SUPPRESSION` p=0.9 d=3-10, `COUP_RISK_UP` p=0.3 d=5-20 |
| `MEDIA_SUPPRESSION` | 1 | `UNREST` p=0.5 d=10-35; scandal exogenous p halved |
| `NAVAL_LOSS` | 2 | `SUPPLY_DISRUPTION` p=0.7 d=2-6 |
| `PORT_CLOSURE` | 2 | `TRADE_HALT` p=0.9 d=1-3 |
| `SUPPLY_DISRUPTION` | 1 | `PRICE_SPIKE` p=0.8 d=2-8 |
| `RAIL_COLLAPSE` | 2 | `GRAIN_TRANSPORT_FAILURE` p=0.9 d=2-6, `TREASURY_DRAIN` p=0.5 d=2-5 |
| `GRAIN_TRANSPORT_FAILURE` | 2 | `FAMINE_WARNING` p=0.8 d=3-10 |
| `POWER_OUTAGE` | 2 | `INDUSTRY_SHUTDOWN` p=0.8 d=1-4, `UNREST` p=0.3 d=10-25 |
| `INDUSTRY_SHUTDOWN` | 1 | `GDP_TICK_REDUCTION` p=0.9 d=1-3, `UNEMPLOYMENT_SPIKE` p=0.5 d=5-15 |
| `INFRASTRUCTURE_DAMAGE` | 1 | (condition delta on all asset classes, §6.7.2) |
| `INFRASTRUCTURE_RESTORED` | 0 | (positive condition delta on target class) |

#### 6.6.6 Social

| Kind | Sev | → Consequences |
|---|---|---|
| `UNREST` | 2 | `COUP` p=varies d=5-20, `CRACKDOWN` p=varies d=3-8, `CIVIL_WAR_RISK` p=0.1 d=20-60 |
| `FAMINE_WARNING` | 2 | `UNREST` p=0.5 d=10-30, `POPULATION_LOSS` p=0.3 d=15-40 |
| `FAMINE` | 2 | triggered when `grain_stock = 0`; `POPULATION_LOSS` p=1.0 d=1-5, `REVOLUTION` p=0.4 d=10-30 |
| `POPULATION_LOSS` | 2 | (stat delta; no further chain) |
| `STABILITY_DROP` | 1 | (stat delta; may trigger threshold `UNREST`) |
| `STABILITY_UP` | 0 | (stat delta) |
| `EDUCATION_DECLINE` | 1 | `INNOVATION` exogenous p reduced; `LABOUR_SHORTAGE` p=0.2 d=60-180 |
| `EDUCATION_REFORM` | 1 | `INNOVATION` exogenous p increased for 200 ticks |
| `BRAIN_DRAIN` | 1 | `EDUCATION_DECLINE` p=0.5 d=20-60, `GDP_TICK_REDUCTION` p=0.4 d=10-30 |
| `CULTURAL_RENAISSANCE` | 1 | `STABILITY_UP` p=0.7 d=5-15, `IMMIGRATION_WAVE` p=0.3 d=30-80 |
| `EPIDEMIC_FEAR` | 1 | `GDP_TICK_REDUCTION` p=0.6 d=3-10 (people stop going out) |
| `SPORTS_VICTORY` | 0 | `STABILITY_UP` p=0.5 d=1-3 (minor morale bump) |

### 6.7 Infrastructure assets

#### 6.7.1 Overview

Infrastructure is a real engine concept, not just frontend fiction. Each country carries an
`InfrastructureBlock` tracking counts and condition of asset classes. The engine tracks
aggregate counts and condition per class. The frontend may project the corresponding number of
satellite glyphs but must not invent individual mission histories or mutable health. Physical
in-flight shipments retain their real engine IDs and settlement fields (contract v2).

```python
@dataclass
class AssetClass:
    count: int           # total units (functional + degraded)
    condition: float     # 0.0–1.0 average; drives failure probability
    last_maintained_tick: int

@dataclass
class InfrastructureBlock:
    satellites: AssetClass
    naval_fleet: AssetClass      # cargo + military combined
    air_fleet: AssetClass
    rail_network: AssetClass     # condition = fraction of routes functional
    power_grid: AssetClass
    communications: AssetClass   # press_freedom × condition = effective media reach
```

#### 6.7.2 Maintenance system

`InfrastructureSystem` (position 5 in the tick order, §6.1). Each tick per country:

```
maintenance_cost = sum(asset.count * BASE_COST[class] for asset in infra)
affordable = min(maintenance_cost, treasury_available * MAINTENANCE_SHARE_CAP)
maintenance_ratio = affordable / maintenance_cost   # 0.0–1.0
```

If `maintenance_ratio < 1.0`, condition degrades:
```
condition -= (1 - maintenance_ratio) * DEGRADATION_RATE[class]
```

Low stability also degrades condition independently:
```
condition -= max(0, (50 - stability) / 50) * INSTABILITY_DEGRADATION[class]
```

Condition recovers slowly when maintenance is fully funded:
```
condition += maintenance_ratio * RECOVERY_RATE[class]
condition = clamp(condition, 0.0, 1.0)
```

All rates are in `config.py`. Infrastructure condition is serialised into snapshots and
participates fully in the ledger (maintenance payments are ledger transfers from treasury).

> **r4 note (§12.1.1, §12.3.3):** post-v1, asset classes become load-bearing — trade volume
> capped by fleet capacity, staple distribution by rail, GDP by grid throughput. v1 code must
> route all count/condition effect math through helpers in `engine/assets.py` (e.g.
> `effective_capacity(asset_class)`), never inline `count * condition` at call sites.

#### 6.7.3 Failure events and their chains

When an asset class's condition falls below a threshold, `InfrastructureSystem` rolls for
failure events. These are ordinary `EventSpec` entries (catalog §6.6.5):

| Kind | Condition threshold | Typical chain |
|---|---|---|
| `SATELLITE_FAILURE` | satellites < 0.4 | → `COMMS_BLACKOUT` → `MEDIA_SUPPRESSION` → `UNREST` |
| `NAVAL_LOSS` | naval_fleet < 0.4 | → `SUPPLY_DISRUPTION` → `PRICE_SPIKE` |
| `PORT_CLOSURE` | naval_fleet < 0.25 | → `TRADE_HALT` → `TREASURY_DRAIN` |
| `RAIL_COLLAPSE` | rail_network < 0.35 | → `GRAIN_TRANSPORT_FAILURE` → `FAMINE_WARNING` |
| `POWER_OUTAGE` | power_grid < 0.35 | → `INDUSTRY_SHUTDOWN` → `GDP_TICK_REDUCTION` |
| `COMMS_BLACKOUT` | communications < 0.3 | → `MEDIA_SUPPRESSION`, `COUP_RISK_UP` |

`COMMS_BLACKOUT` has a particularly rich cascade: suppressed media reduces scandal visibility
(exogenous `SCANDAL` events fire less often against this country — its `press_freedom` is
factored into the roll), but also raises unrest because people know something is wrong. A
paranoid leader uses it deliberately; a reformist one scrambles to restore it.

#### 6.7.4 Infrastructure as an intervention target

The intervention palette includes infrastructure-class interventions:

- `INTERVENE_DESTROY_SATELLITE` — `satellites.count -= 1`, fires `SATELLITE_FAILURE`
- `INTERVENE_NAVAL_BLOCKADE` — sets target naval routes to `condition = 0.1`, fires `PORT_CLOSURE`
- `INTERVENE_BLACKOUT` — fires `COMMS_BLACKOUT` directly at severity 2
- `INTERVENE_INFRASTRUCTURE_BOOST` — sets a class condition to 0.9, fires `INFRASTRUCTURE_RESTORED`
- `INTERVENE_POWER_GRID_FAILURE` — fires `POWER_OUTAGE` directly

These are normal `EventSpec` entries with `is_intervention=True`. The frontend renders them
with ✦ and violet; the engine handles them identically to organic failures.

#### 6.7.5 The satellite → media → politics chain (full spec)

This is the showcase deep chain. Documented in full so implementors can verify correctness:

```
LOW TREASURY (DEBT_CRISIS at tick T)
  → MINT (T+2..4) → CURRENCY_SLIDE (T+5..8) → INFLATION_CRISIS (T+10..18)
  [meanwhile]
  → MAINTENANCE_SHORTFALL (T+1) [not a fired event; a stat condition]
    → satellite.condition degrades over ~60 ticks
    → SATELLITE_FAILURE (T+60..90, p=0.4/tick while condition < 0.4; parented to the DEBT_CRISIS)
      → COMMS_BLACKOUT (T+63..98, p=0.8, delay 3..8)
        → MEDIA_SUPPRESSION (T+66..108, p=0.9, delay 3..10)
          → UNREST (T+76..143, p=0.5, delay 10..35)  [people notice silence]
          → COUP_RISK_UP (T+76..143, p=0.3)          [military sees opportunity]
```

`COUP_RISK_UP` sits four levels beneath the `DEBT_CRISIS` that started it — comfortably within
`MAX_DEPTH = 8`, and the trace view renders the full ancestry in one keypress, showing exactly
this chain.

### 6.8 Conquest and absorption

When a country's stability reaches 0 AND it is at war AND the enemy relation is < −80,
`PoliticsSystem` rolls for `OCCUPATION_BEGIN`. An occupied country:

- Has its leader replaced by a military governor.
- Pays `OCCUPATION_TRIBUTE` to the occupier each tick (ledger transfer).
- Has its infrastructure degraded (§6.7.2's instability term, amplified).
- Can be `ANNEXED` after 90+ ticks of occupation with low resistance, or `LIBERATED` if
  stability recovers and a relief war begins.

Annexed countries become `status=ANNEXED`; their code stops appearing in live stats frames.
The annexing country gains their territory (population, grain, a GDP boost). This is a
permanent world-state change, reversible only by a future liberation war or intervention.

### 6.9 User-configurable rules (`WorldSettings`)

Tier 3 of the control surface (§3.8). Sent from client to engine via:

```json
{ "cmd": "updateSettings", "settings": { "<key>": "<value>" } }
```

Engine replies `{ "type": "settingsAck", "settings": <full current settings> }` and immediately
applies changes. This command/reply pair is specified here for now; it lands in
`docs/frontend-contract.md` as §10 and is implemented when the bridge is built (M6). Settings
are serialised into save files (§4.4). Implement settings as a
`WorldSettings` dataclass on `World`; all systems read from it, never from `config.py` directly
for any value that appears here (`config.py` holds the compile-time defaults).

**World rules:**

| Key | Type | Default | Effect |
|---|---|---|---|
| `allow_secession` | bool | true | SECESSION events can fire |
| `allow_conquest` | bool | true | OCCUPATION/ANNEXATION can occur (§6.8) |
| `allow_nukes` | bool | false | Nuclear event kinds enabled (requires explicit toggle) |
| `allow_extinction` | bool | false | Meteor/plague can reduce population to 0 and dissolve a country |
| `max_countries` | int | 20 | Hard cap on live country count; secession refused beyond this |
| `max_wars_concurrent` | int | 4 | Engine will not start a new war if at or above this count |
| `protected_countries` | list[str] | [] | Codes immune to intervention targeting |
| `observer_only` | bool | false | All interventions disabled; god mode palette hidden |

**Simulation tuning:**

| Key | Type | Default | Effect |
|---|---|---|---|
| `cascade_decay` | float | 0.70 | §6.4.1 decay factor |
| `max_depth` | int | 8 | §6.4.3 depth cap |
| `drama_multiplier` | float | 1.0 | Scales all exogenous base_p values uniformly |
| `relation_decay_rate` | float | 0.005 | §5.2 per-tick decay |
| `ticks_per_year` | int | 365 | Cosmetic only — affects date display in UI |
| `snapshot_interval` | int | 50 | Ticks between full world snapshots |
| `enabled_event_tags` | list[str] | ["*"] | Whitelist of tags; "*" = all |
| `disabled_event_tags` | list[str] | [] | Blacklist; checked after whitelist |
| `silent_god_edits` | bool | false | God edits leave no event record (§3.5); frontend shows ⚠ |

**World generation (restart required — flagged in `settingsAck`):**

| Key | Type | Default | Effect |
|---|---|---|---|
| `starting_country_count` | int | 8 | Countries generated at seed |
| `starting_stability_range` | [float, float] | [40, 85] | worldgen sample range |
| `starting_inflation_range` | [float, float] | [1, 6] | worldgen sample range |
| `rival_pairs` | int | 4 | Seeded negative-relation pairs at start |

## 7. Tech stack & repo layout

- Python **3.11+**. Runtime deps: **`websockets`** (bridge only — the pure engine and the
  headless CLI have zero runtime deps). The web frontend has **no dependencies and no build
  step** (plain HTML/JS/CSS; see `web/README.md`). There is no Textual/`rich` dependency — r1's
  TUI was removed in r2.
- Dev deps: `pytest`, `hypothesis`, `ruff`, `mypy --strict` (engine at least), `import-linter`,
  `pytest-cov`.
- Everything `dataclass`-based; no Pydantic (no I/O boundary that needs it).

```
meddler/
  engine/    config.py, rng.py, model.py (World/Country/Leader/InfrastructureBlock/WorldSettings),
             events.py (Event, EventLog — string kind keys), ledger.py,
             registry.py (EVENT_REGISTRY, register(), validate_all()),
             kinds/ (natural.py, economy.py, politics.py, military.py, diplomatic.py,
                     infrastructure.py, social.py — each registers EventSpecs at import),
             systems/ (production, trade, fx, fiscal, infrastructure, inflation, stability,
                     relations, politics, exogenous, consequence — one file each, §6.1 order),
             assets.py (asset-condition helpers), settings.py (apply_settings()),
             tickloop.py, timeline.py (snapshots/forks/restart, Multiverse), worldgen.py, saves.py
  text/      headlines.py (templates keyed by kind), names.py
  bridge/    server.py (asyncio WebSocket server, ws://127.0.0.1:7677/ws),
             adapter.py (engine events → frontend-contract messages),
             commands.py (frontend-contract commands → engine calls)
  cli.py     argparse entry: run (headless) / serve (bridge) / --trace / --seed / --ticks
web/         the frontend (already built on the mock engine): index.html, style.css,
             app.js, globe.js, engine.js (mock), README.md
tests/       unit/, property/, golden/ (golden/seed1337_1000t.txt checked in), bridge/
docs/        frontend-contract.md, design-guide.md, original-vision.md (old README),
             design-decisions.md, demo.md
PROPOSAL.md  (this file)
README.md    (rewritten for the new project, task M8.3)
```

`bridge/` is the layer that connects the Python engine to the web frontend. It sits above
`engine/`/`text/` (may import them) and below nothing; it has **no simulation logic** — purely
translation and transport. The import-linter contract (M0.4) covers it. The bridge is optional:
`meddler run --headless` and the golden-master test never import it.

## 8. Testing strategy

1. **Golden master:** headless run, seed 1337, 1000 ticks → output must equal the checked-in
   file byte-for-byte. Regenerating it (`make golden`) is allowed only when a task explicitly
   changes simulation behavior; the diff must be eyeballed and mentioned in the commit message.
2. **Determinism test:** two fresh runs, same seed, in-process → identical event logs. Also:
   world reconstructed at tick 500 via snapshot+replay == world from continuous run; fork
   without intervention diffs to zero at every tick (§4.3.5); restart from tick T reproduces a
   deterministic — deliberately *different* — future under the genesis reseed (§4.3.6).
3. **Property tests (Hypothesis), over random seeds and 300+ ticks:** Invariant A (per-currency
   conservation), Invariant B (ledger reconstruction), stats in bounds (stability 0–100,
   condition 0–1, pools ≥ 0, depth ≤ `max_depth`), every non-root event's parent exists and
   `depth == parent.depth + 1`, relations ∈ [−100, 100], population ≥ 0.
4. **Registry tests:** `registry.validate_all()` passes (every referenced `child_kind` exists,
   probabilities ∈ [0,1], sane delay ranges); every registered kind has ≥ 4 headline templates
   and every template renders against a synthetic payload without KeyError; every intervention
   kind is reachable from the generated palette.
5. **Dynamic-world tests:** a scripted secession grows the country list and the new code appears
   in subsequent frames; a scripted conquest annexes a country and its code stops appearing;
   `restart` prunes countries born after the target tick.
6. **Unit tests** per system with hand-built minimal worlds ("two countries, one in drought").
7. **Bridge integration tests:** spin up `bridge/server.py`, connect a test WebSocket client,
   assert the `hello`/`status`/`snapshot`/`frame` handshake and that each contract §4/§7/§9/§10
   command produces its specified reply. No browser needed.
8. **Frontend smoke tests:** the headless `node --check` + `smoke*.js` harness already in
   `web/` (stubs `window`/canvas, asserts protocol behavior on the mock) — kept green as the
   contract evolves; see `docs/design-guide.md` §B8.

## 9. Milestones & tasks

Working rules: one task per branch/PR, in order within a milestone. Do not start
task N+1 with task N's tests red. Do not refactor outside the task's file list. Every task ends
with: tests added/updated, `ruff` + `mypy` clean, checklist in §10 ticked (edit this file).
When a formula or threshold feels wrong in play, do not silently change it — implement as
specced, then note it in `docs/design-decisions.md` under "tuning candidates".

The build order is: engine core → systems → drama → the signature engine capabilities → the
bridge that lights up the existing web frontend → the deep-simulation catalog → ship. The web
UX already exists on the mock engine (`web/`); most milestones make the *real* engine produce
what the frontend already knows how to render.

**M0 — scaffolding**
- M0.1 `pyproject.toml` (deps §7, entry point `meddler=meddler.cli:main` with `run`/`serve`
  subcommands), package dirs, `ruff`/`mypy`/`pytest` config. *Accept:* `pip install -e .` works;
  `meddler --version` prints.
- M0.2 Move old README → `docs/original-vision.md`; stub new README (quickstart pointing at
  `meddler serve` + `web/`, link to PROPOSAL.md). *Accept:* links resolve.
- M0.3 CI (GitHub Actions): lint, typecheck, tests on 3.11/3.12. *Accept:* green on push.
- M0.4 import-linter contract: `engine` imports nothing from `bridge`/`text`; `text` imports
  nothing from `bridge`; `bridge` may import `engine`/`text`. *Accept:* a violating import fails
  CI (prove with a temporary bad import on a test branch).

**M1 — deterministic core**
- M1.1 `rng.py` (seeded wrapper: `choice/uniform/roll(p)/randint/sub(seed_key)`), `config.py`,
  `events.py` (`Event` with **string** `kind` keys, sequential ids, `EventLog`), `registry.py`
  skeleton (`EVENT_REGISTRY`, `register()`, `validate_all()`). *Accept:* unit tests; determinism
  of id assignment.
- M1.2 `model.py` (`Country` full stat block §5.1, `InfrastructureBlock`/`AssetClass` §6.7,
  `WorldSettings` §6.9, dynamic country list) + `ledger.py` with Invariants A/B as Hypothesis
  tests over random transfer sequences. *Accept:* property tests pass; pools are ints.
- M1.3 `worldgen.py` (§5.3: `starting_country_count` countries, names, leaders, relations,
  `InfrastructureBlock` genesis at condition 0.7–0.9). *Accept:* same seed ⇒ identical world;
  names pronounceable (regex: alternating consonant/vowel clusters).
- M1.4 `tickloop.py` + `timeline.py`: tick scaffold with no-op systems, snapshots every
  `snapshot_interval` ticks, `world_at(tick)` replay, `Multiverse` (prime + up to 3 forks),
  `restart(tick)` truncate+reseed. *Accept:* determinism tests §8.2 (including fork-zero-diff and
  restart reseed).

**M2 — systems: economy, infrastructure, relations**
- M2.1–M2.5 the economy systems (Production, Trade, Fx, Fiscal, Inflation+Stability) per §6.2,
  one task each. *Accept per task:* unit tests with minimal worlds; invariants still pass; each
  system emits its events with correct parents.
- M2.6 `InfrastructureSystem` (maintenance funding, degradation, recovery §6.7.2) +
  `RelationsSystem` (decay + threshold `RELATION_SHIFT` §5.2). *Accept:* zero-treasury world
  degrades condition over time; a grudge decays toward 0 at the specced half-life.
- M2.7 Threshold bridge (§6.3) with hysteresis via each country's `armed` dict. *Accept:* a
  forced doom-spiral scenario (mint → inflation → unrest) produces the full parent-linked chain.

**M3 — drama (registry-driven events)**
- M3.1 `registry.py` complete (`EventSpec`/`ConsequenceRule`/`Condition` §4.7) + `ExogenousSystem`
  (rolls roots from the registry, honoring `drama_multiplier` and enabled/disabled tags) +
  `ConsequenceSystem` (cascade decay §6.4.1, scheduled delays §6.4.2 with queue order §4.3.7,
  depth cap §6.4.3). Register the core catalog subset (§6.6 build order) in `engine/kinds/`.
  *Accept:* over 2000 ticks × 10 seeds, ≥ 1 war and ≥ 3 crises occur; depth cap holds; clip
  budget not exceeded.
- M3.2 `PoliticsSystem`: elections/coups/leader change with trait re-bias; conquest/absorption
  rolls (§6.8). *Accept:* unit tests; a scripted stability-0 + active-war world triggers
  `OCCUPATION_BEGIN`.
- M3.3 `text/headlines.py` + `names.py` (§6.5) for the core subset. *Accept:* template-coverage
  property test (§8.4); golden file generated and checked in; headless CLI (`meddler run
  --headless`) works.

**M4 — signature engine capabilities: causal + temporal navigation**
- M4.1 Trace: ancestry/descendant graph walk producing the `trace` message (contract §3).
  *Accept:* a doom-spiral trace returns the full DFS tree with correct depths; clipped cascades
  carry the ∞ marker.
- M4.2 Scrubbing + deep time: `world_at` reconstruction feeding `worldAt`/`resumeLive`;
  `restart {tick}` end to end (§3.7); event-archive query backing `annals` (contract §9).
  *Accept:* scrub to t matches `world_at(t)`; restart truncates and resumes; countries born
  after t are pruned from the reply.

**M5 — signature engine capabilities: counterfactual + god mode**
- M5.1 God mode: interventions generated from the registry as root events (`is_intervention`);
  `godEdit`/`godRelation`/`godPeace` emit synthetic root events (`GOD_EDIT` etc., §3.5) unless
  `silent_god_edits` is set. *Accept:* an intervention appears with ✦ and its consequences trace
  back to it; a god edit is traceable (and invisible when the setting is on).
- M5.2 Fork + compare: `Multiverse` with up to 3 forks, `adoptFork` (dissolves siblings), per
  country `diff`. *Accept:* fork with no intervention ⇒ zero diff at every tick (the strongest
  determinism test in the project); fork with intervention ⇒ diffs render; adopt promotes B and
  drops C/D.

**M6 — the web bridge (light up the real engine)**
- M6.1 `bridge/server.py`: asyncio WebSocket server at `ws://127.0.0.1:7677/ws`; `meddler
  serve --seed N` starts it. *Accept:* a browser connects; `hello` received.
- M6.2 `bridge/adapter.py`: engine ticks → `frame`; plus `snapshot`, `status`, `trace`,
  `forkStarted`, `countryAdded`, `toast`, and the fork-focus messages. All server→client types
  in contract §3/§6/§7/§9 covered. *Accept:* `web/index.html` against the real engine (mock
  disabled) shows a live-running world.
- M6.3 `bridge/commands.py`: every client→server command in contract §4/§6/§7/§9 plus
  `updateSettings` (§6.9; add contract §10). *Accept:* all frontend interactions (pause,
  intervene, fork, adopt, godEdit, restart, countryDetail, updateSettings) work against the real
  engine with no mock fallback.
- M6.4 Settings round-trip: `updateSettings` → `settingsAck` → frontend ⚙ overlay reflects it.
  *Accept:* toggling `allow_secession` off prevents SECESSION events over the next 500 ticks
  (headless verification).

**M7 — deep simulation (the full catalog)**
- M7.1 `engine/kinds/` fully populated with every kind in §6.6 as `EventSpec` entries, each with
  ≥ 4 headline templates. *Accept:* `registry.validate_all()` passes; a property test confirms
  every kind has templates; a headless 5000-tick × 20-seed run produces ≥ 1 of every kind not
  flagged `low_frequency_ok`.
- M7.2 Infrastructure failure chains (§6.7.3): degradation → failure events → cascades.
  *Accept:* a scenario with zero treasury for 100 ticks produces `SATELLITE_FAILURE` and the
  full chain of §6.7.5.
- M7.3 Conquest and absorption (§6.8): `OCCUPATION_BEGIN` → `ANNEXATION`/`LIBERATION`. *Accept:*
  a scripted scenario (stability 0 + active war + relation < −80) triggers the full chain; the
  frontend prunes the annexed country.
- M7.4 Deep tuning pass: 30 seeds × 5000 ticks headless; drama cadence ≥ 1 severity-2 event /
  80 ticks; infrastructure chains fire in ≥ 80% of runs; ≥ 1 country absorbed or dissolved in
  ≥ 40% of runs; no permanent single-country equilibrium. Document changes in
  `design-decisions.md`; regenerate golden. *Accept:* cadence stats printed by a script.

**M8 — ship it**
- M8.1 `docs/design-decisions.md` (why tick-based, why legibility>realism, determinism rules,
  ledger design, why the registry, why a bridge not a bundled UI) + `docs/demo.md` (scripted
  60-second demo: seed, what to show, in order).
- M8.2 Settings overlay + Annals polish pass on the frontend against the real engine (the only
  remaining UI work — everything else already exists on the mock). *Accept:* Tier-3 settings and
  the Annals render from real engine data; `docs/design-guide.md` checklist satisfied.
- M8.3 Final README: GIF (record the browser with the real engine), quickstart, feature tour,
  architecture sketch, "origin story" link. *Accept:* a stranger can run it from README alone.

## 10. Progress checklist

- [x] M0.1 · [ ] M0.2 · [x] M0.3 · [x] M0.4
- [x] M1.1 · [x] M1.2 · [x] M1.3 · [x] M1.4
- [x] M2.1 · [x] M2.2 · [x] M2.3 · [x] M2.4 · [x] M2.5 · [x] M2.6 · [x] M2.7
- [ ] M3.1 · [ ] M3.2 · [ ] M3.3
- [ ] M4.1 · [ ] M4.2
- [ ] M5.1 · [ ] M5.2
- [ ] M6.1 · [ ] M6.2 · [ ] M6.3 · [ ] M6.4
- [ ] M7.1 · [ ] M7.2 · [ ] M7.3 · [ ] M7.4
- [ ] M8.1 · [ ] M8.2 · [ ] M8.3

## 11. Glossary

**Tick** — 1 simulated day. **Root event** — exogenous or intervention event, `parent_id=None`.
**Timeline** — one event log + snapshots + live world. **Prime timeline** — the un-forked one.
**Multiverse** — container of the prime timeline plus up to 3 forks (`B`/`C`/`D`). **Fork** — a
counterfactual timeline branched at a tick with a reseeded RNG (§4.3.5). **Adopt** — promote a
fork to prime, dissolving its siblings. **Restart** — rewind prime, truncate later history, and
resume on fresh dice (§4.3.6). **Checkpoint** — a client-side ◈ bookmark of a tick (§3.7).
**Pool** — a money balance (integer minor units) owned by a country sector. **Veri** — abstract
FX reference unit; never a spendable currency. **Drama threshold** — stat boundary that emits an
event when crossed (§6.3). **Golden master** — checked-in headless output for seed 1337 that CI
diffs against.

**EventSpec** — declarative descriptor for one event kind; the unit of extension (§4.7).
**Registry** — global dict of EventSpecs; validated at startup; the single list of everything
that can happen. **ConsequenceRule** — one entry in an EventSpec's consequence list; carries
`base_p`, `delay_min/max`, target, and conditions. **Condition** — a predicate on world state
evaluated at schedule or fire time. **Cascade decay** — the α in `p_spawn = base_p · α^depth`
(§6.4.1). **Depth cap** — `max_depth` (default 8); consequences past it are clipped (§6.4.3).
**InfrastructureBlock** — per-country struct of asset counts and condition floats (§6.7).
**AssetClass** — one row of it (count, condition, last_maintained_tick). **Maintenance ratio** —
`affordable / maintenance_cost`; drives degradation (§6.7.2). **Status (country)** — ACTIVE |
OCCUPIED | ANNEXED | DISSOLVED (§6.8). **WorldSettings** — user-configurable rules dataclass on
World; all systems read from it, never `config.py` directly, for any key it exposes (§6.9).
**Bridge** — the `meddler/bridge/` layer: WebSocket server + protocol adapters (§4.1, §7).
**Annals** — the world's long-record history view (eras, wars, records); engine-backed by an
event-archive query, fabricated client-side in the prototype (§3.7).

r4 terms (defined in §12; none exist in v1 code): **Flow model** — trade/transport as capacity-
constrained flows over infrastructure, so consequences emerge from physics instead of authored
`ConsequenceRule`s (§12.1.1). **Attribution ledger** — per-delta records of *which upstream
events contributed how much* to a stat change, so emergent effects stay traceable (§12.1.3).
**Physics edit** — a god-mode live edit of registry/config parameters, recorded as a
`PHYSICS_CHANGED` event (§12.1.4). **Operation** — a multi-tick composed intervention (a
scheduled sequence of root events with its own paper trail) (§12.1.4).

## 12. r4 preview — the deep-causation expansion (post-v1)

**Status: approved direction, NOT approved implementation.** Nothing in this section is part of
any v1 milestone. Do not build anything described in §12.1–§12.2 — §12.3 is the only
part of this section that binds v1 work. This preview exists so that v1 code is *shaped* to
receive r4 without rewrites, and so design questions during v1 get answered in the direction r4
needs. When v1 ships (M8.3 done), this section gets expanded into a full r4 revision with exact
formulas, tables, and milestones, replacing this preview.

### 12.0 The motivating critique

v1's causation is **authored, not emergent**: every cascade is a hand-written `ConsequenceRule`
("RAIL_COLLAPSE spawns GRAIN_TRANSPORT_FAILURE p=0.9 d=2–6"). The trace view is real, but what
it traces is a probability graph we wrote by hand. Infrastructure is condition floats that roll
dice; satellites and ships don't *carry* anything. r4's thesis: consequences should fall out of
shared physical state — capacity, flows, markets — with the authored cascade graph retained
only for genuinely discrete shocks (coups, scandals, meteors). The hard engineering problem —
and r4's headline feature — is keeping every emergent effect **traceable** (§12.1.3) so the
signature UX survives the shift from scripted to emergent causation.

### 12.1 The four pillars

#### 12.1.1 Flow-based infrastructure (assets become load-bearing)

Infrastructure stops being decorative condition floats and starts carrying the economy:

- **Shipping:** international trade volume is capped by `naval_fleet` capacity
  (`count × condition × per-unit tonnage`). A blockade or fleet loss doesn't *roll* a
  `PRICE_SPIKE` — the grain physically fails to arrive, the shortage ratio rises, and §6.2's
  existing price formula produces the spike organically.
- **Rail:** domestic distribution of staples is capped by `rail_network` capacity; grain in
  stock but undeliverable is a famine with full silos (a story v1 cannot tell).
- **Power:** industrial `gdp_tick` is gated by `power_grid` throughput rather than a scripted
  `INDUSTRY_SHUTDOWN` chain.
- **Comms/satellites:** effective information quality — scandal visibility, FX reaction speed,
  election fairness — derives from `communications`/`satellites` capacity.
- **Shared inter-country assets** (new class): pipelines/undersea cables owned jointly by two
  countries — the first asset whose sabotage has a built-in diplomatic dimension.

Most of §6.6.5's scripted chains become *verifications* ("the flow model reproduces this
story") instead of implementations. The §6.6 catalog survives for discrete shocks.

#### 12.1.2 A small commodity set (interdependence needs ≥ 2 goods)

One commodity cannot produce interdependence. r4 adds **fuel** (consumed by fleets, rail, and
power grids) and **goods** (industrial output; consumes fuel, feeds household consumption) —
capped at three commodities total, ever, per the legibility principle (§3.1.3). The loops
nobody authored are the point: a fuel shortage simultaneously throttles shipping (grain
imports stall → famine) and browns out the grid (GDP falls) — two crises, one root cause,
discovered by the simulation. Prices come from per-commodity market clearing (supply/demand),
generalizing v1's single hardcoded grain-market formula.

#### 12.1.3 The causal attribution ledger (the hard part, and the point)

The moment effects flow through shared state (prices, capacities, market clearing), `parent_id`
alone can no longer answer "why did this happen" — a shortage has *several* upstream causes in
different proportions. The fix generalizes the pattern v1 already uses for money (`LedgerEntry`)
and stats (`StatDelta`):

- Every systemic delta gains an **attribution vector**: `[(event_id, weight), ...]` — which
  upstream events contributed, and how much (e.g. "shortage this tick: 0.6 from event #412
  blockade, 0.3 from #371 drought, 0.1 ambient").
- Threshold events (§6.3) parent to the **dominant contributor** instead of "same-tick system
  event or root".
- The trace view gains weighted, multi-parent edges ("mostly because of the blockade; partly
  the drought") — strictly more honest than v1's single-parent tree, and the UX that justifies
  the whole expansion.

This is a schema change (`Event`/`StatDelta`), a replay change, and a contract change — the
reason it must be designed as its own document before any r4 code, and the reason §12.3's
rules protect the delta-recording discipline so fiercely.

#### 12.1.4 The unbounded intervention surface ("intervene in any way possible")

Three tiers above v1's palette, all still EventSpec/event-record shaped:

- **Raw injection:** inject *any* registered kind with an arbitrary (validated) payload at any
  target — the palette becomes a convenience, not a boundary. `godEdit` extends to asset
  counts/conditions, capacities, and stockpiles.
- **Physics editing:** live-edit registry parameters (`base_p`, consequence probabilities,
  delays, thresholds, §6.9-style constants) as a god action, recorded as a `PHYSICS_CHANGED`
  root event so even changes to the laws of nature leave a paper trail. "What if satellites
  were fragile in this world" is a more interesting counterfactual than "what if this
  satellite broke."
- **Operations:** multi-tick composed interventions — fund a resistance movement for 50 ticks,
  run an embargo campaign, covert sabotage with a discovery roll that craters relations if
  caught. Mechanically just scheduled sequences of root events sharing an `operation_id`, so
  the existing queue, trace, and fork machinery handle them unchanged.

#### 12.1.5 Candidate new artifacts (subordinate to the pillars)

Strategic reserves (a policy buffer that dampens shocks), mercenary/proxy actors, and the
shared assets of §12.1.1. Each must earn its place by participating in a flow or an operation —
no artifact ships because it demos well.

### 12.2 What r4 will still NOT add

The §2.2 non-goals all survive r4 unchanged (no real-world data, no multiplayer, no DB, no
LLMs, no hosted deployment). Additionally r4 explicitly rejects: characters/citizens as
entities, companies, banks, stock markets, per-city simulation (the original README's tar pit
— cities stay presentation-layer), more than three commodities, and any physics too subtle to
produce a headline (§3.1.3 still governs).

### 12.3 Binding forward-compatibility rules for v1 code

These rules bind **now**, during M3–M8. None of them changes any v1 formula, constant, output,
or acceptance criterion — they constrain *code shape only*, so r4 lands as additions rather
than rewrites. Where a rule and a literal spec skeleton conflict on shape, follow the rule and
note it in `docs/design-decisions.md`.

1. **Never mutate world state without an owning `Event` carrying the delta** (`stat_deltas` /
   `ledger`). This is already binding for replay correctness (see design-decisions.md, "Stat
   deltas"); r4 makes it load-bearing twice over — recorded deltas are the raw input the
   attribution ledger (§12.1.3) attributes. Do not "optimize" ambient per-tick drift into
   unrecorded mutation, ever.
2. **Keep the staple abstract at the seams.** `Country`'s `grain_*` fields stay exactly as §5.1
   specs them, but market/trade *logic* (clearing, pricing, shortage ratio) must live in
   functions shaped `(supply, need, stock, ...)` — not smeared across systems as `grain_`
   literals — so r4 can call the same code per commodity. One commodity is a parameter value,
   not an architecture.
3. **Route asset-class effect math through one helper module** (`engine/assets.py`, already in
   §7's layout): anything that computes an effect from `count`/`condition` (failure-roll
   inputs, §6.7.3 thresholds) calls a helper like `effective_capacity(asset_class)` rather
   than inlining `count * condition` per call site. r4 swaps the helper's downstream use, not
   thirty call sites.
4. **Never enumerate intervention kinds by name outside the registry.** The god-mode palette,
   the bridge's `hello.interventions`, and any tests iterate `EVENT_REGISTRY` filtered on
   `is_intervention` — no hardcoded lists in `bridge/`, `cli.py`, or `web/`. §4.7 already says
   this; r4 (raw injection, operations) breaks loudly anywhere it was violated.
5. **Treat `Event.payload` and schedule-queue entries as open dicts.** Do not add code that
   whitelists payload keys or assumes a closed set; r4 adds keys (`operation_id`, attribution
   metadata) to existing kinds.
6. **Keep the threshold bridge's parent-attribution logic in one place** (the M2.7 module), not
   copied into individual systems — it is precisely the code the attribution ledger replaces.
7. **Serialize forward-compatibly.** Save files (§4.4) and snapshot formats must tolerate
   unknown keys on load (ignore, don't crash) and carry a schema version they already require —
   r4 bumps the version and adds keys; it must not need a migration tool for v1 saves.
8. **The bridge adapter derives message vocabulary from engine data, not literals**, wherever
   the contract allows: intervention catalogs, settings keys, and country lists come from the
   registry/`WorldSettings`/`World` — so r4's new kinds, settings, and commodities flow through
   without touching adapter code they didn't add.

### 12.4 Sequencing

r4 work begins only after M8.3 ships. First r4 deliverable is a **design document for the
attribution ledger** (§12.1.3) — it is the schema-changing pillar and gates the other three.
Build order after that: flow-based infrastructure (§12.1.1) → commodities (§12.1.2) →
intervention tiers (§12.1.4) → artifacts (§12.1.5). Each pillar gets PROPOSAL-style formulas,
catalog tables, settings keys, and acceptance criteria before its first task, same discipline
as v1.
