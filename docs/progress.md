# Engineering log

A dated lab notebook kept during the build, milestone by milestone. It is dense and unedited
for narrative: measurements as they were taken, test counts at the time, and the occasional
retraction when a later measurement proved an earlier claim wrong (those are left in, marked).
For the readable version, see [`devlog.md`](devlog.md). For the reasoning behind each decision,
see [`design-decisions.md`](design-decisions.md).

Status legend: `[x]` done and verified (tests green), `[~]` in progress, `[ ]` not started.

## Known gaps carried forward

Open as of the last entry in this log. Items that were closed later are marked as such.

- **Several interventions are still declarative only.** `INTERVENE_WAR`, `INTERVENE_PEACE`,
  `INTERVENE_ALLIANCE`, `INTERVENE_EMBARGO` and `INTERVENE_SECEDE` apply their stat effects and
  cascade normally, but do not change `at_war_with`, blocs, embargoes, or the roster. The organic
  kinds became structural in M7.3 (war/peace) and M11 (alliance/embargo); the intervention path
  was never wired to the same handlers. The intended fix is unchanged: route both paths through
  one `engine/structural.py` handler per concept, not a bolt-on in god mode.
- **Secession never creates a country** (structural country creation, §5.1's dynamic roster).
  `SECESSION` and `INTERVENE_SECEDE` fire and cascade, but the roster only ever shrinks, through
  annexation. `countryAdded` has a real message shape and is unreachable for the same reason.
  `god.god_relation`'s auto-end-war-on-raised-relation is live now that wars are structural.
- **A fork's snapshots don't inherit prime's pre-fork snapshots.** A fork starts with a single
  snapshot at its own branch tick. After `adoptFork` promotes it to prime, `restart`/`world_at`
  to any tick before that branch point raises `"no snapshot at or before tick T"`: the new
  prime's scrub-back history is truncated at the tick it branched from. Found while writing
  `tests/bridge/test_handshake.py` (see its comment at the `restart` call). Fix: seed a new
  fork's snapshots from prime's snapshots at or before the fork tick (cheap now that snapshots
  carry SQLite log boundaries instead of copied logs), with its own test.
- **`annals()`'s war/records derivation is a best-effort heuristic** (PEACE is targets=1, not a
  pair, so war-end matching is "first PEACE fired by either belligerent").
- **The M7.4 annexation shortfall was never re-measured at the 30-seed bar.** At 5 seeds,
  annexation sat at 20% of runs against a 40% target. The evidence-backed lever is moving
  `_declare_war`'s guard up to the eligibility sites (see the M7.4 section), not
  `ANNEXATION_BASE_P`. The world has changed a great deal since (v2 track, pacing pass), so the
  number needs measuring again before anyone acts on it.
- **Closed:** the `world_at` structural-replay gap (fixed in M4.2 with the `REPLAY_EFFECTS`
  registry, tested in `tests/unit/test_scrub.py`); the missing per-stat history behind
  `snapshot.spark`/`countryDetail.series` (built in M8.2a); the M14 "trade money rounds to zero"
  finding (fixed by the post-M15 settlement work); M14's frozen-demand gap (fixed in M15).
- **Event ids are unique per timeline, not across A/B.** Every message names its timeline, and
  the contract says to key on `(timeline, id)`. Documented, not a bug.

## Milestones

- [x] M0 — Scaffolding
- [x] M1 — Deterministic core
- [x] M2 — Systems: economy, infrastructure, relations
- [x] M3.1 — Registry types + exogenous/consequence systems + core catalog
- [x] M3.2 — PoliticsSystem (elections, coups, leader changes, conquest rolls)
- [x] M3.3 — Headlines, names, golden master, headless CLI
- [x] M4.1 — Trace (`engine/trace.py`)
- [x] M4.2 — Scrubbing, restart, annals query (+ fixed the world_at structural-replay gap
      flagged during M3.2 — see "Known gaps" above, now resolved)
