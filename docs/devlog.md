# Devlog

How Meddler got built, and the bugs that taught me the most. The engineering log
([`progress.md`](progress.md)) has every measurement and test count. This is the version with the
plot. Dates are 2026.

## The premise

Most simulation UIs let you move through *time*: scroll the feed, drag the chart. I wanted one
where the main axis is *causality*. You click a headline and ask why it happened, then what it
caused, then what the world would have done without it. That turns into a list of hard
requirements. The engine has to be deterministic, or "rewind" is a lie. Every change has to be
recorded against a cause, or "why" is a guess. And forks have to share their dice with the
original, or "what changed" is noise.

The design ([`PROPOSAL.md`](../PROPOSAL.md)) went through three revisions before any engine code
existed. The second one moved the frontend from a terminal UI to a local web app. The frontend
was then built first, against a frozen JSON contract and a JavaScript mock engine, so the
interaction design could be judged by using it rather than by reading about it. The real engine
came after, in milestones M0 to M8, each ending in a command that had to pass.

## Determinism is a property you lose quietly

Three bugs from the first week, none of which would have failed a casual test.

**`hash()` is salted.** The design specified fork seeds as
`hash((base_seed, fork_tick, "fork", fork_id))`. CPython salts string hashing per process, so
that expression returns a different number every run unless `PYTHONHASHSEED` happens to be
pinned. That breaks the project's first promise the moment anything is compared across two
processes, which is exactly what the golden-master test does. `Rng.sub()` now derives seeds from
SHA-256. Pinning the variable in CI would have hidden the bug for CI only; everyone else's
`meddler run` would still have wandered.

**Replay only replayed money.** The first `Timeline.world_at` rebuilt history by replaying ledger
entries on top of the nearest snapshot. The tests passed, because the test systems only moved
money. Real systems change stability, inflation, and relations every tick. A fixture that dropped
stability by 1.0 per tick showed it: reconstructed at tick 237, every country was off by exactly
7.0, the distance back to the snapshot at 230. The fix became the engine's central rule. Every
mutation, even ambient per-tick drift, is an event carrying post-clamp `StatDelta`s, the same way
money carries `LedgerEntry`s. Replay then just adds numbers.

**Forks need the same dice, not new ones.** The design reseeded each fork. But once random
systems exist, a fork with *no* intervention drifts from prime simply because it rolls different
numbers, and the headline feature, "every difference is yours", becomes false. Forks now restore
prime's exact RNG state at the fork tick, which is why the timeline records RNG state every tick
and not just at snapshots. A no-intervention fork matches prime bit for bit forever. That is
the strongest determinism test in the suite.

## M7: the acceptance test that had never run

M7 grew the event catalog from 50 kinds to 114 and restored the consequence edges an earlier
milestone had trimmed. Its acceptance test, "every non-rare kind fires at least once across 20
seeds of 5000 ticks", had been written but never run to completion. Running it found real bugs,
not tuning problems:

- The threshold system fired six crisis kinds with a bare `log.append()` instead of the cascade
  path, so their stat effects were never applied and their consequences never scheduled.
  Harmless while nothing hung off them, and a silent hole once M7 wired revolutions and
  secessions underneath.
- **Peace was unreachable.** War drains stability by 0.3 per tick and recovery adds 0.1, so
  stability falls for a war's entire length. Peace was gated on stability above 30. In a
  2-seed sample, wars lasted 4,500 ticks of a 5,000-tick run.
- **Occupation was a black hole.** An occupied country stayed "at war", so the war penalty pinned
  its stability at zero, and both ways out (annexation and liberation) required stability to
  recover first. One dump had all eight countries occupied, every one at stability 0.0.
  Clearing the war fixed that, until occupied countries were dragged into *new* wars through
  other paths. The guard went into `_declare_war` itself, the one place every war passes
  through.

Annexation still landed at 20% of runs against a 40% target. I stopped there and wrote down the
shortfall, and the likelier cause (a residual stability drag from "fizzled" war declarations),
rather than turning a probability knob until the number came out green.

## 553 seconds to 2

A code review after M7 found that three per-tick lookups scanned the *entire* event log to find
events from the current tick, once per country. That is O(countries × ticks²) over a run, and it
was why a 5000-tick run took over nine minutes. Events are appended in tick order, so the
current tick is a contiguous tail: walk backwards and stop at the first older event. The third
lookup, the start of an occupation, had no natural stopping point, so it became a field on
`Country`, set by both the live handler and its replay twin.

