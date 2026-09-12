# Simulation-Driven World — Trade, Distance, Logistics, Assets, Diplomacy & War

**Status:** implemented as milestones M9–M16 (July 2026). This is the design as written before
the work started. Where the build departed from it, the reasoning is in
[`../design-decisions.md`](../design-decisions.md), one section per milestone, and the story is in
[`../devlog.md`](../devlog.md). The largest departures: §9's "retire `web/engine.js`" did not
happen (the mock survives as an explicit `?engine=mock` fallback and is never selected silently),
and §14's supply/demand band became a floor because the band as written could not be satisfied.
**Date:** 2026-07-15
**Supersedes:** the v1 stance that globe assets were frontend fiction (`docs/frontend-contract.md`).

---

## 0. Motivation & guiding principle

Today the globe's ships/planes/trains/satellites — and the map geometry itself — are
**client-side fiction** (`globe.js`), explicitly so per the frontend contract. Trade is
non-bilateral and non-spatial: each country trades grain against an abstract "world
market", money minted/burned from nowhere; there is **no distance, no partners, no
routes** in the engine.

This design makes the world **causally whole**: every glyph on the map is a reflection of
real simulation state, distance is a first-class quantity that affects everything, and the
physical assets (fleets, satellites, grids) have real economic jobs. The chains this is
after — *scarcity → war → conquest → resource capture → economic gain*, and *GDP falls →
trade falls*, and *a famine-struck friend gets relief by boat (or airlift if the sea is
blockaded)* — becomes literally true in the model, not aspirational.

**Non-negotiables carried from the existing engine:**
- **Determinism/replay:** same seed ⇒ byte-identical event log. Every new piece of state is
  deep-copied in snapshots and reconstructed by a `REPLAY_EFFECTS` handler; RNG draw order
  is pinned. Golden master regenerated per milestone.
- **Legibility > realism** where they conflict: physical enough to be honest, abstract
  enough to trace every headline to a root cause in ≤1 keypress.
- **Modularity / no rewrite:** additive systems slotted into the existing tick loop, ledger,
  event cascade, and structural/replay machinery — which are reused unchanged. New concepts
  live in their own modules (`engine/space.py`, a `Commodity` registry, `TradeSystem`/
  `LogisticsSystem`, `engine/diplomacy` for alliances) with defined seams.

---

## 1. Spatial foundation (`engine/space.py`)

- Each country is seeded at worldgen with an **abstract position on a unit sphere**
  (`Country.position`) — *not* Earth lat/lon. Distance is derived, so it is automatically
  self-consistent (triangle inequality holds; no contradictory pairwise distances).
- Countries are grouped into **regions/landmasses** (`Country.region`), positions clustered
  by region. This gives real neighbours and enables the land/sea distinction.
- `distance(a, b)` = great-circle distance between positions. **Same-region** pairs are
  "land-connected"; **cross-region** pairs require sea or air.
- Relations seeding is **proximity-correlated**: nearby countries are likelier to be seeded
  as rivals (friction breeds among neighbours) and, later, alliances form among the
  compatible. Replaces `worldgen._seed_relations`' population-adjacency stand-in.
- **Frontend consequence:** the bridge sends positions + regions; the globe renders the
  **real** layout. Client-side blob *placement/adjacency* becomes engine-driven (stylised
  continent shapes may remain cosmetic, but distance/adjacency are authoritative).

## 2. Endowments

- Each country is seeded with **endowment levels** per extractive commodity (energy, raw
  materials, and arable capacity for food). Endowments drive base output of those
  commodities → some countries are oil-rich, some ore-rich, some breadbaskets. This is what
  creates genuine **interdependence** (nobody is self-sufficient in everything).
- Endowments are **territory-bound**: captured on annexation (see §8).

## 3. Commodities & production

