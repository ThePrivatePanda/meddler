# 60-second demo script

A scripted walkthrough for showing off the engine's real range in about a minute. Every
step below names the concrete bridge command or code path it exercises, verified against
`meddler/cli.py`, `meddler/bridge/commands.py`, `meddler/bridge/adapter.py`, and `web/app.js` —
nothing here is aspirational.

Everything below runs on the real engine. `meddler serve` hosts the frontend and the WebSocket
engine on one port, and the UI (`web/realengine.js`) connects to it automatically, so each step
drives the Python engine and names the exact bridge command it sends. (`?engine=mock` still runs
the approximate in-browser mock if you want a look without Python.) The same script works on the
hosted in-browser demo, which runs the same engine under Pyodide.

---

## 0. Start the engine (10s)

```sh
pip install -e .
meddler serve --seed 1337 --open
```

This starts `meddler/bridge/server.py`, which serves the app and the engine on one port at
`http://127.0.0.1:7677/`; `--open` opens it in your browser. The UI starts the
world running on open, so headlines flow in the CHRONICLE pane and country cards tick in WORLD
right away. (Press `space` or the pause button to hold it.)

*Say while it loads:* "Same seed, byte-identical log every time — that's not a claim, it's a CI
test (`test_golden_master_matches`)."

## 1. Watch a cascade, trace it to its root cause in ≤ 1 keypress (15s)

Let the world run until a crisis-glyph (`⚡`) headline appears in the feed — a consequence of
something further back, not a root event. Click it, then press **`t`**.

- Real command: `{ cmd: "trace", eventId: <id> }` → server replies `trace`
  (`bridge/commands.py`'s `_trace` → `adapter.trace_message` → `engine/trace.py`'s
  `trace(log, event_id)`).
- What it proves: `trace()` walks to the event's root cause and returns its whole causal
  component in DFS order — root at top, the clicked event marked. This is the literal
  `PROPOSAL.md` §2.3 success criterion: "every headline traceable to a root cause in ≤ 1
  keypress."

*Say while the trace tree renders:* "One key. Root cause at the top, every ripple in between,
severity glyphs and all."

## 2. Scrub back in time (10s)

Drag the timeline ribbon at the bottom, or press **`←`**/**`Shift+←`**, back to a tick before
the crisis — e.g. the root event's own tick.

- Real command: `{ cmd: "worldAt", tick: <t> }` → server replies `snapshot`
  (`bridge/commands.py`'s `_world_at` → `Timeline.world_at`, `meddler/engine/timeline.py`).
- What it proves: this is read-only reconstruction from the nearest snapshot plus replayed
  ledger/stat/structural deltas — no systems re-run, no RNG touched, cannot diverge. The world
  really did look exactly like this at that tick.

## 3. Fork with a god intervention, read the ΔWORLD diff (15s)

Press **`g`** to open the intervention palette, pick something visible and fast (e.g. a drought
or a mint), and confirm on the country the crisis started in.

- Real command: `{ cmd: "intervene", kind: <kind>, country: <code>, atTick: <t> }` → server
  replies `forkStarted` + `status` (`bridge/commands.py`'s `_intervene`). This is the only
  fork path in the real bridge — there is no separate bare "fork" command; forking always
  happens *through* an intervention (`session.multiverse.fork(at_tick=..., intervention=None)`
  followed immediately by `god.intervene(...)` on that fork), matching `PROPOSAL.md` §3.5: "An
  intervention at a past tick (or `f` at any tick) forks the timeline."
- The split view now shows prime (A) alongside the new fork (B); as both advance, the `frame`
  message carries a `diff` field — the `ΔWORLD` strip, from `engine/diff.py`'s
  `diff(world_a, world_b)`, comparing stability/inflation/gdp per country, only where they've
  actually diverged. Let it run a few ticks and read the strip out loud: "same seed, same dice —
  every number that's different is different *because of that one intervention*, nothing else."
  (This is provable, not just narrated: `test_fork_with_intervention_diverges_via_world_state_not_dice`
  in the test suite pins it down.)

*Note:* `godEdit`/`godRelation`/`godPeace` (direct stat edits) are a different, non-forking
mechanism — they mutate the *focused* timeline in place with no fork, per `PROPOSAL.md` §3.5.
Don't reach for those here; this step is specifically about the fork-and-diff story.

## 4. Adopt the fork (10s)

Click the fork tab's ★ ("adopt as reality") button.

- Real command: `{ cmd: "adoptFork", id: <fork_id> }` (`bridge/commands.py`'s `_adopt_fork`,
  `Multiverse.adopt_fork`). The fork becomes the new prime; any sibling forks dissolve.
- *Say as it happens:* "That's the whole loop — watch, ask why, rewind, change one thing,
  and choose which reality you keep."

---

## What this script leaves out

- `godEdit`/`godRelation`/`godPeace`, checkpoints, restart, the dossier charts, Trade Operations,
  and the Annals are all real, but they don't fit a tight 60-second cut. Good follow-up beats if
  there's time: `restart {tick}` ("begin anew": rewinds and reseeds, refused while forks exist),
  a direct `godEdit` on the focused fork from step 3 before adopting it, or the event inspector's
  IMPACT view on the intervention itself.
- Some interventions in the palette are weaker than their names. Intervening war, peace, an
  alliance, an embargo, or a secession applies a stability effect and cascades normally, but does
  not yet change war, bloc, or embargo state, or create a country (see `engine/god.py`). Droughts,
  mints, plagues, quakes, meteors, assassinations, blockades, and blackouts are the ones to demo.