553 seconds became 2.0, and the full test suite went from about two minutes to eight seconds.
The golden master was byte-identical before and after, which is the best available proof that an
optimization changed nothing but speed. It is also a bug I should have caught while writing it.

## M8: the UI had never met the engine

M8 was meant to be polish. It turned out the frontend had never run against the real engine at
all: there was no browser WebSocket client, and `app.js` hardcoded the mock. So M8 grew a real
adapter (`web/realengine.js`), a per-stat history buffer so charts and sparklines had real data,
and a server that hosts the frontend and the WebSocket on one port. Every message the browser
exchanges was checked with a Node smoke test running the real adapter code against a live
server, and every field the UI reads was reconciled against the message builder that produces
it.

## v2: no fiction on the globe

At the end of v1, the globe's ships, planes, trains and satellites were decoration drawn by the
client, and the docs said so. Trade was one commodity against an abstract world market, with
money minted and burned from nowhere. There was no distance at all. The v2 track (M9 to M16,
[design](design/v2-simulation-driven-world.md)) set out to make every glyph on the map a real
object in the simulation. The two recurring lessons were to measure the world before building on
an assumption about it, and to treat a calibration that "looks fine" as unproven.

### M9: space

Countries got positions on a sphere, regions, and resource endowments. No two countries may be
closer than a minimum separation. The tempting argument is that regions are disjoint caps, so
countries in different regions can't collide. That argument has no margin at all: the gap
between caps exactly equals the required separation once each region holds a single country, a
configuration the test suite uses constantly. What actually carries the guarantee is a direct
clearance check against every placed country. Across about 17,000 generated worlds, rejection sampling never needed more than 7 of 64
attempts. Deleting the clearance check made 50 tests fail, so the tests are real.

M9 also introduced the first platform caveat to "byte-identical everywhere": trig functions are
not guaranteed bit-identical across libm implementations. It's documented rather than hidden.

### M10 and M11: the world had to be checked, not assumed

Six commodities replaced grain. The design asked for aggregate supply to sit inside a fixed band
around demand. That band turned out to be mathematically unsatisfiable: population spans 2 to 60
million, so the population-weighted endowment swings from seed to seed, and no constant fits.
It became a floor, calibrated against the worst of 30 seeds.

M11 was supposed to form alliances from high mutual relations. A quick measurement first: over
20 seeds and 1000 ticks, the *highest* relation between any two countries was −0.1. Worldgen
seeded rivalries, and everything decayed toward zero, so there had never been a friendship in the
world. Genesis now seeds a few friendships among near neighbours. After tuning, 29 of 30 seeds
form an alliance and 18 see a coalition war.

### M12 and M13: trade that conserves money and takes time

Bilateral trade replaced the world market: allies first, then the cheapest landed cost, with
lanes closed by war, embargo, or open enmity. Money no longer appears from nowhere. Each fill is
two single-currency transfers bridged by a per-currency FX desk, so conservation holds by
construction. The conservation test had to learn to count the desks, and it's a stronger test
for it.

M13 put the goods on ships. The first interdiction probability, 4% per tick per ship in a war
zone, compounded over a twelve-tick voyage to roughly 46% losses and multiplied famine by 14.
Transit delay alone, with interdiction off, changed famine not at all. The shipped value is
0.15%.

### Pacing: a bug disguised as drama

Watched at 4x speed, the world was a constant barrage: 141 crises per 1000 ticks per seed. Most
of it was one bug. Stability and inflation both treated "produces less food than it eats" as a
shortage. Since M12, that describes every country that imports its food, fed or not. Those
countries got a permanent stability penalty with no recovery, and an inflation push that
dragged them toward 17%. Switching both systems to a stock test cut mean inflation from 9.1% to
4.7%. With some honest tuning on top (longer terms of office, rarer coups, a calmer default drama
setting), crises fell to 43 per 1000 ticks. That is 3.3x calmer, against a 6x target. The rest
are genuine crises, and I'd rather ship a world that is alive than one that is quiet.

### M14: nothing varied, because nothing cost anything

M14 was meant to give every infrastructure class an economic job: fleets carry freight, the power
grid multiplies output, satellites extend trade reach, communications realize discounts. Before
writing any of it, I measured asset condition. Mean 0.996, 98% of country-ticks above 0.9, and
satellite failures: zero. Coupling the economy to a constant is not coupling.