- **Six causal buckets**, behind a generic `Commodity` abstraction so a seventh is data,
  not code:

  | Bucket | Produced by | Needed by | Shortage bites via | Carried by |
  |---|---|---|---|---|
  | Food | `grain_output` (land endowment + climate shocks) | pop | famine→unrest→revolution *(existing)* | sea+rail |
  | Energy | energy endowment × `power_grid` condition | industry (`gdp_tick`) + homes (pop) | gdp penalty, power strain, unrest | sea+rail |
  | Raw materials | ore endowment | feeds manufacturing | manufacturing throughput drops | sea+rail |
  | Manufactured | `gdp_tick` × `innovation_mult`, needs raw+energy inputs | investment + pop wealth | **GDP↓ ⇒ exports↓** | sea+air |
  | Consumer | `gdp_tick` + labour (pop) | pop + household wealth | stability/health dip, inflation up | sea+air |
  | High-tech | `innovation_mult` × `education` | advanced economies | innovation/education growth slows | air |

- Per-country per-commodity **output / need / stock** (stock = shock buffer, like grain
  today). New `Country` fields hold these as `dict[Commodity, float]`.
- **Production chains:** manufactured/high-tech require raw + energy *inputs*; a country
  starved of energy can't fully convert its GDP into manufactured exports. Legible one-hop
  chains, not a full input-output matrix.
- Shortage consequences wire into **existing** stats (gdp, stability, unrest, inflation,
  innovation) via the threshold/cascade system already in place.
- Implemented as a `CommodityProductionSystem` slotted alongside the existing
  `ProductionSystem`.

## 4. Bilateral trade & pricing (`TradeSystem`, replacing the world-market model)

- Each tick, per commodity: compute every country's **surplus/deficit** (output+stock−need).
- **Match** deficits to surpluses **greedily by net landed cost**, with **partial fills
  across multiple suppliers**, in a fixed deterministic order; **allies and friends
  prioritised**. (Greedy, not global optimisation — legible and deterministic.)
- **Price** of a matched unit:
  `commodity_base × (1 + distance_cost) × relation_modifier × scarcity_multiplier × (1 + importer_tariff)`
  - friends discount, enemies **blocked**, embargo **blocks**, farther = pricier, scarcer =
    pricier, importer `tax_rate` → tariff. Intra-alliance: no tariff + extra discount (§7).
- **Money is conserved:** importer `treasury` → exporter `treasury` **transfer** (ledger
  `transfer` entries), not mint/burn. FX handled via the existing `fx_pools` for
  cross-currency legs.
- A matched trade does not settle instantly — it becomes a **shipment** (§5).

## 5. Logistics — physical shipments (`LogisticsSystem`)

- A matched trade produces a **`Shipment`** object stored on `World`:
  `{id, from, to, commodity, qty, carrier, value, depart_tick, arrive_tick, status}`.
- **Carrier auto-selected** by cost + urgency + reachability:
  - **rail** — same region only, under a distance cap; cheapest, medium capacity.
  - **sea** — any distance, bulk, cheap/unit, **slow**, **blockade/war-interdictable**.
  - **air** — any distance, fast, expensive, low capacity; **high-value goods** and
    **emergency relief** (famine/critical shortage from an ally) — **bypasses blockades**.
- **Transit time** = `distance ÷ carrier_speed` (ticks). **Effective throughput** per
  carrier per country = `capacity × fleet_condition` (§6) — caps qty dispatched per tick.
- Lifecycle events (each with structural + replay handlers):
  - `SHIPMENT_DISPATCHED` — goods + money committed at depart.
  - `SHIPMENT_ARRIVED` — goods → importer stock, money → exporter, at `arrive_tick`.
  - `SHIPMENT_LOST` — interdicted (war on the lane, or third-party `NAVAL_BLOCKADE`
    controlling the route): economic loss + crew casualties; sea most vulnerable, air
    bypasses.
- **Emergency relief:** a famine/critical shortage + a high-relation/ally supplier triggers
  a subsidised relief shipment — sea normally, **air if the sea lane is blocked**. This is
  the "a struggling friend gets relief by boat" story, made literal.

## 6. Asset coupling — every asset gets a real job

Effective capacity of each = `base_capacity × condition` (condition from the **existing**
M7.2 failure/threshold system, which now has economic teeth):

