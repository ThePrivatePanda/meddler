# Frontend ⇄ Engine Contract (v2)

**Status:** binding for the web frontend and Python engine bridge.
**See also:** `design-guide.md` — binding visual/UX rules and design method for anything in `web/`.
**Scope:** amends PROPOSAL.md — the primary v1 frontend is a **local website**, not the Textual
TUI (§3's interaction model — watch / trace / scrub / god mode / fork — is unchanged; only the
rendering surface moves to the browser). The engine remains pure (PROPOSAL §4.1); this contract
is implemented by a thin transport layer on top of it.

## 1. Transport

- WebSocket, `ws://127.0.0.1:7677/ws`, JSON text frames, one client per socket.
- All server→client messages have a `type` field; all client→server messages have a `cmd` field.
- The default client is the real WebSocket adapter (`web/realengine.js`). The optional
  `?engine=mock` path uses `web/engine.js` as an explicitly approximate offline fallback; it is
  never selected silently and is not an authoritative implementation of protocol-v2 world objects.

## 2. Shared objects

### Event
```json
{
  "id": 412, "tick": 412, "kind": "UNREST", "severity": 2,
  "country": "ELB", "country2": null,
  "parentId": 408, "parentIds": [401, 408],
  "causes": [
    {"eventId": 401, "role": "contributor", "detail": "reduced stability toward crisis"},
    {"eventId": 408, "role": "trigger", "detail": "direct consequence"}
  ],
  "depth": 3, "intervention": false,
  "headline": "Bread riots erupt in Elbonia as inflation hits 11% — President Grum blames the elites.",
  "payload": { "inflation": 11.2 },
  "ledger": [ { "from": "ELB.treasury", "to": "ELB.households", "amount": 120, "currency": "ELB" } ],
  "effects": [
    {"type":"stat", "target":"ELB", "metric":"stability", "delta":-2.0,
     "before":44.1, "after":42.1, "unit":""}
  ]
}
```
- `id` is sequential within its timeline; use `(timeline,id)` across forks. `parentId` is retained
  as the legacy direct-consequence edge; `null` means no direct parent. `parentIds` is the sorted,
  deduplicated set of every causal predecessor. `causes` assigns each edge a `trigger`,
  `contributor`, or `context` role. Cause count never changes severity.
- `effects` is normalized inspection data. `type` is `stat`, `money`, or `structural`; numeric
  effects carry `delta`, and exact observations carry `before`/`after` when available. Replay
  remains driven by ledger/stat deltas and registered structural payload handlers.
- `severity`: 0 info · 1 notable · 2 crisis. `headline` is **server-rendered prose** (text
  generation lives engine-side per PROPOSAL §6.5; the frontend never composes headlines).
- `ledger` amounts are in millions of local currency in the mock; integer minor units in the
  real engine (PROPOSAL §4.5).

### CountryStats (per country, per frame)
```json
{ "stability": 44.1, "inflation": 11.2, "gdp": 62.4, "grainDays": 9.8,
  "fx": 0.44, "treasury": 380, "war": true }
```

### WorldObjects (authoritative globe projection)

`worldObjects` is an observational bridge projection of one specific `World`; it never mutates
state or consumes RNG. Countries are keyed and sorted by code. It contains:

- `tick`; `countries:{CODE:{code,position:{lat,lon},region,territories:[{position,region}],
  endowments,commodities:{name:{output,need,stock}},assets:{assetClass:{count,condition,
  effectiveCapacity,lastMaintainedTick}},blocId,status}}`
- `blocs:[{id,members,formedAt}]`, sorted by id with sorted members.
- `shipments:[{id,origin,dest,commodity,qty,carrier,buyerPool,baseCost,tariffDuty,cost,
  proceeds,dispatchEventId,departTick,arriveTick,progress,distanceKm,remainingDistanceKm,etaTicks,
  status:"in_transit",warExposed,relief,condition,originCondition,destCondition,route:{from,to}}]`.
  Progress is clamped to `[0,1]`; `distanceKm` is the engine's great-circle endpoint distance and
  `remainingDistanceKm` is `distanceKm * (1 - progress)` for that frame. Engine-created shipments
  guarantee `arriveTick - departTick >= 7` for sea, air, and rail. The array includes every active
  shipment even if an endpoint nation was subsequently annexed or dissolved.
