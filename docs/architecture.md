# Architecture

Meddler is a deterministic simulation with a time machine attached. This page is the tour: how
the pieces fit, what each layer is allowed to do, and which rules keep history reproducible.
For *why* each piece is shaped this way, see [`design-decisions.md`](design-decisions.md). The
wire protocol is in [`frontend-contract.md`](frontend-contract.md).

## The shape

```
 web/                  browser client: canvas globe, broadsheet feed, inspectors
   │                   plain HTML/JS/CSS, no build step, no dependencies
   │  JSON over one WebSocket (protocol v2, docs/frontend-contract.md)
   ▼
 meddler/bridge/       server runtime: owns the session, the wall clock, the ticker;
   │                   translates engine state into contract messages
   ▼
 meddler/text/         headline templates and name flavor (pure, RNG-free)
   ▼
 meddler/engine/       the simulation: tick loop, ledger, event log, registry,
                       timelines and forks. No I/O, no clock, no sockets.
```

Imports only point downward: `bridge → text → engine`. An import-linter contract enforces this
in CI, plus a second contract that keeps `engine/space.py` a leaf. The engine has no idea a
browser exists. That is why the whole simulation can be exercised headlessly
(`meddler run --headless`), why the golden-master test needs no server, and why the same engine
and bridge could be moved into a browser tab for the hosted demo without changing either.

## One tick

`engine/tickloop.py` advances the world by one tick (one simulated day). It runs a fixed list of
systems, in a fixed order, and threads one explicit RNG through all of them:

| # | System | What it does |
|---|---|---|
| 1 | `production` | GDP per tick from base capacity, stability, innovation |
| 2 | `commodity_production` | output and inputs for six commodities; occupation tribute |
| 3 | `trade` | bilateral matching per commodity; dispatches shipments |
| 4 | `logistics` | moves shipments; arrivals, interdiction at sea |
| 5 | `fx` | exchange-rate drift from trade balance and inflation |
| 6 | `fiscal` | taxes and spending |
| 7 | `infrastructure` | upkeep, condition drift, failure rolls |
| 8 | `inflation` | money supply and shortage pressure |
| 9 | `stability` | war, shortage and inflation penalties; recovery |
| 10 | `relations` | decay toward neutral |
| 11 | `thresholds` | crisis events when a stat crosses a line (with hysteresis) |
| 12 | `politics` | elections, coups, leader changes, occupation, annexation, peace |
| 13 | `diplomacy` | alliance formation, mutual defense, bloc cohesion and strain |
| 14 | `tariffs` | hostile tariffs, retaliation, repeal |
| 15 | `war` | strikes along active fronts; resource-motivated declarations |
| 16 | `exogenous` | random root events (droughts, scandals, discoveries…) |
| 17 | `consequence` | fires scheduled consequences of earlier events |

Order is part of the model. Commodity production runs before trade, so trade matches this
tick's surpluses and deficits. Diplomacy runs before war, so an ally that joins a war can strike
the same tick. The exogenous and consequence systems run last, so every random root sees the
settled state of the tick.

## Every change is an event

The engine has one rule that everything else leans on: **world state changes only through an
`Event`, and the event records exactly what changed.** An event carries:

- `ledger`: money movements as integer minor units. Every movement is a `transfer`, `mint`, or
  `burn` between named pools (`ELB.treasury`, `fx:ELB`, …). Two invariants are property-tested:
  per-currency totals change only through explicit mint/burn, and replaying every ledger entry
  from tick 0 reproduces the current pools exactly.
- `stat_deltas`: every non-money change (stability, inflation, relations, infrastructure
  condition, commodity stocks), recorded *after* clamping, so replay just adds numbers and never
  needs to know a field's range.
- a `payload` for structural changes that aren't a number: a new leader, a war declared, a bloc
  formed, a shipment dispatched, a territory annexed. Each such kind registers two handlers in
  `engine/structural.py`: a live one that makes the change and records its result on the
  payload, and an RNG-free replay one that rebuilds the same change from that payload.
- `effects`: normalized before/after observations of all of the above, used for inspection only.

