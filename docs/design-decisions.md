# Design decisions

Every non-obvious call in Meddler, with the reasoning and, where it exists, the measurement that
settled it. The first part argues the overall shape of the system. After that, the log follows
the build milestone by milestone: spec gaps that had to be filled, bugs caught before shipping,
and each v2 milestone's decisions. Entries are edited in place when a later milestone supersedes
them, and they say so.

For a tour of the system as it stands, read [`architecture.md`](architecture.md) first. For the
build told as a story, see [`devlog.md`](devlog.md).

## r3.1: the r4 preview and why it shaped v1 code early (2026-07-08)

**The decision:** `PROPOSAL.md` gained §12, a post-v1 roadmap ("r4") for deep, emergent
causation: flow-based infrastructure, a small commodity set, a causal attribution ledger, and an
unbounded intervention surface. None of it was buildable in v1, but §12.3's eight
forward-compatibility rules bound all v1 work from M3 onward.

**Why write it before v1 was finished:** the honest critique of r3 was that its causation is
authored (hand-written `ConsequenceRule` graphs), not emergent, and its infrastructure decorative
(condition floats that roll dice rather than assets that carry trade, power, or comms). Fixing
that after v1 required v1 code to be *shaped* correctly from the start. Otherwise r4 would mean
rewriting the trade system (grain literals everywhere), the god path (hardcoded kind lists), and
replay (unrecorded mutations). §12.3 turns each foreseeable rewrite into a cheap shaping rule
that costs nothing in behavior.

**What deliberately did not change:** every v1 formula, constant, probability, milestone, and
acceptance criterion, including the golden master. r3.1 was documentation only; if a §12.3 rule
ever forced a behavior change, that would be a spec bug to raise, not a change to make.

**Where the edits landed:** `PROPOSAL.md` (revision note, §12, short "r4 note" anchors at
§3.5/§4.4/§4.5/§4.7/§5.3/§6.3/§6.7.2, glossary additions); `docs/design/implementation-spec.md`
(a global convention pointing at §12.3, notes on M3.1/M5.1/M6.2/M7.2, a quick-reference row);
`docs/frontend-contract.md` (the "Forward compatibility" section: unknown-key tolerance,
data-driven intervention catalog, open stats sets). In hindsight it was the best-value page in
the design: the v2 track (M9–M16) landed as additions, as intended.

## Why the system is built this way

These six sections argue the shape of the system as a whole, drawn from how the code actually
works. The decision log that follows fills in the specifics each one only gestures at.

### Why tick-based