- `recentShipments:[{...shipment,status:"arrived"|"lost",terminalTick,terminalEventId,ageTicks,
  retentionTicksRemaining}]` reconstructs terminal state from the event log and dispatch parent.
  Arrivals are present at ages 0–3 (four ticks), frozen at progress `1` and zero remaining distance,
  then absent at age 4. Losses preserve progress/distance at the loss tick for ages 0–7 and are
  absent at age 8.
- `lanes:[{id,origin,dest,carrier,volume,shipmentCount,shipmentIds,recentShipmentCount,
  recentShipmentIds,condition,distanceKm,route}]`. Active volume/count/IDs never include retained
  terminal rows. Lanes are directional and carrier-separated; terminal-only lanes remain
  indexable for roster continuity but are not drawn as active routes.
- `tradeStats:{activeShipments,activeLanes,inTransitQuantity,remainingDistanceKm,averageProgress,
  reliefShipments,warExposedShipments,arrivalsLast4Ticks,lossesLast8Ticks,
  arrivedQuantityLast4Ticks,lostQuantityLast8Ticks,byCarrier,byCommodity}` contains deterministic,
  unit-safe counts/quantities/distances. It deliberately has no aggregate monetary value because
  shipment costs are denominated in different destination currencies.
- Route and global rosters preserve immutable `dispatchEventId,id` order for active and lost rows.
  Progress is never a sort key. Arrival is the sole exception: that row moves to a bottom partition
  for its four-tick hold, then disappears; lost rows keep their original position for eight ticks.
- `strikes:[{id,eventId,tick,origin,dest,power,age,visibleTicks,route}]`, newest first,
  covering recent real `STRIKE` events. `origin` is `event.country2` (attacker) and `dest` is
  `event.country` (target).

Positions are degrees. Conditions are engine fractions in `[0,1]`; costs are integer minor
currency units.

## 3. Server → client messages

| type | when | payload |
|---|---|---|
| `hello` | on connect, first; reply to `newWorld` | `protocol:2, seed, tps, countries:[…], roster:[…], interventions:[…], settings:{…}, worldObjects` |
| `status` | on connect + whenever changed | `running:bool, tps:int, fork:null \| {tick,label}` |
| `snapshot` | after hello; reply to `worldAt`, `resumeLive`, `dropFork`, `adoptFork`, `restart`, `newWorld` | `tick, live:bool, liveTick, scrubbed:bool, roster, stats, wars, spark, recentEvents, leaders, worldObjects` — a scrub snapshot projects the reconstructed historical `World`, never live prime; `liveTick` is where PRIME's live edge is, so a client that reloads mid-scrub can draw the ribbon before its first frame |
| `frame` | each engine tick while running (and once after `step`/fork) | `tick, liveTick, focus?, timelines:{A:{stats,events,worldObjects}, B?:{stats,events,worldObjects}}, wars,warsB?,diff?,leaders,leadersB?` — fork A and B projections are aligned at the frame tick, and in fork mode `timelines.A.events` are PRIME's recorded events *at that tick*, not prime's live ones (`liveTick` may be ahead of `tick`) |
| `countryAdded` | when a country comes into existence, before any projection carrying it | `tl, tick, parent, country:{code,name,currency,leader,population,parent,bornAt,ordinal}, worldObject` |
| `forkAdopted` | reply to `adoptFork`, before the new prime `snapshot` | `id, tick` |
| `annalsData` | reply to `annals` | `tl, tick, country, eras, wars, records, majorEvents, majorEventCount` |
| `trace` | reply to `trace` | `selectedId, nodes:[{id,tick,kind,severity,country,headline,intervention,d,parentIds,causes,cascadeClipped}]` — deterministic DFS over the whole causal DAG component; shared nodes appear once, `d` is deterministic display depth |
| `eventImpact` | reply to `eventImpact` | `tl,eventId,horizon,event,causes,ancestors,descendants,immediateEffects,downstreamEffects,cumulativeTotals,horizonDiff` — descendants and totals are deduplicated by event id; `horizonDiff.available` is true only for an aligned fork/prime comparison and otherwise includes `reason` |
| `annalsImpact` | reply to `annalsImpact` | `tl,tick,sortBy,eventCount,scope:{startTick,endTick,rankedEvents,complete},leaders:[{event,directChildren,descendants,generations,affectedCountries,recordedEffects,crisisDescendants,lastDescendantTick,children}]` — descendants are deduplicated and ranking uses recorded counts, never a mixed-unit weighted score. The ranking pass covers the `scope` tick window (the last 500 ticks; `complete:true` when that is the whole timeline) so a long history cannot stall the server; clients should show the scope rather than claim the whole record |
| `forkStarted` | reply to `intervene`/`fork` | `id,tick,liveTick,label,roster,sharedRecent:[Event..20],worldObjects` (the new fork at its fork tick) |
| `forkDropped` | reply to `dropFork` | `id` (the discarded fork), `kept` (the timeline now focused: `"A"` or a surviving fork id) — followed by that timeline's view (`snapshot` or `timelineFocus`) and `status` |
| `toast` | anytime | `text, tone:"info"\|"warn"` |