- **naval_fleet** → sea freight capacity.
- **rail_network** → rail capacity + domestic distribution efficiency.
- **air_fleet** → air freight + relief airlift capacity.
- **power_grid** → **production multiplier** for energy/manufactured/consumer/high-tech.
- **satellites** → **trade reach & matching quality**: sets the max distance a country can
  trade over efficiently and the quality of price discovery. `SATELLITE_FAILURE` **shrinks
  reach** and widens spreads → measurably less/worse trade.
- **communications** → **coordination**: how well relation/alliance discounts realise and
  how cleanly shipments route (loss ⇒ more friction/loss even among friends).

Frontend: glyph count/size/health on a lane = **real** per-lane throughput and fleet
condition. A failure *shows* (a boat vanishes, a satellite goes dark, a lane thins).

## 7. Diplomacy — alliances & coalitions (`engine/diplomacy`)

Makes the existing declarative `ALLIANCE`/`ALLIANCE_BROKEN`/`EMBARGO` kinds **structural**
(closes a gap documented since M5).

- **Alliance/bloc state** on `World`. Blocs **form** from high mutual relations + shared
  rivals + proximity (and via god/intervention). `ALLIANCE` now sets bloc membership, not
  just a +70 relation record.
- **Effects:**
  - Trade: intra-bloc = tariff-free, discounted, priority-matched (§4).
  - **Mutual defense:** when a member is attacked, each ally **may join the war**, prob ∝
    `alliance_strength × relation ÷ distance` — distant allies hesitate. Produces coalition
    wars (many-vs-many).
  - Coalition **strikes** (§8) against shared enemies.
  - **Relation propagation:** ally-of-ally warms, enemy-of-ally cools.
- **Breaking:** strain (separate peace, betrayal, diverging interests) → `ALLIANCE_BROKEN`.
- **EMBARGO** made structural: blocks the trade lane between the pair.

## 8. War, strikes & conquest economics

- **Strikes:** during an active war, periodic `STRIKE` events fire (NEW conventional kind;
  `NUCLEAR_STRIKE` already exists). Rate/accuracy ∝ `1 ÷ distance` (near enemies hit harder
  and sooner). Each does real attrition: stability, **infrastructure-condition damage**
  (feeding back into §6 capacities), population casualties. Coalition members contribute
  strikes.
- **Resource-war motivation:** `WAR_DECLARED` probability gains a term
  `f(own_scarcity(commodity C) × proximity_to_C-rich_neighbour × low_relation × not_allied)`
  — distance-gated, so you fight reachable rivals, not the far side of the world. Wired into
  the existing exogenous/politics war roll.
- **Conquest capture:** annexation transfers the loser's **endowments + commodity stocks +
  territory/position** into the annexer (extending the existing population/grain merge) —
  the resource payoff that motivated the war. Occupation redirects the loser's endowment
  output as tribute.
- Interdiction (§5) and blockades give war a **direct economic front**, not just a stability
  drain.

## 9. Visualization & level-of-detail (frontend-contract **v2**)

- **Zoom ladder** (all engine-driven, zero fiction):
  - **Far** → conflict layer: `STRIKE` missile arcs between countries actively at war;
    alliance-bloc tints.
  - **Mid** → orbital layer: satellites; a dark one = a real `SATELLITE_FAILURE`.
  - **Near** → surface layer: boats/planes (rail at closest), **sized by real lane volume**,
    tinted by fleet condition.
- **Hard requirement:** failures and magnitudes are **clearly visible**.
- **Bridge v2 payload** adds: positions, regions, endowments, per-commodity
  stock/output/need, per-lane trade volumes, asset inventories + condition, in-flight
  shipments, `STRIKE` events, alliances/blocs.
- **Retire `web/engine.js`** (the mock): the real bridge is the sole source of truth.
  `globe.js`/`app.js` consume real data; negative-id fake headlines and client-side asset
  dossiers are removed.

## 10. Determinism & replay plan

- New `World`/`Country` state (positions, regions, endowments, commodity dicts, shipments,
  alliances) is deep-copied by snapshots.