The root cause was magnitude. Infrastructure upkeep was 50 minor units per asset against tax
revenue in the millions: 0.001% to 0.1% of revenue. Maintenance was always fully funded, so
condition sat at 1.0 forever. The same mismatch was why M7's runs had never produced a debt
crisis. The fix anchors upkeep to a country's *potential* GDP, not its current output. Assets are a
sticky obligation and revenue is cyclical, so a country that collapses politically can no longer
maintain what it built while healthy.

Freight capacity took three calibrations. Set by eye, fleets never passed 58% utilization. Set
at twice *peak* demand, an A/B test against unlimited freight moved exactly zero units over three
seeds, because demand is so right-skewed that the peak only half-fills the hold. Twice the
*mean* shipped: typical trade clears, while peaks and degraded fleets are metered.

Then an adversarial review of M14 found that I had several things wrong, and the log keeps both
versions:

- I had reported that the fiscal change made the world calmer. Paired measurement said the
  opposite: +5.8 crises per run on its own, t = 2.9. My first run had been unpaired and used an
  unfaithful baseline.
- The upkeep share sat on the wrong side of its own crossover point, so funding alone could
  never rot an asset. The real mechanism was different from the one documented, and the docs were
  corrected.
- The freight cap was also throttling emergency relief. That was fixed: relief now bypasses the
  budget but still uses it up.
- The satellite-reach calibration was circular. I had measured distances on lanes that had
  already been priced by the very term being calibrated.
- And the big one: 71% of all shipments cost literally zero, because a typical price rounded
  below one minor unit. The same magnitude bug, one layer over. That became its own piece of
  work instead of hiding inside M15. Trade settlement was rescaled, and in a later sample every
  shipment carried a nonzero price, with trade worth 8 to 20% of tax revenue.

### M15 and M16: war with an economy, and a globe that tells the truth

M15 made war physical. Strikes along active fronts scale with distance, resource-starved
countries pick fights with richer neighbours, occupiers take a quarter of the occupied country's
extractive output as tribute, and annexation captures stocks, endowments, territory, and half of
the infrastructure. Territory markers became separate from a country's capital, so a conqueror
keeps its own location instead of teleporting to the land it took.

Around then the causal model got an upgrade too. Events can have several typed causes, not just
one parent, and the impact inspector totals an event's downstream effects without
double-counting a consequence two causes share. It refuses to call a descendant list a
counterfactual. A "what if" is only offered where a real fork ran.

M16 changed the protocol to v2 and rebuilt the globe on it. Countries sit where the engine put
them. Every ship, plane, and train is a real shipment with a real id and progress. Trade lanes are
directional volumes. Strikes are real strike events. The mock survives only behind
`?engine=mock`, labelled as such.

## After M16: long runs

Two findings from letting the world run for a long time.

**Memory.** A server's memory grew to 460 MiB by tick 300. Snapshots deep-copied the cumulative
event log every 50 ticks, so the snapshots held 119,615 copied events alongside 34,189 real ones.
History moved into a per-run SQLite file, with snapshots holding a boundary instead of a copy and
forks sharing their parent's prefix. The same run now holds about 42 MiB at tick 1000. The golden
master stayed byte-identical through the whole rework.

**A world that stopped trading.** A tick-7069 history showed its last shipment dispatched at tick
1222. Freight fleets had decayed to exactly zero condition. At zero stability, full-funded
recovery (+0.005) exactly cancels instability damage (−0.005). Zero fleets meant no trade, which
meant shortages, which pinned stability at zero, which meant no recovery. A true absorbing state.
The fix is deliberately narrow: a fleet that exists always moves at least 5% of its capacity.
Crises stay severe, but there is always a way back.

## September: making it public

Publishing meant a pass over everything a stranger would touch: packaging, CI, reproducible
screenshots, a browser QA pass over every feature against the real engine, and this
documentation. It also meant the hosted demo, which runs the same Python engine and bridge
inside the page under Pyodide, so trying Meddler takes a link instead of an install. The
details are in the last entries of [`progress.md`](progress.md).

## What's still open

Listed in full at the top of [`progress.md`](progress.md). The notable ones: several god-mode
interventions (war, peace, alliance, embargo, secession) still only nudge stats instead of
changing structural state, secession never creates a new country, and adopting a fork truncates
scrub-back history before the fork point.