## 4. Client → server commands

| cmd | fields | effect |
|---|---|---|
| `pause` / `resume` | — | stop/start the tick interval (ack via `status`) |
| `setSpeed` | `tps` ∈ {0.5,1,2,4} | playback rate (§6); anything else is refused with a `toast` |
| `step` | — | advance exactly one tick while paused (both timelines if forked) |
| `worldAt` | `tick` | pause + reply `snapshot` reconstructed at `tick` (scrubbing) + `status`. Always PRIME history, even while a fork is focused. A tick outside `[0, liveTick]` is refused with a `toast` — the server never invents a future |
| `resumeLive` | — | leave scrub view, reply the current view (`snapshot`, or `timelineFocus` if a fork is focused) + `status`, resume if previously running |
| `trace` | `eventId, tl?` | reply `trace` for the selected timeline |
| `eventImpact` | `eventId, tl?, horizon?` | reply `eventImpact`; `horizon` is a non-negative tick distance from the selected event and bounds descendants/comparison |
| `annalsImpact` | `tl?, sortBy?, limit?` | reply `annalsImpact`; `sortBy` is `descendants`, `children`, `effects`, or `countries`; `limit` is clamped to 1–100 |
| `intervene` | `kind, country, target2?, atTick?` | **forks**: timeline B = world at `atTick` + intervention root event (✦); prime timeline A untouched. `atTick` is optional — omitted it means the tick on screen: the scrub tick while scrubbing, otherwise prime's present |
| `dropFork` | `id` | discard that fork and return to the timeline that is left (legacy `keep:"A"\|"B"` is gone; `adoptFork` is how a counterfactual becomes prime) |
| `annals` | `tl?, country?, tick?` | reply `annalsData` — the engine's real archive for one timeline at one tick; defaults to the focused timeline at the tick on screen, all countries |
| `newWorld` | `seed?, settings?` | **Genesis again**: regenerate the world from `seed` (default: the current seed) and the current settings plus any `settings` given. Forks are dissolved, history is discarded, and the reply is a fresh `hello` + `status` + `snapshot` handshake |

### 4.1 Request correlation, reconnects, and settings

Any client command may include an opaque string or integer `requestId`. Every direct reply to that
command echoes the same value, including `toast` errors and every message in a multi-message reply.
Unsolicited ticker frames have no request ID. This is correlation metadata only and never participates
in deterministic simulation state.

`meddler serve` owns one runtime, SQLite history, and ticker for the process lifetime. A WebSocket is a
replaceable view: connecting or reconnecting returns `hello`, current `status`, and the appropriate
live/focused/scrubbed snapshot without creating or resuming a world. Simulation continues while no
browser is attached, although frame projection is skipped. Stopping the server destroys this ephemeral
runtime and database; durable process-restart save/load is not part of this contract.

`updateSettings {settings:{...}}` applies known, type-valid partial changes at the current timeline tick
and replies `settingsAck {settings:{...}, restartRequired:[...]}`. Unknown setting keys are ignored.
Accepted changes are deterministic timeline events: reconstruction before the event sees the old value,
reconstruction at or after it sees the new value, forks inherit the setting at their fork tick, and a
restart before the event truncates/reverts it. Restart-required fields are still recorded immediately,
but their genesis-only effect begins with the next Genesis.