- New events (`SHIPMENT_DISPATCHED/ARRIVED/LOST`, `STRIKE`, structural `ALLIANCE`/
  `ALLIANCE_BROKEN`/`EMBARGO`, annexation-capture) each get **both** a live structural
  handler and a `REPLAY_EFFECTS` handler — the M7-era rule that a missing replay side
  silently diverges `world_at`/scrubbing.
- RNG draw order pinned at every new decision point (matching, carrier choice, interdiction
  roll, ally-join roll, strike roll). Golden master regenerated + eyeballed per milestone.

## 11. Milestone roadmap

Each lands with the full suite green, lint/mypy/lint-imports clean, and golden regenerated.

- **M9 — Spatial foundation:** positions on sphere, regions/landmasses, `distance()`,
  endowments, proximity-correlated relation seeding.
- **M10 — Commodities & production:** 6 buckets, per-commodity output/need/stock, production
  chains, shortage→stat consequences.
- **M11 — Diplomacy/alliances:** structural blocs, formation/breaking, relation propagation,
  structural `EMBARGO` (trade & war will read this state).
- **M12 — Bilateral trade & pricing:** matching, conserved treasury transfers, distance/
  relation/alliance/tariff/scarcity pricing, embargo/war blocking.
- **M13 — Logistics:** discrete in-flight shipments, carrier selection, transit time,
  interdiction/blockade, emergency relief airlift.
- **M14 — Asset coupling:** fleet condition → throughput; satellites → reach; comms →
  coordination; power_grid → production; failures produce visible economic shifts.
- **M15 — War & conquest economics:** distance-gated `STRIKE` events, coalition war-joining,
  resource-war motivation, annexation captures endowments/stocks/territory.
- **M16 — Bridge/contract v2 & frontend:** v2 payload, globe LOD ladder, retire the mock.

## 12. Modularity / non-rewrite strategy

- `engine/space.py` — the distance primitive every system reads.
- A generic `Commodity` registry — buckets are data.
- `CommodityProductionSystem`, `TradeSystem`, `LogisticsSystem` — separate tick-loop slots,
  not edits to existing systems' internals.
- Asset capacity read through a single `effective_capacity(asset, condition)` helper (M7.2
  already has the shape).
- Alliances as diplomacy state read by trade (§4) and war (§8).
- The tick loop, ledger, event cascade, and structural/replay registries are **reused
  unchanged** — new events just register handlers.

## 13. Open risks & tuning

- **Balancing surface** grows a lot (6 commodities × production chains × pricing × alliances).
  Mitigated by a measured tuning pass and an adversarial review per milestone, and by
  documenting shortfalls honestly instead of forcing constants.
- **Determinism surface** grows (shipments, alliances, strikes) — mitigated by the strict
  structural+replay discipline above and a replay-equivalence test per new stateful event.
- **Performance:** per-tick matching is ~O(countries² × commodities); fine at the ≤20-country
  cap, but watch it (and reuse the O(ticks²)-fix lesson: no full-log scans).

## 14. Known technical traps (surfaced in design review — resolve in the relevant plan)

- **Cross-currency settlement:** §4's "importer treasury → exporter treasury transfer" is only
  a plain ledger `transfer` when both use the same currency. Cross-currency legs must be
  burn-in-A / mint-in-B or an `fx_pools` round-trip (per §4.5) — the transfer is *not* a
  simple two-pool move. Nail this in the M12 plan.
- **Land/sea topology is not cheap on the frontend:** rendering a believable globe where
  engine positions + region land/sea adjacency actually *look* coherent (coastlines, which
  pairs are "across water") is real work hiding in M16, not a free consequence of §1.
- **Seeding constraint from conserved money:** bilateral trade + conserved money means
  aggregate supply must roughly meet aggregate demand per commodity at worldgen, or every
  country is perpetually short and trade never clears. This is a worldgen *invariant*, not
  just a tuning knob — assert it in the M9/M10 plans.
- **Retiring the mock raises the run bar:** dropping `web/engine.js` makes `web/index.html`
  require a running `meddler serve`. Check this against PROPOSAL §2.3 / implementation-spec
  definition-of-done "a stranger can run it from the README alone" before committing to it in
  M16 (may need a bundled one-command launcher).