- [x] M5.1 — God mode
- [x] M5.2 — Fork + compare
- [x] M6 — Web bridge (server/adapter/commands)
- [x] M7 — Deep simulation (full catalog):
      - [x] M7.1 — populated engine/kinds/ with every §6.6 kind (114 total registered,
            up from 50) + ≥4 templates each (text/headlines.py); restored every edge
            M3.1 had trimmed; added "worst_relation" ConsequenceRule target (WAR_SPARK)
            and the "infra_all" stat_deltas key (cascade.py) so INFRASTRUCTURE_DAMAGE/
            RESTORED have real per-class effects. Golden master regenerated + eyeballed
            (expected per the landmine). Fixed a grammar bug found along the way:
            text/names.py's REGIONS mixed singular/plural place names, breaking
            subject-verb agreement in several templates (including one pre-existing
            since M3.3) -- made the list uniformly singular instead of patching each
            template by hand.
      - [x] M7.2 — engine/assets.py (FAILURE_THRESHOLDS/effective_capacity helpers) +
            wired InfrastructureSystem's failure-threshold rolls (§6.7.3, hysteresis via
            Country.armed, same pattern as systems/thresholds.py). Verified with a
            scripted zero-treasury/zero-stability world producing SATELLITE_FAILURE and
            its full chain, traced end to end (tests/unit/test_infrastructure_failures.py).
      - [x] M7.3 — conquest/absorption completion: WAR_DECLARED now structurally
            populates at_war_with + sets relation -90 (§5.2) via a new structural.py
            handler -- this is what finally makes OCCUPATION_BEGIN's own gate
            organically reachable (it needed at war + relation < -80, and nothing
            produced either before). PEACE structurally clears at_war_with generically
            (god.god_peace's own duplicate copy of this logic was removed in favor of
            it). PoliticsSystem now rolls ANNEXATION (90+ ticks occupied, low
            resistance) and LIBERATION_WAR (occupied country's stability recovers);
            both have forward + REPLAY_EFFECTS structural handlers (population/grain
            merge into the annexer, status transitions). bridge/adapter.py gained
            `active_countries()` filtering out ANNEXED/DISSOLVED from every frame/
            hello/countryDetail country list (contract §6/§9). Golden master
            regenerated again (this milestone changes simulation behavior further).
      - [x] M7.4 — tuning pass (iterate ~5 seeds; 30-seed validation NOT run, see
            below) + final golden regen.
            First step (closing out M7.1's own acceptance test, which had never
            actually been run to completion against final code) surfaced two real
            bugs beyond tuning, both fixed:
            1. `test_catalog_coverage.py` counted `INTERVENE_*` kinds as required —
               they're `is_intervention=True` and can only ever fire via god mode, so
               a headless run could never satisfy it. Excluded by that flag instead of
               `low_frequency_ok` (categorical, not a rarity call).
            2. `systems/thresholds.py` fired INFLATION_CRISIS/UNREST/CIVIL_WAR_RISK/
               FAMINE_WARNING/FAMINE/DEBT_CRISIS via a bare `world.log.append()`
               instead of `cascade.emit_event()` — so none of their EventSpec
               `stat_deltas` were ever applied and none of their registered
               `consequences` ever scheduled. Harmless pre-M7.1 (nothing was
               downstream); became a real gap once M7.1 wired REVOLUTION/
               GOVT_TYPE_CHANGE/SECESSION/MINT/CREDIT_FREEZE as their children.
               Rerouted through `cascade.emit_event` (a review pass confirmed no
               double-counting against `StabilitySystem`'s separate ambient penalty).
            Also: 5 true orphans (no exogenous roll, no consequence parent at all —
            BRAIN_DRAIN, CIVIL_RIGHTS_REFORM, EDUCATION_REFORM, EPIDEMIC_FEAR,
            PROPAGANDA_CAMPAIGN) made small-p exogenous roots, same footing as
            SPORTS_VICTORY/SCANDAL. EMBARGO and INFRASTRUCTURE_RESTORED (both
            reachable only through a not-yet-built intervention structural handler,
            already documented as deferred) marked `low_frequency_ok` — categorical,
            not statistical. Added an organic PEACE trigger (`systems/politics.py`
            run() section 5, `config.PEACE_STABILITY_THRESHOLD`/`PEACE_BASE_P`):
            closes a TODO(M7.3) left in `kinds/diplomatic.py` — wars started
            organically since M7.3 but could previously only end via `god_peace`.
            4-seed diagnostic before these fixes: 17/71 required kinds never fired.
            After: 6/69 (COMPANY_COLLAPSE, COUP_RISK_UP, DEBT_CRISIS,
            GDP_TICK_RESTORATION, MINT, PEACE) — all confirmed to have a working
            organic parent chain (checked each one's registered parent individually),
            just not enough seeds/ticks in a 4-seed sample to hit deep chains or rare
            thresholds (treasury<0, a war outlasting stability recovery). Real 20-seed
            x 5000-tick acceptance run: COUP_RISK_UP resolved (just needed the seed
            count), 5 remained (COMPANY_COLLAPSE, DEBT_CRISIS, GDP_TICK_RESTORATION,
            MINT, PEACE) — 1157s wall-clock, first real evidence this test had ever
            produced. Diagnosed each:
            - **PEACE was a real logic bug in the M7.4 fix above, not tuning.**
              `STABILITY_WAR_PENALTY` (0.3/tick) exceeds `STABILITY_RECOVERY_BONUS`
              (0.1/tick), so stability falls monotonically for a war's *entire*
              duration — confirmed via a 2-seed sample where wars lasted
              4500-4800/5000 ticks. Gating PEACE on "stability > 30 while still at
              war" was very close to unsatisfiable, hence 0/20 seeds. Fixed: dropped
              the stability gate entirely, flat per-tick roll instead (same
              "condition holds -> roll" shape as `OCCUPATION_BASE_P`), config
              `PEACE_BASE_P = 0.003`. Re-verified on 2 fresh seeds: 3 PEACE events in
              one, GDP_TICK_RESTORATION followed in both — both close as a side
              effect of this fix, not touched separately.
            - **DEBT_CRISIS is a genuine economy-scale mismatch, not wiring or luck.**
              Starting treasury is `gdp_tick * STARTING_TREASURY_TICKS` (observed
              1.4B-32B across sampled seeds); the only drain, `TREASURY_DRAIN`, burns
              a flat 500_000/event. Treasury never came within orders of magnitude of
              0 across 20 seeds x 5000 ticks x 4 countries despite 20+ wars firing per
              seed. Marked `low_frequency_ok=True` with that reasoning inline
              (`kinds/economy.py`) rather than forced via a probability hack —
              reaching it for real means rebalancing income/expense magnitudes across
              the whole fiscal system, out of scope for a coverage-test fix. **Flagged
              here as a real M7.4 finding for whoever next tunes the economy.** MINT
              (DEBT_CRISIS's only child) inherits the same flag/reasoning.
            - **COMPANY_COLLAPSE is a genuine chain-depth tail, not unwired.** Its
              parent CREDIT_FREEZE now fires (confirmed in the 20-seed run) via
              PRICE_SPIKE/DROUGHT → CURRENCY_SLIDE → INFLATION_CRISIS →
              CAPITAL_FLIGHT → CREDIT_FREEZE — 5 hops with compounding probabilities
              and 2-60 tick delay windows per hop; the run's own 5000-tick budget
              rarely leaves room for COMPANY_COLLAPSE's own final hop to also land.
              Marked `low_frequency_ok=True` with that reasoning inline.
            `low_frequency_ok` count after all of the above: 28/114 (24.6%), still
            well under the `test_low_frequency_kinds_are_a_real_minority` 40% guard.
            All of `test_thresholds.py`/`test_politics.py`/`test_conquest.py`/
            `test_cascade.py`/`test_determinism.py` (49 tests) re-verified green after
            every change in this milestone. Full 20-seed x 5000-tick coverage test
            (`test_catalog_coverage.py`) reran green: 2 passed in 1216s. M7.1's own
            stated acceptance test — "≥1 of every kind not flagged low_frequency_ok" —
            is now actually satisfied, not just written.

            **Tuning proper (`tuning_check.py`, 8 countries — the product default,
            not the coverage test's 4): the first 5-seed run surfaced a THIRD instance
            of the same deadlock pattern as PEACE, this time fatal to the acceptance
            bar.** 3/5 (then, after the PEACE fix alone, 5/5) sampled runs ended with
            ≤1 ACTIVE country out of 8 — a real violation of "no permanent
            single-country equilibrium." Root cause: `_begin_occupation` never cleared
            the occupied country's `at_war_with`, so `StabilitySystem`'s war penalty
            drained its stability to 0 forever, and since both ANNEXATION (needs
            stability > 15) and LIBERATION_WAR (needs stability > 50) gate on
            recovered stability, occupation became a dead end no country ever left —
            confirmed directly: a single-seed dump showed all 8 countries OCCUPIED,
            every one pinned at stability 0.0. Fixed: `_begin_occupation` (+ its
            `_replay_occupation_begin` mirror) now clears the occupied country's wars
            bidirectionally, same shape as `_make_peace`, payload-recording
            `ended_wars_with` for replay (`systems/politics.py`).

            That fix alone wasn't sufficient — a second diagnostic showed occupied
            countries getting dragged into a *fresh* WAR_DECLARED (via the exogenous
            root, or WAR_SPARK/CEASEFIRE's cascade — neither of which filter target
            eligibility by country status), re-populating `at_war_with` and
            re-deadlocking the same way. Fixed at the single chokepoint that catches
            every source: `_declare_war` (WAR_DECLARED's structural handler, reached
            via `emit_event` regardless of path) now no-ops if either party isn't
            `CountryStatus.ACTIVE`; `_replay_declare_war` mirrors this using the
            absence of the `relation_after` payload key as the replay-time signal that
            the live handler no-op'd (verified LIBERATION_WAR is unaffected — it has
            no structural handler of its own, so it never reaches this guard). Added
            `test_war_declared_no_ops_at_war_with_when_a_party_is_not_active` and
            `test_world_at_reconstructs_a_no_op_war_declared` to `test_conquest.py`
            (12 passed) — the live no-op and its replay counterpart were otherwise
            unexercised by any existing test.

            **Known incompleteness in this guard, left as-is rather than expanded
            (after review):** `_declare_war` guards the *structural* state
            mutation only. WAR_DECLARED's own declarative `stat_deltas`
            (`{"stability": -3.0}`) and consequences (TREASURY_DRAIN both sides,
            REFUGEE_CRISIS) apply in `emit_event` *before* `structural.run()` is
            reached, so a "declared but fizzled" war against an occupied country still
            dents its stability by -3 and still schedules a treasury drain — working
            somewhat against the very recovery this fix chain was meant to enable.
            Moving the guard up to the eligibility sites instead (`exogenous.py`'s
            `codes` filter and `cascade.py`'s `_resolve_target`) would close this fully
            but touches more surface for a diminishing-returns cosmetic gain (a
            phantom "WAR DECLARED" headline against a vassal reads as a plausible
            "diplomatically rebuffed" fizzle, not obviously wrong), and risked a
            fourth deadlock-hunting round there was no budget left for in M7. **This, not "needs more seeds," is the more likely explanation
            for the 20%-vs-40% annexation shortfall below** — flagging it as the
            correct next lever ahead of `ANNEXATION_BASE_P`.

            Also corrected `tuning_check.py`'s own equilibrium metric: "active
            countries at tick 5000" is a snapshot, not evidence of *permanence* (the
            word the spec actually uses) — a world that consolidates to a few active
            powers ruling occupied vassals, while liberations/coups/elections keep
            firing, is dynamic consolidation, not a frozen equilibrium. Replaced with
            a check for zero non-mechanical events in the final 500 ticks of a run
            (the actual "is this world alive" question), kept the old snapshot count
            as informational only. The script (`tuning_check.py`) was a
            standalone tuning aid, deliberately not part of the repo or test suite, per the
            implementation spec's description of this step.

            **Final 5-seed result after all three fixes:** severity-2 cadence 1/8.2
            ticks (target ≤1/80, OK), infra chains 100% of runs (target ≥80%, OK),
            runs ending ≤1 active country 0/5 (down from 5/5 pre-fix — the equilibrium
            bar is met), annexation 20% of runs (target ≥40%, **shortfall, documented
            not forced**). A single-seed max-stability trace showed occupied countries
            genuinely reaching stability 50-100 at points during the run (the
            ANNEXATION_LOW_RESISTANCE_STABILITY=15 gate is demonstrably reachable, not
            a fourth deadlock) — but per the incompleteness note above, phantom
            WAR_DECLAREDs against occupied countries still apply a -3 stability hit
            and schedule TREASURY_DRAIN even when the guard no-ops their war state, so
            the more likely explanation for the 20%-vs-40% gap is that residual
            stability drag, not that 5 seeds was too small a sample. The rule for this
            pass was "give it one attempt, then document the shortfall rather than
            distorting constants to force green", so no further probability tuning was
            applied without stronger evidence of exactly which knob is short. **Next step for whoever picks this up:** rerun `tuning_check.py` at
            the spec's real 30-seed bar (not attempted here — each 5-seed x 5000-tick
            x 8-country run already cost ~18-20 min on the development machine at the time, and M7
            was closed here) to see whether 20% holds up at scale. If it
            does, the evidence-backed next lever is moving `_declare_war`'s guard up
            to the eligibility sites (per the incompleteness note above) rather than
            `ANNEXATION_BASE_P` or the stability threshold — both of the latter are
            demonstrably not the bottleneck.

            Golden master regenerated twice more in this milestone (once after the
            PEACE/threshold-routing fixes, once after the occupation/re-war fixes) —
            each time verified deterministic (two in-process runs diffed identical)
            and eyeballed (no broken fallback strings, no `None`/`null`, headlines
            read coherently) before overwriting. Full suite (`pytest tests/ -q`,
            heavy coverage test deselected since it was already confirmed green
            above) + `ruff check` + `mypy meddler/` + `lint-imports` all clean as of
            this milestone's close.

            **M7 closed here.** M8 (design-decisions.md/demo.md, frontend settings overlay,
            final README) followed as a separate milestone.

## M7.1 landmines (written down before starting)

1. **Golden master goes red at M7.1 and stays red until regenerated.** Every new
   exogenous kind inserts an rng.roll() into ExogenousSystem's sorted-registry loop,
   shifting the whole downstream RNG sequence from tick 1. This is EXPECTED, not a
   regression -- regenerate + eyeball once right after M7.1 (deliberate behavior change,
   sanctioned), again after M7.4 tuning. `test_determinism` (internal consistency, not
   golden comparison) must still pass throughout.
2. **M3.1's "acyclic by construction" property dies here.** M3.1 dropped
   CAPITAL_FLIGHT->CURRENCY_SLIDE specifically to avoid cycles, so `clip_count == 0`
   trivially. Restoring the full catalog's real edges reintroduces cycles -> cascades
   hit MAX_DEPTH -> clip_count becomes nonzero for the first time. `test_depth_never_
   exceeds_cap` still must pass; `clip_count <= CLIP_BUDGET` becomes a REAL tuning
   signal for M7.4, not a formality.
3. **Nukes are unsatisfiable in a default coverage run.** `allow_nukes` defaults false,
   so NUCLEAR_TEST/NUCLEAR_STRIKE can never fire -- flag them (and anything else gated
   behind a default-off setting or near-zero probability) `low_frequency_ok=True`.

## Performance: O(ticks²) scan bug found post-M7.4 — FIXED 2026-07-15

**RESOLVED.** All three instances fixed as behavior-preserving perf optimizations (no
RNG touched, so no simulation behavior changes). Measured on a 5000-tick single-seed run
(`seed 1337`): **553.0s → 2.0s wall clock, ~274x speedup**; the full test suite dropped
from ~121s to ~8s as a side effect. The golden master (`seed 1337 --ticks 1000`) is
**byte-identical** — `test_golden_master_matches` passes unchanged, so no regen was needed
(a passing golden is the strongest possible proof the optimization is behavior-preserving).
Fixes applied:
- `EventLog` (`engine/events.py`) gained `__reversed__` (yields `reversed(self._events)`,
  O(1)/element) so `reversed(world.log)` is lazy and cheap.
- `thresholds.py`'s `_find_same_tick_event` and `inflation.py`'s `_minted_this_tick` now
  scan `reversed(world.log)` and `break` on the first `event.tick < world.tick` — this
  tick's events are a contiguous tail (log is non-decreasing in tick; these run live where
  `world.tick` is the max tick present), turning O(ticks) per call into O(events this tick).
- `politics.py`'s `_occupation_start_tick` (the unbounded backward search) is **deleted**;
  occupation start is now direct `Country.occupation_start_tick` state, set in BOTH
  `_begin_occupation` (live, `= world.tick`) and `_replay_occupation_begin` (replay,
  `= event.tick` — the OCCUPATION_BEGIN event's own tick is the original live tick, so no
  payload recording is needed). The replay side is covered by an extended assertion in
  `test_world_at_reconstructs_occupation_status_and_occupied_by` (`tests/unit/test_scrub.py`)
  — the exact scrub-then-live divergence a green golden/live run would NOT catch. One test
  fixture (`test_conquest.py::_occupied_world`) that hand-builds occupied state bypassing
  the handlers had to set the field manually (`= 0`, matching its hand-appended event's tick).
- Verified: full suite 184 passed (+ new replay assertion), `ruff`/`mypy`/`lint-imports`
  all clean, determinism/scrub/conquest tests green.

The historical diagnosis below is kept for the record.

Found in a code review after M7.4 closed. It was not caught during implementation or in
review, and is recorded here as a real miss, not swept under. **Two systems
do a full linear scan of the entire `world.log` on every tick, for every country,
looking only for THIS tick's events:**

- `systems/thresholds.py`'s `_find_same_tick_event()` — called twice per country per
  tick (INFLATION_UPDATE and STABILITY_UPDATE lookups).
- `systems/inflation.py`'s `_minted_this_tick()` — called once per country per tick.

Both filter on `event.tick == world.tick`, meaning they only ever want events appended
THIS tick, yet scan from index 0 every time. Since `world.log` grows ~linearly with
ticks (roughly `31 * COUNTRIES` events/tick observed), this makes both functions
O(log size) = O(ticks) per call, called O(countries) times per tick, for O(countries *
ticks) work per tick and **O(countries * ticks²) over a full run** — this is very
likely the dominant cost behind why 5000-tick runs took multiple minutes each
during M7 (a small pure-Python sim with a handful of countries should not be that slow),
and why `COUNTRIES=8` (tuning_check.py) was consistently >2x slower than `COUNTRIES=4`
(the coverage test) at the same tick count — country count compounds the blowup from
both directions (more scans/tick, and each scan is longer).

**A third, differently-shaped instance of the same underlying problem:**
`systems/politics.py`'s `_occupation_start_tick()` (written during M7.3, with a comment already flagging "No dedicated Country field") does an
UNBOUNDED backward search for the latest OCCUPATION_BEGIN event for a country, called
every tick for every OCCUPIED country. This is worse than the same-tick case because
there's no natural early-exit — occupation could have started thousands of ticks ago.

**Proposed fix, as written before it was implemented:**
- `thresholds.py`/`inflation.py`: since events are appended in non-decreasing tick
  order, this tick's events are a contiguous run at the tail of `world.log`. Scan
  BACKWARD and stop at the first `event.tick < world.tick` — turns O(ticks) per call
  into O(events this tick), no external bookkeeping needed. May need `EventLog` (the
  custom class behind `world.log` — not a plain list, given `.append(tick=..., ...)`
  keyword-based construction) to expose efficient reverse iteration first; check what
  it currently supports before assuming `reversed()` is free.
- `politics.py`'s `_occupation_start_tick()`: the backward-scan trick does NOT apply
  (unbounded lookback). Instead, store `occupation_start_tick` directly as `Country`
  state, set once when occupation begins — eliminates the search entirely rather than
  optimizing it, consistent with how `occupied_by` is already a direct field rather
  than derived. **Implementation note for whoever does this:** must be set in BOTH
  `_begin_occupation` (live) and `_replay_occupation_begin` (replay) — the same rule
  that applied to every other structural change in M7 (`at_war_with`,
  `occupied_by`). Missing the replay side means `world_at()`/scrubbing silently
  diverges from the live run, uncaught by any existing test.

**(Originally deferred; fixed 2026-07-15. See the RESOLVED note at the top of this
section.)** As predicted, it cut wall-clock time
substantially (~274x on a 5000-tick single-seed run), so the deferred 30-seed M7.4
validation and any M8 heavy runs are now far cheaper.

## M7.1-M7.3 deferred/documented simplifications (not silently skipped)

- Several §6.6 table entries describe a TEMPORARY per-country probability modifier
  (COUP_RISK_UP, PRESS_SUPPRESSION's scandal-halving, PROPAGANDA_CAMPAIGN's stability-
  drop-prevention, EDUCATION_DECLINE/REFORM's innovation-p-modifier) or a fire-time
  STRUCTURAL choice (INFRASTRUCTURE_RESTORED's "target class", the 5 new §6.7.4
  interventions' single-asset targeting). None have an engine mechanism (no expiring-buff
  system, no per-firing parameter beyond the static EventSpec model) -- each is registered
  with a declarative approximation and a comment at its call site. See each kind file.
- WAR_SPARK has no relation-based Condition gate (Condition compares a country stat, not
  a World.relations pair) -- registered as a plain low-p exogenous root instead, flagged
  low_frequency_ok. WAR_DECLARED keeps its own unconditional exogenous roll alongside it.
- NUCLEAR_TEST's "-20 to ALL countries" and TRADE_BOOST's "both parties" have no true
  fan-out target (ConsequenceRule.target has no "everyone" option); approximated as a
  single random-other-country / primary-only effect respectively. Low-frequency/cosmetic,
  not worth a new target enum value the way WAR_SPARK's worst_relation was.
- ALLIANCE/ALLIANCE_BROKEN/EMBARGO/INTERVENE_WAR/INTERVENE_ALLIANCE/INTERVENE_EMBARGO
  still don't mutate World.relations/at_war_with structurally (only WAR_DECLARED/PEACE/
  ANNEXATION do, since those were what M7.3's conquest chain actually needed). A future
  pass could extend the same structural.py pattern to these.
## M8 — Ship it

- [x] M8 — Ship it:
      - [x] M8.1 — docs/design-decisions.md (narrative pass + M3.2–M8 log + honest
            cosmetic-assets section) + docs/demo.md. Done + reviewed.
      - [~] M8.2 — frontend against the real engine (scope-corrected: the UI had NEVER
            run on the real engine — no browser WS client existed; app.js hardcoded the
            mock). Broken into:
            - [x] M8.2a — browser↔bridge WebSocket adapter (`web/realengine.js`) +
                  mock/real switch (default REAL, `?engine=mock` offline, `?ws=` override);
                  built the missing stat-history buffer (`Timeline.stat_history`, mirrors
                  rng_states, determinism-safe, powers snapshot.spark + countryDetail
                  series/ticks — adapter.py/commands.py); globe.js self-generates cosmetic
                  cities so the real engine (no geometry) still gets a populated globe;
                  confirmed adoptFork works via snapshot+status (no forkAdopted needed).
                  VERIFIED: full command tour + spark/series round-trip end-to-end against
                  a live `meddler serve` via a Node smoke test (Node 22 global WebSocket
                  runs the exact browser adapter code); golden byte-identical; suite 184
                  green; spark/series asserts added to tests/bridge/test_handshake.py.
                  Browser-render check was not possible in this environment at the time.
                  **Also: `meddler serve` now HOSTS the frontend + engine on
                  one port and prints `open http://127.0.0.1:7677/` — no more manual file://
                  open.** Served via websockets' own `process_request` (static files for GET,
                  `/ws` upgrades) — no new dependency, path-traversal-safe (tests/bridge/
                  test_static.py, 5 tests). realengine.js derives the ws URL from the page
                  origin (port-agnostic). Verified: curl / → index.html, /app.js → js,
                  /../pyproject.toml → 403, /ws → still upgrades + full smoke test green.
            - [x] M8.2b — built the settings overlay from scratch (⚙ toolbar button →
                  `settingsHTML`, instrument-voice per design-guide: sans labels, mono
                  values, blue accents, NO violet since settings aren't god actions). Sends
                  partial `updateSettings`, reflects `settingsAck` (toasts restart-required
                  keys). Added `settings` to the `hello` payload (adapter.py `settings_dict`,
                  shared with settingsAck) so the overlay renders current values on load;
                  contract + test_handshake updated. Annals already existed and renders from
                  the real event stream (verified the data it needs is delivered) — no
                  rebuild needed. VERIFIED via the Node smoke test (updateSettings→settingsAck
                  round-trip, restart-required flag) + suite green. Browser-render check
                  (does the overlay look right) was left for a manual pass.
      - [x] M8.3 — final README.md written (quickstart, two-minute tour, architecture
            sketch, honest cosmetic-globe note + v2 link, dev/test commands, origin links).
            GIF left as a labelled placeholder (browser capture unavailable here — flagged,
            not faked). Quickstart VERIFIED for real: `pip install -e .` creates the
            `meddler` console script; `meddler serve --seed 1337` starts the bridge and the
            browser adapter drives it end-to-end (Node smoke test, ALL PASS). "A stranger
            can run it from the README" holds — except the literal visual GIF.

**M8 status:** functionally complete and verified at the protocol/engine level AND by static
frontend-consumption reconciliation. Every message the browser exchanges is proven via a Node
smoke test running the real adapter code against a live `meddler serve`; `node --check` passes
on all touched JS; and, closing a gap flagged in review (the smoke test proves the adapter
EMITS correct shapes, not that app.js CONSUMES them), every `m.<field>` read in the complex
handlers (`onFrame` fork mode, `onTimelineFocus` incl. its `recentA.filter`/`recentB.forEach`,
`onForkStarted`, `traceHTML` nodes) was reconciled field-by-field against its adapter builder:
no mismatches. The one thing NOT verifiable here is a human/browser actually RENDERING the UI
(layout, label collisions, settings-overlay appearance, the demo GIF); that needed a manual pass.
See docs/design-guide.md §B8.

**First-run UX (resolved):** the server still starts PAUSED at tick 0 (kept, for test
determinism — no background ticking during the bridge tests), but the FRONTEND now auto-sends
`resume` in onHello, so visiting the URL shows a live, advancing world immediately (matches the
mock's "alive on open" and PROPOSAL's "runs on its own"). Frontend-only, so server tests stay
deterministic. Verified: smoke test "resume -> frames stream (world alive on open)" passes.
Press space / pause to hold.

## v2 track — "Simulation-driven world" (M9–M16 complete)

After the perf fix, the next target was the frontend's "globe assets are pure frontend fiction"
stance (contract §7). The goal: NO fiction — satellites/ships/planes/trains/missiles must be
real simulation objects (trade driven by GDP/relations/distance, friendly countries trade
cheaper, famine-struck friends get relief by boat/airlift, wars fought to capture resources,
alliances/coalitions, distance affecting everything). This is a genuine multi-subsystem build
(bilateral trade, physical in-transit logistics, 6 commodities + production, abstract distance/
positions, asset→economy coupling, structural alliances, distance-gated strikes, conquest
economics, contract v2, frontend rebuild) — realistically larger than M0–M7 combined.

Full north-star design: [`design/v2-simulation-driven-world.md`](design/v2-simulation-driven-world.md)
(roadmap M9–M16, determinism plan, and §14 known technical traps).

**Decision (2026-07-15): ship M8 first, pursue this as a tracked v2.** M8.3's
README/M8.1's design-decisions.md honestly documented that the globe's individual movers were
cosmetic for that release, linking the v2 spec as the planned path to making them real.

- [x] M9 — Spatial foundation

      New leaf module `meddler/engine/space.py` (imports `config`+`rng` only, never
      `model` — enforced by a new "space is a leaf" import-linter forbidden-import
      contract, since `model` imports `Position` from `space` and the reverse edge would
      silently create a cycle) gives every country a real position on an abstract unit
      sphere (`Position(lat, lon)`, `central_angle`/`distance_km`), a `region` (landmass —
      same region = land-connected by rail, cross-region = sea/air only), and
      per-extractive-commodity `endowments` (energy/raw_materials/arable, levels in
      [0,1]). Region centers are a Fibonacci-sphere lattice rotated per-seed
      (`space.region_centers`/`rotated_region_centers`); countries are rejection-sampled
      inside disjoint per-region spherical caps (`space.generate_layout`) until they clear
      `min_country_separation` from every already-placed country, cross-region included —
      that direct clearance check, not the cap geometry, is what actually guarantees
      no-overlap (the cap margin collapses to exactly zero once `country_count <=
      region_count`, a config the existing suite runs constantly). `worldgen.py`'s
      `_seed_relations` now seeds rivalries from real proximity (nearest `rival_pairs *
      RIVAL_CANDIDATE_POOL_MULTIPLIER` country pairs) instead of the old
      population-adjacency stand-in. Endowment specialties are assigned round-robin
      (`_assign_specialties`), guaranteeing every extractive commodity has at least one
      specialist for `starting_country_count >= 3` (default is 8) — not by luck.

      **No-overlap evidence (measured, not assumed):** rejection sampling fell back 0
      times and never needed more than 7 of 64 attempts across ~17,000 generated worlds
      (region counts 2-6, country counts 2-20). The invariant was additionally
      mutation-tested: deliberately removing the direct clearance check made 50 tests
      fail, proving the no-overlap tests are real assertions, not tautologies.

      **§14's seeding invariant is explicitly NOT satisfied by M9 — flagged so M10 cannot
      miss it.** v2 spec §14 requires "aggregate supply must roughly meet
      aggregate demand per commodity at worldgen." There is no demand model until M10, so
      M9 cannot assert it; what M9 delivers is the strictly weaker "every commodity has at
      least one specialist," which says nothing about whether aggregate supply meets
      aggregate demand. `ENDOWMENT_BASE_RANGE`/`ENDOWMENT_SPECIALIST_RANGE` (`config.py`)
      are the knobs M10 must calibrate against its own demand figures —
      **M10 owns the real §14 assertion.** Relatedly, the specialist guarantee
      itself only holds for `starting_country_count >= 3` (3 extractive commodities;
      below that more commodities exist than countries) — not a live problem since nothing
      reads endowments until M10, but M10 must not inherit it as unconditional.

      No `REPLAY_EFFECTS` structural handler was added for the new state — nothing
      MUTATES position/region/endowments yet, so `Timeline.world_at`'s existing
      `copy.deepcopy(world)` snapshotting deep-copies them for free. **M15 later added the
      required paired live/replay handler** when annexation became the first mutation of
      endowments and explicit territory ownership.

      Golden master (`tests/golden/seed1337_1000t.txt`) deliberately regenerated (413
      lines, was 423) — the sanctioned "golden goes red per behavior-changing milestone"
      pattern (v2 spec §11), not a regression: endowment draws land mid-stream in
      `_generate_country`, re-rolling every country's population/GDP/stability, which
      changes which gated events fire. Before regenerating: confirmed deterministic (two
      in-process runs byte-identical) and eyeballed (coherent headlines, no
      None/null/unrendered placeholders).

      **Verified:** full suite 377 passed (baseline before M9 was 189); `ruff check .`
      clean repo-wide; `mypy meddler/` clean (52 source files); `lint-imports` 2 contracts
      kept, 0 broken (the new "space is a leaf" contract). The slow
      `test_every_non_low_frequency_kind_fires_at_least_once` (20 seeds x 5000 ticks) was
      run UNDESELECTED and passed — M9's genesis re-roll did not push any event kind below
      reachability.

      **Two forward-looking caveats, full reasoning in `docs/design-decisions.md`:** M9 is
      the first milestone whose determinism depends on the platform's libm (genesis now
      calls `cos`/`sin`/`atan2`/`asin`, which — unlike everything before M9 — are not
      IEEE-754-guaranteed bit-identical across platforms; `sqrt` remains safe). And a
      correction worth carrying forward: worldgen's new RNG draws do NOT shift the tick
      stream — `generate_world` creates and discards its own `Rng`, while
      `cli._run_headless` builds a fresh one for the tick loop; this safety is load-bearing
      but currently accidental (nothing enforces that `generate_world` discards its `Rng`).

- [x] M10 — Commodities & production

      Six commodity buckets (food, energy, raw_materials, manufactured, consumer, high_tech,
      spec §3) with a production DAG. The three scalar `grain_*` Country fields are REPLACED by
      `commodity_output/need/stock: dict[str, float]`; `grain_*` survive as read/write @property
      aliases over the `food` bucket, so every pre-M10 consumer (thresholds famine checks,
      trade.py, god edits, natural.py stat_deltas, bridge) keeps working against unified data.

      New leaf module `engine/commodities.py` (registry + `genesis_output`, imports only config);
      new ambient system `systems/commodity_production.py` (emits unregistered COMMODITY_PRODUCTION
      via `world.log.append`, consumes NO rng, recomputes only produced commodities, never touches
      food's stock); new registered kinds in `kinds/commodities.py`: ENERGY_SHORTAGE,
      MATERIALS_SHORTAGE, MANUFACTURING_SLUMP, CONSUMER_SHORTAGE, TECH_STAGNATION with cascade
      chains. `stats.apply_commodity_stat` + a `commodity:` branch in `replay_stat_delta` handle
      the dict-field replay (a dict field is not a plain attribute — the central trap). Extractive
      output is STATIC after worldgen (moved only by shocks like DROUGHT); produced output derives
      from gdp_tick/innovation_mult/education per §3 so it genuinely varies country-to-country.

      **§14 supply/demand invariant:** the spec's exact-band assertion was mathematically
      unsatisfiable (variance-blind; ratio linear in the tuning constant, so NO value fit). Replaced
      with a FLOOR (`AGGREGATE_BALANCE_MIN = 1.05`) calibrated against the worst of 30 seeds; the
      "nobody self-sufficient" claim was corrected to WORLD level (spec §2), not per-country (per-country
      was a false reading — ~20% of countries ARE self-sufficient heavyweights). Measured across 30
      seeds every commodity clears the floor (food min 1.14, energy min 1.06, raw_materials min 1.10).

      **Empirical result (the open question at M10 start): all five new shortage kinds FIRE
      organically.** `test_every_non_low_frequency_kind_fires_at_least_once` passes UNDESELECTED — no
      `low_frequency_ok` cheat used. ~40% of countries run real produced-commodity deficits.

      **Bridge:** `countryDetail` gains a `commodities` block (six buckets × output/need/stock),
      dossier-only — deliberately NOT in per-tick frame/snapshot (6× payload for something only the
      dossier reads). Contract doc + handshake assertion added.

      **Golden regenerated + eyeballed** (deliberate behavior change: five new firing kinds + cascades
      grew the seed-1337/1000-tick log 413→675 lines; determinism reconfirmed, zero unrendered
      tokens, headlines coherent). **Verified:** full suite 446 passed (was 377 after M9), coverage
      test undeselected, `ruff check .` clean, `mypy` clean (55 files), `lint-imports` 2 kept 0 broken.

      **Gaps carried forward:** (a) food's stock is a shock-buffer only; commodity_production does NOT
      touch it because trade.py still settles the food gap in cash — **M12 owns unifying the food/non-food
      stock asymmetry**. (b) The `cascade.py:100` `hasattr` landmine (a `commodity:`-prefixed stat in
      `_apply_spec_effects` would be silently skipped) is untouched by M10 and **owned by M11/M12**.
      (c) Annexation transfers all six commodity stocks via payload-based replay, but
      endowment transfer on conquest is **M15's** to add (needs a REPLAY_EFFECTS handler + recompute of
      the annexer's static extractive output).

- [x] M11 — Diplomacy & alliances

      The declarative ALLIANCE/ALLIANCE_BROKEN/EMBARGO kinds are now STRUCTURAL. New `Bloc`
      dataclass + `World.blocs`/`bloc_seq`/`embargoes` (all default-empty, deep-copied by snapshots).
      New system `systems/diplomacy.py` (after politics, before exogenous) with four deterministic
      phases: **formation** (fires ALLIANCE for near, high-relation pairs — shared rival is a
      probability BONUS, not a hard gate), **mutual defense** (fires WAR_DECLARED to pull allies into
      a bloc-mate's war, p ∝ strength × relation ÷ distance, capped by max_wars_concurrent),
      **propagation** (one invisible ambient BLOC_COHESION event per bloc carrying intra-bloc warming
      + enemy-cooling relation stat_deltas), and **strain** (fires ALLIANCE_BROKEN when a bloc sours).
      ALLIANCE/ALLIANCE_BROKEN/EMBARGO gained structural + payload-based REPLAY handlers in
      `systems/politics.py` (the WAR_DECLARED precedent); a country is in AT MOST ONE bloc.

      **The pivotal finding (the plan missed it):** the pre-M11 world had NO positive relations at all
      — rivalries are seeded negative and everything decays toward 0 (measured peak relation over 20
      seeds × 1000 ticks = **−0.1**). So "form blocs from high mutual relations" was categorically
      unreachable. Fix: **worldgen now seeds genesis FRIENDSHIPS** (`friendly_pairs=4`,
      `FRIENDLY_RELATION_RANGE=(60,85)`, drawn from the nearest not-already-rival pairs, after
      rivalries in pinned RNG order) — the positive-relation seed alliances crystallise from. Tuned
      empirically (30 seeds): 29/30 form alliances, 23/30 keep a bloc to t1000, 18/30 see a coalition
      war; ~2 ALLIANCE + ~1 ALLIANCE_BROKEN per 1000 ticks (rare — good pacing). Durability required
      warming (0.5/tick) to out-run decay and a TINY strain base (0.0005) so only *soured* blocs break.

      **Determinism:** proven by `test_world_at_reconstructs_bloc_and_embargo_state_exactly`
      (tests/unit/test_scrub.py) — live vs `world_at`-reconstructed bloc/embargo/relation fingerprints
      match exactly across scrubbed ticks. **Bridge:** `countryDetail` gains a dossier-only `bloc`
      field ({id, members, formedAt} or null); NOT in per-tick frame/snapshot (globe-level bloc render
      is M16). **Coverage:** ALLIANCE/ALLIANCE_BROKEN now fire organically (19/20 seeds × 5000 ticks),
      so their `low_frequency_ok=True` flags were REMOVED (honest strengthening, not a weakening).

      **Golden regenerated** (413→675 was M10; M11 → **525 lines**: friendly seeding + the diplomacy
      system reshape the RNG stream from tick 1, and a world with genesis friendships is less
      relentlessly warlike, so fewer cascades — a different valid trajectory, not a regression;
      determinism reconfirmed, zero unrendered tokens, BLOC_COHESION correctly invisible, diplomatic
      headlines coherent). **Verified:** full suite **465 passed** (was 446 after M10), coverage
      undeselected, `ruff`/`mypy` (56 files)/`lint-imports` all clean, `Contracts: 2 kept, 0 broken`.

      **Scope walls / gaps carried forward:** (a) EMBARGO records the lane on `World.embargoes` but
      lane-blocking + intra-bloc trade discounts are **M12's** (no bilateral lanes exist yet).
      (b) **Bloc MERGING** (two established blocs combining) is deferred — formation only seeds a fresh
      pair or adds a bloc-less country. (c) Coalition **strikes** and **resource-war motivation** (spec
      §8) are **M15's**. (d) The `cascade.py:100` `hasattr` landmine remains M11-untouched (M12).

- [x] M12 — Bilateral trade & pricing

      The abstract single-commodity world market (grain minted/burned against a reference price) is
      REPLACED by per-commodity bilateral matching across all six commodities (v2 spec §4).
      `systems/trade.py` rewritten: per commodity, greedily fill each importer's deficit from the
      available surpluses — allies/friends first (a discrete priority tier ahead of price, so a
      distant ally beats a cheap stranger), then cheapest landed cost, ties by code; partial fills
      across suppliers. Lanes blocked by war / embargo (`World.embargoes`, finally consulted) /
      declared enmity (`relation ≤ RELATION_TRADE_BLOCK_THRESHOLD`). Price =
      `base[commodity] × (1 + distance_cost) × relation_mod × scarcity_mult × (1 + tariff)` with an
      intra-bloc tariff waiver + extra discount (blocs get economic teeth). Consumes NO RNG.

      **Money is conserved with NO mint/burn** (the world market minted/burned): each fill is two
      single-currency `transfer` legs bridged by the `fx:<code>` desk (`importer.treasury → fx:IMP`,
      `fx:EXP → exporter.treasury`), so Invariant A holds by construction and `world.fx_pools`
      (dormant since it was added) carries the net FX position. The per-currency conservation test was
      corrected — not weakened — to count `fx:<code>` as part of currency `<code>`'s supply (a
      conserved transfer into the desk would otherwise read as a leak); it is now a stronger check.

      **The M10 food/non-food stock asymmetry is CLOSED.** The stock-fold is deleted from
      `commodity_production` (which now only produces output + consumes inputs) and done once, in
      `trade`, uniformly for all six commodities:
      `new_stock = clamp(stock + output + imported − exported − need, 0, ceiling)`. Food now flows
      through inventory exactly like energy — a food deficit trade can't clear drains `grain_stock`
      and drives FAMINE through the same mechanism as an energy shortage.

      **No new structural/replay handlers**: `TRADE` carries its ledger legs (replayed by
      `apply_to_world`), `TRADE_SETTLE` carries `commodity:` stat_deltas (replayed by
      `replay_stat_delta`); both are invisible (`headlines.AMBIENT_KINDS`, replacing
      TRADE_IMPORT/TRADE_EXPORT). Determinism proven by
      `test_world_at_reconstructs_trade_pools_stock_and_fx_exactly` (test_scrub.py) — a money leak or
      stock divergence fails it.

      **Emergent scarcity from thin margins, not an affordability cap:** §14 leaves only ~5–14%
      global slack/commodity, so greedy priority allocation exhausts surpluses before every importer
      is filled → tail importers stay short → shortages/famine reachable. Money is unbounded this
      milestone (import into debt = what DEBT_CRISIS models); a fiscal cap is a later lever.

      **Measured pacing (12 seeds × 1000 ticks, 8 countries):** ~21 matched fills/tick (active market,
      every seed trades); FAMINE ~3.8/seed + FAMINE_WARNING ~8.7/seed (occasional acute crisis, rare
      per country, not relentless); produced-commodity shortages now rare (trade relieves them) but
      ALL still reachable — `test_every_non_low_frequency_kind_fires_at_least_once` passes
      UNDESELECTED. Tuning constants were hand-set to plausible values and this run confirms a healthy
      regime; they were NOT adversarially swept. **If famine reads too hot, the next lever is
      `COMMODITY_BASE_PRICE_VERI["food"]` or `RELATION_TRADE_BLOCK_THRESHOLD`, not the shortage
      constants (demonstrably reachable).**

      **Verified:** full suite 475 passed (was 465 after M11); `ruff`/`mypy` (56 files)/`lint-imports`
      all clean. Golden regenerated (525 → 528 lines) after confirming determinism (two in-process
      runs byte-identical) and eyeballing (coherent trade-deficit/famine/shortage headlines,
      TRADE/TRADE_SETTLE invisible, zero unrendered tokens).

      **Scope walls / gaps carried forward:** (a) A matched trade settles INSTANTLY; **M13** inserts
      the in-flight `Shipment` (carrier, transit time, interdiction, relief airlift) between match and
      settlement. (b) Distance affects PRICE only — satellite-gated trade REACH + fleet-condition
      throughput are **M14**. (c) The `cascade.py:100` `hasattr` landmine is still untouched (no
      registered kind needed a `commodity:` spec-effect; **M13+** if one does). (d) Tuning was
      self-measured, not adversarially reviewed — flagged for a second-opinion pass.

- [x] M13 — Logistics: physical in-flight shipments

      A matched trade no longer settles instantly. New `Shipment` dataclass on
      `World.shipments`/`shipment_seq` (the first multi-tick stateful object since occupation).
      TradeSystem now calls `logistics.dispatch` per fill: pick a carrier (rail if same-region+near;
      air for high-value or relief-through-a-war-zone; sea otherwise), compute `arrive_tick = depart
      + transit(distance, carrier)`, emit **SHIPMENT_DISPATCHED** (exporter stock leaves, importer
      pays money leg-1 into the fx desk, Shipment created). New **`systems/logistics.py`** (after
      trade) advances the fleet each tick: sea shipments in a war zone may be interdicted
      (**SHIPMENT_LOST**, RNG), and shipments reaching `arrive_tick` land (**SHIPMENT_ARRIVED**:
      goods → importer stock clamped, money leg-2 → exporter). Emergency **relief**: a famine-critical
      importer buying from a friend ships subsidised, air-routed if the sea lane is a war zone.

      **Determinism (the sacred part):** all three lifecycle events have structural + RNG-free replay
      handlers reconstructing the Shipment from `event.payload`; money rides `LedgerEntry`, stock rides
      `commodity:` `StatDelta`, both generic-replayed. `test_world_at_reconstructs_in_flight_shipments`
      + the extended `test_scrub.py` fingerprint (now incl. shipments) are the proofs. Conservation
      holds across time: leg-1 at dispatch, leg-2 at arrival; a lost shipment strands the importer's
      leg-1 in the fx desk = the economic loss. `commodity_production` restored its `(output-need)`
      fold for all six (trade goods now move as shipment deltas). Interdiction was TUNED down hard
      (0.04→0.0015): per-tick × per-shipment × ~12-tick sea transit compounded to ~46% war-zone loss
      and drove famine 14× (transit delay alone did NOT — famine held at ~M12 with interdiction off).

      **Verified:** full suite 483 passed; `ruff`/`mypy` (58 files)/`lint-imports` clean; coverage
      undeselected green; golden regenerated. SHIPMENT_LOST fires organically (a war sinks a cluster
      of convoys). **Gaps:** fleet-condition throughput caps + satellite reach are **M14**.

- [x] M14 — Asset coupling

      Every infra asset class finally has an economic job (v2 spec §6). Four couplings, all
      routed through `engine/assets.py` per the r4 note ("never inline `count * condition` at
      call sites"), split QUANTITY vs QUALITY: freight is `count × condition`; production
      multiplier / trade reach / coordination are `condition` ALONE (a country does not buy
      better price discovery by owning more satellites).
      - **naval/rail/air → freight throughput.** `trade.run` keeps a per-tick
        `(country, carrier) → units` budget and meters every fill against BOTH endpoints
        (a lane needs a fleet to load AND one to land — this is what gives an *importer's*
        own NAVAL_LOSS a cost). The unlifted remainder needs NO queue state: deficits are
        recomputed from flow each tick, so it simply re-matches next tick while
        `commodity_production`'s fold drains the importer's stock in the meantime.
      - **power_grid → production multiplier** for energy/manufactured/consumer/high_tech,
        applied in `commodity_production._target_output`, deliberately NOT inside
        `commodities.genesis_output` (that states POTENTIAL supply, which is exactly what
        M10's §14 `AGGREGATE_BALANCE_MIN` floor is calibrated against; folding a
        genesis-condition multiplier in would have silently invalidated it).
      - **satellites → trade reach + price discovery**: a graded beyond-reach PREMIUM plus a
        discovery spread, never a hard lane gate (a deliberate call: with only 5–14% global
        slack, gating lanes manufactures famine).
      - **communications → coordination**: how much of an earned discount (relation_mod < 1,
        the intra-bloc discount) is realised, plus transit friction taking the WORSE end of
        the lane. `assets.realised_discount` deliberately leaves a *premium* (≥ 1.0) untouched
        — otherwise a blackout would profitably bargain away the price a hostile pair pays.

      **The pivotal finding, measured BEFORE writing code (same shape as M11's "there were no
      positive relations"): asset condition never varied, so every coupling would have been
      inert.** 4 seeds × 1000 ticks × 8 countries: condition mean 0.996, 98% of country-ticks
      above 0.9, and the §6.7.3 failure thresholds (0.25–0.40) effectively unreachable
      (SATELLITE_FAILURE fired 0 times). Root cause: `INFRA_BASE_COST_PER_UNIT = 50` minor
      units made upkeep **0.001%–0.1% of tax revenue** (a country with a 1.1bn treasury and
      8.5m/tick of tax paid 1,450/tick), so `maintenance_ratio` was always 1.0 and condition
      pinned at 1.0 forever. Same magnitude mismatch M7.4 flagged behind an unreachable
      DEBT_CRISIS. **Decision (2026-07-23): fix the fiscal magnitude inside M14**, over
      the narrower options of shipping a latent coupling or only widening genesis spread.

      **The fiscal fix (`assets.upkeep_cost`, `INFRA_UPKEEP_GDP_SHARE = 0.11`):** upkeep is a
      PHYSICAL obligation anchored to `base_gdp` (potential), never `gdp_tick` (current
      output) — the asset stock is sticky while revenue is cyclical
      (`gdp_tick ∝ 0.5 + stability/200`), so a country in political collapse can no longer
      cover the upkeep it took on while healthy. Anchoring to `gdp_tick` would move both sides
      together and change nothing. 0.11 is derived, not guessed: revenue is
      `0.15 × base_gdp × (0.5 + stability/200)`, and condition only falls when the funding
      ratio drops below 2/3 (where `INFRA_DEGRADATION_RATE` and `INFRA_RECOVERY_RATE` cross),
      so upkeep must sit near revenue at high stability and ~1.5× revenue at low stability.
      `INFRA_BASE_COST_PER_UNIT` is deleted; `worldgen._infra_counts_for_population` moved to
      `assets.infra_counts_for_population` (upkeep needs the same baseline to ask "is this
      asset stock oversized for its population?" and two copies would drift).

      **Freight capacity took THREE calibration passes, two of them rejected by measurement**
      — worth recording because the first two looked fine and were inert:
      1. set by eye (1.6/1.6/0.5): ~15× headroom, fleet utilisation never above 58%, never bound.
      2. 2× PEAK demand (1.2/0.85/0.6): utilisation max 0.496, and a direct A/B against
         *unlimited* freight moved **zero** units across 3 seeds × 600 ticks — including a seed
         with 100 ticks of sub-0.5 fleet condition. Demand is right-skewed (peak ≈ 4–5× mean),
         so "2× the peak" means the peak itself only half-fills the hold.
      3. **2× MEAN demand (0.27/0.17/0.06), shipped** — matching the description of the option
         that was chosen ("typical trade clears fully, but peak-demand ticks and any
         condition loss meter the flow"): median tick ~50% utilisation, ~11% of
         country-carrier-ticks saturate, and a degraded fleet meters trade in normal play.
         **Both side-effect claims made in the first pass here were WRONG and are retracted**
         (see the adversarial-review section below): metering does NOT leave total flow
         unchanged — paired measurement puts it ~10% down, food ~8-24% down — and the
         "calming" crisis/famine figures were 6 unpaired seeds of noise.

      **Determinism: M14 adds NO new stateful engine data**, so no new structural/replay
      handler pair — freight budgets are per-tick locals in `trade.run`, and every other
      coupling is a pure function of `infrastructure.*.condition`, which already rides `infra:`
      StatDeltas through generic replay. That claim is PROVEN, not asserted, by
      `test_world_at_reconstructs_a_metered_partially_filled_world` (tests/unit/test_scrub.py):
      a world with 1-unit fleets at 0.3 condition and every quality asset degraded, fingerprinting
      pools/fx/stock/shipments/**condition**/**output** live vs `world_at` across scrubbed ticks,
      with an in-test assertion that metering actually bound (or the test proves nothing).
      **Mutation-tested**: deleting the shipment reconstruction from `_replay_dispatch` makes it
      fail. A second mutation (a coupling reading non-replayed state) did NOT fail it — correctly,
      because `world_at` never re-runs systems, so decision logic cannot diverge; only state
      mutations can, which is exactly what the fingerprint covers.

      **Two existing tests were RETARGETED, not weakened** (both used `energy`, which M14 makes
      grid-fed and therefore recomputed every tick): `test_extractive_output_is_static_across_ticks`
      → `raw_materials` (renamed `test_shockable_extractive_output_is_static_across_ticks`), and
      `test_surplus_stock_stops_at_the_ceiling` → `raw_materials`. The guard keeps its teeth for
      every commodity a shock can actually reach (food/DROUGHT keeps its own dedicated test), and
      energy gained a STRONGER, categorical protection instead:
      `test_no_event_kind_shocks_a_grid_fed_commodity_output` walks EVENT_REGISTRY and fails loudly
      if any kind ever writes a recomputed commodity's output — converting a future silent no-op
      into a build break.

      **Measured result (16 seeds × 1000 ticks, pre-M14 vs M14, re-run at the FINAL constants):**
      infra failure events 0.38 → 4.00 and grid-condition spread sd 0.00 → 0.04 (min 0.82) —
      assets now genuinely vary, which was the whole point; countries active at t1000
      7.25 → 7.56 (no spiral); shipments 18046 → 18611 (unchanged within noise). Crises
      62.6 ± 26.9 → 50.1 ± 24.4 and famine 10.1 ± 5.9 → 6.0 ± 4.4: directionally calmer, but that
      is **~1.5–2 standard errors on very noisy counts and is NOT claimed as a real effect.** An earlier 4-seed ablation appeared to
      show the grid multiplier cutting famine 10.0 → 3.5; on inspection the commodity aggregates
      were identical (food stock/need 24.9 vs 25.0) and the moving event kinds were political
      (COUP, LEADER_CHANGE, WAR_DECLARED) — i.e. RNG-stream reshuffling, not causation. **That
      attribution was wrong and is retracted here rather than left in the record.**

      **Verified:** full suite **521 passed** (was 483 after M13); `test_catalog_coverage.py`
      run UNDESELECTED and green (no kind pushed below reachability); `ruff check .` clean;
      `mypy meddler/` clean (58 files); `lint-imports` 2 kept, 0 broken. Golden regenerated
      316 → 323 lines (deliberate: the grid multiplier moves output from tick 1) after
      confirming determinism (two in-process runs byte-identical) and eyeballing (zero
      None/null/unrendered tokens, ambient kinds invisible, headlines coherent).

      **Doom-loop check (the fiscal change's biggest risk, tested to 5000 ticks — 1000 is far
      too short to see it).** The feedback IS closed: condition → grid multiplier → output →
      shortage events → stability → `gdp_tick` → revenue → funding ratio → condition. Measured
      4 seeds × 5000 ticks against a couplings-OFF control on the same seeds:
      | | M14 @ t5000 | pre-M14 @ t5000 |
      |---|---|---|
      | grid condition | 0.13–0.50 | 0.88–0.93 |
      | mean stability | 0.0–28.6 | 0.0–20.0 |
      | active countries | 7,5,7,5 (mean 6.00) | 5,5,5,7 (mean 5.50) |
      **The long-run stability collapse and country attrition are PRE-EXISTING** — the control
      ends at stability 0.0 in two of four seeds and with FEWER countries standing. What M14
      changes is that infrastructure condition now *follows* that decline instead of sitting at
      0.9 through a world falling apart, which is the coupling working as designed. No evidence
      of an M14-induced spiral; treasuries at t5000 hold 30–100 ticks of GDP, so the decline is
      driven by the instability term, not by bankruptcy.

      **Gaps / observations carried forward:**
      (a) **Satellite reach measured as having ZERO organic effect** in a 4-seed ablation
      (identical crisis/famine/shipment counts) — at healthy condition reach ≈ π so nothing is
      ever beyond it, and the spread only reorders candidates. It is real and unit-tested when a
      satellite is degraded, but it is the weakest of the four couplings in practice; making it
      bite without hard-gating lanes is open.
      (b) **36% of golden headlines are convoy-loss lines** (113/316 before, 116/323 after) —
      PRE-EXISTING from M13's SHIPMENT_LOST in a war-heavy seed, not an M14 regression, and left
      alone under the standing rule not to re-tune pacing inside a feature milestone. Flagged for
      a future feel pass.
      (c) Metering binds ~11% of country-carrier-ticks; if trade volume grows in M15, it tightens
      automatically (the constants are per fleet unit, not absolute).
      (d) Bridge/frontend exposure of per-lane throughput and fleet condition (spec §6's "a
      failure SHOWS — a boat vanishes, a lane thins") is **M16's**, not done here.

      **ADVERSARIAL REVIEW (2026-07-24, after M14 was first reported complete). It found real
      defects; every claim below was independently re-verified before acting on it.**
      - **The fiscal change INCREASES crises — my "calming" claim was wrong-signed.** Paired
        n=24 (mine) and n=32 under two seeding regimes (reviewer's) agree closely: fiscal
        change alone gives **crises +5.83, t=+2.90** (reviewer: +6.28, t=+2.98) and famine
        +0.92, t=+2.53. Full M14 (fiscal + couplings) nets to crises −7.88 (t=−1.00, **not**
        significant) and famine −3.38 (t=−2.40). The only large, robust effect remains infra
        failures 0.4 → 6.2. The wrong claim is retracted in both `config.py` and above; my
        original 16-seed run was unpaired AND used an unfaithful pre-M14 baseline (it left
        energy recomputed), which is how it got the sign wrong.
      - **`INFRA_UPKEEP_GDP_SHARE = 0.11` sits on the WRONG SIDE of its own stated crossover.**
        Spending is capped on a STOCK, so the funding ratio converges to `tax/upkeep`, which at
        share 0.11 is **0.682 even at stability 0** — just above the 2/3 crossover, giving a
        funding-only condition delta of **+0.00023/tick**. The funding channel alone can never
        rot an asset (crossover is at 0.1125). **M14's real mechanism is removing the recovery
        term that had been exactly cancelling `INFRA_INSTABILITY_DEGRADATION_RATE`**, measured
        ~2:1 instability:funding. Verified independently. The narrative in `config.py` and
        `assets.upkeep_cost` is corrected; the constant is KEPT (the observed dynamics are the
        ones wanted, and raising it is a behaviour change needing its own tuning pass).
      - **Metering hard-gated emergency relief — FIXED.** `if qty <= 0: continue` applied the
        freight cap to relief, cutting relief VOLUME ~35% while leaving the relief COUNT
        unchanged (every lift shrunk). That directly contradicts this milestone's own reason
        for pricing satellite reach instead of gating it. Relief now bypasses the budget but
        still DEBITS it, so it crowds out commercial cargo rather than being free
        (`test_emergency_relief_bypasses_the_freight_budget` + a crowd-out test).
      - **The reach calibration was CIRCULAR.** I measured lane angles on *dispatched* lanes
        (median 0.95) — a sample already filtered by the pricing the term exists to reorder.
        The right population is candidate pairs (median **1.83**, max **3.07**). Figures
        corrected in `config.py`, `assets.py` and `test_assets.py` (which asserted against a
        wrong "longest lane observed"). Beyond-reach fires on only ~3% of priced candidates.
      - **Two latent bugs fixed:** `maintenance_ratio` was unclamped, so a negative treasury
        would degrade condition at a rate proportional to the DEBT (−0.085/tick at ratio −5,
        8.5× the documented floor) — now clamped to [0,1]; and `(angle − reach)/reach` divided
        by zero if `SAT_REACH_FLOOR` were ever set to 0 — now guarded.
      - **Freight constants are 2× CONDITIONAL mean** (zero-demand ticks excluded), so real
        headroom is sea 2.2× / rail 3.3× / air 4.6×, and the "~11% saturation" figure describes
        sea only (6.9% aggregate). Documented rather than re-tuned.
      - **The guard test was weaker than it looked** — it string-matched two spellings. Added
        `test_recompute_erases_output_shocks_and_static_commodities_keep_them`, which asserts
        the MECHANISM over every commodity, so a change to the recomputed set re-checks the
        contract automatically.
      - **Confirmed defensible** (reviewer's own list): both-endpoint freight charging is
        port-capacity accounting, not double-counting (the constant was measured the same way);
        applying the grid multiplier in `_target_output` not `genesis_output`; `realised_discount`
        leaving premiums untouched; choosing the carrier once; "no new stateful engine data".

      **OPEN — needs its own decision, NOT fixed here (would be an economy-wide rescale):**
      **trade money rounds to zero.** `cost = round_money(price_veri × qty × exchange_rate)`
      with typical value 0.42 minor units means **71% of shipments pay literally 0** (verified
      independently: 8,897 shipments, mean cost 0.29, max 2, total trade spend 3,119 against
      125.8bn of tax revenue). This is the SAME magnitude bug M14 just fixed for upkeep,
      sitting one layer over. Consequence: the satellite spread and the comms discount-realisation
      are **decorative** — forcing a 25% spread on every trade of every country produces
      bit-identical worlds, because a common per-importer multiplier cannot reorder candidates
      and the money rounds away. Only the reach premium (via reordering) and comms transit
      friction are live. Fixing it means rescaling `COMMODITY_BASE_PRICE_VERI`/qty units so a
      shipment costs a non-trivial sum — which would also make tariffs, bloc discounts and
      relation pricing bite, and would make the (now-clamped) negative-treasury path reachable.
      Flagged as its own decision; M15 deliberately did not hide this economy-wide rescale
      inside war/conquest work. (Fixed afterwards: see "Post-M15 extension" below.)

      **Also open:** `commodity_need` is frozen at genesis while energy OUTPUT now tracks
      population (M14 made energy grid-fed/recomputed). So `NUCLEAR_STRIKE` (−5.0 population
      against populations of 8–58) now cuts a country's energy supply by up to 60% with demand
      unchanged, guaranteeing ENERGY_SHORTAGE; `MIGRATION_WAVE` mints free energy the same way.
      Only one side of the balance tracks population. Not fixed — it needs a decision on whether
      need should follow population.

      **Re-verified after all review fixes:** full suite **524 passed**, coverage undeselected
      green, `ruff`/`mypy`/`lint-imports` clean, golden regenerated again (323 → 354 lines,
      deliberate: relief now lifts in full) after re-confirming determinism and eyeballing.

## M15 — War & conquest economics (v2 spec §8)

- [x] M15 — War & conquest economics (completed 2026-07-24)

      A dedicated `WarSystem` now runs after diplomacy and before exogenous roots. M11's
      existing mutual-defense path remains the ONE coalition-joining mechanism; M15 does
      not duplicate it. Because diplomacy runs first, allies that join an existing war can
      contribute a directed strike front in the same tick. Every active attacker/foe pair
      is evaluated in sorted order. Conventional `STRIKE` rate and damage both use
      `1 / (1 + STRIKE_DISTANCE_K × central_angle)`, so nearby fronts hit sooner and harder
      while distant fronts remain possible. Each hit applies real stability, population and
      all-class infrastructure attrition. Dynamic outcomes are payload-recorded after
      clamping and reproduced by an RNG-free `REPLAY_EFFECTS` handler.

      Resource wars use the existing structural `WAR_DECLARED` kind rather than a parallel
      war state. For each active aggressor, the system deterministically selects its single
      strongest extractive opportunity:
      `domestic flow scarcity × richer target endowment × hostility × proximity`, excluding
      allies, current enemies, inactive countries and targets beyond the 1.4-radian gate.
      One roll is consumed for that best opportunity, and `max_wars_concurrent` remains the
      hard global cap. The declaration payload records `cause=resource`, commodity,
      motivation and distance for auditability.

      Occupation now redirects 25% of gross extractive output into the active occupier's
      stock balance before local consumption. `CommodityProductionSystem` was split into
      deterministic output and balance phases so ordering by country code cannot decide
      whether tribute sees current-tick production. The outgoing and incoming quantities
      ride ordinary `commodity:` StatDeltas. The same refactor closes M14's frozen-demand
      gap: every commodity's need now follows live population, which is required once
      recurring strikes create casualties; annexed/dissolved countries have zero demand
      and no commodity production, and FX skips those retired countries.

      `ANNEXATION` now captures every stock, population-weighted endowments, recomputed
      extractive capacity, explicit territory markers and surviving infrastructure. A new
      immutable `Territory(position, region)` marker distinguishes spatial ownership from
      `Country.position`, which remains the administrative capital/distance anchor. This is
      the coherent implementation of the spec's singular "territory/position" wording:
      annexation moves all holdings without teleporting the winner's capital or losing its
      original location. Endowment intensity is population-weighted so
      `merged_population × merged_endowment` preserves combined extractive potential.
      Half of each M14 asset class survives (`floor(count × 0.5)`), with count-weighted
      condition folded into the winner; the rest is destroyed. Captured grid condition is
      applied before energy capacity is recomputed. Every structural result is recorded on
      ANNEXATION payload and replayed exactly.

      **Determinism/replay:** strike decisions use sorted directed fronts; resource decisions
      use sorted aggressors and deterministic score/code/commodity tie-breaking; neither
      occupation tribute nor production consumes RNG. New structural state has paired live
      and replay handlers. Focused M15 coverage is 13 tests, including three repeated seeded
      traces, exact strike replay and a complete annexation-state fingerprint.

      **Verified:** full suite **537 passed**; Ruff clean; mypy clean across 59 source files;
      import-linter 2 contracts kept, 0 broken. The seed-1337 golden was deliberately
      regenerated after two byte-identical runs (`sha256 d38f7fcd…`), then inspected for
      chronology, unrendered/null tokens and M15 narrative coherence. It changed 354 → 301
      lines: 18 conventional-strike lines landed while shipping-loss lines fell 133 → 84 as
      the new fixed RNG decision points changed the deterministic history. This is the
      expected behavior-changing-milestone update, not nondeterministic drift.

      **Scope wall (closed by M16 below):** M15 deliberately changed no bridge or frontend
      contract. The M14 finding that most trade money rounds to zero was not silently folded into
      conquest work; it remains a separately documented economy-scale calibration issue.

- [x] M16 — Bridge/contract v2 and authoritative globe (completed 2026-07-24)

      Added pure `bridge/world_objects.py`, projecting positions, regions, territory ownership,
      endowments, six commodity buckets, all infrastructure counts/conditions/capacities, blocs,
      in-flight shipments, directional carrier-separated current-volume lanes, and recent real
      STRIKE routes. Projection is deterministic, observational, and RNG-free.

      Bumped protocol to 2 only after wiring `hello.worldObjects`, `snapshot.worldObjects`,
      timeline A/B frame projections, `timelineFocus.worldObjectsA/B`,
      `forkStarted.worldObjects`, `countryDetail.worldObject`, and
      `countryAdded.worldObject`. Scrubbing projects the reconstructed world passed to the
      snapshot builder, not live prime.

      Replaced the default real-engine globe path with `setWorldObjects`: authoritative country
      placement and territory ownership; deterministic bloc tint; lane-only routes; real SHIP-id
      sea/air/rail movers at projected progress; volume/condition sizing and fading; aggregate
      satellite glyph count/condition with presentation-only orbits; persistent wars plus separate
      recent STRIKE arcs; and far/mid/near/closest LOD. Real shipment and aggregate-asset dossiers
      are read-only and contain no invented mission/log fields. Vanished shipments leave no ghost;
      SHIPMENT_LOST adds a transient pulse. Cosmetic generation/mutation remains only behind the
      explicit `?engine=mock` fallback.

      Added focused projection, adapter/handshake, historical-world, and static frontend honesty
      tests. No engine system, event, state transition, RNG order, or golden fixture changed.
      **Verified:** full suite 570 passed in 17m51s; compileall and JavaScript syntax checks pass;
      bridge mypy is clean; touched M16 Python is Ruff-clean under the project's established rules;
      import-linter keeps both contracts. The golden fixture was not regenerated and remains
      byte-for-byte at SHA-256
      `3b70f62d094c1e575ad7275610662b4c5913382b8dee0a1c24fdb993ac0d613a`.

## Pacing pass — 2026-07-22 ("events are extremely frequent")

      Watching at 4× produced a constant barrage of famines/wars/instability. Measured: ~0.63
      rendered headlines/tick and **~141 severity-2 crises/seed/1000t** (a crisis every ~7 ticks).
      Target: ~6× calmer crises + much rarer political churn. Result: **crises
      141→~43/seed** (a crisis every ~23 ticks; headlines 0.63→0.16/tick), world stays dynamic
      (7/8 active at t1000, wars+coups still fire). Two of the changes were genuine CORRECTNESS bugs
      the M10+ trade model exposed:
      - **Flow-vs-stock shortage bug (the big one), fixed in BOTH `stability.py` and `inflation.py`:**
        `shortage = grain_output < grain_need` flagged every structural food IMPORTER — a country
        well fed through trade — as "in shortage" every tick. In stability that meant a permanent
        penalty AND no recovery (recovery required `not shortage`); in inflation a permanent +0.3/tick
        bump that, against the 0.02 decay, pulled inflation toward ~17%. Net: mean inflation ran 9.1%
        (baseline 2%), 44% of country-ticks sat above the inflation-crisis line, and 42% below the
        UNREST line — the real engine of the flood. Now STOCK-based (the FAMINE_WARNING line): mean
        inflation 9.1→4.7%, stability<35 42→18%.
      - **Stability recovery gate widened:** recovery now applies whenever a country is not actively
        penalised (no war / no food-stock shortage / inflation below the crisis line), not only in the
        old near-unreachable `inflation < 4` window. Invariant preserved: recovery (0.22) < WAR_PENALTY
        (0.3), so wars still drain stability monotonically (M7.4).
      - **Tuning:** `ELECTION_INTERVAL_TICKS` 90→500 (quarterly→~1.4yr terms), `COUP_BASE_P`
        0.01→0.0014 (~7×), `drama_multiplier` default 1.0→0.4 (global exogenous scaler; the settings
        overlay still lets a viewer crank it up), widened UNREST/CIVIL_WAR_RISK/INFLATION_CRISIS/
        commodity-shortage recovery margins (announce once per episode, not per hover).
      - **Coverage test** now runs at `drama_multiplier=1.0` (reachability is a mechanics property
        measured at normal drama; the calm default must not read as a dead catalog) — PORT_CLOSURE/
        TRADE_HALT confirmed reachable there.

      Landed ~3.3× (not the full 6×): the remaining crises are now GENUINE economic/political events
      in a healthy world, and pushing further would either freeze the world or deviate from spec crisis
      thresholds, so the last 2× is left as a judgment call on feel. Golden 598→316 lines.

## Post-M15 extension — real tariffs and causal impact inspector (2026-07-24)

- [x] **Material trade settlement and tariff policy.** Shipment dispatch now records untaxed
      border value, buyer sector, duty, total buyer cost, and exporter proceeds separately.
      Households fund food/consumer imports; corporates fund all other imports; duty reaches the
      importing treasury; arrival pays exporter corporates only the pre-duty proceeds. Tariffs are
      bilateral/commodity policy state with wildcard specificity, bloc exemption, organic hostile
      imposition, retaliation, thaw/alliance repeal, structural replay, and typed causal links.
      Shipment arrival/loss is causally linked to dispatch and shipment creation/removal is a
      normalized structural effect.
- [x] **Multi-cause event DAG and normalized effects.** Legacy `parent_id` remains protocol-
      compatible while typed additional trigger/contributor/context links form the canonical
      sorted/deduplicated DAG. Threshold crises attribute up to three bounded recent state
      contributors. Severity is unchanged by cause count. Generic ledger/stat mutations and major
      structural handlers expose normalized target/metric/delta/before/after/unit records.
- [x] **Deduplicated impact analysis and bridge/UI.** `engine/impact.py` separates immediate from
      descendant effects, deduplicates shared descendants by event id, totals numeric effects, and
      supports tick horizons. `eventImpact` adds causes, ancestors, descendants, immediate effects,
      downstream effects, cumulative totals, and an honest aligned-fork horizon difference. Prime
      or unavailable baselines say why instead of pretending descendant history is a
      counterfactual. The web inspector renders WHY / IMMEDIATE / DOWNSTREAM / CUMULATIVE /
      HORIZON sections; DAG trace nodes appear once and convergent nodes show a shared-cause badge.
- **Measured calibration before acceptance:** across seeds 1, 17, and 1337 at 500 ticks (8
  countries), 100% of 10,893–12,638 shipments had nonzero settlement; aggregate pre-duty trade
  value was 7.89%–19.66% of tax revenue, while all sampled household/corporate balances remained
  positive. Across seeds 1–10 at 1000 ticks, organic tariffs appeared in 5/10 worlds (mean 1.9
  impositions; repeal and retained-policy cases both observed). This supports the intended
  material-but-non-dominant scale without claiming exhaustive economic calibration.
- **Validation boundary:** protocol/static JS and Node syntax checks cover the shipped web contract;
  no browser automation dependency exists, so no real-browser interaction claim is made.

- **Final validation:** the monolithic suite exercised all 554 tests and produced 553 passes with
  only the intentionally stale M15 golden failing. The reviewed replacement was generated twice
  byte-identically, contains 161 rendered lines with no None/null/template tokens, and has SHA-256
  `3b70f62d094c1e575ad7275610662b4c5913382b8dee0a1c24fdb993ac0d613a`; both golden tests then
  passed, including their own two-run determinism assertion. The affected-system set passed 107
  tests; bridge/static checks passed 13 tests; Ruff, mypy (62 source files), Python compilation,
  both import-linter contracts, `git diff --check`, and Node syntax checks all passed. The entire
  expensive suite was not rerun a second time after replacing only the fixture; no non-golden test
  failed in the complete run.


## Post-M16 transport pacing — seven-tick minimum (2026-07-25)

- Every engine-created sea, air, and rail shipment now has
  `arrive_tick - depart_tick >= MIN_TRANSPORT_TICKS == 7`.
- Distance, carrier speed, and communications friction still extend transit beyond the floor.
- The recorded `arrive_tick` remains authoritative and replay-safe; bridge progress therefore
  advances over at least seven simulation intervals instead of collapsing into one tick.
- Carrier lifecycle and protocol progress regressions cover ticks 0/7 through 7/7.
- **Validation:** 84 affected logistics, trade, tariff, scrub, bridge projection, and static-contract
  tests passed. Ruff, strict engine mypy, and Python compilation passed. The reviewed 1000-tick
  golden was generated twice byte-identically (165 lines) and accepted at SHA-256
  `cace259499f75bad59b98c94394c7299e017119cc540971e4c8cf7731d03bf2b`; both golden tests then
  passed, including their two-run in-process determinism assertion.


## Post-M16 trade observability — stable rosters and Trade Operations (2026-07-25)

- Route rosters now use immutable dispatch-event/id order. Progress never participates in sorting,
  so a row keeps one shipment identity for its active lifetime.
- Arrived and lost shipments are reconstructed from their terminal event and parent dispatch. Lost
  rows preserve their dispatch position for event ages 0–7 and disappear at age 8. Arrival is the
  sole ordering exception: an arrived row moves to the bottom for ages 0–3 and disappears at age 4.
  The projection remains exact under live play, pause, scrub, replay, and fork focus; terminal
  movers are not redrawn.
- `worldObjects.tradeStats` supplies unit-safe active counts, quantities, distances, exposure,
  recent outcomes, carrier summaries, and commodity summaries without summing unlike currencies.
- The global Trade Operations overlay (`V` / `⇄ TRADE`) exposes all active shipments plus retained
  outcomes with filters, search, pagination, route/cargo/progress/ETA/risk/condition/settlement
  detail, responsive layouts, and exact shipment selection.


## Post-M16 investigation UX — live Trade rows and chart causes (2026-07-25)

- Trade Operations now rerenders on every authoritative frame even while a filter/search control is
  focused, restoring control focus, text selection, and overlay scroll after each refresh. Row
  selection caches normalized details and closes the overlay only after inspector selection succeeds.
- Country detail responses are timeline-qualified and include full authoritative country-involving
  chart event summaries in addition to exact ≤240-point statistic series.
- Stability, inflation, GDP, and exchange charts share a synchronized pointer/keyboard cursor. Hover
  reports the sampled tick, all values and deltas, and events in the interval since the prior sample.
- PRIME chart jumps scrub to the selected tick and open the strongest recorded interval event for
  impact/trace investigation. Fork charts remain event-inspectable without pretending PRIME scrub is
  fork history. Event-free intervals state that no cause is inferred.


## Post-M16 bounded history and memory architecture (2026-07-28)

- Reproduced server RSS growth without browser/payload activity: 30.0 MiB at t0, 128.9 MiB at
  t100, 268.4 MiB at t200, and 460.5 MiB at t300. Snapshot logs held 119,615 cumulative event
  copies at t300 versus 34,189 authoritative events; disabling extra snapshots yielded 100.3 MiB.
- Replaced the in-memory event list with a file-backed stdlib SQLite store while preserving
  sequential IDs, ordered/reversed iteration, indexed lookup, truncate/reuse, replay, and golden
  determinism. Events are finalized once per transaction and hot hydration is capped at 512 rows.
- World snapshots now carry lightweight immutable history boundaries. Forks share database prefixes
  and append isolated suffixes; drop/adopt/restart reclaim appropriate rows. RNG states and exact
  country-stat observations also moved to SQLite-backed bounded-cache mappings.
- Added normalized causal edge, country/effect, and ledger indexes. Per-tick threshold, inflation,
  and tariff lookups use selective SQL rather than repeatedly hydrating full history.
- Country dossier payloads no longer grow with lifetime events. Hover fetches one sampled interval,
  returns an exact count plus ≤6 strongest authoritative summaries, and caches only 24 intervals.
  Frontend per-timeline event summaries cap at 1,000 and ribbon markers at 2,000; dropped fork caches
  are released.
- Acceptance: byte-identical 1,000-tick golden output and repeat-run determinism; complete bridge
  suite 47 passed; property suite 15 passed; unit modules passed in two bounded groups (310 + 310).
  Final seed-1337 RSS stays nearly flat: 32.3 MiB at t0, 40.1 MiB at t300, and 42.3 MiB at
  t1000 (formerly 460.5 MiB at only t300). At t1000 the compressed database is 145.6 MiB,
  the dossier is 38.3 KiB with zero lifetime chart events, and one interval response is 2.7 KiB
  for six summaries plus an exact total. Session close removes the database, WAL, and SHM files.


## Engine — god-mode structural effects, convoy reporting, stability equilibria (2026-09-11)

### God mode stops being a headline generator

Five of the palette's interventions fired their declarative stat deltas, cascaded normally,
and changed nothing structural. A war declared by hand left `at_war_with` empty; an
alliance formed no bloc; an embargo blocked no lane; "split a country in two" — advertised
in the README's two-minute tour — docked the parent five points of stability and created
nobody. The original notes deferring this were right that a per-intervention bolt-on would
be wrong. The answer was that the organic kinds already carry exactly the handlers needed,
so each intervention now shares its twin's structural and replay handler. An intervened war
is indistinguishable in state from one the world started itself, and `world_at`
reconstructs both the same way.

- `INTERVENE_SECEDE` creates a real country (`systems/secession.py`). One handler decides
  every number once — identity, capital, the quarter of the people, output, stocks and
  assets it takes, its relations — and records the result, so the RNG-free replay handler
  rebuilds the same state when history is scrubbed, forked or adopted. Placement keeps the
  no-overlap invariant: a parent holding conquered territory lets the farthest of it go,
  otherwise a capital is sampled near the parent's and must clear the current minimum
  separation from every marker. No room, roster full, parent too small or not active: the
  secession fizzles and records why, rather than producing an overlap.
- Opening balances cross the two FX desks, so a new state's money is issued against the old
  and every currency still conserves with its desk counted.
- The asset-class interventions mutate the class they name. A blockade caps the target's
  naval fleet below every failure threshold; destroying a satellite removes a unit; the
  repair lifts every class back to working order. Each records its realised change after
  clamping and replays exactly those numbers.
- Direct relation edits now record the war they end, so scrubbing past a god-mode peace no
  longer resurrects the war.
- `Multiverse.fork(intervention=...)` replays the recorded event's structural half as well
  as its ledger and stat deltas. Not reachable from the browser (the bridge forks with no
  intervention and then intervenes on the fork's own world), but the API was silently lossy.
- The god-mode mint is sized in ticks of national output rather than a fixed 1,000,000
  minor units, which was about 0.01% of a typical treasury — a mint nobody could see.
  `PoolTransfer.gdp_ticks` scales any spec's amount the same way.

### Lost convoys became a report instead of a manifest

One `SHIPMENT_LOST` line per sinking put 34 of 393 feed lines on individual convoys in a
war-heavy seed. `LogisticsSystem` now folds every twelve ticks' sinkings into one
`CONVOY_LOSSES` report per destination, carrying the count, the season's running total, the
most-lost cargo, the full manifest and whether relief was among it. A destination with a
single loss gets no report; it still counts toward a later one's season total. The report is
derived entirely from the event log, records no money and no stat change — so `world_at`
replays it as a no-op with no handler — and its parent is the first sinking it speaks for,
with the rest as causes, so the trace reaches every convoy behind the headline.

### Stability had no equilibrium but the ceiling

Measured on seed 1337 at the shipped drama of 0.4, over 700 ticks: **55.6% of all
country-ticks were spent pinned at stability 100.0**, and 93.6% were spent in the
"recovering" state, climbing. `UNREST` fired four times in 5,600 country-ticks.

The cause was structural rather than a tuning value. `StabilitySystem` subtracted for high
inflation, food shortage and war, and otherwise added a flat +0.22 — then clamped at 100.
Nothing pulled stability down in ordinary conditions, and no country had an equilibrium of
its own, so the clamp was the only attractor in the model. A country reached it in
`(100 - genesis_draw) / 0.22` ticks: 68 from a draw of 85, 273 from a draw of 40. The
measured arrival ticks back-solve to within 0.2 of each country's genesis draw
(LIE@82 → 82.0 vs an actual 82.0; GUC@140 → 69.2 vs 69.2). No value of the bonus changes
that destination; a smaller one only delays it.

Two of the three penalties could not fire in a settled world. Inflation reverts to
`2 + 2.5 x pct_minted` per tick, which at the observed minting rate settles near 2.4%
against a stability gate of 8.0 — fifteen times the steady-state rate. Shortage is
stock-gated and rare once trade works. War was the only live downward pressure.

`starting_stability_range` (40, 85) is the only per-country stability variation the world
generates, and the ratchet destroyed all of it inside 273 ticks: the draw decided how long
a country took to reach the same value as everyone else, and nothing else. A
world-generation parameter whose entire effect is an arrival time is doing no work.

Stability now reverts toward a target the country carries, closing 2% of the gap per tick —
deliberately the same rate and the same idiom as inflation's pull toward its baseline. The
target is the genesis draw, persisted as `Country.base_stability`, shifted by how well the
country treats its people and capped below 100 so the ceiling is approached and never sat
on. Nations settle at different levels; a shock heals in proportion to its size.

A country at war has no equilibrium: its target is zero, so the delta is negative at every
stability above zero and monotonic war drain is structural rather than resting on the
arithmetic accident of 0.22 < 0.3. The additive war penalty survives that change because
reversion alone is geometric, and geometric decay never arrives — without it, stability
would pass under the unrest line and then approach zero forever, leaving
`OCCUPATION_BEGIN`'s `stability <= 0.0` gate unreachable and quietly deleting conquest from
the world. With it, a war drains 100 to 0 in 101 ticks against the old 333. The test for
that asserts the limit and not the direction, because every direction-asserting test passes
under both rules.

**What this fixes and what it does not.** Mean reversion cures the ceiling pile-up, which is
what was observed. It does not by itself cure flatness: at equilibrium each country sits
still at its own target instead of still at 100 — eight constants instead of one. Whether
the world reads as alive depends on shocks being frequent enough to knock countries off
target, and on the targets themselves moving.

### What mean reversion actually bought, measured the same way

Same harness, same seed, same 700 ticks, against the committed change:

| | before | after |
|---|---|---|
| country-ticks at stability 100 | 3,115 / 5,600 (55.6%) | **0 / 5,600 (0.0%)** |
| final spread | 15.9 points (made by one war) | 32.9 points (genesis temperaments) |
| second-half sd, non-war countries | 0.00 0.00 0.08 0.37 0.61 0.74 | 0.00 0.23 0.46 0.97 1.33 1.82 |
| severity>=2 crises per 1000 ticks | 21 | 54 |
| UNREST / CIVIL_WAR_RISK / CRACKDOWN | 4 / 0 / 0 | 6 / 2 / 2 |
| SECESSION / OCCUPATION_BEGIN / ANNEXATION | 0 / 0 / 0 | 2 / 1 / 1 |
| PEACE | 2 | **0** |

The ceiling pile-up is gone outright, and five of the six crisis classes that could not fire
now do. Two results are worse than that summary suggests, and both are recorded here rather
than left for someone to rediscover:

**Motion is still poor.** Five of eight countries have a second-half standard deviation
under two points, and one is still bit-identical for 350 consecutive ticks -- at 71.2 now
instead of 100.0. The median non-war country went from about 0.5 to about 0.9. Mean
reversion cured the pile-up and did not cure flatness: at equilibrium a country with nothing
happening to it has no reason to move, so the world is now eight constants instead of one.
Making the world feel alive needs shocks frequent enough to displace countries, and targets
that move during a country's life -- neither of which this change provides.

**Conquest crowded out peace, and the fix moved the motion problem too.** Before, both wars
ended by treaty and nobody was occupied; after mean reversion, no war ended by treaty and
one ended in annexation. The cause was arithmetic: `PEACE_BASE_P` was a flat 0.003 per tick,
expecting a ~333-tick wait, while the new drain reaches the occupation gate in 101. Making
conquest reachable had made it the only exit, and a world where every war ends with a
country erased is a different flatness from one where no war ends.

Raising it to 0.010 -- the same 3.3x the drain gained, and the value at which the expected
wait equals the drain time -- restores two live exits, and it did something the stability
change alone could not:

| | baseline | reversion | reversion + peace |
|---|---|---|---|
| WAR_DECLARED / PEACE / OCCUPATION / ANNEXATION | 2 / 2 / 0 / 0 | 2 / 0 / 1 / 1 | **6 / 3 / 1 / 0** |
| countries with second-half sd > 2 | 2 (one stuck war) | 2 (the same war) | **4** |
| final spread | 15.9 (war artifact) | 32.9 | **63.2** |
| severity>=2 crises per 1000 ticks | 21 | 54 | 34 |

Wars that RESOLVE are what move the world: six wars starting, ending and starting elsewhere
displace four countries from their equilibria, where two wars that never end displaced only
the two nations fighting them. The crisis rate fell rather than rose, because resolving a
war quickly means fewer country-ticks parked at zero stability generating threshold events.

What remains is the same gap in a new seat: one country is still exactly frozen and three
barely move -- whichever nations no war happens to touch. A country that nothing happens to
still has no reason to change, which is the temperament-moves work below, not a tuning
value.

### A test that pins a count for a seed is a golden master wearing a different name

`tests/unit/test_history_storage.py` asserts seed 1337's event totals at its snapshot
boundaries. It is a storage test -- its subject is that periodic snapshots share one
database file, hydrate no events, keep the live cache bounded, and hold RSS at t200 to 41.9
MiB against a pre-migration 268.4 -- so no reasonable reading of "the files my change
affects" reaches it, and a CPU budget that forbids running the whole suite makes that
reading the working rule. It went red on merged main and stayed red, because every
behaviour change in this push moves the number it pins.

The counts are fixtures rather than claims, so re-pinning them after an intended change is
the same act as regenerating the golden, and none of the architectural assertions moved.
But the general point is worth more than the repair: a behaviour change has to go looking
for every pinned count, not only the one that lives in `tests/golden/`. This is the only
other one in the suite.

### A rule worth carrying forward

When a change replaces a linear rule with a proportional one, every threshold that rule has
to CROSS becomes a new question, because proportional rules approach and linear rules
arrive. Three dead gates surfaced in one day and all three had the same signature — tests
asserting a DIRECTION where the product needs a LIMIT: `UNREST_THRESHOLD` at 35 under a
universal ceiling, an inflation trigger at 8% against an analytic steady state of 2.4%, and
`OCCUPATION_BEGIN`'s `stability <= 0.0` under a decay that never arrives. Wherever a
threshold reads `<= 0.0` or `>= X`, there should be a test asserting it is reachable, with
an explicit failure if the loop finishes without crossing — otherwise "never arrived"
passes vacuously.

A second one, from the same night, about measuring rather than modelling. A baseline run
was queued behind the machine-wide job lock, and the engine was then patched while that job
was still WAITING rather than executing — so it would have started afterwards, loaded the
new code, and reported the new behaviour under the label "before". The numbers would have
been internally consistent and would have shown the change working. The fix is two-part:
measure a baseline against a pristine `git archive HEAD` tree rather than against a working
tree that is about to change, and have the measuring script print which rule it actually
loaded rather than the label it was invoked with. (That harness is also the one `meddler run
--headless` uses -- `generate_world(seed, WorldSettings())` and a bare `tickloop.tick` loop
-- so these figures are directly comparable to the generator behind the golden master.) A label is an assertion about a run;
reading the loaded code is evidence about it.

### What the ratchet actually cost, measured

Seed 1337, 700 ticks, shipped drama 0.4, before the change:

- 3,115 of 5,600 country-ticks (55.6%) spent pinned at stability 100.0.
- Second-half standard deviation per country: 0.00, 0.00, 0.08, 0.37, 0.61, 0.74, 19.8,
  20.1. Two nations were bit-identical for 350 consecutive ticks. The only two that moved
  were the two in the single war.
- The final spread of 15.9 points is manufactured entirely by that war. Without it, six
  countries sit inside 3.5 points of each other and none of them is moving. Spread is not
  the measurement that matters here; motion is.
- Six crisis classes fired ZERO times in 5,600 country-ticks: CIVIL_WAR_RISK, REVOLUTION,
  CRACKDOWN, SECESSION, OCCUPATION_BEGIN, ANNEXATION. UNREST fired four times, COUP once.
  **That is a fact about this seed, not about the engine.** An independent clean run of seed
  7 fires five of those six (CIVIL_WAR_RISK 7, CRACKDOWN 5, SECESSION 2, OCCUPATION_BEGIN 2,
  REVOLUTION 1; only ANNEXATION stays at zero) and is broadly more turbulent -- four wars
  against 1337's two. So the low-stability branch is reachable; on a quiet seed it simply is
  not reached. The case for mean reversion rests on the ceiling pile-up and the frozen
  standard deviations above, which are properties of the rule rather than of a seed, and
  not on this list.
- Severity>=2 crises: 15 in 700 ticks, i.e. 21 per 1000. (An earlier note recorded ~43 per
  seed after the 2026-07-22 pacing pass; 21 is what reproduces, so that is the figure any
  later pacing work should be judged against.)
- Two wars declared, both ended in peace; no country was occupied or annexed. At 0.3/tick a
  war needs 333 ticks to reach the occupation gate, which is longer than either war lasted,
  so conquest was never an available outcome.

Every figure above is from one 700-tick run driving `tickloop.tick` with default
`WorldSettings` (8 countries, drama 0.4) against a fixed archive of the tree, not a working
copy. Both halves of that sentence are load-bearing. A second run briefly disagreed about
how those two wars ended, and the cause was that it had been pointed at a working directory
mid-edit: it loaded a half-written change and so described a state that was never committed
and will never exist again. A seed names a world only once the settings and the tree are
also pinned, so a measurement should print the settings it constructed and the code it
loaded rather than the label it was invoked with.

### A country died, and the world put it back

The first thousand-tick run after occupation became organically reachable crashed at t777:
`FXSystem` divided by a country's food need, which scales with population, and the
population was zero. Three commits came out of it and only the third is the cause, which is
worth recording in order because the first two are the kind that look like fixes.

**The guard.** The division is now guarded, matching what `systems/trade.py` and
`systems/war.py` already do with a zero need — a nation with no mouths to feed has no food
trade imbalance. Three sites divide by a commodity need; two already guarded and this was
the third. Worth knowing that `assets.upkeep_cost` divides by `expected_units(population)`
and is safe only because `infra_counts_for_population` floors every class with `max(1, …)`:
it is one edit to those floors away from being the identical bug.

**The attrition rule.** Population loss was a flat subtraction floored at zero, so it
*arrived* there — the mirror of the war-drain lesson below, on the side where arriving is
the defect rather than the requirement. Losses now take a share of whoever is left, so zero
is approached and never reached, and ordinary play is untouched (0.3M off a 40M nation is
far below the cap and still costs exactly 0.3M). This removed a real class of arriving-at-
zero. It did not remove this one: measured afterwards, the country still sat at exactly
zero, because nothing had been subtracting it.

**The cause.** `ANNEXATION` transfers the annexed country's whole population to the annexer
and leaves it at zero, which is correct — the people are somebody else's now. But
`OCCUPATION_END` restored its country to ACTIVE without checking what it currently was, and
an occupation can end while the country is already ANNEXED, because annexation is what an
occupation turns into. On seed 1337 Numoania was folded into Bolaania at t770 and returned
to the roster at t776: a live nation with no people, no output and no needs, which went on
to suffer shortages, warm its relations with a neighbour, and hold an election at t1000 with
nobody left to vote. A garrison withdrawing cannot undo a border being redrawn. Both the
live and the replay handler take the same guard; guarding one only would have had `world_at`
resurrect a country the live tick left annexed, and any fork off that reconstruction would
have diverged from prime.

Measured at t1000 afterwards: seven ACTIVE countries, the smallest at 10.3M people, none at
zero, and Numoania correctly ANNEXED with its 24.4M counted in Bolaania's 64.6M.

**But the annexed country still makes news, and that is a wider gap than the one just
closed.** Counting `CountryStatus` references per system: `politics.py` has sixteen, and
`exogenous.py`, `thresholds.py`, `stability.py`, `inflation.py`, `infrastructure.py` and
`relations.py` have none at all. `exogenous.run` draws its eligible pool as
`sorted(c.code for c in world.countries)` with no filter, and the others iterate every
country. So an annexed nation keeps suffering wildfires and famines, keeps having its
stability and inflation updated, and keeps warming its relations -- the published golden
contains Numoania holding an election at t1000, thirty ticks after being absorbed. The
resurrection fix was necessary and is not sufficient: "annexed" is a label that almost
nothing reads. Closing it properly means one shared "is this country still in the world"
predicate at the top of six system loops, with a test per system rather than one aggregate
test, since six independent omissions is what produced this.

**What made this hard to see.** `CountryStatus.DISSOLVED` exists and is already honoured in
eight places, including the two that decide whether a country reaches the client's roster
and the globe. That made "extinction is unimplemented" feel like the diagnosis and finishing
it feel like the fix. It was neither. The presence of scaffolding is not evidence that the
building was a good idea, and a half-built state is a strong pull toward completing it
rather than asking whether the thing it would catch should ever have happened.

### Eight consumers found one at a time means the abstraction is wrong

`Country.in_world` closed six systems, and the regenerated golden still showed Numoania voting
at t1000. Two more consumers were behind it. `politics.run` iterated every country: of its five
paths only elections and coups could reach an annexed nation, because occupation, annexation,
liberation and peace already require the country to be ACTIVE or OCCUPIED — so it takes two
guards, not a blanket skip that would also have suppressed the events recording a country's
removal. And `ConsequenceSystem` fired a queued consequence without asking whether its target
still existed: a `LIBERATION_WAR` at t764 queued `OCCUPATION_END`, Numoania was annexed at t770,
and at t776 the feed reported a quiet celebration of the occupation's end. The handler already
refused to change state; the headline printed anyway. Due entries aimed at a country that has
left the world are now consumed without firing, and the candidate pools in `cascade.py` (ally,
random, worst relation, and the any/all conditions) no longer include such countries. Replay
needs no matching guard: a consequence that never fires records no event, so `world_at` has
nothing to reconstruct differently. Each guard has its own test, and each was checked by
forcing it off and watching its tests fail.

Then every iteration over `world.countries` and every candidate-pool construction in
`meddler/engine/` was audited: 83 sites, 21 guarded, 19 safe by a status precondition, 27
bookkeeping where including a gone country is correct, and **16 still unguarded**. The ones hit
in ordinary play:

- `production.py` and `fiscal.py` mint GDP and collect tax for an annexed country every tick —
  annexation zeroes population but not `base_gdp`, so the ledger keeps growing a nation with no
  people. It prints no headline, which is why the golden never showed it.
- `diplomacy._propagation`, `diplomacy._strain` and `_break_alliance`: annexation never removes a
  country from its bloc, so a dead member still takes relation shifts and can break an alliance.
- `politics.run` path 4 names `occupied_by` as the annexer or liberation enemy without checking
  it. If an occupier is itself annexed, its former conquest can be annexed *into a country that
  no longer exists*. `OCCUPATION_END` is the only thing that clears `occupied_by`.
- `tariffs._repeals` and `_retaliation` can act on a policy against a gone country; narrow.

Also unguarded but out of reach today: shipments in `logistics.py` (transit is far shorter than
the ninety-tick occupation annexation needs) and god-mode targets (the client roster hides gone
countries, but the bridge does not refuse them).

**Recommendation for the next milestone.** Stop adding the check site by site. The default
iteration should be the safe one: an accessor such as `world.living_countries()` that yields only
countries still in the world, with the full roster behind an explicitly named call for the
bookkeeping sites that need it, and annexation detaching a country from its bloc, its tariffs
and anything it occupies. Then the sixteen sites above become a mechanical migration, and a
seventeenth cannot be written by accident. Not built here — the publish branch takes only the
fixes whose absence was visible in the published artifact.

### Known gaps recorded rather than closed

- The four social stats (`civil_rights`, `press_freedom`, `education`, `health`) are
  identical constants for every country at genesis, so a fresh world's dossiers differ only
  in economics. They diverge through events, slowly.
- Temperament never moves except through a god edit, so a nation no war touches has no
  reason to change: one country still shows a second-half standard deviation of exactly
  zero. `COUP`, `LEADER_CHANGE`, `ELECTION` and `REVOLUTION` should plausibly shift where a
  nation settles. Any such mutation must ride a recorded delta, or `world_at` rebuilds the
  wrong target and a fork off that reconstruction diverges from prime. This is the named
  next step for the flatness work, and the measurement above is its justification.
- Sixteen engine sites still simulated or selected an annexed country. Closed in the
  living-countries section below, except shipments in transit and god-mode targets.
- `REVOLUTION` is the one crisis kind that still never fires on either measured seed.
- Organic `SECESSION` remains declarative. Routing it through the structural handler would
  grow the roster in ordinary play, which is a pacing decision rather than a wiring one.

## 2026-09-12 — Living countries: one accessor instead of a guard per system

`World.living_countries()` returns the countries still in the world, sorted by code, and every
per-country system now iterates it. The full roster stays on `World.countries`, because replay,
diffs, snapshots and the client's roster indices need departed countries too. What stops the
next system from repeating the old bug is `tests/unit/test_living_countries.py`: it walks the
engine's source and fails on any read of `.countries` outside a short allowlist of bookkeeping
sites, each listed with its reason. A new system cannot iterate the dead without someone
writing down why.

Done in three steps, so each could be checked on its own:

1. **Accessor, plus the sites that were already guarded or filtered to ACTIVE.** The golden
   master passed unchanged, which shows the migration itself changed no behaviour.
2. **Production and fiscal.** These minted GDP and collected tax for an annexed country every
   tick, because annexation zeroes population but not `base_gdp`.
3. **Annexation detaches the country.** It leaves its bloc (a bloc left with fewer than two
   members dissolves), and every tariff and embargo naming it is dropped. A country it was
   occupying passes to the annexer, which took its territory; if that country is the annexer
   itself, the occupation ends. This closes the bloc-diplomacy, tariff and path-4 occupier
   sites, which read those structures rather than the roster. Detachments are
   payload-recorded and mirrored by the replay handler, so an ordinary annexation's payload is
   unchanged.

**Verified.** New tests: one per migrated system, plus per-detachment tests and a
replay-parity test. Each was forced off to check it can fail:
- Making the accessor return every country fails 17 tests.
- Disabling the live detach fails 7 of 8 detach tests.
- Disabling the replay detach fails the parity test.
- Adding a raw roster loop to a system fails the scan.

The golden was regenerated twice, under `PYTHONHASHSEED` 1 and 4242, and the runs were
byte-identical. It still has 129 lines, and the first changed line is t889, after the t770
annexation. Every change is to a headline's wording; tick, country and event kind are the same.
Reading the file confirms Numoania's last line is its annexation. The golden, history-storage
and determinism/`world_at` property tests pass.

**Still open.** A shipment already in transit to or from an annexed country is delivered. That
is deliberate, and not reachable in ordinary play. God-mode intervention still accepts a
departed country as a target. `_secede` counts departed countries against `max_countries`. The
scan catches attribute reads only, so `getattr(world, "countries")` would get past it.

## 2026-09-12 — The t362 stall was a query plan, not a lookback

On seed 1337, tick 362 took 2.5–3.3 s against a ~40 ms neighbour. All of it was one call to
`EventLog.recent_effect_events` from `tariffs._recent_relation_causes`, which passes no
`start_tick`. It is the only such call in the run: t362 is the one tariff imposed for
protection, and the walls at t376, t904 and t944 are retaliations, which cite the policy
they answer and never search history.

`flush()` accounted for 1 ms. The query started from `events`, so SQLite walked that table's
primary key across all 42,532 events, probing each one for a matching effect, even though
only 364 effect rows matched. Starting the same predicate from `event_effects` lets the
covering `(target, metric)` index drive it. That version returns the same ids, and t362 now
takes 37 ms. Narrowing the lookback was rejected because it would have changed which causes
are found.

A hard `INDEXED BY` was tried first and dropped. Measured at t1000 across real call shapes,
it made two-metric threshold lookups about 30 times slower (0.3 ms to 11 ms). The unforced
query is fastest or tied in all 16 cases, and every shape returned the same ids.

`tests/unit/test_recent_effect_events.py` checks the results against a brute-force scan of
the log across signatures, windows, directions and exclusions. It also counts SQLite VM steps
for a full-history lookup; the old query fails that bound by a wide margin. The golden master
is byte-identical.

## 2026-09-13 — Departed countries: god mode, the roster cap, and short history reads

Two of the items left open above are now closed, along with a slow read measured while checking
an older indexing idea. The god-mode fix also covers the direct edits, which had the same gap.

**God mode refuses a departed country.** `god.intervene` raises `InterventionError` ("XYZ
is no longer in the world") when either target has been annexed or dissolved. So do
`god_edit`, `god_relation` and `god_peace`, which had the same gap. The check runs against the
world being acted on, so a fork taken before an annexation may still target that country. The
bridge needed no new code: `_intervene` already drops the fork and answers with a warning toast,
and `handle` does the same for the direct edits. The client never offered these countries in
the first place, because the god-mode target grid lists only codes present in the focused
timeline's stats (`countryGridHTML`), and `all_stats` is built from `active_countries`.

**A departed country no longer holds a secession slot.** `_secede` compared the full roster
with `max_countries`, so each annexation permanently used up a slot. Nothing is sized by the
cap. Client colours key off the roster ordinal and cycle through a second palette past the
first eight. Placement already used the living count. The cap now counts living countries, and
the new name and code must still differ from every country's, departed ones included.

**Short tick windows read through the tick index.** `events_between` and
`events_of_kinds_between` bound each segment by id, and a segment's ids cover its whole branch,
so the planner walked every event in the branch whatever the tick window. On seed 1337 at
t1000 with default settings (116k events), the bridge's one-tick chronicle read took 36 ms per
frame. A ten-tick read of the globe's three shipment kinds took about 40 ms. Forcing
`events_tick` cut those to 0.8 ms and 1.9 ms. The two plans tie near 300 ticks, and past that
the id sort makes the index slower: 0.25 s against 0.58 s for the whole history. So the hint applies only
to windows of 100 ticks or fewer, and the annals ranking's 500-tick pass keeps the old plan.
Every window compared returned the same ids under both plans.
`tests/unit/test_tick_window_reads.py` checks results against a scan of the log on both sides
of the threshold and across a fork's two segments. It also checks that a short read costs the
same number of SQLite VM steps after the history doubles. Without the hint that count doubled
with the log, about four steps per event.

**Verified.** Every new test was first run against the unfixed code and failed there: eight
for god mode and the roster cap, and the cost test for the tick index. The golden master
passes unchanged with all three changes applied. mypy is clean on the engine and the bridge.

**The sixteen sites.** The audit's full list of sixteen was never written down. The notes above
name ten of them. Verdicts on those ten:
- `production.py` and `fiscal.py`: guarded, since both iterate `living_countries()`.
- `diplomacy._propagation`, `_strain`, `politics._break_alliance`: safe by precondition.
  Annexation removes the country from its bloc.
- `politics.run` path 4 (`occupied_by`): safe by precondition. Annexation passes or ends the
  occupations the annexed country held.
- `tariffs._repeals`, `_retaliation`: safe by precondition. Annexation drops every tariff and
  embargo that names the country.
- Shipments in `logistics.py`: deliberate. See below.
- God-mode targets: guarded, as described above.

Two preconditions were checked rather than assumed. No engine code ever assigns `DISSOLVED`,
so every dissolved-only path is unreachable. And annexation leaves no departed code in any
`at_war_with`: only an `OCCUPIED` country can be annexed, occupation clears both sides' war
lists, and `WAR_DECLARED` does nothing unless both sides are `ACTIVE`. The other six sites
were never named, so they have no individual verdict. If any of them loops over the roster, the
source scan in `test_living_countries.py` would catch it. It cannot catch one that reads a
country code stored in another structure, the way blocs and tariffs did.

**Shipments stay deliberate.** The rationale is in `logistics.run` and in the section above.
Trade dispatches only between `ACTIVE` countries, including relief shipments, and an annexation
needs at least ninety ticks of occupation first, which is far longer than any transit.

**Retraction check.** No published text (README, CHANGELOG, `docs/` outside this log, `web/`)
claims that particular crisis classes never fire. The "six crisis classes fired zero times"
line above was a measurement on the seeds sampled at the time, and the 2026-09-11 entry already
records that five of them now fire.

## 2026-09-13 — Assassination was starved, not dead

The catalog coverage run (20 seeds, 5000 ticks, drama 1.0) never saw an ASSASSINATION. The
kind's only organic parent was RESISTANCE_MOVEMENT, itself a 0.5 child of OCCUPATION_BEGIN.
Tracing the chain found no drop anywhere on it. In 3 seeds × 2000 ticks there were 7
occupations and 2 resistance movements, and 0 assassinations were queued. Over 2 seeds × 5000
ticks there were 6 occupations and 3 resistance movements. Resistance fires at depth 1, so the
0.2 edge rolls at 0.2 × 0.7 = 0.14. Occupation happens only a few times per seed before
conquest consolidates the map. The expected count was well under one per seed, and some of
those would be lost to annexation, which can come before the 40–120 tick chain finishes.
Forcing the rolls shows the edge itself works: a resistance movement queued an assassination,
and it fired at depth 1.

ASSASSINATION now also rolls as an exogenous root (p 0.0015, scaled by drama). That is how
PROPAGANDA_CAMPAIGN and CIVIL_RIGHTS_REFORM were made reachable. It is gated on the target's
stability being below 20, the same gate SECESSION uses. Some country is below 20 on about
three ticks in four, so the gate picks a plausible target and the probability keeps it rare.
The resistance edge stays, and the gate now applies to it at fire time too. No state was
added. Side effect: INTERVENE_CHAOS draws from the sorted exogenous kinds, so it can now
resolve to an assassination, and a given RNG state may pick a different kind.

Over 3 seeds × 2000 ticks, assassinations went from 0 to 12 at drama 1.0 (25 coups) and from
0 to 3 at the shipped drama of 0.4 (26 coups). In `tests/unit/test_assassination_root.py`,
the forced-roll root test fails before the change and passes after it. Two guards pass on
both sides: a stable world rolls no assassination, and the resistance edge still fires. The
extra roll each tick changes the golden master, which needs regenerating.

## 2026-09-13 — Every sunk convoy reaches the chronicle

**Before.** The review's 44% / 82% unreported figures were taken against the clock-anchored
window. This tree already counts since each destination's last report, and the residue is
smaller but still structural. Measured with `tickloop.tick` and a scan of the log: seed 1337,
1000 ticks: 11 `SHIPMENT_LOST`, 3 reports, **3 sinkings (27%) never reported**, convoy lines
3 of 129 headlines. Seed 1337, 2000 ticks: 14 lost, 4 reports, **4 unreported (29%)**, 4 of
223. Seed 7, 2000 ticks: 5 lost, 2 reports, **1 unreported (20%)**, 2 of 304. Every unreported
sinking was the only one its destination suffered that season, and every one had already
aged out of the lookback. `CONVOY_REPORT_MIN_LOSSES = 2` makes a lone sinking unreportable by
construction. Convoy spam is no longer a live risk: at `SEA_INTERDICTION_P = 0.0015` sinkings
run at under one per 100 ticks, and the 36% figure predates that value.

**Design.** `CONVOY_LOSSES` stays the only visible kind and stays derived from the log. At each
12-tick boundary, a destination's unreported sinkings are reported when any of these holds:
(a) there are at least two; (b) relief is among them; (c) the oldest has waited
`CONVOY_REPORT_MAX_WAIT_TICKS` (24). The lookback is at least `MAX_WAIT + INTERVAL` ticks,
whatever the calendar. A sinking at tick t is therefore still unreported, and still inside
the lookback, at the first boundary b >= t + 24. Since b - t <= 24 + 11, rule (c) fires
there. No sinking can be silent for more than 35 ticks, and none can age out. A report whose
count is one gets singular lines, with a relief form. Rate: at most one report per
destination per interval, as before. At today's sinking rates that is about one line per
isolated loss, or 1–3% of the feed. "First loss on a lane" and per-war tallies are made
redundant by (c), so neither was built.

**Replay.** Unchanged in kind. The report records no ledger, stat delta or structural effect,
so `world_at` replays it as a no-op. The rule reads only the log, and a fork's log is a branch
of prime's, so a fork reports prime's pending sinkings exactly as prime would. Golden output
changes (new lone-loss lines). Regenerating it is master's job.

**After.** Same runs. Seed 1337, 1000 ticks: 11 of 11 sinkings reported, 7 reports, convoy
lines 7 of 133 headlines (5.3%, up from 2.3%). Seed 1337, 2000 ticks: 14 of 14, 9 reports,
9 of 228 (3.9%). Seed 7, 2000 ticks: 5 of 5, 4 reports, 4 of 306 (1.3%). No unreported sinking
is older than 36 ticks, and none is still pending at run end. Every other headline is the
same event at the same tick. The golden diff shows the four added lines and, after t636,
reworded lines, because template picks hash the shifted event ids. The cost shows in a hot war:
seed 1337's t600–t696 sea war now carries seven convoy lines rather than three. One of them is
Liesia's lone loss at t636, which reached the wait twelve ticks before its next three losses
would have carried it. A longer wait would merge more, at the price of latency. That is a
feel call, and the constant is `CONVOY_REPORT_MAX_WAIT_TICKS`.
`test_a_lone_sinking_is_reported_once_it_has_waited` and
`test_every_sinking_is_reported_exactly_once_and_on_time` both fail with the wait rule
disabled, and `test_a_single_lost_convoy_reads_in_the_singular` covers the new lines.