### 4.2 Session state: scrub, focus, and the clock

A session holds three pieces of view state — **scrub** (viewing prime's past, read-only),
**focus** (prime, or one fork), and **running** (is the clock ticking). Scrubbing holds the
clock: no frames are produced while `scrub_tick` is set. Every command that moves the world
forward therefore *leaves* the scrub view first, and the server always says what the client
should now be looking at:

| command while scrubbing | scrub | running | reply |
|---|---|---|---|
| `worldAt {tick}` | set to `tick` | held (remembers the pre-scrub state) | `snapshot(scrubbed)` + `status` |
| `resumeLive` | cleared | restored | current view + `status` |
| `intervene` | cleared | restored | `countryAdded*` + `forkStarted` + `status` |
| `focusTimeline` | cleared | restored | current view + `status` |
| `dropFork` / `adoptFork` / `restart` | cleared | restored | see §3 rows |
| `step` | cleared | stays paused | current view + `frame` |
| `godEdit` / `godRelation` / `godPeace` | unchanged | unchanged | `toast` — refused, see §7 |
| anything else | unchanged | unchanged | as documented |

"Current view" is one message: `snapshot` when prime is focused, `timelineFocus` when a fork
is. A `snapshot` always means *prime, one column* — the server never answers a fork-mode
command with one.

Reconnecting re-derives the same view: `hello`, `status`, then the scrub snapshot if
scrubbing, else `timelineFocus` if a fork is focused, else the live snapshot.

**The focused timeline is the clock.** While prime is focused it advances one tick per
frame. While a *fork* is focused the fork advances one tick per frame and prime is advanced
only when the fork catches up to it, so a fork taken in the past replays prime's recorded
history in the A column instead of racing a live prime further ahead. `frame.liveTick` is
prime's live edge either way. Once the fork draws level, prime advances with it every tick,
so the two stay in step with no gap and no double-counted tick.

Focus, like scrub and the clock, is a property of the **session**, not of a connection. A
session that holds several sockets shares one focused timeline, so while any client focuses a
past fork, prime's live edge is held for **all** of them until the fork catches up — the
others see `liveTick` stop advancing. That is acceptable for the single-user demo this
contract describes; a multi-viewer deployment would need per-connection focus.

## 5. Behavioral guarantees

1. Frames are the only source of world state while running; the frontend does zero simulation.
2. Every id in an event's `parentIds` exists earlier in the same timeline. `parentId`, when set,
   is included in `parentIds` and remains the direct trigger for legacy clients.
3. In fork mode both columns advance one tick per frame; `diff` always compares at the frame's
   (B-aligned) tick. The A column is prime *at that tick* — its state reconstructed from
   history and its events read from the log — so a fork branched from the deep past shows the
   history the fork diverged from, never prime's live edge (§4.2).
4. Scrubbing never mutates simulation state; `worldAt` is read-only reconstruction. God edits
   are refused while scrubbing rather than silently applied to the present (§7).
5. **Real engine only:** identical seed ⇒ identical message stream; fork-without-intervention
   diffs to zero. The **mock** deliberately reseeds timeline B, so it always diverges — it
   exists to judge UX, not determinism. (This is why the mock UI only offers forking *through*
   an intervention.)
6. **Known limitation — historical headlines are rendered against the present.** `trace` and
   `eventImpact` re-render each event's `headline` from the timeline's **current** world, not
   from the world as it stood at that event's tick. The causal structure is exact — ids, edges,
   depths, ticks and recorded effects all come from the log — but a slot filled from live state
   (a country's current name or leader, a stability descriptor) can read differently from what
   the feed said when the event fired. A headline in a trace is therefore the event described in
   today's terms, not a quotation of the original.

   This is deliberate for now, not an oversight. Rendering at the event's tick means
   reconstructing the world there, and reconstruction is `deepcopy(nearest snapshot)` plus
   replay, with snapshots every `snapshot_interval` (50) ticks. One trace spanning 30 distinct
   ticks would cost 30 whole-world deep copies and up to 1,500 replayed ticks, which is seconds
   of latency for a single click and grows with history — so the panel would become unusable in
   exchange for the fidelity. The real fix is to let headline rendering take a tick's recorded
   `country_stats` instead of a live `World`, which needs no reconstruction at all; that is a
   change to the text layer's `render()` signature and is not part of this protocol revision.
   Clients should not present a traced headline as the historical wording of the original.

## 6. v1.1 additions

- **Pace:** default is 1 tick/sec; `setSpeed.tps` ∈ {0.5, 1, 2, 4}.
- **Dynamic countries:** `hello.countries` is only the set alive when the client connected.
  `INTERVENE_SECEDE` creates a real new country at runtime (organic secession cascades may
  later do the same):
  - server → client `countryAdded`: `{tl,tick,parent,country:{…},worldObject}` — always
    delivered before the first projection whose stats include the new code, whether that
    projection is a `frame`, a `forkStarted`, or a `timelineFocus`. Stats and
    `worldObjects.countries` gain keys mid-stream. `tl` says which timeline the country
    exists in: a god-mode secession lives in its fork until that fork is adopted.
  - Every world projection also carries a **`roster`** — `snapshot.roster`,
    `forkStarted.roster`, `timelineFocus.rosterA`/`rosterB` — listing `{code,name,currency,
    leader,population,parent,bornAt,ordinal}` for the countries in *that* projection's stats.
    A client that reconnects into a fork, or scrubs forward across a secession, names the new
    nation from here instead of rendering a bare code. `hello.roster` is the wider list: every
    country prime has ever had, annexed and dissolved included, each with `status` and a stable
    `ordinal` (index in genesis order, secessions appended) for stable colours.
- **Two-target interventions:** catalog entries carry `targets: 1 | 2`; for `targets: 2` the
  client sends `intervene.target2` (≠ `country`). Kinds: `INTERVENE_WAR`, `INTERVENE_ALLIANCE`,
  `INTERVENE_EMBARGO`.
- **`INTERVENE_CHAOS`** resolves server-side to a random root event, still flagged
  `intervention: true`.
- **Country dossier:** client → `{ cmd:"countryDetail", code, tl }`; server →
  `{ type:"countryDetail", tl, code, meta, stats, commodities, bloc, leader, ticks:[..],
  series:{…}, chartEventMode:"interval", chartEvents:[], relations:[{code,value}], recentEvents,
  bornAt, worldObject }`. Series are full-history, downsampled to ≤ 240 exact sampled ticks.
  Hover context is bounded and on demand: client →
  `{ cmd:"countryChartEvents", code, tl, startTick, endTick }`; server →
  `{ type:"countryChartEvents", tl, code, startTick, endTick, total, events:[Event…] }`.
  `total` is the exact count in `(startTick,endTick]`; `events` contains at most the six strongest
  authoritative country-involving summaries, ordered by severity/tick/id. The client keeps at most
  24 interval responses and 1,000 event summaries per timeline. All charts share one hover cursor;
  the tooltip shows exact values/deltas, interval events, inspection, and PRIME `worldAt` jump.
  Event proximity is not asserted as causation, and fork charts do not claim unsupported scrubbing.
  The explicit empty `chartEvents` field remains for compatibility with older clients/mock data.
- **`commodities`** (v2 §3, dossier-only — never in `frame`/`snapshot`): the six buckets
  `food, energy, raw_materials, manufactured, consumer, high_tech`, each `{output, need, stock}`
  in commodity units per tick (stock is a level, not a rate). `food` is the same bucket the
  per-frame `grainDays` scalar summarises. From M12 (bilateral trade) `stock` is a real
  post-trade inventory for ALL six buckets uniformly — food included — so a shrinking `food.stock`
  is a country the market is failing to feed. The same six buckets also appear in `worldObjects`.
- **`bloc`** (v2 §7, dossier-only): the country's alliance bloc `{id, members:[code..], formedAt}`,
  or `null` if unaligned. Blocs are structural (mutual-defense coalition wars, relation
  propagation); globe-level bloc rendering is contract-v2 / M16.
- **Stats gain `pop`** (population, millions) — it changes with plagues, meteors, secession.
- The globe receives authoritative positions, territory ownership markers, routes, shipments,
  strikes, blocs, and aggregate asset inventories through `worldObjects`. It does not fabricate
  cities in real-engine mode; the country position is the capital marker.

## 7. v1.2 additions (prototype-driven; frontend-first)

- **Multiple forks:** up to 3 concurrent fork timelines (`B`/`C`/`D`), all branching from PRIME.
  `status` now carries `focus` + `forks:[{id,tick,forkTick,label}]`. New commands:
  `focusTimeline {id}` (reply: `snapshot` for PRIME, `timelineFocus {id,tick,live,forkTick,label,
  statsA,statsB,warsA,warsB,leadersA,leadersB,recentA,recentB,worldObjectsA,worldObjectsB}` for a fork), `dropFork {id}`,
  `adoptFork {id}` (adopting dissolves sibling forks). `forkStarted` gains `id`; frames gain
  `focus` and always carry the *focused* fork as `timelines.B`.
- **God edits** (mutate the focused timeline in place, no fork):
  `godEdit {code, field, value, tl?}` (stability/inflation/gdp/pop/treasury/grainDays/fx),
  `godRelation {a, b, delta, tl?}` (relations above −30 auto-end wars),
  `godPeace {code, foe?, tl?}` (fires ✦ PEACE events).
  `tl` defaults to the **focused** timeline, so an edit made while watching a fork lands in
  that fork, and the reply is that timeline's view: `timelineFocus` in fork mode, `snapshot`
  when prime is focused. God edits are refused with a `toast` while scrubbing — the past is
  read-only, and the honest way to change it is to fork from the tick on screen.
- **Historical v1 note, superseded by protocol v2:** the prototype once fabricated globe
  movers and mutable asset dossiers. The default real-engine path must not generate those objects,
  mutate them locally, or inject negative-id asset headlines. Only explicit `?engine=mock` may use
  approximate cosmetic movers.

## 7.1 additions — real settlement and causal impact

- Shipment events expose `buyer_pool`, `base_cost`, `tariff_duty`, `cost`, and `proceeds` in
  payload. Food/consumer buyers are households; other commodity buyers are corporates. Duty is
  importing-treasury revenue, and exporter corporates receive only pre-duty proceeds on arrival.
- Tariff policy changes are real `TARIFF_IMPOSED` / `TARIFF_REPEALED` events. Policies are
  importer/exporter/commodity-specific, may use `*`, and are waived within a bloc.
- `eventImpact.immediateEffects` belongs to the selected event. `downstreamEffects` belongs to
  deduplicated descendants within `horizon`; `cumulativeTotals` combines both numeric sets once
  per event. Structural effects with no meaningful numeric delta remain visible but are not
  forced into a total.
- A valid `horizonDiff` is the whole-world fork-minus-prime state difference at one aligned tick,
  including population, stability, inflation, treasury, commodity stocks, and infrastructure
  conditions. It is not represented as suppression of an organic event. When no genuine fork
  baseline exists, the response explicitly says it is unavailable.

## 8. Mock deviations (allowed in `web/engine.js` only)

- Money as float millions, not integer minor units; simplified ledger entries.
- ≥2 headline templates per kind instead of ≥4; probabilities hand-tuned for drama cadence
  (~1 crisis / 60–120 ticks) rather than config-derived.
- No save/load, no `MAX_DEPTH` counter surfacing; forks always diverge (reseeded RNG — the
  mock exists to judge UX, not determinism).

## 9. v1.3 additions (history & checkpoints; frontend-first)

- **`restart {tick}`** (client → server): rewinds PRIME to `tick`, truncates all later
  history/events, drops orphaned scheduled consequences, reseeds the RNG (the rewound world
  does not replay itself), and resumes live. Refused with a `toast` while forks exist.
  Reply: `snapshot` (at `tick`, `scrubbed:false`) then `status`. Countries born after `tick`
  un-happen — their codes simply stop appearing in stats; clients must prune them from
  country lists and the globe.
- **`newWorld {seed?, settings?}`** is the other half, and the two are not the same thing:
  `restart` keeps the world it has and rewinds it, so the **genesis-only settings**
  (`starting_country_count`, `starting_stability_range`, `starting_inflation_range`,
  `rival_pairs`, `friendly_pairs`, `region_count`) cannot take effect; `newWorld` generates a
  world from scratch out of `seed` and the current settings, which is what those keys need.
  `settingsAck.restartRequired` therefore means "waiting for the next **Genesis**" — the keys
  are recorded immediately and apply the moment a `newWorld` builds a world with them.
  `newWorld` dissolves every fork, discards history honestly (the old timeline and its store
  are gone, not hidden), keeps the current play/pause state, and replies with a full
  `hello` + `status` + `snapshot` handshake — so a client resets its country roster, event
  cache, checkpoints and ribbon exactly the way it does on a fresh connection. A settings
  combination the generator refuses is answered with a `toast` and leaves the running world
  untouched. Other sockets attached to the same runtime are not re-handshaked; they see the
  new world from their next frame and should reload.
- **Checkpoints are client-side bookmarks** in the prototype: `{id, tick, label, note}`,
  drawn as ◈ flags on the ribbon. The only engine round-trip is `restart` ("begin anew").
  A real engine may later persist them (`saveCheckpoint` / `listCheckpoints` → `checkpoints`),
  but this contract only requires `restart`.
- **Real Annals: `annals {tl?, country?, tick?}` → `annalsData`.** The archive is derived from
  the event log by the engine (`engine/annals.py`), not from whatever events a page happens to
  hold:
  - `eras:[{startTick,endTick,eventCount,dominantKind}]` — structural windows, each closed by a
    severity-2 event. They have no poetic names because the engine does not name them.
  - `wars:[{aggressor,defender,startTick,endTick,ticks,outcome:"peace"|"ongoing"}]` — from real
    `WAR_DECLARED`/`PEACE` events. `endTick`/`ticks` are `null` while a war is ongoing.
    **There are no death tolls, war names, or victors: the engine records none.**
  - `records:{worstInflation?,longestWar?,mostCoups?}` — only superlatives that rest on a
    recorded number.
  - `majorEvents:[{id,tick,kind,severity,country,country2,intervention,headline}]` — every
    severity ≥ 1 event, most recent `ANNALS_MAJOR_EVENT_LIMIT` (200) first delivered, with
    `majorEventCount` giving the exact total so the client can label the scope honestly.
  - `country` filters to events involving one code (`null`/`"ALL"` = the whole world), and
    `tick` bounds the archive to that moment in that timeline's history (defaults to the tick
    on screen). A tick with no reconstructible history answers with a `toast`.
  A client rendering fabricated eras, tolls or war titles must label them as set dressing and
  must not present them as engine history; in real-engine mode use `annalsData`.
- **The Impact tab is real too.** It queries `annalsImpact` over the selected timeline and
  displays only real event edges, deduplicated descendant counts, direct children, generations,
  affected countries, and recorded effect counts. The explicit offline mock computes the same
  fields only over its delivered local cache and labels that scope.
  - **`scope` is the honest bound on that answer, and clients must show it.** Ranking streams a
    bounded tick window rather than the whole branch, because an unbounded pass grows with
    history and stalls the tick loop. `scope.startTick`/`scope.endTick` are the inclusive tick
    bounds actually ranked (`endTick` is the timeline's tick; `startTick` is
    `max(0, endTick - 500 + 1)`, 500 being the current window). `scope.rankedEvents` is how many
    events fell in that window. `scope.complete` is `true` only when the window reached back past
    tick 0 — i.e. the whole timeline really was ranked — and a client may say "the complete
    record" only then; otherwise it must name the window.
  - **Edges are counted within the window only.** `directChildren`, `descendants`, `generations`,
    `recordedEffects`, `crisisDescendants` and `affectedCountries` are computed over the events in
    `scope`; a cascade that reaches out of the window is truncated at its edge, and a parent
    older than `startTick` is not ranked at all. These are therefore in-window counts, not
    all-time totals, which is why `eventCount` (the timeline's full event count) is reported
    separately. Cascades are short-lived, so in practice the window rarely clips a live one.
- **Acts-of-God explorer** is assembled client-side from delivered events with
  `intervention: true` plus locally injected negative-id events (which show "no paper trail").

## Forward compatibility (r4 preview — PROPOSAL §12)

Binding tolerance rules so the post-v1 expansion extends this contract instead of breaking it
(§10 remains reserved for `updateSettings`, added in M6.3):

- **Both sides ignore unknown keys.** Clients must not crash on unrecognized fields in any
  message; the server must ignore unrecognized command fields. r4 adds keys to existing
  messages. Multi-cause DAGs already extend legacy `parentId` additively with `parentIds` and
  typed `causes`; future revisions may add edge metadata without removing those compatibility
  fields. Events may likewise gain `operationId`.
- **The intervention catalog is data, not code.** `hello.interventions` is the complete,
  authoritative list — clients never hardcode intervention kinds. r4 grows this list
  substantially (raw injection, physics edits, multi-tick operations) with no client changes
  beyond rendering what `hello` delivers.
- **Stats and commodity fields are open sets.** `CountryStats` gains keys mid-protocol-version
  in r4 (per-commodity stocks/prices, asset capacities); clients render known keys and ignore
  the rest, exactly as `frame` stats dicts already gain country codes mid-stream (§6).


## 10. Protocol-v2 globe rendering boundary

The real-engine LOD ladder is data-driven. Every zoom shows territories, capitals, nation
labels, persistent wars, recent real strikes, and the sea/air lanes with their in-flight shipments
(thin and volume-weighted when far, full width when near). Satellites (one glyph per aggregate
count) join above 1.05× and rail above 1.6×. Route width/visibility derives from current in-flight
volume, and route/mover fading derives from real endpoint fleet condition. Bloc membership is a
treaty rim around members' coasts; each nation keeps its categorical color.

The remaining honesty boundary is deliberately narrow: **territory shapes and satellite orbital
paths are visual projections.** Territory shapes are landmasses derived only from marker
positions, regions, and owners (a disc per marker, an isthmus to same-region markers, clipped to
the marker's spherical Voronoi cell). Their centers/count/condition are authoritative. Country position,
territory ownership, region, endowments, commodity magnitudes, asset inventory, bloc membership,
lane direction/volume, shipment identity/progress/settlement fields, strike direction/power, and
all conditions are authoritative engine state. Shipment disappearance removes the mover; a visible
`SHIPMENT_LOST` event may add a transient loss pulse, but no ghost mover is retained.

Satellite phase is interpolated over the current simulation tick interval, so projected orbits move
continuously while running; pausing snaps to the authoritative tick and freezes them, and a manual
step advances them once. Shipment movers interpolate from their last drawn position toward the next
position implied by their authoritative departure/arrival schedule, never beyond the scheduled
endpoint. The inspector continues to report the latest authoritative progress; pausing snaps the
glyph to that progress and freezes it. This lets an arriving mover reach its known destination
before the engine removes it. A selected vanished shipment may
remain as a terminal inspector snapshot, classified as lost only from a matching visible
`SHIPMENT_LOST`, as arrived only when it disappears at/after its known arrival tick, and otherwise
as unavailable in the reconstructed view. The globe never retains the vanished mover, and the UI
does not fabricate an arrival event or an unsupported price effect.

For legibility and frame-time stability, dense lanes render a stable bounded sample of real shipment
IDs while retaining every current shipment in the inspector lookup and country logistics totals.
A rendered shipment keeps the same visual slot for its whole flight; during live play, a vacated
slot is filled only by a shipment at route departure, never by making an existing shipment appear
mid-route. Rendered movers are never synthetic. Air, sea, and rail tracks receive a small
presentation-only lateral separation, plus stable mover fan-out, that returns to zero at both
authoritative endpoints; shipment route endpoints and progress remain unchanged. Mover
activation—including double-click—selects the right inspector and does not automatically open a
modal dossier.

Visible lane strokes are selectable interaction targets with a forgiving screen-space hit area. A
selected lane is highlighted and the right inspector exposes every currently active shipment ID on
that exact directional carrier lane, including cargo, quantity, progress, schedule, and condition.
Dense lanes paginate the roster at 40 shipments per page rather than dropping entries; selecting a
roster row focuses the corresponding real mover. Route disappearance clears the route selection
because lanes aggregate only current in-flight traffic.