Ambient bookkeeping, such as per-tick inflation drift or a shipment arriving, is an event too.
It just has no headline template, so it never appears in the feed. Unrendered is fine;
unrecorded is not.

## The registry

Event kinds are data, not code. `engine/registry.py` holds an `EventSpec` for each of the 123
registered kinds (20 of them god-mode interventions), grouped by domain under `engine/kinds/`.
A spec declares its severity, targets, stat deltas, pool transfers, tags, an optional exogenous
probability with `Condition` gates, and a list of `ConsequenceRule`s: which child kinds it may
spawn, with what probability, delay window, and target. One shared cascade path in
`engine/cascade.py` fires any spec: apply its effects, append the event, run its structural
handler, then roll its consequences. A consequence's spawn probability decays geometrically with
cascade depth, and a depth cap stops runaway chains. Nothing in the core loop names a specific
kind. The intervention palette the browser shows is generated by filtering the registry.

Headlines come from `meddler/text/headlines.py`: at least four templates per narrative kind
(a property test enforces it), chosen by `event.id % len(templates)`. Choosing a headline never consumes randomness, so rewording the
feed can never change history.

## Causality

Every event has a `parent_id` (the consequence rule that spawned it, if any) and may carry typed
`CauseLink`s: additional triggers, state contributors, or context. A threshold crisis, for
example, credits up to three recent events that pushed the stat over the line. Together they
form a causal DAG over the event log.

- `engine/trace.py` returns an event's whole causal component: root causes, siblings, and
  descendants, with convergent nodes shown once. Press `t` in the UI.
- `engine/impact.py` separates an event's immediate effects from its downstream ones and totals
  them, deduplicating by event id so a consequence reached by two causal paths is counted once.
  It can cut off at a tick horizon.
- For a fork, the inspector can also show the whole-world difference between the fork and prime
  at the same tick. That is labeled as a fork-versus-prime difference, never as "what this one
  event caused", because the engine does not invent counterfactuals it didn't run.

## Time: snapshots and replay

A `Timeline` snapshots `World` state every 50 ticks (`snapshot_interval`) and records the exact
RNG state and a sample of every country's stats every tick. `Timeline.world_at(t)`:

1. deep-copies the nearest snapshot at or before `t`;
2. replays the recorded ledger entries, stat deltas, and structural payloads of every event
   between the snapshot and `t`;
3. returns that world.

No system runs during reconstruction, and no random number is drawn, so a scrubbed world cannot
drift from what actually happened. The tests for each stateful system compare `world_at(t)` with a
continuous run field by field, and the golden master pins the whole event stream.

`restart(t)` is different. It truncates prime's future after `t` and continues with *fresh*
randomness: "begin anew". It is refused while forks exist.

## Forks

`Multiverse` holds prime and up to three forks. A fork starts from `world_at(t)`, applies an
intervention as a root event, and restores **prime's exact RNG state at `t`**. It does not
reseed. A fork with no intervention therefore tracks prime bit for bit forever, and a fork *with*
one differs only because of the world state the intervention changed. That is what lets the
ΔWORLD strip say, truthfully, that every difference on screen is yours. (This departs from the
original design, which reseeded forks. See "Fork RNG semantics resolved" in
`design-decisions.md`.)

Forks can be dropped, or adopted as the new prime, which dissolves the siblings. While a fork is
focused, the server advances prime and that fork together, tick for tick.

## History storage

Long runs used to be memory-bound: every snapshot deep-copied the cumulative event log, and a
seed-1337 run reached 460 MiB by tick 300. History now lives in a per-run SQLite file (stdlib
`sqlite3`, no new dependency):

- `EventLog` keeps its list-like API (sequential ids, ordered and reversed iteration, indexed
  lookup) over the store, with a bounded LRU of 512 hydrated events.
- Deep-copying a `World` copies only a read-only boundary into the store, so snapshots share
  history instead of duplicating it. Forks share prime's immutable prefix and append their own
  suffix. Dropping, adopting, and restarting reclaim the rows nobody can reach any more.