The engine advances in discrete integer ticks (`meddler/engine/tickloop.py`'s `tick(world,
rng)`, "1 tick = 1 simulated day" per `PROPOSAL.md` §4.2) rather than real-time or event-driven
simulation, and never reads the wall clock (§4.3 rule 1 — no `time.time()`). This is not a
stylistic choice; it is what makes replay, scrubbing, forking, and trace possible at all.
`Timeline.world_at(at_tick)` (`meddler/engine/timeline.py`) reconstructs exact historical state
by finding the nearest snapshot ≤ a tick number and replaying recorded deltas — a well-defined
operation only because "tick 371" is a stable, addressable coordinate. An event-driven or
continuous-time design would have no equivalent fixed index to snapshot against or scrub to; "the
world as of 2.3 real-time seconds into a variable-speed playback" is not a reproducible
statement, but "the world as of tick 371" is, byte-for-byte, on any machine. Playback speed
({0.5, 1, 2, 4} ticks/sec) is explicitly a UI-side timer wrapped around this pure step function
(§4.2) — the engine itself has no notion of speed, only of the next tick. Discrete ticks also
give every event an unambiguous `tick` field to sort, cascade-schedule (`fire_tick`), and hang a
causal tree from (`parent_id`/`depth`), which the trace and cascade machinery below depend on
structurally, not just conventionally.

### Legibility > realism

`PROPOSAL.md` §3.1's third design principle is the product's actual thesis: "every number on
screen should be explainable by pointing at events. If a mechanic can't produce a headline, it's
too subtle — cut it or amplify it." This is why the M2 binding convention (see "Stat deltas: the
Ledger pattern extended to non-money state" above) forces *every* mutation, including ambient
per-tick drift with no discrete narrative moment, to be recorded as an `Event` carrying
`stat_deltas`/`ledger` — not because the drift itself needs to be a headline (unrendered ambient
events are fine, per that section), but because legibility requires that *if* a player asks "why
did stability drop," there is always a recorded cause to point at, never a silent field mutation.
`engine/trace.py`'s whole-causal-component DFS and `engine/cascade.py`'s `parent_id`/`depth`
bookkeeping exist to make that pointing operation cheap: `PROPOSAL.md` §2.3's success criterion
is "every headline traceable to a root cause in ≤ 1 keypress" (`t`, wired to the `trace` bridge
command), and that bar is only reachable because nothing in the simulation is allowed to affect
outcomes without also being an inspectable node in that tree. The corollary this ethos rules out
deliberately: a "more realistic" continuous system that models subtle, hard-to-attribute forces
(smooth interpolation, hidden multipliers with no event) is explicitly the wrong call here even
if it were more accurate — §3.1's own words are "cut it or amplify it," not "model it faithfully
but quietly."

### Determinism rules

Full mechanism detail lives in the sections above and below this one — this entry is the index,
not a restatement. The binding guarantees (`PROPOSAL.md` §4.3): same seed ⇒ byte-identical event
log across runs *and machines*; a single explicit RNG stream threaded through every system, never
a module-level or wall-clock source; sorted iteration everywhere RNG consumption or event
ordering is involved; a pinned RNG draw order per event kind. Three load-bearing corrections to
the letter of §4.3 are recorded in detail elsewhere in this file and cross-referenced rather than
repeated: `Rng.sub()`'s stable SHA-256 hash instead of the salted builtin `hash()` (see
"`Rng.sub()` uses a stable hash" above — the cross-machine half of the guarantee depends on this,
not just same-machine reruns); fork RNG mirroring prime's exact draw sequence instead of
reseeding (see "Fork RNG semantics resolved" above); and the pinned exogenous/consequence RNG
draw order documented in `meddler/engine/cascade.py`'s module docstring, which the M3.1
statistical coverage test cannot itself catch a violation of. The fourth pillar —
snapshot+replay reconstructing state without re-running systems, hence without consuming RNG and
without any possibility of diverging — is `Timeline.world_at`'s core property (§4.4) and is what
lets scrubbing and forking coexist with the "no re-simulation" rule at all.

### Ledger design

Every money mutation is `Ledger.transfer`/`mint`/`burn` (`meddler/engine/ledger.py`), recorded as
a `LedgerEntry` **on the event that caused it** (`PROPOSAL.md` §4.5), in integer minor units —
never floats in a pool. This buys two invariants for free: Invariant A (per-currency
conservation — pool totals change only via explicit mint/burn) and Invariant B (replaying every
ledger entry from tick 0 reproduces current pools exactly), both directly property-tested
(`tests/property/test_ledger.py`) rather than merely asserted. `StatDelta`
(`meddler/engine/events.py`, see "Stat deltas: the Ledger pattern extended to non-money state"
above) is the same discipline generalized to every other piece of mutable world state
(`stability`, `inflation`, infra `condition`, `World.relations`, ...) that isn't money: post-clamp
delta recorded on the owning event, so replay adds deltas without needing to know any field's
clamp range. The two patterns are deliberately one idea applied twice, not two separate
mechanisms — an owning `Event` is the only way any world state is allowed to change, which is
also the substrate the r4 causal-attribution ledger builds on (§12.1.3/§12.3.1, r3.1 above).

### Why a registry

`meddler/engine/registry.py`'s `EVENT_REGISTRY: dict[str, EventSpec]` replaces what `PROPOSAL.md`
notes r1 had — a closed ~25-kind enum with hardcoded switch logic — with a declarative catalog:
each kind is a self-contained `EventSpec` (stat_deltas, pool_transfers, `ConsequenceRule`
children, `Condition` gates) that `ConsequenceSystem`/`ExogenousSystem` execute generically, with
"no special-case code per kind" (§4.7). v1 alone needed 114 kinds (M7.1) across natural,
economic, political, military, infrastructure, and social categories; a switch-statement
architecture at that scale is exactly the maintainability failure r1 hit. Adding a kind is one
`EventSpec` plus ≥ 4 headline templates — no core-loop change, no UI change (the god-mode palette
and bridge's intervention catalog are both *generated* from the registry, filtered on
`is_intervention`, never enumerated by name — `PROPOSAL.md` §12.3.4). The one deliberate escape
hatch, `meddler/engine/structural.py`, exists precisely because a few kinds (`LEADER_CHANGE`,
`OCCUPATION_BEGIN`, `SECESSION`) exceed what a scalar `stat_deltas` dict can express, and even
that hatch is itself a kind-string-keyed registry, not an if/elif chain — the same plugin shape
one layer down. This shape is also what makes the r4 preview's "hundreds more kinds, an
unbounded intervention surface" (§12.1.4) additive rather than a rewrite: r3.1's §12.3
forward-compatibility rules bind v1 registry code today specifically so r4 only adds entries.

### Why a bridge, not a bundled UI

`meddler/bridge/` is a thin translation-and-transport layer (`bridge/adapter.py`'s own docstring:
"Pure functions: World/Event/Country -> the exact JSON-serializable dicts the contract specifies
... no simulation logic") over a WebSocket protocol (`docs/frontend-contract.md`), not a UI
toolkit or a bundled application. `engine/` stays free of `websockets`, sockets, and wall-clock
reads (§4.1's layering wall, CI-enforced via `lint-imports`), so the *entire* simulation is
usable and testable without a browser: the headless CLI (`meddler run --headless`, used by the
golden-master test) and the bridge integration suite
(`tests/bridge/test_handshake.py`, a real `websockets` client against a real server, no browser
involved) both exercise the real engine through paths that never touch `web/`. That separation is
also what let the frontend be built and design-iterated (`web/` on `web/engine.js`'s in-browser
mock) *before* the real engine existed, against a frozen contract, and is what keeps a future
second client (a TUI, a different web build, a bot) a matter of implementing the same
`{connect, send}` surface rather than forking the simulation. The cost is real and current — see
the M16 authoritative-globe entry below and the M6 bridge decision log. The protocol is tested
independently from the renderer so the bridge boundary remains explicit.

## Determinism

### `Rng.sub()` uses a stable hash, not the builtin `hash()`

**What the skeleton said:** `docs/design/implementation-spec.md` M1.1 gives `Rng.sub()` as
`Rng(seed=hash((self._seed, seed_key)))`, and the fork/restart recipes in `PROPOSAL.md`
§4.3.5/§4.3.6 are literally `hash((base_seed, fork_tick, "fork", fork_id))` /
`hash((base_seed, restart_tick, "genesis"))`.

**The problem:** those tuples contain strings (`"fork"`, `"genesis"`, country/fork-id codes).
CPython salts `str`/`bytes` hashing per process via `PYTHONHASHSEED` unless it is pinned, so
`hash((base_seed, fork_tick, "fork", fork_id))` returns a *different* integer on every process
invocation unless `PYTHONHASHSEED` happens to be fixed in the environment. That silently
contradicts the engine's #1 property (`PROPOSAL.md` §4.3, success criterion §2.3.2:
"byte-identical event log across runs and machines") the moment any fork- or restart-derived
output is compared across two separate process runs — which golden-master CI, by definition,
does.

**The fix:** `meddler/engine/rng.py`'s `_stable_hash()` derives the child seed via
`int.from_bytes(hashlib.sha256(repr(parts).encode()).digest()[:8], "big")` instead of the
builtin `hash()`. SHA-256 has no process-level salt, so the same inputs always produce the same
seed on every machine and every run. `Rng.sub(seed_key)` and every fork/restart call site route
through this.

**Verified empirically** (see `tests/unit/test_rng.py::test_sub_is_stable_across_process_hash_seeds`,
and manually):
```
$ python3 -c "print(hash(('x',1)))"                 # differs every run
$ PYTHONHASHSEED=0 python3 -c "print(hash(('x',1)))" # stable only if pinned
```

**Why not just pin `PYTHONHASHSEED=0` in CI instead:** that only protects CI; a bare `pytest`
or a user's local `meddler run` would still be nondeterministic across their own machine's
invocations, and "same seed ⇒ byte-identical event log... on every machine" is a user-facing
guarantee, not a CI-only one. The stable-hash fix makes the guarantee intrinsic to the code
instead of dependent on an environment variable nobody outside CI would think to set.

**Scope:** this is a determinism-mechanism defect fix, not a change to any specced formula,
probability, or constant — no simulation behavior changes as a result.

## Spec gaps filled

### `Country` gained `currency_name` / `currency_symbol`

**What's missing:** `PROPOSAL.md` §5.1 transcribes the `Country` dataclass without any currency
field, but two other sections need one: §5.3 (worldgen) says each country gets "currency (name,
symbol from a curated list)", and `docs/frontend-contract.md`'s `hello`/`countryAdded` messages
both carry `currency:{name,symbol}` per country. There is no other struct in the PROPOSAL for a
country's currency identity to live on.

**The fill:** added `currency_name: str` and `currency_symbol: str` directly to `Country`
(`meddler/engine/model.py`). The *ledger* currency identifier (the `currency` argument to
`Ledger.transfer/mint/burn`) remains the country's `code` — per §4.5, "Pools per country:
treasury, households, corporates (all in local currency)" — so these two new fields are
display-only and never touched by ledger/invariant logic.

**Scope:** additive struct fields only; no new mechanic, no formula change. Not a "stop and
ask" case under implementation-spec.md rule 3 (that rule is about un-specified numbers/formulas)
— this is a struct field two other already-approved sections require to exist somewhere.

### Name/currency/leader generation lives in `engine/worldgen.py`, not `text/names.py`

**What the guide said:** `docs/design/implementation-spec.md` M1.3 lists `text/names.py` as a file to
create, and describes `generate_world` (in `engine/worldgen.py`) as giving each country "a
syllable-grammar name (`text/names.py`)" — i.e. worldgen calling into `text/names.py`.

**The conflict:** `PROPOSAL.md` §4.1 is explicit and CI-enforced (via M0.4's import-linter
contract, `pyproject.toml`'s `[tool.importlinter]` layers): `engine/` may import nothing above
it, and `text/` sits *above* `engine/` in the layering (`bridge → text → engine`). If
`engine/worldgen.py` imported `text/names.py`, `lint-imports` would fail — I verified this
exact failure mode in M0.4 by adding a deliberate `engine → bridge` import and watching it get
rejected; the same mechanism would reject `engine → text`.

**The fix:** all genesis name/code/currency/leader-trait generation (syllable grammar, the
3-letter code scheme, the curated currency list, the fixed trait list, leader-name generation)
lives directly in `engine/worldgen.py`, with zero import of anything under `meddler/text/`.
`text/names.py` is deferred to M3.3, where it holds a genuinely different, disjoint concern:
headline flavor-word lists (e.g. "scapegoat" fillers, §6.5) selected by hashing `event.id` for
prose rendering — text-layer content that never needs to reach back into `engine/`.

**Scope:** file-organization fix forced by a hard, already-CI-verified layering rule; no
worldgen formula, probability, or output distribution changes as a result.

## Tuning candidates (worldgen defaults with no PROPOSAL formula)

`PROPOSAL.md` §5.3 specifies *what* worldgen must sample (population, gdp/capita tier, grain
surplus/deficit, infra condition, rival relations) but gives exact numeric ranges only for
population (2-60M) and infra condition (0.7-0.9); the rest ("gdp/capita tiers", "grain_output
and grain_need", "slightly negative" relations) name the mechanism without a number. All such
gaps are centralized in `meddler/engine/config.py`'s "Worldgen (§5.3)" block with one-line
comments, so a human tuning pass only has one file to touch:

- `GDP_PER_CAPITA_TIERS = (800, 4000, 15000)` + `GDP_PER_CAPITA_JITTER` — three flat tiers,
  ±20% jitter, sampled uniformly (no distribution skew toward poor/rich specified anywhere).
- `GRAIN_NEED_PER_CAPITA`, `GRAIN_SURPLUS_JITTER` — grain_need scales linearly with population;
  grain_output is need ± 30%, so genesis mixes surplus and deficit nations roughly evenly.
- `STARTING_TREASURY_TICKS` / `..._HOUSEHOLDS_TICKS` / `..._CORPORATES_TICKS` — genesis pool
  balances expressed as "N ticks' worth of gdp_tick" rather than an absolute figure, so they
  scale sensibly across the population/gdp range.
- `STARTING_CIVIL_RIGHTS/PRESS_FREEDOM/EDUCATION/HEALTH` — flat mid-range (50-65) genesis
  defaults; §5.1 defines what these stats *do* but not their starting values.
- `RIVAL_RELATION_RANGE = (10, 30)` — magnitude of "slightly negative" (PROPOSAL's own words)
  seeded rivalry; applied as `-uniform(10, 30)`.
- Starting `govt_type` — every genesis country is `GovtType.DEMOCRACY` (simplest legible
  default); PROPOSAL never specifies a genesis government-type distribution.

None of these affect determinism, invariants, or any specced formula — they are inert
initialization choices needed to make `Country`'s required fields constructible at all. Revisit
during M7.4's tuning pass if genesis feels numerically off in play.

## Bugs caught and fixed before shipping

### `Country.pools` had ledger-prefixed keys instead of bare keys; `World.fx_pools` was missing

**Found while:** designing M1.4's replay (`Timeline.world_at`), which needs to apply a logged
`LedgerEntry` back onto real `World` state and therefore needs to resolve a ledger pool name to
its backing dict.

**The bug:** `PROPOSAL.md` §5.1 is explicit that `Country.pools` uses **bare** keys —
`"treasury"`, `"households"`, `"corporates"`. §4.5 separately specifies that *ledger entries*
use the **prefixed** global form, `"<CODE>.treasury"` etc., specifically so a `LedgerEntry` can
name a pool unambiguously across countries. The original M1.3 `worldgen.py` conflated the two
and stored `Country.pools` with prefixed keys (`{"ELB.treasury": ...}` instead of
`{"treasury": ...}`), which would have been silently wrong — nothing caught it until something
tried to resolve a ledger entry's pool name against real per-country state.

**The fix:** `Country.pools` now uses bare keys (`meddler/engine/worldgen.py`). Added
`World.fx_pools: dict[str, int]` (`meddler/engine/model.py`) as the backing store for `"fx:<pair>"`
bookkeeping pools, which §4.5 requires but which belong to no single country, so they have
nowhere else to live. Added `ledger.resolve_pool(world, pool_name)` and
`ledger.apply_to_world(world, entry)` (`meddler/engine/ledger.py`) to translate a ledger pool
name (`"<CODE>.<name>"` or `"fx:<pair>"`) into the right backing dict + local key. The existing
`Ledger.apply(pools, entry)` (M1.2, still used by `tests/property/test_ledger.py`) is unchanged
and stays a generic flat-dict utility; `apply_to_world` is the new World-aware counterpart.

**Verification:** M1.2's ledger invariant tests still pass unchanged (20/22 total tests before
this fix, 22/22 after, all green); `test_worldgen.py` re-run clean after the key-format change.

### Stat deltas: the Ledger pattern extended to non-money state

**Found while:** reviewing M1.4's `Timeline.world_at`. The original
implementation replayed only `event.ledger` — every non-money mutable field (`stability`,
`inflation`, `gdp_tick`, `population`, `grain_*`, infra `condition`, `World.relations`) stayed
frozen at the nearest snapshot during reconstruction. All M1.4 tests passed anyway because both
fixture systems mutated exclusively through the ledger — they didn't represent what M2's systems
actually do (§6.2/§6.7.2/§5.2 mutate exactly those fields, continuously, every tick).

**Proven empirically before fixing:** added a fixture system that decrements `country.stability`
by 1.0/tick with no ledger entry, ran 300 ticks with `snapshot_interval=10`, and asserted
`world_at(237)` matches a continuous run to 237. It failed exactly as predicted — every country's
reconstructed stability was off by 7.0 (237 - 230, the nearest snapshot boundary), e.g.
`FET: -169.80` (reconstructed) vs `-176.80` (live). See
`tests/property/test_determinism.py::test_world_at_reconstructs_stat_deltas_not_just_ledger`.

**The fix:** added `StatDelta` (`meddler/engine/events.py`) as a direct structural counterpart to
`LedgerEntry` — `{target, stat, delta}`, where `target` is a country code or the literal string
`"relation"` (with `stat` encoding the sorted pair as `"CODEA:CODEB"`). Added
`Event.stat_deltas: tuple[StatDelta, ...]`, parallel to `Event.ledger`. Added
`meddler/engine/stats.py` (`apply_country_stat`, `apply_relation_delta`, `replay_stat_delta`) as
the Ledger-equivalent apply/record helpers. `Timeline.world_at` now replays `event.stat_deltas`
the same way it replays `event.ledger`.

**The binding convention for M2 (read this before writing any system):** every system —
including *continuous, ambient, non-narrative* per-tick drift with no discrete event in the §6.6
catalog (e.g. "stability -0.4/tick if inflation > 8%") — must still emit an `Event` (via
`world.log.append`) carrying its `stat_deltas` and/or `ledger`, exactly the way §4.5 already
requires for money. This is not optional or narrative-only bookkeeping: it is how replay
reconstructs state at all. Background/ambient events can use `severity=0` and a kind that
`text/headlines.py` (M3.3) simply never renders as a headline (no template lookup happens unless
the CHRONICLE pane asks for one) — being unrendered is fine; being unrecorded is not.

**The delta must be post-clamp.** A system computes `new_value = clamp(old + raw_change, lo,
hi)`, then records `delta = new_value - old`. This makes replay exact without `stats.py` needing
to know any field's clamp range — it just adds whatever delta was recorded.

**Verification:** full suite green (33/33) after the fix; the regression test above fails
without it (confirmed by temporarily commenting out the `replay_stat_delta` call and re-running)
and passes with it restored.

## Fork RNG semantics resolved: mirror prime's dice, don't reseed

**The conflict (found during M1.4):** §4.3.5 says a fork's timeline gets an independently
reseeded `Rng(seed=hash((base_seed, fork_tick, "fork", fork_id)))` — unconditionally, whether or
not there's an intervention. Separately, §3.5 says "every divergence is attributable to the
intervention" and M5.2 (`docs/design/implementation-spec.md`) bills "fork with no intervention ⇒ zero
diff at every tick" as **"the strongest determinism test in the project."** These can't both hold
once real RNG-consuming systems exist (M3.1's `ExogenousSystem`/`ConsequenceSystem`): an
independently-reseeded fork advancing stochastic systems will diverge from prime even with
`intervention=None`, simply because it draws different random numbers, not because of anything
the player did.

**Decision (2026-07-08, a product call):** a fork **mirrors prime's exact RNG
state** at the fork tick rather than reseeding. Absent an intervention, a fork therefore tracks
prime bit-for-bit forever — genuinely satisfying "zero diff," not just passing on a technicality
(confirmed under a real RNG-consuming fixture system, not just a deterministic one — see
`test_fork_zero_diff_without_intervention_under_real_randomness`). With an intervention, the
fork's dice are still identical to prime's; only the world state differs (the intervention's
effect), so any subsequent divergence is provably attributable to the intervention and nothing
else (`test_fork_with_intervention_diverges_via_world_state_not_dice`).

**Implementation** (`meddler/engine/rng.py`, `meddler/engine/timeline.py`):
- `Rng.get_state()`/`Rng.from_state(seed, state)` snapshot/restore the underlying
  `random.Random`'s exact internal state (not a seed — the full generator state, so a restored
  `Rng` continues the identical draw sequence as an independent object).
- `Timeline.rng_states: dict[int, tuple]` records `rng.get_state()` **every tick** (not just on
  `snapshot_interval`, unlike World snapshots). This is necessary because forking can happen at
  any past/scrubbed tick (§3.5: "An intervention at a past tick... forks the timeline"), and
  prime's *current* Rng object has already advanced past that point by the time you fork into
  history — only a per-tick log of historical state lets `fork()` recover the exact dice prime
  had at an arbitrary earlier tick. Cheap: a few KB per tick, same unbounded-growth tradeoff the
  project already accepts for World snapshots and the event log.
- `Multiverse.fork()` now does `Rng.from_state(seed, self.prime.rng_states[at_tick])` instead of
  `Rng(seed=derive_seed(...))`. Verified this works correctly for scrubbed-past forks too
  (`test_fork_at_scrubbed_past_tick_mirrors_prime_historical_dice`) and that two no-intervention
  forks from the same tick are identical to each other
  (`test_two_no_intervention_forks_from_same_tick_are_identical`) — expected, since "nothing
  changed" twice should produce the same counterfactual twice.
- `derive_seed`/the literal `hash((base_seed, ..., "fork", fork_id))` recipe from §4.3.5 is
  **retired for forking** — `fork_id` no longer participates in any RNG derivation, only in
  which `Multiverse.forks` slot (B/C/D) a fork occupies. `derive_seed` remains exactly as
  originally implemented for **restart** (§4.3.6), which is a different operation (rewinding and
  resuming the *same* single prime timeline, not branching a second one) where "deliberately
  fresh dice" is the explicitly stated, unaffected intent — `restart()` also now prunes and
  reseeds `rng_states` alongside `snapshots` so a post-restart fork recovers the *new* dice, not
  the truncated future's stale ones.

**Verification:** full suite green (36/36); the previous test asserting a no-intervention fork
*diverges* under `_rng_system` was inverted (it now asserts equality) since that was the old,
now-superseded, semantics.

## M2 economy systems (M2.1-M2.5)

Each system's module docstring carries its own specific reasoning; this section is the index and
covers the two decisions that are load-bearing across multiple systems.

- **`base_gdp` added to `Country`** (`meddler/engine/model.py`): §6.2's formula
  `gdp_tick = base_gdp * (0.5 + stability/200) * innovation_mult` needs a roughly-fixed
  structural capacity distinct from the live, fluctuating `gdp_tick` it computes — without it,
  gdp_tick would decay toward zero every tick (the multiplier is always <= 1.0). Genesis sets
  `base_gdp` from worldgen's population*gdp_per_capita calculation and derives the *initial*
  `gdp_tick` from the same §6.2 formula so genesis is self-consistent with tick 1 onward
  (`meddler/engine/worldgen.py`).
- **`tax_rate` is fraction-scale (0.15 = 15%), not percent-number-scale**, because §6.2's tax
  formula is the literal `treasury += tax_rate * gdp_tick` — no `/100`. This is the opposite
  convention from `inflation`/`stability`/etc., which are percent-number-scale (§6.3 compares
  `inflation > 8%` directly against `8`, not `0.08`). Caught while writing `fiscal.py`, before it
  shipped with a double-conversion bug; `config.STARTING_TAX_RATE`'s comment documents the
  convention so it isn't relitigated by the next system that reads `tax_rate`.
- **TradeSystem models an abstract "world market" priced in veri**, not bilateral country
  pairing — `Country` has no geography/trade-partner fields in PROPOSAL's simplified §5.1 (unlike
  the old README's richer trade block), and no matching algorithm is specced. Deficit countries
  import their full shortfall at the §6.2 markup price; surplus countries export their full
  excess at the unmarked-up base price; both settle through treasury via burn/mint (§4.5's
  specced conversion pattern). `grain_stock` is deliberately left untouched by this baseline
  trade (production/consumption nets to zero once trade fully clears the gap every tick) — it's
  a shock buffer that only moves once M3+/M7 exogenous events (drought, embargo, blockade)
  disrupt output or trade itself. Full reasoning in `meddler/engine/systems/trade.py`.
- **FX drift's `trade_balance_norm` sign convention and `drift_from_inflation` formula**
  (neither given by §6.2) are chosen so the literal `+0.002*tanh(trade_balance_norm) +
  drift_from_inflation` structure produces economically sensible directions: a deficit country's
  currency weakens (exchange_rate rises), higher inflation weakens a currency further. Full
  reasoning in `meddler/engine/systems/fx.py`.
- **InflationSystem's "money supply minted this tick"** counts every mint-kind ledger entry any
  earlier system recorded this tick for that country (Production's gdp_tick mint, Trade's
  export-proceeds mint) — the only bookkeeping available, since there's no separate "mint total"
  field. **Verified empirically, not just reasoned about**: `test_inflation_stays_in_legible_band_over_full_pipeline`
  runs the real six-system pipeline for 300 ticks and asserts inflation stays bounded
  (empirically settles near ~8% equilibrium, doesn't spiral) — a check added in review,
  and it passed on the first implementation, so this reading stands as specced/implemented, not
  narrowed. Full reasoning in `meddler/engine/systems/inflation.py`.
- **StabilitySystem excludes the "-5 one-shot on scandal" rule** — that's the SCANDAL EventSpec's
  own `stat_delta`, applied once the M3.1 registry/ConsequenceSystem exists, not a continuous
  per-tick condition this system evaluates. StabilitySystem covers only the four ambient rules
  (high inflation, shortage, war, low-inflation recovery).
- **The world_at-vs-continuous-run reconstruction check is now standard per system**, not just a
  one-off M1.4 regression test: every `test_<system>.py` in M2 has a
  `test_world_at_reconstructs_*` case plugging the *real* system into `tickloop.SYSTEMS` and
  diffing `world_at(t)` against a continuous run's live state for every field that system
  touches. This converts "did I remember to record a stat_delta for every mutation" from a
  discipline into a per-system test failure if forgotten.
- **`apply_infra_condition`/nested StatDelta encoding** (`meddler/engine/stats.py`): infra asset
  condition lives at `country.infrastructure.<class>.condition`, not a direct `Country`
  attribute, so `apply_country_stat`'s plain `setattr` can't reach it. Extended `StatDelta`'s
  existing `stat`-as-router pattern (already used for `target="relation"`) with a
  `stat="infra:<class>"` convention rather than adding a third dataclass shape.
- **InfrastructureSystem's 24 per-class rate constants** (base cost, degradation, instability
  degradation, recovery — 6 asset classes x 4 rate types) have no PROPOSAL values at all, only
  named formulas (§6.7.2). v1 uses one uniform constant per rate type across all 6 classes rather
  than inventing unjustified relative costs (e.g. "satellites cost more than rail" — plausible,
  but nothing in PROPOSAL supports a specific ratio). Maintenance payments transfer
  treasury→corporates (upkeep spending stays in the tracked economy) rather than burning.
- **RelationsSystem's proportional decay is provably overshoot-safe**: `new = value*(1-rate)` for
  `0 < rate < 1` can never cross zero or flip sign in one tick, so no clamping/overshoot guard is
  needed (an earlier draft had one; a test proved it dead code, since a value can only
  asymptotically approach zero under this formula — see the system's own comment).
- **Threshold bridge (M2.7) parent attribution**: `INFLATION_CRISIS`/`UNREST`/`CIVIL_WAR_RISK`
  are parented to that country's same-tick `InflationSystem`/`StabilitySystem` event (a clean,
  unambiguous single cause) — `FAMINE_WARNING`/`FAMINE`/`DEBT_CRISIS` use root, since no single
  M2 system owns `grain_stock` (nothing mutates it yet — TradeSystem's design deliberately leaves
  it as a shock buffer, see trade.py) or `treasury` (touched by Trade/Fiscal/Infrastructure) is a
  clean enough single cause to attribute. As scoped in review, the *full*
  "mint → INFLATION_CRISIS → UNREST" chain from `docs/design/implementation-spec.md`'s M2.7 "done when"
  text is **not** fully buildable yet — there is no standalone `MINT` event kind in M2 (minting
  is bundled inside `PRODUCTION`/`TRADE_EXPORT` events) and cascade scheduling is M3.1's
  `ConsequenceSystem`. What's implemented and tested instead: `INFLATION_CRISIS`'s parent
  correctly links to the `InflationSystem` event that caused it (and `UNREST`'s to
  `StabilitySystem`'s), with hysteresis (arm/re-arm via `Country.armed`) verified to suppress
  repeat-firing while a stat sits past its threshold and to re-fire after recovering past the
  margin. Recovery margins (the hysteresis dead-zone width) are undocumented magnitudes in
  PROPOSAL ("re-arm only after recovering past a margin" — no number given); tuning candidates,
  see `config.py`'s "Threshold bridge" block.
- **M2 end-to-end verification** (`tests/unit/test_tickloop_integration.py`): all nine M2 systems
  are wired into `tickloop.SYSTEMS` in §6.1 order (no dedicated slot for the threshold bridge, so
  it runs last). Confirmed empirically, not just by unit test: 500 ticks/8 countries with no
  errors; per-currency money conservation holds exactly against the log's own mint/burn entries;
  `world_at` matches a continuous run across every field five systems touch; and — because §6.2
  has no probabilistic branches, so all nine M2 systems are entirely RNG-free — fork-zero-diff
  now holds against the *real* engine (not a hand-written fixture) for 50 ticks, while an
  intervention-fork provably diverges.

## M3.1 drama systems (registry, exogenous, consequence)

- **Schedule queue / clip counter live on `World`, not module-level state.** ConsequenceSystem's
  pending-consequence queue (`World.schedule`), its monotonic `schedule_seq`, and the per-timeline
  `clip_count` are fields on `World` so snapshots and forks deep-copy them for free and two
  timelines running in the same process (exactly what `tests/property/test_determinism.py` does)
  can never share queue state. Module-level state would have silently broken that test. Known
  boundary: `Timeline.world_at` reconstructs state by replaying an event's ledger/stat_deltas, not
  by re-running systems, so it does **not** rebuild queue state mid-interval — fine for M3.1 (its
  fork tests fork at exact snapshot ticks, where the queue is preserved in the deep-copied
  snapshot), to be addressed when scrubbing needs mid-cascade reconstruction (M4).
- **`PoolTransfer` shape is an M3.1 invention.** §4.7 references `PoolTransfer` only in a type
  annotation with no body. Defined here as a country-templated ledger movement
  (`kind`/`src_pool`/`dst_pool`/`amount`/`currency`, with `{primary}`/`{secondary}` tokens
  substituted at fire time), resolving to a concrete `LedgerEntry`.
- **Pinned RNG draw order** (the guardrail M3.1's statistical test can't see; enforced in
  `cascade.py`): exogenous roots iterate specs *sorted by kind*, roll once per spec always, then
  `rng.choice` the target(s) only on a hit; scheduling children rolls each rule (spawn-roll) first,
  resolves the target (`rng.choice` only for `target="random"`), then draws the delay, in that
  order, only on a successful spawn. Countries are always iterated `sorted(key=code)`.
- **Depth cap clips at the event level.** A fired event at `depth == max_depth` skips its whole
  consequence-rule loop (consuming no spawn RNG), sets `payload["cascade_clipped"]=True` once, and
  bumps `clip_count` once — but only if the spec actually has consequence rules (a leaf at the cap
  has nothing to clip and is left unmarked). Keeps `clip_count` deterministic and independent of
  dice. The registered core catalog is acyclic with max chain depth 7 < cap 8, so live runs never
  clip (clip_count == 0); the cap logic is covered by hand-built unit tests instead.
- **`WAR_DECLARED` is a temporary unconditional exogenous root.** The real §6.6.4 root is
  `WAR_SPARK` (p=0.002, gated on some relation < −60), which M3.1 doesn't implement. Critically, in
  an M2-only world no system drives a relation below zero (worldgen seeds rivalries at −10..−30 and
  RelationsSystem decays toward 0), so a −60 gate would fire zero wars and fail the acceptance test
  deterministically. So `WAR_DECLARED` is registered as an unconditional low-p root; replace with
  `WAR_SPARK` + the relation gate once relations can reach that range (M3.2).
- **Closure policy for the registry (landmine).** The named core-15 kinds are not closed under
  their consequence edges. Policy: register the core 15 + all interventions + the direct
  consequence children they reference, giving each real `stat_deltas` but *trimming* any onward
  edge that would point at a §6.6 kind outside this set (documented at each call site). One trim is
  load-bearing: `CAPITAL_FLIGHT`'s back-edge to `CURRENCY_SLIDE` is dropped, breaking the only
  cycle so the graph stays acyclic (depth ≤ 7). Full catalog population is M7.1.
- **Effects that exceed the declarative EventSpec model are flagged, not hacked.** `SECESSION`
  (creates a country), `WAR_DECLARED`/`PEACE`/intervention war/alliance/embargo (mutate
  `at_war_with`/relations — structural), `LEADER_CHANGE` (leader replacement), and
  `INFRASTRUCTURE_DAMAGE` (per-asset-class condition delta, which needs an apply path beyond
  `stat_deltas: dict[str,float]` over direct Country fields) register their declarative sub-effects
  only, with TODOs marking the M3.2/M5.1/M7.1 boundary. `INTERVENE_CHAOS` is registered as metadata
  only; its "fire a random exogenous root" resolution belongs to god-mode invocation (M5.1).
- **Settings tag filtering (§6.9) is honored** in ExogenousSystem (`enabled_event_tags` wildcard/
  intersection + `disabled_event_tags`).

## M3.2 — PoliticsSystem (elections, coups, leader changes, conquest rolls)

`meddler/engine/systems/politics.py`. This is where the M3.1 "effects that exceed the
declarative EventSpec model" list (above) first became real code: `LEADER_CHANGE` needs to
replace a `Leader` object, not apply a scalar delta, so M3.2 introduced
`meddler/engine/structural.py`'s `STRUCTURAL_EFFECTS` registry — a kind-string-keyed dispatch
table for structural mutations, the same plugin shape as `EVENT_REGISTRY` itself, rather than an
if/elif chain in `cascade.py` (see "Why a registry" above). **Load-bearing gap this created,
caught and fixed later:** `Timeline.world_at`'s replay-only reconstruction (§4.4) had no
counterpart for structural effects — a scrubbed/forked world would reconstruct a country with
its *original* leader even after a live `LEADER_CHANGE`, since replay only applied
`ledger`/`stat_deltas`. Flagged during M3.2, fixed in M4.2 (see next section) by adding a
parallel `REPLAY_EFFECTS` registry, deterministic and RNG-free, driven only by what the live
handler already recorded onto `event.payload`.

## M4 — Trace (M4.1) and scrubbing/restart/annals (M4.2)

- **M4.1 — `engine/trace.py`.** `trace(log, event_id)` returns the event's *whole causal
  component* in DFS pre-order (root first, then every child's full subtree before the next
  sibling) — not merely `event_id`'s direct ancestors/descendants. This matches `PROPOSAL.md`
  §3.3's mock trace view, which shows `TREASURY_DRAIN` as a sibling branch off `PRICE_SPIKE`
  even though it's neither an ancestor nor a descendant of the traced event. `d` (tree depth for
  indentation) is just `Event.depth`, since the causal tree is single-parent by construction — no
  separate bookkeeping needed.
- **M4.2 — scrubbing, restart, annals query, and the structural-replay gap fix.** `Timeline`
  gained `world_at`/scrub support and `Multiverse.restart`; `annals()` queries the event log for
  eras/wars/records. The M3.2 gap above is resolved here: `engine/structural.py`'s
  `REPLAY_EFFECTS` registry, called by `Timeline.world_at` for every replayed event alongside
  `event.ledger`/`event.stat_deltas`, tested in `tests/unit/test_scrub.py`. `docs/progress.md`'s
  "Known gaps" section confirms this was the only outstanding gap through M4.2 and marks it
  resolved.

## M5 — God mode (M5.1) and fork + compare (M5.2)

- **M5.1 — `engine/god.py`.** Interventions are ordinary `EventSpec`s (`is_intervention=True`,
  registered in `engine/kinds/interventions.py` by M3.1); `intervene()` is a thin invocation path
  with no per-kind functions and no hardcoded kind lists (the god-mode palette is generated by
  filtering `EVENT_REGISTRY` on `is_intervention`, never enumerated — `PROPOSAL.md` §12.3.4).
  **Deliberately deferred, documented in the module's own docstring:**
  `INTERVENE_WAR`/`INTERVENE_ALLIANCE`/`INTERVENE_EMBARGO` fire their declarative `stat_deltas`
  and cascade normally but do not yet mutate `at_war_with`/`World.relations` structurally, and
  `INTERVENE_SECEDE` does not yet create a country — consistent with the same gaps already noted
  for the organic `WAR_DECLARED`/`SECESSION` kinds since M3.1. The stated intent is a single
  shared `engine/structural.py` handler for both the organic and intervention paths once war
  becomes structural, not a one-off wired into god mode alone (delivered in M7.3, below).
- **M5.2 — `engine/diff.py`.** `Multiverse.fork`/`adopt_fork`/`drop_fork`/`restart` were already
  complete from M1.4/M4.2; M5.2's own addition is `diff(world_a, world_b)`, comparing two worlds
  on the three headline stats the `ΔWORLD` strip shows (stability/inflation/gdp) per country,
  returning only countries where at least one differs — so a no-intervention fork's diff is `{}`,
  not a dict full of `[x, x]` pairs, matching the "fork with no intervention ⇒ zero diff at every
  tick" acceptance bar (`tests/property/test_fork.py`).

## M6 — Web bridge (server, adapter, commands)

`meddler/bridge/server.py` owns one wall-clock ticker, `Session`, and SQLite history for the
lifetime of `meddler serve`. WebSocket connections are replaceable views onto that runtime rather
than owners of fresh worlds. Reload/reconnect therefore preserves the tick, running/paused state,
speed, settings, forks, focus/scrub state, and event history; the simulation keeps advancing without
a browser, while expensive frame projection is skipped until a client attaches. Runtime shutdown
closes and removes the ephemeral database. Durable save/load across process restarts remains a
separate feature and must serialize world/snapshots, RNG, multiverse metadata, history, and session
fields rather than relying on this reconnect lifecycle.

`bridge/adapter.py` is pure translation (its own docstring: "no simulation logic... every value here
is read from engine state some engine/ function already computed") — engine snake_case to contract
camelCase, plus a few renames with no 1:1 field (`gdp`/`gdp_tick`, `pop`/`population`,
`fx`/`exchange_rate`, `grainDays` derived from `grain_stock`/`grain_need`).
`bridge/commands.py` dispatches commands through the shared session. Optional string/integer
`requestId` values are echoed on all direct replies, including errors and multi-message responses;
unsolicited frames remain uncorrelated. `updateSettings` routes through `Timeline.update_settings`,
which records a validated `SETTINGS_CHANGED` structural event and replaces the same-tick snapshot.
Settings are therefore timeline-owned state with exact scrub, fork, restart, and replay semantics,
not browser or connection preferences. Unrecognized settings and command fields remain ignored for
forward compatibility.

**Known simplifications, each documented at its own call site (not silent):** event `id`s are
unique within a timeline but not remapped to be globally unique across A/B (the contract now says
so: use `(timeline, id)`); `countryAdded` has a real message shape but is unreachable in practice,
because nothing creates countries yet (the same root cause as the `SECESSION` gap above). At M6,
`snapshot.spark` and `countryDetail.series` were omitted rather than faked because the engine had
no per-stat history; M8.2a added that history (`Timeline.stat_history`, later moved to SQLite),
and both fields are now real.

**The protocol is tested independently of browser automation.**
`tests/bridge/test_handshake.py` drives the real server with real `websockets` clients, covering the
handshake, commands, correlation, cadence, and disconnect/reconnect lifecycle. The production
frontend selects the same-origin real WebSocket adapter by default; `?engine=mock` is the only path
to the explicitly approximate in-browser engine. These tests prove the transport and lifecycle
contracts without claiming pixel-level browser verification.

## M7 — Deep simulation: full catalog, failure chains, conquest, tuning

- **M7.1 — full catalog.** `engine/kinds/` grew to 114 registered kinds (from M3.1's 50),
  restoring every consequence edge M3.1 had trimmed to stay acyclic, plus a `"worst_relation"`
  `ConsequenceRule` target (for `WAR_SPARK`) and an `"infra_all"` `stat_deltas` key so
  `INFRASTRUCTURE_DAMAGE`/`RESTORED` have real per-class effects. Golden master regeneration here
  is expected, not a regression — every new exogenous root shifts the RNG draw sequence from tick
  1 (documented landmine, `docs/progress.md`'s M7.1 section).
- **M7.2 — infrastructure failure chains.** `engine/assets.py`'s `FAILURE_THRESHOLDS`/
  `effective_capacity` helpers, wired into `InfrastructureSystem`'s failure rolls with hysteresis
  (`Country.armed`, same pattern as `systems/thresholds.py`). Verified with a scripted
  zero-treasury/zero-stability world producing `SATELLITE_FAILURE` and its full chain, traced end
  to end (`tests/unit/test_infrastructure_failures.py`).
- **M7.3 — conquest/absorption completion.** `WAR_DECLARED` now structurally populates
  `at_war_with` and sets relation −90 (§5.2) via a new `structural.py` handler — the missing
  piece that finally makes `OCCUPATION_BEGIN`'s own gate (at war + relation < −80) organically
  reachable, closing the gap M5.1 had deferred. `PEACE` clears `at_war_with` generically (removing
  `god.god_peace`'s own duplicate copy of that logic). `PoliticsSystem` now rolls `ANNEXATION`
  (90+ ticks occupied, low resistance) and `LIBERATION_WAR` (occupied country's stability
  recovers), each with forward + `REPLAY_EFFECTS` handlers. `bridge/adapter.py` gained
  `active_countries()`, filtering `ANNEXED`/`DISSOLVED` out of every frame/hello/countryDetail
  country list (contract §6/§9).
- **M7.4 — tuning pass.** Closing out M7.1's own never-fully-run acceptance test surfaced real
  bugs beyond tuning:
  - **Threshold-routing-through-cascade fix.** `systems/thresholds.py` fired
    `INFLATION_CRISIS`/`UNREST`/`CIVIL_WAR_RISK`/`FAMINE_WARNING`/`FAMINE`/`DEBT_CRISIS` via a
    bare `world.log.append()` instead of `cascade.emit_event()` — harmless before M7.1 (nothing
    downstream), but meant none of their own `stat_deltas` applied and none of their newly-wired
    M7.1 children (`REVOLUTION`, `GOVT_TYPE_CHANGE`, `SECESSION`, `MINT`, `CREDIT_FREEZE`) ever
    scheduled. Rerouted through `cascade.emit_event` (a review pass confirmed no
    double-counting against `StabilitySystem`'s separate ambient penalty).
  - **PEACE deadlock, a real logic bug, not tuning.** `STABILITY_WAR_PENALTY` (0.3/tick) exceeds
    `STABILITY_RECOVERY_BONUS` (0.1/tick), so stability falls monotonically for a war's entire
    duration (confirmed: a 2-seed sample had wars running 4500–4800/5000 ticks). Gating organic
    `PEACE` on "stability > 30 while still at war" was very close to unsatisfiable (0/20 seeds).
    Fixed: dropped the stability gate, flat per-tick roll instead (`PEACE_BASE_P = 0.003`),
    added in `systems/politics.py`'s `run()` section 5.
  - **Occupation/re-war deadlock, found in `tuning_check.py`'s 8-country runs (not the 4-country
    coverage test).** `_begin_occupation` never cleared the occupied country's `at_war_with`, so
    the war-penalty drain above pinned its stability at 0 forever, and both `ANNEXATION`
    (needs stability > 15) and `LIBERATION_WAR` (needs stability > 50) gate on recovery — a dead
    end confirmed directly (a single-seed dump showed all 8 countries `OCCUPIED`, every one at
    stability 0.0). Fixed at two points: `_begin_occupation` (+ its replay mirror) clears wars
    bidirectionally, the same shape as `_make_peace`; `_declare_war` (reached via `emit_event`
    regardless of path) now no-ops if either party isn't `CountryStatus.ACTIVE`, with the
    replay side signaled by the absence of the `relation_after` payload key. **Known
    incompleteness, left as-is after review:** the guard is structural-only — a "declared
    but fizzled" war against an occupied country still applies `WAR_DECLARED`'s own `stat_deltas`
    (`stability -3`) and schedules `TREASURY_DRAIN`, since those apply in `emit_event` before
    `structural.run()` is reached. This residual drag, not sample size, is flagged in
    `docs/progress.md` as the more likely explanation for the 20%-vs-40% annexation shortfall
    below.
  - **`low_frequency_ok` policy (28/114 kinds, 24.6%, under the 40% guard).** Applied for two
    distinct, individually-documented reasons, not a blanket unblock: categorical
    unreachability (`is_intervention` kinds; settings-gated kinds like nukes behind
    `allow_nukes`; `EMBARGO`/`INFRASTRUCTURE_RESTORED` behind an intervention handler not yet
    built) versus genuine rarity with a confirmed working organic parent chain but a run budget
    too short to reliably reach it (`DEBT_CRISIS`/`MINT` — an economy-scale mismatch, treasury
    never came within orders of magnitude of 0 across 20 seeds × 5000 ticks despite 20+ wars/seed;
    `COMPANY_COLLAPSE` — a real 5-hop chain with compounding probabilities and delay windows that
    rarely all land within one run's budget).
  - **Result, documented as an open shortfall, not force-tuned:** 5-seed sample (the 30-seed spec
    bar was not run; M7 was closed at this point): severity-2 cadence and
    infra-chain rates both comfortably clear their targets; the "no permanent single-country
    equilibrium" bar is met (0/5 runs frozen, after the occupation fix); annexation sits at 20%
    of runs against a ≥40% target. The rule for this pass was "give it one attempt, then document the shortfall rather than
    distorting constants to force green", so no further probability tuning was applied. The evidence-backed next lever, per the incompleteness note
    above, is moving `_declare_war`'s guard up to the eligibility sites — not
    `ANNEXATION_BASE_P` or the stability threshold, both demonstrably not the bottleneck (a
    single-seed trace showed occupied countries genuinely reaching stability 50–100).

## Performance — O(ticks²) scan bug, found post-M7.4, fixed 2026-07-15

**Verified empirically**, not just reasoned about. `systems/thresholds.py`'s
`_find_same_tick_event()` and `systems/inflation.py`'s `_minted_this_tick()` each did a full
linear scan of `world.log` from index 0, every tick, for every country, looking only for that
tick's own events; `systems/politics.py`'s `_occupation_start_tick()` did a further unbounded
backward search per occupied country per tick. Together this made per-tick work
`O(countries × ticks)` and a full run `O(countries × ticks²)` — the dominant cost behind
multi-minute 5000-tick runs. **The fix:** `EventLog` (`engine/events.py`) gained `__reversed__`
(O(1)/element), so the two same-tick scans walk backward and `break` at the first
`event.tick < world.tick` (events are non-decreasing in tick, so this tick's events are a
contiguous tail); `_occupation_start_tick()` is deleted entirely in favor of a direct
`Country.occupation_start_tick` field set in both the live handler (`_begin_occupation`) and its
replay mirror (`_replay_occupation_begin`) — the same "record it on both paths or scrubbing
silently diverges" discipline as every other structural field (`at_war_with`, `occupied_by`).
Measured on a 5000-tick seed-1337 run: **553.0s → 2.0s, ~274× speedup**; full test suite dropped
from ~121s to ~8s as a side effect. The golden master (seed 1337, 1000 ticks) is **byte-identical
before and after** — the strongest available proof the optimization is behavior-preserving, since
no RNG draw order changed, only when a scan stops. Full numbers and the historical diagnosis:
`docs/progress.md`'s "Performance" section.

## M16 — authoritative globe projection (supersedes the cosmetic-assets boundary)

Protocol v2 closes the old v1 honesty gap without changing simulation behavior. A pure bridge
projection (`meddler/bridge/world_objects.py`) reads one supplied `World` and serializes positions,
regions, current territory ownership, endowments, six commodity buckets, all infrastructure
inventories, blocs, in-flight shipments, directional current-volume lanes, and recent real
`STRIKE` routes. It neither mutates state nor receives an RNG. Because every message constructor
projects the world it was actually given, `worldAt` naturally shows historical shipments and
territory ownership, and fork A/B projections remain tick-aligned.

**Why lanes are directional:** settlement, carrier capacity, endpoint condition, and cargo all
belong to an origin→destination shipment. Combining opposite directions would erase which economy
is exporting and could make a healthy reverse flow visually mask a degraded outbound fleet.
Carrier is also part of lane identity because sea, air, and rail have different conditions and
LOD thresholds.

In real-engine mode `globe.js` replaces its mover set on every projection: routes come only from
`lanes`; movers come only from `shipments` and retain their real `SHIP…` identifiers. A vanished
shipment is removed immediately (arrival is ambient; `SHIPMENT_LOST` is visible and adds only a
transient pulse). Satellite glyph count and fading come from aggregate engine inventory; glyph
orbits are deterministic presentation and do not claim individual missions or mutable health.
Client-side asset god actions and negative-id asset headlines are disabled for authoritative
objects. The old cosmetic generator survives only behind explicit `?engine=mock`.

**Remaining projection boundary:** stylized territory outlines and satellite orbital paths are
visual. Their territory centers/owners and satellite count/condition are authoritative, as are
country positions, regions, endowments, commodities, infrastructure, blocs, lanes, shipments,
strikes, conditions, and magnitudes.

## M9 — Spatial foundation (v2 spec §1–2)

`meddler/engine/space.py` is the
first v2-track milestone to land. It is a new **leaf** module — imports `config` and `rng` only,
never `model` — enforced by a new import-linter contract (`pyproject.toml`, `"space is a leaf"`,
`type = "forbidden"`) rather than left to discipline alone: `model` imports `Position` from
`space` to type `Country.position`, so the reverse edge would silently create a cycle the moment
anyone reached for a `Country` from inside `space.py`.

- **Lat/lon storage over a raw (x, y, z) vector.** `Position(lat, lon)` stores degrees, not the
  unit vector every geometry function actually computes with internally (`unit_vector()` derives
  it on demand from lat/lon). The frontend needs to place a blob on a map and a human reading a
  dossier needs a legible number — "37.2, -122.1" reads, "(0.61, -0.79, 0.04)" doesn't. (x, y, z)
  is the natural *computation* basis (it's what `central_angle`, rotation, and cap-sampling all
  operate on), but a poor storage/display one, so the struct stores the human-facing form and
  derives the computational form on demand, not the reverse.

- **Fibonacci-sphere region centers over a platonic-solid lookup table.** `space.region_centers
  (count)` generates `count` near-evenly-spaced points via the golden-angle spiral, generic in
  `count`. A hand-written table of platonic-solid vertices (tetrahedron=4, octahedron=6, ...)
  would need a bespoke case per region count and has no natural entries between them; the
  Fibonacci lattice stays near-optimally spaced for any count with one formula. The per-seed
  rotation (`rotated_region_centers`) is layered on as a separate, later step specifically so
  `region_centers` itself stays a pure, cacheable function of `count` alone.

- **Disjoint caps + a direct clearance check as the no-overlap guarantee — and the direct check
  is the one actually doing the work.** Countries are rejection-sampled inside per-region
  spherical caps (`REGION_CAP_FACTOR=0.40` leaves a 20% no-man's-land between regions), but the
  real invariant — no two countries closer than `min_country_separation`, checked against *every*
  previously placed country, cross-region ones included — is enforced directly in
  `generate_layout`, not by the cap geometry. This is easy to get backwards: it's tempting to
  argue "regions don't overlap, and each region only places its own countries inside its own cap,
  so cross-region overlap is geometrically impossible" — but the cap margin (`gap = 0.20*S`,
  where `S` is the minimum region-center separation) only exceeds the required separation
  (`min_country_separation = 0.20*S/sqrt(per_region)`) when `per_region > 1`. At `per_region == 1`
  (i.e. `country_count <= region_count`) the two are exactly equal, and that configuration
  (`starting_country_count=2` at `region_count=4`, and similar) runs constantly throughout the
  existing test suite. Behavior is correct there anyway, because of the direct clearance check,
  not because of the cap margin — `tests/unit/test_space.py::
  test_cross_region_gap_is_never_tighter_than_the_required_country_separation` pins this boundary
  explicitly rather than letting it be silently assumed.

- **`1/sqrt(countries_per_region)` separation scaling.** `min_country_separation` shrinks by
  `1/sqrt(per_region)` as a region gets more crowded, holding the packing fraction inside a cap
  constant (~6.5%, measured) regardless of how many countries land in one region — a fixed
  absolute separation would instead make rejection sampling jam as `country_count` grows.
  Measured across ~17,000 generated worlds (region counts 2–6, country counts 2–20): 0
  fallbacks, never more than 7 of 64 sampling attempts. The invariant was mutation-tested, not
  just asserted: deliberately removing the clearance check made 50 tests fail, confirming the
  no-overlap tests exercise real behavior rather than being tautological.

- **Round-robin endowment specialties over independent random draws.** `_assign_specialties`
  shuffles the 3 extractive commodities and cycles them round-robin across countries, rather than
  drawing each country's specialty independently. Independent draws can leave a commodity with
  zero specialists purely by chance — "every commodity has at least one supplier" is meant to be
  a worldgen *invariant*, not a probability, since a commodity nobody is good at is a market that
  can never clear. This guarantee holds only for `starting_country_count >= 3` (there are 3
  extractive commodities; below that, more commodities than countries exist and somebody must go
  unsupplied) — not a live problem today since nothing reads `Country.endowments` until M10, but
  M10 must not inherit the guarantee as unconditional.

  **Related, and more consequential: v2 spec §14's seeding invariant — "aggregate supply must
  roughly meet aggregate demand per commodity at worldgen" — is NOT satisfied by M9.** There is
  no demand model until M10, so M9 cannot assert the real §14 invariant; "every commodity has a
  specialist" is a strictly weaker substitute that says nothing about aggregate supply vs.
  demand. `ENDOWMENT_BASE_RANGE`/`ENDOWMENT_SPECIALIST_RANGE` (`config.py`) are the knobs M10
  must calibrate against its own demand figures — **M10's plan owns the real §14 assertion.**
  (Same note, in context, in `docs/progress.md`'s M9 entry.)

- **Earth-like `WORLD_RADIUS_KM = 6371.0` on an explicitly non-Earth world.** The world is an
  abstract unit sphere with no geography — the radius exists purely so `distance_km()` reads
  legibly in the UI ("1,530 km" beats "0.24 radians"); no Earth continent/city layout is implied
  by the number, and the value itself is otherwise arbitrary.

- **M9 is the first milestone whose determinism depends on the platform's libm.** Every worldgen
  calculation before M9 was IEEE-exact — `Rng.uniform` is `a + (b-a)*random()`, and everything
  downstream only ever adds or multiplies. M9 introduces `cos`/`sin`/`atan2`/`asin` into the
  genesis path (region rotation, cap sampling, position round-tripping). `sqrt` is IEEE-754
  correctly-rounded and therefore safe; the trig functions are not — they're libm-dependent, and
  different platforms' libm implementations can disagree in the last bit or two. The golden
  master was generated on a Linux development machine (Python 3.14) and is byte-compared in CI on
  `ubuntu-latest` under several Python 3 versions. The practical risk is negligible — a 1-ULP difference only changes the actual
  world if it lands exactly on a `clearance >= required` sampling boundary — but "same seed ⇒
  byte-identical" now carries an unstated platform caveat that a future cross-platform golden
  diff should be triaged against first, before assuming a real regression. If it ever does bite,
  the fix is to quantise positions (round lat/lon to a fixed number of decimals) before storing
  them.

- **A correction worth recording as a trap for future milestones: M9's new worldgen RNG draws do
  NOT shift the tick stream.** It's tempting to assume they would — `generate_layout`'s rejection
  sampling draws a variable number of randoms depending on how many attempts it takes, and
  worldgen now draws far more randoms overall (rotation + positions + specialties + endowments)
  than before M9. But `generate_world` creates `rng = Rng(seed)` as a **local variable and
  discards it**; `cli._run_headless` then builds a **fresh** `Rng(seed)` for the tick loop,
  restarting the draw count at zero. Worldgen's draw count is therefore invisible downstream by
  construction, and the golden master changed for M9 not because "the RNG order shifted" but
  because **world state** changed — endowment draws land mid-stream in `_generate_country`,
  re-rolling every country's population/GDP/stability/leader, which changes which gated events
  fire later. This safety is load-bearing but currently accidental: nothing in the code enforces
  that `generate_world` discards its `Rng` rather than returning, stashing, or resuming it — doing
  so would silently couple worldgen's variable draw count to the tick stream and break fork/
  replay. Anyone debugging a future "the tick RNG order shifted" theory should rule this out
  first.

**Verified:** full suite 377 passed (baseline before M9: 189); `ruff check .` clean repo-wide;
`mypy meddler/` clean (52 source files); `lint-imports` 2 contracts kept, 0 broken.
`test_every_non_low_frequency_kind_fires_at_least_once` (20 seeds x 5000 ticks, normally
deselected for speed) was run in full and passed — M9's genesis re-roll did not push any event
kind below reachability. Golden master regenerated (413 lines, was 423) after confirming
determinism (two in-process runs byte-identical) and eyeballing the new output (coherent
headlines, no `None`/`null`/unrendered placeholders).

## M10 — Commodities & production (v2 spec §3, §14)

Six commodity buckets and a production DAG replace the single grain scalar. Design decisions
that were non-obvious enough to have burned a naive implementation (several were caught only by
adversarial review of the plan, not during coding):

- **`grain_*` becomes a @property alias, not a renamed field.** `Country.commodity_output/need/
  stock` are the real storage (dicts keyed by `config.COMMODITY_ORDER`); `grain_output/need/stock`
  are read/write properties over the `food` bucket. The write side is mandatory, not cosmetic:
  `stats.apply_country_stat` does `setattr(c, stat, getattr(c, stat) + delta)`, so a read-only
  property would silently break every food stat_delta (DROUGHT, famine relief, god edits). Verified
  the alias survives dataclass machinery — properties in the class body are not fields, so
  `__init__`/`__repr__`/`__eq__`/`deepcopy`/mypy --strict all behave.

- **`genesis_output` lives in the leaf `commodities.py`, not worldgen.** Both `worldgen` and
  `systems/commodity_production` need it; putting it in the leaf (imports only config) lets both
  import it at module scope with no cycle. An earlier draft had worldgen own it and the system
  reach in via a function-local import — a smell that signaled the wrong home.

- **Extractive output is STATIC after worldgen; produced output is recomputed each tick.**
  Extractive (food/energy/raw_materials) = population × per-capita × endowment, fixed at genesis
  and moved ONLY by shocks (DROUGHT dents food permanently). If it were recomputed each tick, a
  shock's `-Δ` would be erased before any system read it (the recomputed-field trap). Produced
  (manufactured/consumer/high_tech) derives from gdp_tick/innovation_mult/education per §3's table,
  which is what makes output vary country-to-country — an earlier draft used population × constant,
  giving ZERO variance and making CONSUMER_SHORTAGE/TECH_STAGNATION provably unreachable (0/240
  country-runs). The corrected model runs real deficits in ~40% of countries.

- **The production chain debits real stock (flow vs flow).** Input requirement is
  `target_output × per_unit` compared against `output + stock`; the scarcest input binds via min,
  and stock is actually debited via `apply_commodity_stat`. An earlier draft compared a flow+30-tick-
  stock against a bare flow, so availability measured 1.0 everywhere and nothing was ever consumed —
  the chain was decoration.

- **food's stock is untouched by `commodity_production`.** `systems/trade.py` still clears the food
  gap in cash every tick; draining food stock here too would double-settle (countries pay for grain
  that never arrives AND starve) and convert `grain_stock` from a shock buffer into a running balance,
  which trade.py's docstring forbids. **M12 owns unifying this** food/non-food asymmetry.

- **§14's supply/demand invariant is a FLOOR, not the spec's exact band.** The band was
  mathematically unsatisfiable: it nailed the mean but ignored that population spans 2–60M, so the
  pop-weighted endowment mean swings; the ratio is linear in the tuning constant, so no value fit
  (measured spread 1.6–1.8× vs a 1.324× band). Replaced with `AGGREGATE_BALANCE_MIN = 1.05`,
  calibrated against the worst of 30 seeds. The "nobody self-sufficient in everything" clause is a
  WORLD-level claim (§2), not per-country — ~20% of countries are self-sufficient heavyweights, which
  is intended, not a bug; the test asserts world-level interdependence (measured 80%, floor 60%).

- **`COMMODITY_ORDER` is load-bearing** — it must be a topological order of the input DAG or
  production reads stale values within a tick. Pinned by `test_commodity_order_is_a_valid_topological_
  order`.

- **Annexation transfers all six stocks via payload-based replay (not `apply_commodity_stat`).**
  `_annex_country` is a structural handler; `cascade.emit_event` freezes `stat_deltas` before
  `structural.run()`, so structural mutations must record their own payload for replay
  (`payload[f"commodity_transferred_{name}"]`) rather than going through the stat_delta path.
  Endowment transfer on conquest was separate and **later shipped in M15** with paired
  live/`REPLAY_EFFECTS` handlers and extractive-output recomputation.

- **Left untouched deliberately: the `cascade.py:100` `hasattr` gate.** A `commodity:`-prefixed
  stat routed through `_apply_spec_effects` would be silently skipped (the field is a dict, not an
  attribute). M10 never routes commodity effects that way, so `_STAT_CLAMP` entries for them would
  be dead code — not added. **Owned by M11/M12** if/when a registered kind needs a spec-effect on a
  commodity bucket.

- **Bridge exposure is dossier-only.** `countryDetail` carries a `commodities` block (six buckets ×
  output/need/stock); it is deliberately NOT in the per-tick frame/snapshot (6× the per-country
  payload for something only the dossier reads). `countryDetail` is request-scoped — the right place.

**Verified:** full suite 446 passed (was 377 after M9); `ruff check .` clean repo-wide; `mypy
meddler/` clean (55 source files); `lint-imports` 2 kept, 0 broken.
`test_every_non_low_frequency_kind_fires_at_least_once` run UNDESELECTED and passed — all five new
shortage kinds fire organically, no `low_frequency_ok` cheat. Golden master regenerated (675 lines,
was 413) after confirming determinism (two in-process runs byte-identical) and eyeballing (coherent
headlines including the new shortage lines, zero unrendered tokens). The 413→675 growth is the
deliberate behavior change of five new firing kinds plus their cascade chains.

## M11 — Diplomacy & alliances (v2 spec §7)

The declarative ALLIANCE/ALLIANCE_BROKEN/EMBARGO kinds become structural state on `World`.
The non-obvious decisions (several caught only during implementation, not planning):

- **The world had no friendship, so alliances could not form.** Formation on "high mutual
  relations" is unreachable when every relation is seeded negative (rivalries) or zero and
  decays toward zero — measured peak relation over 20 seeds × 1000 ticks was −0.1. The fix is
  at the root: `worldgen._seed_relations` now seeds genesis FRIENDSHIPS (`friendly_pairs`,
  `FRIENDLY_RELATION_RANGE`) from the nearest not-already-rival pairs, drawn AFTER rivalries in
  pinned RNG order. This was a gap M9's relation seeding left (it added rivalries but no
  friendships); M11 owns closing it because M11 is the first milestone that reads positive
  relations. This is the single most important M11 decision and my written plan missed it.

- **Shared rival is a probability DRIVER, not a hard gate.** The spec lists "high mutual
  relations + shared rivals + proximity" as formation drivers. Requiring a shared rival as a
  hard AND-gate rejected 70 of 80 eligible friendly pairs (rivalries are sparse: only
  `rival_pairs` seeded). Relation + proximity are hard gates; a shared rival multiplies the
  formation probability (`ALLIANCE_SHARED_RIVAL_BONUS`).

- **Blocs must persist to be "structural."** Mutual defense only matters if the bloc still
  exists when a war lands. Two tunings make blocs durable: intra-bloc warming (0.5/tick) must
  out-run relation decay (~0.35/tick at +70, so a bloc settles near +100), and the strain BASE
  rate is tiny (0.0005/tick — a healthy bloc lives ~2000 ticks). The real break driver is the
  strain `factor`, which multiplies up as a bloc's avg relation sours below the reference. A
  first cut used base 0.01 and warming 0.15, which broke every bloc within ~150 ticks
  (ALLIANCE_BROKEN count equalled ALLIANCE count — nothing persisted). Measured final regime
  (30 seeds): 29/30 form alliances, 23/30 keep a bloc to t1000, 18/30 see a coalition war.

- **`rng.roll(p)` does not clamp** (`random() < p`), so every diplomacy probability passes
  through `_clamp01` — the strain `factor` can exceed 1.0, which would otherwise break a soured
  bloc every single tick. This was caught in plan review, not runtime.

- **Bloc/embargo state is replayed via structural payload, not re-derived.** ALLIANCE,
  ALLIANCE_BROKEN, and EMBARGO each register a structural handler (live mutation of
  `world.blocs`/`embargoes`, recording the resulting bloc id/members/relation onto
  `event.payload`) AND an RNG-free replay handler that reconstructs from that payload — the
  WAR_DECLARED precedent. `Timeline.world_at` calls `structural.replay` per event, so a
  scrubbed/forked world shows the exact alliances the live tick produced. Relation propagation
  needs no structural handler: it rides one ambient `BLOC_COHESION` event per bloc whose
  `stat_deltas` are replayed like any other. `test_world_at_reconstructs_bloc_and_embargo_
  state_exactly` (tests/unit/test_scrub.py) is the proof; a divergence there means scrubbing
  silently lies about the diplomatic state.

- **Propagation is one invisible event per bloc, for pacing.** Warming every intra-bloc pair
  and cooling toward every bloc-mate's enemy could be O(pairs) events per tick. Instead it is
  ONE `BLOC_COHESION` event per bloc carrying all of that bloc's relation deltas, bounding
  propagation to O(blocs). `BLOC_COHESION` is in `AMBIENT_KINDS` (never renders), like
  RELATION_DECAY. The world should not be noisy.

- **A country is in at most one bloc; merging is deferred.** Formation only ever seeds a fresh
  pair or adds a bloc-less country to the other's bloc. Two established blocs never combine
  (would complicate the structural/replay handlers with a dissolve-and-merge). Coalition wars
  still work: a whole bloc joins via mutual defense.

- **EMBARGO records the lane only.** The structural handler appends the sorted pair to
  `world.embargoes` for M12 to consult. Actual trade-lane blocking and intra-bloc discounts are
  M12's — current `trade.py` is an abstract world-market with no bilateral lanes. EMBARGO's
  existing TRADE_HALT/CURRENCY_SLIDE consequences already give the target a bite, so no extra
  stability constant was added.

**Verified:** full suite 465 passed (was 446 after M10); `ruff check .` clean repo-wide; `mypy
meddler/` clean (56 source files); `lint-imports` 2 kept, 0 broken.
`test_every_non_low_frequency_kind_fires_at_least_once` run UNDESELECTED and passed; ALLIANCE and
ALLIANCE_BROKEN now fire organically (19/20 seeds × 5000 ticks) so their `low_frequency_ok` flags
were removed — an honest strengthening. Golden regenerated (675 → 525 lines) after confirming
determinism (two in-process runs byte-identical) and eyeballing (coherent diplomatic headlines,
BLOC_COHESION correctly invisible, zero unrendered tokens). The 675→525 change is a different valid
trajectory: friendly seeding + the diplomacy system reshape the RNG stream from tick 1, and a world
with genesis friendships fights less.


## M12 — Bilateral trade & pricing (v2 spec §4)

The abstract single-commodity world market (grain minted/burned against a reference price) is
replaced by per-commodity bilateral matching between countries with a real surplus and a real
deficit. The decisions that shaped it:

- **The stock asymmetry is closed by MOVING the settlement, not adding a special case.** M10 left
  food a pure shock-buffer (trade.py settled the food gap in cash) while non-food folded its flow
  balance into stock inside `commodity_production`. M12 deletes that fold from
  `commodity_production` entirely and does ONE settlement, in `trade`, for all six commodities:
  `new_stock = clamp(stock + output + imported − exported − need, 0, ceiling)`. Food now flows
  through inventory exactly like energy, so a food deficit trade cannot clear drains `grain_stock`
  and drives FAMINE through the same mechanism as an energy shortage. `commodity_production` keeps
  only output + input consumption — it makes goods; `trade` distributes them and banks the rest.

- **Money is conserved through the fx desk, with NO mint/burn.** Each currency is a country's own
  code (the pre-M10 ledger convention). A fill of `value_veri = price × qty` becomes two
  single-currency `transfer` legs: `importer.treasury → fx:<IMP>` (importer currency) and
  `fx:<EXP> → exporter.treasury` (exporter currency). Per-currency conservation holds by
  construction — Invariant A never sees a cross-currency amount — and the `fx:` pools carry the net
  FX position. This is the first real use of `world.fx_pools` (unused since it was added). The old
  world-market minted export proceeds / burned import cost; removing that is a real behavior change
  that also relieves trade-driven inflation pressure. `test_full_pipeline_conserves_money_...` was
  corrected (not weakened) to count `fx:<code>` balances as part of currency `<code>`'s supply —
  otherwise a conserved transfer into the desk reads as an unbacked leak. It is a stronger check
  now: it also proves the fx desk is conserved.

- **No new structural/replay handlers.** TRADE carries its `LedgerEntry` legs (replayed by
  `ledger.apply_to_world`); TRADE_SETTLE carries `commodity:` `StatDelta`s (replayed by
  `stats.replay_stat_delta`). Both ride the generic replay paths `Timeline.world_at` already runs,
  so pools, stock and fx_pools reconstruct exactly with zero bespoke machinery.
  `test_world_at_reconstructs_trade_pools_stock_and_fx_exactly` (test_scrub.py) is the proof — a
  money leak or stock divergence would fail it.

- **Emergent scarcity comes from thin margins, not an affordability cap.** §14 guarantees only
  ~5–14% global surplus per commodity. Greedy priority allocation (allies → friends → neutrals as a
  discrete tier ahead of price, so a distant ally beats a cheap stranger; then cheapest landed cost)
  genuinely exhausts surpluses before every importer is filled, leaving tail importers short. That
  thinness — plus lanes blocked by war / embargo / declared enmity (`relation ≤
  RELATION_TRADE_BLOCK_THRESHOLD`) — is what keeps shortages and famine reachable. Money is
  unbounded this milestone (a poor country imports into debt, which is what DEBT_CRISIS models); a
  fiscal import cap is a later lever, documented not hidden.

- **Pricing reads distance/relation/alliance/tariff/scarcity.** `base[commodity] × (1 +
  distance_cost) × relation_mod × scarcity_mult × (1 + tariff)`, with an intra-bloc tariff waiver +
  extra discount (spec §7 gives blocs economic teeth). Seeded rivalries (−10..−30) sit ABOVE the
  block threshold, so a fresh rivalry still trades — dearer via `relation_mod` — while only
  event-driven enmity (WAR_DECLARED sets −90) actually closes the lane.

- **Trade stays RNG-free**, so it perturbs the roll stream only through the state it changes — the
  golden's shift is entirely downstream (post-trade stock drives which FAMINE/shortage rolls fire),
  not a reseeding.

**Scope walls / carried forward:** (a) A matched trade settles INSTANTLY this milestone; M13 inserts
the in-flight `Shipment` (carrier, transit time, interdiction, relief airlift) between match and
settlement. (b) Everything is reachable-unless-blocked; distance affects PRICE only —
satellite-gated trade REACH and fleet-condition throughput are M14. (c) Sub-unit rounding can move a
negligible quantity of goods with a zero-rounded price; immaterial at real deficit magnitudes.

**Measured pacing (12 seeds × 1000 ticks, 8 countries):** ~21 matched fills/tick (an active market,
every seed trades); FAMINE ~3.8/seed and FAMINE_WARNING ~8.7/seed (an occasional acute crisis — one
famine per ~250 ticks somewhere in the world, rare per country, not relentless — food now fails like
any commodity when the market can't feed a country); produced-commodity shortages now rare
(ENERGY_SHORTAGE 0.3/seed, MATERIALS 3.8, MANUFACTURING 6.1, CONSUMER 2.6, TECH 0.1) because trade
relieves most of them — yet all five remain reachable (coverage test passes undeselected). The base
prices / `TRADE_DISTANCE_COST_COEFF` / `RELATION_TRADE_BLOCK_THRESHOLD` / `TRADE_SCARCITY_EXPONENT`
were set by hand to plausible values and this measurement confirms a healthy regime; they were NOT
swept adversarially — the next lever if famine reads too hot is `COMMODITY_BASE_PRICE_VERI["food"]`
or the block threshold, not the shortage constants (demonstrably reachable).

**Verified:** full suite 475 passed (was 465 after M11); `ruff check .` clean; `mypy meddler/` clean
(56 files); `lint-imports` 2 kept, 0 broken. `test_every_non_low_frequency_kind_fires_at_least_once`
UNDESELECTED and green (bilateral trade did not push any shortage below reachability). Determinism
proven by `test_world_at_reconstructs_trade_pools_stock_and_fx_exactly` and the corrected
per-currency conservation test. Golden regenerated (525 → 528 lines) after confirming determinism
(two in-process runs byte-identical) and eyeballing (coherent trade-deficit/famine/shortage
headlines, TRADE + TRADE_SETTLE correctly invisible, zero unrendered tokens).

## Pacing pass (2026-07-22)

Watching at 4x speed, the world read as a constant barrage: about 141 severity-2 crises per seed
per 1000 ticks, a crisis every seven ticks. The target was roughly 6x calmer crises and much rarer
political churn. Measurement found that most of the flood was a bug, not tuning.

- **Shortage is a stock condition, not a flow condition.** `stability.py` and `inflation.py` both
  tested `grain_output < grain_need`. Since M10 made trade real, that flags every structural food
  importer, including countries fed perfectly well through trade, as "in shortage" every tick. In
  stability it meant a permanent penalty and no recovery (recovery required `not shortage`). In
  inflation it meant a permanent +0.3/tick push against a 0.02 decay, dragging inflation toward
  ~17%. Mean inflation ran 9.1% against a 2% baseline, and 42% of country-ticks sat below the
  UNREST line. Both systems now use the stock test (the FAMINE_WARNING line): mean inflation fell
  to 4.7%, and time below stability 35 fell from 42% to 18%.
- **Recovery applies whenever nothing is actively hurting a country** (no war, no food-stock
  shortage, inflation under the crisis line), not only in the old, nearly unreachable
  `inflation < 4` window. The M7.4 invariant still holds: recovery (0.22) stays below the war
  penalty (0.3), so a war still drains stability for its whole duration.
- **Tuning, after the fixes:** elections every 500 ticks instead of 90; `COUP_BASE_P` 0.01 to
  0.0014; `drama_multiplier` default 1.0 to 0.4 (the settings overlay can still turn it up); wider
  re-arm margins on UNREST, CIVIL_WAR_RISK, INFLATION_CRISIS, and the commodity shortages, so each
  announces once per episode.
- **Reachability is tested at normal drama.** The catalog coverage test runs at
  `drama_multiplier=1.0`; the calm default must not make the catalog look dead.

Result: about 43 crises per seed per 1000 ticks (one every ~23 ticks; rendered headlines 0.63 to
0.16 per tick) with the world still moving (7 of 8 countries active at t1000, wars and coups still
firing). That is about 3.3x, short of the 6x target. The remaining crises are genuine, and pushing
further would mean freezing the world or abandoning the spec's crisis thresholds.

## M13 — Logistics: physical in-flight shipments (v2 spec §5)

A matched trade stops settling instantly. It becomes a `Shipment` on `World.shipments` that
departs, spends real ticks in transit, and then either arrives or is lost. It is the first
multi-tick stateful object mutated mid-tick since occupation, so most of the design effort went
into keeping replay exact.

- **Three lifecycle events, each with a live and a replay handler.** `SHIPMENT_DISPATCHED`
  (ambient) creates the shipment, removes the exporter's stock, and moves the importer's payment
  into its FX desk. `SHIPMENT_ARRIVED` (ambient) credits the importer's stock and pays the
  exporter out of its desk. `SHIPMENT_LOST` is visible, with a headline and a stability hit to the
  destination. Replay rebuilds the shipment from `event.payload` only. Carrier and `arrive_tick`
  are recorded at dispatch and read back, never recomputed, so later tuning of the selection
  logic cannot make a scrubbed world disagree with the live one. `shipment_seq` replays
  monotonically (`max(seq, id + 1)`), the same pattern as `bloc_seq`.
- **Carrier selection is a short, legible rule** (`logistics.select_carrier`). Rail is used when
  both ends share a region and sit within `RAIL_MAX_ANGLE`; it is cheap and cannot be interdicted.
  Air carries high-value goods (`high_tech`) and emergency relief when either end is at war. Sea
  carries everything else: any distance, slow, and exposed.
- **Money is conserved across time, not just within a tick.** Leg one (importer to `fx:<IMP>`)
  posts at dispatch and leg two (`fx:<EXP>` to exporter) at arrival. A lost shipment only ever
  posts leg one, so the importer's payment stays stranded in its desk. That stranded money *is*
  the economic loss, and per-currency conservation still holds at every tick.
- **Interdiction was tuned down hard, and the measurement is why.** The first value
  (`SEA_INTERDICTION_P = 0.04`) is a per-tick, per-shipment roll. Over a roughly 12-tick sea
  transit that compounds to about 46% loss in a war zone, and it drove famine up 14x. Transit
  delay on its own did not: with interdiction switched off, famine held at the M12 level. The
  shipped value is 0.0015.
- **Relief is a subsidised lane, not a gift.** An importer whose stock of a commodity has fallen
  below `RELIEF_CRITICAL_DAYS` of need, buying from a supplier at relation 40 or better, gets a
  50% price discount and an air route if the sea lane runs through a war.
- **Ordering traps, pinned.** Interdiction iterates `sorted(shipments, key=id)`, so the roll
  stream cannot depend on container order. Transit is always at least one tick, so nothing
  arrives on its dispatch tick and gets double-counted by `logistics` running after `trade`.
  (Transit was later raised to a seven-tick floor for observability; see "Minimum transport
  lifetime" below.) The production fold is the only other writer of stock, and it touches
  neither trade leg.

**Verified:** full suite 483 passed; `ruff`/`mypy`/`lint-imports` clean; coverage test green
undeselected; golden regenerated. `test_world_at_reconstructs_in_flight_shipments` and the
`test_scrub.py` fingerprint (extended to include shipments) prove the replay side.
`SHIPMENT_LOST` fires organically: a war sinks a cluster of convoys.

## M14 — Asset coupling (v2 spec §6)

The six `InfrastructureBlock` classes had existed since M2 and had failure *events* since M7.2,
but nothing downstream read `condition`: assets were scenery with a headline attached. M14 gives
each one an economic job. The decisions that shaped it:

- **QUANTITY vs QUALITY is the organising split.** Freight throughput is
  `effective_capacity = count × condition` — more ships really do move more cargo. The production
  multiplier, trade reach and coordination read `condition` ALONE. Scaling those by count would
  make a large country a superlinear producer and would let a nation buy better price discovery by
  launching more satellites, which is not what "condition" means. Every one of these lives in
  `engine/assets.py`, honouring the r4 note's original instruction never to inline
  `count * condition` at a call site.

- **The milestone could not be built without first fixing a fiscal magnitude bug — and that was
  measured before any code was written.** Asset condition sat at mean 0.996 with 98% of
  country-ticks above 0.9, and the §6.7.3 failure thresholds (0.25–0.40) were unreachable in
  practice. `INFRA_BASE_COST_PER_UNIT = 50` minor units against a GDP of tens of millions made
  upkeep 0.001%–0.1% of tax revenue, so `maintenance_ratio` was permanently 1.0 and condition
  climbed to 1.0 and stayed. Coupling anything to a constant is not coupling. This is the same
  magnitude mismatch M7.4 documented behind an unreachable DEBT_CRISIS, and it is why the honest
  options were "ship a latent coupling" or "fix the fiscal side". I chose the fix.

- **Upkeep is anchored to `base_gdp`, not `gdp_tick`.** This is the load-bearing choice. Revenue
  is cyclical (`gdp_tick = base_gdp × (0.5 + stability/200) × innovation_mult`) while an asset
  stock is a sticky physical obligation. Anchoring the bill to *potential* means a country whose
  stability — and therefore output — collapses can no longer cover the upkeep it committed to
  while healthy, and its infrastructure genuinely rots. Anchoring to current output would have
  moved both sides of the ratio together and reproduced the flat-constant behaviour with extra
  steps. `INFRA_UPKEEP_GDP_SHARE = 0.11` is derived from where `INFRA_DEGRADATION_RATE` and
  `INFRA_RECOVERY_RATE` cross over (funding ratio 2/3), not chosen for feel.

- **The grid multiplier is applied by the CALLER, never inside `commodities.genesis_output`.**
  `genesis_output` states potential output and is what M10's §14 `AGGREGATE_BALANCE_MIN` floor was
  calibrated against; folding a genesis-condition multiplier (0.7–0.9) into it would have shifted
  worldgen supply ~20% and silently invalidated that calibration. This mirrors the existing rule
  that input availability is a side-effecting debit the caller applies — genesis output is
  unconstrained, tick-1 output is constrained, deliberately.

- **`energy` is grid-fed (spec §6 lists it) and therefore recomputed every tick, which cost two
  existing tests their example commodity.** M10 made extractive output static precisely so
  DROUGHT's `grain_output` hit would persist; a recomputed bucket erases any shock against it.
  Rather than deviate from the spec or weaken the guard, the two tests that used `energy` as a
  stand-in were retargeted to `raw_materials` (still extractive, still static, still shockable),
  food keeps its own real DROUGHT test, and `energy` gained a categorically stronger protection:
  `test_no_event_kind_shocks_a_grid_fed_commodity_output` walks the whole EVENT_REGISTRY and fails
  the build if any kind ever writes a recomputed commodity's output. A silent future no-op became
  a loud failure.

- **Satellite reach prices, it does not gate.** A hard reach limit is the more dramatic reading of
  "max efficient trade distance", but with only 5–14% global slack per commodity (§14) cutting
  distant lanes outright manufactures famine. The graded beyond-reach premium still produces a
  *physical* reallocation, because candidates sort by price — a blinded importer's distant friend
  loses the lane to a nearer neutral. Honest caveat: measured over four seeds this coupling had no
  organic effect at all, because a healthy satellite reaches ≈ π radians and nothing is ever beyond
  it. It bites only once a satellite is actually degraded.

- **Freight capacity is charged at BOTH ends of a lane.** A shipment needs a fleet to load it and a
  fleet to land it. Charging only the exporter would mean an importer's own NAVAL_LOSS cost it
  nothing — its partners would simply keep delivering — which drains the failure of consequence for
  the country it happened to.

- **Two capacity calibrations were measured and thrown away before the third shipped.** Demand per
  fleet unit is heavily right-skewed (peak ≈ 4–5× mean), so calibrating against the peak makes the
  whole mechanism inert: at "2× peak", an A/B against unlimited freight moved literally zero units
  over 3 seeds × 600 ticks, including a seed with 100 ticks of sub-0.5 fleet condition. The shipped
  constants are 2× the MEAN, which is what the chosen option actually described — typical trade
  clears, peaks and degraded fleets meter.

- **No new structural/replay handlers, and that is a proof rather than a claim.** Freight budgets
  are per-tick locals; every other coupling is a pure function of condition, which already rides
  `infra:` StatDeltas through generic replay. `test_world_at_reconstructs_a_metered_partially_filled_world`
  drives a world where the couplings genuinely bite and fingerprints it live vs `world_at`. It was
  mutation-tested: removing the shipment reconstruction from `_replay_dispatch` makes it fail.
  Worth recording *why* a second mutation (a coupling reading non-replayed state) did NOT fail it —
  `world_at` never re-runs systems, so decision logic cannot diverge; only state mutations can, and
  those are exactly what the fingerprint covers.


## M15 — War & conquest economics (v2 spec §8)

- **War is a system, not another exogenous special case.** `systems/war.py` owns active-front
  strikes and economically motivated declarations in a fixed slot after Diplomacy and before
  Exogenous. The generic exogenous `WAR_DECLARED` root remains for baseline cadence; resource
  scarcity is an additional, inspectable root that chooses the right target before rolling.
  Coalition joining stays in M11's DiplomacySystem. One structural mechanism per concept avoids
  competing alliance reactions and lets a newly joined ally strike in the same tick.

- **Distance reduces both tempo and effect.** A directed front uses
  `factor = 1 / (1 + k × central_angle)` for its roll and for stability/population/infrastructure
  damage. A hard maximum would make distant active wars unable to act; probability alone would
  make each rare far-side hit implausibly strong. The shared continuous factor gives the spec's
  "near enemies hit harder and sooner" behavior without an unreachable front.

- **STRIKE is structural because its deltas are dynamic.** Static `EventSpec.stat_deltas` cannot
  express distance-scaled effects on the secondary target or all nested asset conditions. The
  live handler applies clamped attrition to the primary target and records every actual delta on
  payload; replay adds only those stored values. Event semantics match occupation: primary is the
  affected country, secondary is the attacker, which also lets existing `{country}`/`{foe}` text
  slots render correctly.

- **Resource motivation is dependency, not momentary warehouse level.** Scarcity is the domestic
  extractive flow deficit `(need - output) / need`. Stock is a transient shock buffer and can be
  refilled by imports; flow dependence is what capturing richer territory can structurally fix.
  Candidate score multiplies scarcity, positive endowment advantage, negative-relation hostility
  and proximity; allies/current enemies/inactive or beyond-gate targets are excluded. Selection is
  deterministic (score, target code, commodity), followed by one probability roll, and success
  emits ordinary structural `WAR_DECLARED` under the existing global war cap.

- **Occupation tribute is part of the commodity balance.** Twenty-five percent of an occupied
  country's gross extractive flow is subtracted before its local consumption and credited to the
  active occupier. Commodity production now computes every country's current output first, then
  balances all stocks, so lexical country order cannot change tribute. The mutation remains generic
  `commodity:` StatDeltas; no extra structural surface or money mint is introduced.

- **Demand follows population.** M14 documented that output changed with population while need was
  frozen at genesis. M15 makes recurring casualties ordinary, so preserving that asymmetry would
  manufacture shortages after every strike. Commodity need is now re-derived from live population
  every tick using the worldgen per-capita table. Retired countries have zero need/production and
  are skipped by FX, while occupied countries remain economically active so tribute has a source.

- **Territory ownership is separate from the capital distance anchor.** The design's instruction to
  transfer a singular `position` cannot represent a state that keeps both the winner's and loser's
  land. Moving the winner would also rewrite all trade/war distances as if its capital teleported.
  M15 therefore adds immutable `Territory(position, region)` markers: genesis seeds one; annexation
  moves the loser's complete marker list; `Country.position/region` remain its capital. M16 can
  expose these authoritative markers without inventing geometry client-side.

- **Endowment capture preserves extractive capacity.** Endowments are intensities in `[0,1]`, not
  additive stocks. Adding them would exceed the domain; taking `max` would discard land. The merged
  level is population-weighted, making `merged_population × merged_endowment` equal the sum of both
  pre-capture potentials at that instant. All extractive outputs are then recomputed; produced
  manufacturing capability is deliberately not captured.

- **Half of physical assets survive annexation.** M14 left its asset-stock/upkeep ratio as the hook
  for capture/destruction. M15 deterministically transfers `floor(loser_count × 0.5)` per class,
  destroys the remainder, and combines condition by unit count. Captured grid quality is folded
  before energy output is recomputed. Exact counts/conditions, endowments, needs, outputs,
  territories and stocks are payload-recorded by ANNEXATION's paired live/replay handlers.

- **M16 remains a hard boundary.** No bridge or browser payload changes land here. STRIKE,
  territory, endowment, asset and shipment visualization—and retiring client-fabricated globe
  surfaces—remain contract-v2/frontend work. The separate zero-rounded trade-money calibration
  issue was deliberately not hidden inside war tuning; it was fixed on its own afterwards (next
  section).


## Real trade settlement, tariffs, and causal impact inspection (2026-07-24)

- **Trade settlement is economically material and sector-funded.** `TRADE_VALUE_MINOR_SCALE =
  4_000_000` converts the simulation's veri-denominated border price into integer minor units
  before rounding. Food and consumer imports debit households; energy, raw materials,
  manufactured goods, and high tech debit corporates. Untaxed value moves into the importer's FX
  desk, while any tariff duty moves separately from the same buyer pool into the importing
  treasury. On arrival, only the pre-tariff export proceeds move from the exporter's FX desk into
  exporter corporates. Governments neither buy ordinary imports nor receive export proceeds, and
  tariff markup can never leak to the exporter. Every leg remains a same-currency transfer, so
  per-currency conservation—including FX desks—still holds.
- **Tariffs are replayable policy state, not a price alias.** `World.tariffs` stores immutable
  importer/exporter/commodity/rate/source records. Keys may use `*`; lookup precedence is bilateral
  commodity, bilateral blanket, importer-global commodity, importer-global blanket. Intra-bloc
  trade is exempt. A fixed system slot after diplomacy and before war performs repeal, retaliation,
  then new hostile policy in sorted order; trade has already run, so policy changes apply next
  tick. Imposition/repeal have paired structural replay handlers and retaliation links to the
  policy event that triggered it.
- **Causality is a DAG with compatibility retained.** `Event.parent_id` remains the legacy direct
  consequence edge. Additional `CauseLink`s are typed `trigger`, `contributor`, or `context` and
  must point to earlier timeline-local events. `Event.parent_ids` is the sorted, deduplicated
  canonical traversal surface. Threshold crises scan a bounded recent window of normalized
  effects for state contributors. Cause count and cause role are observational only: event
  severity remains intrinsic to the resulting event kind.
- **Effects are normalized observations over existing replay mutations.** Every StatDelta and
  LedgerEntry automatically creates an `Effect`; stat helpers retain exact before/after values.
  Structural handlers append realized structural effects for tariffs, shipment lifecycle, strikes,
  war/peace, alliances, embargoes, and annexation. Replay still applies only authoritative ledger,
  stat-delta, and payload-recorded structural state; before/after/effect rows are inspection data
  and consume no RNG.
- **Impact aggregation deduplicates by event id.** `engine/impact.py` computes ancestors,
  descendants, components, immediate effects, downstream effects, and cumulative numeric totals
  over all causal edges. Shared descendants reached through converging causes are counted once.
  Optional horizons are tick cutoffs relative to the selected event. Legacy single-parent traces
  preserve deterministic DFS ordering; DAG traces expose all parents and render shared nodes once.
- **Counterfactual language requires a genuine baseline.** `eventImpact.horizonDiff` is available
  only for a fork with both fork and prime reconstructed at the same requested tick. It is labeled
  as the whole-world fork-minus-prime difference, not as suppression of one organic event. Prime
  events, missing horizons, pre-fork endpoints, and horizons beyond aligned history return an
  explicit unavailable reason. The engine does not fake an organic-event counterfactual from its
  recorded descendants.


## Minimum transport lifetime (2026-07-25)

Shipment duration has a seven-tick floor for every carrier. One-tick short routes made physical
logistics and authoritative progress operationally invisible: dispatch and arrival appeared in
adjacent frames, leaving no useful period for route inspection, interdiction exposure, or mover
progress. The floor is applied once in `_transit_ticks`, before `arrive_tick` is recorded in the
dispatch event, so live play and replay remain identical. It is a minimum rather than a fixed
duration: distance, carrier speed, and communications friction can still produce longer journeys.


## Shipment roster rows are identities, not sorted observations (2026-07-25)

A shipment keeps one dispatch-ordered row while active; progress and remaining distance only update
that row. Lost shipments keep the same row through their eight-tick terminal hold. Arrival is the
single deliberate exception: at 100% the arrived row moves to a bottom partition for four ticks,
then expires. No other lifecycle state may repartition the roster. Route rosters and global Trade
Operations share this invariant.


## Historical chart hover reports intervals, not invented point causes (2026-07-25)

Country charts use exact downsampled statistic ticks. A hovered point reports the value at that tick
and authoritative country-involving events in `(previous sampled tick, hovered tick]`, because a
visible change can accumulate between samples. Event-free intervals explicitly say that ordinary
system drift may be responsible; the UI never assigns a headline as a cause from visual proximity
alone. PRIME can scrub to the tick and open the strongest recorded event; fork charts permit event
inspection but do not masquerade PRIME `worldAt` as fork history.


## History is stored once in SQLite; snapshots carry boundaries, not logs (2026-07-28)

A measured seed-1337 run reached 460.5 MiB RSS at tick 300: `copy.deepcopy(World)` copied the
cumulative `EventLog` into every 50-tick snapshot, retaining 119,615 copied event entries in
snapshots alongside 34,189 live events. Suppressing additional snapshots reduced the same run to
100.3 MiB, proving the ownership problem before implementation.

`EventLog` now preserves its existing sequential/list-like engine API over a file-backed stdlib
SQLite store. A bounded 512-event LRU hydrates hot rows; finalized events are serialized once per
operation after structural handlers finish mutating payload/effects. Normalized causal,
country/effect, and ledger indexes serve trace, chart, attribution, and same-tick system queries
without hydrating unrelated history. Exact RNG states and country-stat observations use the same
branch store with bounded caches. JSON was chosen over pickle or a new codec dependency because all
recorded values are already JSON-native and Python float encoding round-trips binary doubles exactly;
variable JSON blobs use stdlib zlib only when compression reduces their stored size.

`EventLog.__deepcopy__` creates a read-only branch boundary sharing the store, so ordinary World
deepcopy still snapshots all mutable state without copying historical events. Forks create writable
suffixes sharing immutable prefixes; restart truncates only the writable suffix; drop/adopt reclaim
private and inaccessible rows. Session runtime shutdown explicitly closes and removes the ephemeral database. Browser disconnects
leave it intact, so reload/reconnect can recover the same world; persistence across application
process restarts remains a separate save/load product decision.

Country dossiers no longer transfer lifetime event history. Hover requests one exact sampled interval;
the server reports its exact event count and at most six strongest authoritative summaries. The client
keeps 24 interval responses, 1,000 summaries per timeline, and 2,000 ribbon markers. This bounds browser
retention without weakening event inspection or claiming temporal proximity proves causation.


## Residual freight capacity prevents an absorbing no-trade world (2026-07-28)

Read-only inspection of a real tick-7069 history found 19,851 dispatches and 19,851 terminal
shipments, no active shipments, the last dispatch at tick 1222, and the last terminal at tick 1237.
The world had not merely stopped drawing movers: most freight fleet conditions had reached exactly
zero. At zero stability, full-funded recovery (`+0.005`) exactly cancels instability degradation
(`-0.005`). Because freight throughput was `count × condition`, zero throughput caused shortages,
shortages held stability at zero, and infrastructure could never escape—a genuine absorbing state.

The repair is deliberately narrower than a global infrastructure retune. Freight loading/landing
uses `count × max(condition, 0.05)` when count is nonzero; literal count zero still provides no
capacity. `effective_capacity()` and condition-based grid, satellite, and communications quality
remain unchanged. Thus collapsed fleets move only a skeletal 5% flow, preserving severe crisis while
leaving a deterministic recovery path. Seed 1337 at tick 1600 retained 196 active shipments and
5,335 dispatches in the final 250 ticks; maximum UI drama (`drama_multiplier=3.0`) retained 23 active
shipments and 584 final-window dispatches despite five zero-condition fleets by tick 1000.


## Correlated asynchronous UI without forced layout reads (2026-07-28)

Every user-triggered bridge command carries an opaque request ID through one dispatcher. The client
uses it for a persistent activity indicator, contextual trace/dossier/fork loading states, duplicate
suppression, control restoration, and visible correlated errors. Trace requests keep the Annals view
from replacing their pending overlay. Scrubbing coalesces pointer input to one animation frame and
permits one `worldAt` reconstruction in flight, retaining only the newest queued tick.

Feed insertion now batches through a `DocumentFragment`; ribbon and globe dimensions are cached by
`ResizeObserver`; drag geometry is measured at pointer-down; and animation class restarts are split
across animation frames. The hot paths therefore do not read `offsetWidth`, `offsetHeight`,
`clientWidth`, or `clientHeight` immediately after writes.