- RNG states and per-tick country stats live in the same store, behind bounded caches. Causal
  edges, country/effect and ledger rows are indexed, so trace, charts, and same-tick system
  lookups don't hydrate unrelated history.
- Payloads are JSON (Python's float encoding round-trips doubles exactly), zlib-compressed when
  that makes them smaller.

The same run now sits around 42 MiB at tick 1000. The database is ephemeral: it is deleted when
`meddler serve` exits. Saving a world across process restarts is not implemented.

## Determinism rules

Same seed, byte-identical event log, every run. The rules that make that true:

- **One RNG stream**, an explicit `Rng` object passed through `tick()`. Nothing in `engine/` or
  `text/` touches module-level `random`, the clock, `uuid4`, `os.urandom`, or `id()`.
- **Sorted iteration** wherever order affects draws or event order. Countries live in an ordered
  list, and specs, fronts, shipments, and candidates are sorted by stable keys.
- **Pinned draw order** in the cascade: an exogenous spec always rolls exactly once per tick,
  targets are drawn only on a hit, and a consequence's spawn roll comes before its target and
  delay draws. `cascade.py` documents the order.
- **Stable hashing.** `Rng.sub()` derives child seeds from SHA-256, not Python's per-process
  salted `hash()`.
- **Integer money**, sequential event ids, and headline choice by `id % n`.
- **Record, don't recompute.** Anything dynamic (a strike's damage, a shipment's carrier and
  arrival tick, an annexation's transfers) is recorded on the payload at the moment it happens
  and read back on replay.

`tests/golden/` holds the 1000-tick event log for seed 1337 and fails on any byte of drift. CI
runs it on several Python versions. Two caveats are documented rather than hidden. Worldgen uses
trig functions (`cos`, `sin`, `atan2`, `asin`) whose last bit is platform-libm dependent. And
worldgen's variable number of random draws stays invisible to the tick stream only because
`generate_world` discards its own `Rng`. See the M9 section of `design-decisions.md`.

## The bridge

`meddler serve` starts one `ServerRuntime` that owns a single `Session` (the multiverse, the
focus and scrub state, settings, and speed) and an asyncio ticker for the process's lifetime.
The same port serves the static frontend over HTTP and the protocol over `/ws`.

- Browser connections are views. Reloading or reconnecting attaches to the same running world
  with its tick, forks, and history intact. The world keeps advancing with no browser attached,
  and frame projection is skipped until one attaches.
- `bridge/adapter.py` and `bridge/world_objects.py` are pure projections from a `World` to
  contract messages. Because they project whatever world they are given, scrubbing shows the
  shipments, territory, and blocs of that historical tick, not today's.
- Commands carry an optional `requestId` that is echoed on every direct reply, so the client can
  show pending states and correlate errors.
- Settings changes are recorded as a structural `SETTINGS_CHANGED` event on the timeline, so they
  scrub, fork, and replay like any other history.

## The client

`web/` is plain HTML, CSS, and JavaScript with no build step, no dependencies, and no CDN.
`globe.js` is a hand-written Canvas 2D orthographic renderer. `app.js` runs the feed,
inspectors, dossiers, timeline ribbon, and overlays. `realengine.js` is the WebSocket adapter.
By default everything on screen comes from protocol v2: country positions and territory,
blocs, directional trade lanes, every in-flight shipment with its real id and progress, recent
strikes, and aggregate infrastructure. Two things are presentation only: territory shapes
(landmasses derived from the engine's territory markers, regions, and owners) and satellite
orbit paths around the engine's aggregate satellite count. `engine.js`, an approximate
in-browser mock, is available only when explicitly requested with `?engine=mock`.

The visual system is binding and documented in [`design-guide.md`](design-guide.md): color is
semantic, serif for the world, sans for chrome, mono for numbers, and motion only for state
change.

## The in-browser demo

The hosted demo runs the same `meddler` engine and bridge code inside the browser under
Pyodide, behind the same protocol, instead of the JavaScript mock. Its history database lives in
the tab's memory, so it grows with session length and resets on reload. The local server is the
reference way to run Meddler; the demo exists so nobody has to install anything to see it.
