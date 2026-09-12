# Meddler — Frontend Design Guide

**Audience:** anyone changing `web/`.
**Status:** binding. The current UI was built to these rules; features that ignore them will
look bolted-on. When this guide and your instinct disagree, the guide wins. When the guide turns
out to be wrong, change the guide first, then the UI.

This is two things: (§A) the *system* — the exact tokens, semantics, and component recipes of
this UI; and (§B) the *method* — how the design decisions were made, so you can make new ones
that match. Most changes only need §A. Read §B before designing anything genuinely new.

---

## A. The system

### A1. The identity sentence

> **Dark mission-control meets broadsheet newspaper.**

Every visual choice must be defensible against that sentence. Mission-control contributes: dark
surfaces, dense panes, mono numerals, status colors, live meters, restrained chrome. Broadsheet
contributes: serif headlines with real prose, the feed-as-front-page, wry editorial voice.
If a proposed element belongs to neither parent (e.g. neon gradients, rounded bubbly cards,
skeuomorphic textures, emoji-dense chrome), it does not belong here.

### A2. Color is semantic, never decorative

The single most protected rule in this codebase. Each color *means* something; using it for
anything else corrupts the vocabulary the user has already learned.

| Token | Hex | Meaning — and the ONLY permitted uses |
|---|---|---|
| `--violet` | `#9085e9` | **Divine intervention / fork / god power.** The ✦ glyph, intervention events, fork tabs & bars, god buttons, timeline-B accents. Never "just purple." |
| `--blue` | `#3987e5` | **Selection & focus.** Selected cards/events, default meter fill, ◈ checkpoint marks (user bookmarks are a focus act, not a god act). Not status, not branding. |
| `--good` | `#0ca30c` | Status: good/up. Icon or ▲ alongside, never color alone. |
| `--warn` | `#fab219` | Status: warning badges, MALFUNCTION blink. |
| `--serious` | `#ec835a` | Status: serious — war badges, degraded assets. |
| `--crit` | `#d03b3b` | Status: crisis — severity-2 events, destruction, discard actions. |
| `--ink` / `--ink2` / `--ink3` | `#ffffff` / `#c3c2b7` / `#898781` | Text hierarchy: emphatic / body / muted-label. **Text never wears a series or status color as its body color** — a colored chip/glyph next to ink text carries the meaning. |
| `--page` / `--surface` / `--raised` / `--sunken` | `#0d0d0d` / `#1a1a19` / `#222221` / `#141413` | Elevation ladder. Raised = hover/active fills. |
| `--line` / `--line-strong` | `rgba(255,255,255,.09)` / `.16` | Hairline borders; strong = hover state. Never solid gray borders. |

Charts use the validated dark categorical order `#3987e5 #199e70 #c98500 #008300 #9085e9
#e66767 #d55181 #d95926` — assigned in fixed order to entities, never cycled, never reshuffled
when a filter changes the set. If you need a new *meaning*, you almost certainly don't: map it
onto violet (god), blue (focus), or the status ramp. Adding a genuinely new semantic color is a
§B-level decision.

Tinted fills are always the semantic color at low alpha over the surface, e.g.
`rgba(144,133,233,0.12)` for violet hover, `rgba(208,59,59,0.14)` for crisis badges. Don't
invent new opaque fill colors.

### A3. Typography carries the split personality

- `--serif` (Georgia stack) — **the world's voice.** Headlines in the feed, inspector
  headlines, dossier titles, the brand. If the *simulation* is speaking, it's serif.
- `--sans` (system-ui) — **the instrument's voice.** All chrome: buttons, labels, badges, meta.
- `--mono` + `tabular-nums` (`.mono`) — **every number that changes.** Ticks, stats, ledger
  rows, seeds, zoom. Numbers that jitter in width look broken; tabular mono is mandatory.

Scale is deliberately tight: body 13px; meta/labels 9–11px with `letter-spacing: 1–4px` and
uppercase for section heads (`.panehead`, `.insp-sechead`); serif moments 14.5–17px. Do not
introduce large type — density *is* the aesthetic; emphasis comes from serif + `--ink`, not size.

### A4. Glass is rationed

`.glass` (translucent surface + backdrop blur) marks **elevated, temporary, above-the-world
moments only**: the fork/timeline bar, the scrub banner, modal overlays/dossiers. The permanent
panes are opaque. If everything is glass, nothing is. Before adding glass ask: "does this float
*above* the running world?" If it's a resident pane, it's opaque `--surface`.

### A5. Motion is functional or absent

Every animation in the UI encodes a state change: `.flash`/`.flashsel` (this thing just
changed/was targeted), meter width transitions (0.4s — value moved), MALFUNCTION amber blink,
crisis pings on the globe, the globe's own rotation (the world is alive). There are zero
decorative animations — no entrance stagger, no hover lift/scale, no parallax. Keep it that
way; add motion only when it answers "what just happened?"

### A6. Component recipes (copy these, don't reinvent)

- **New pane:** `.pane` > `.panehead` (10px, letter-spaced, uppercase, muted) + scrollable body.
  Panes never scroll the page; each body owns its `overflow-y`.
- **New badge/chip:** the `.badge`/`.kindchip` pattern — 9–10px text in the semantic color over
  that color at ~0.12 alpha, radius 3–4px. Never bordered AND filled.
- **New feed-event affordance:** extend `.ev` modifier classes (`sev0/1/2`, `.iv`, `.sel`,
  `.dim`) — severity lives in the *left border* and glyph color, never the background (except
  violet's faint tint for interventions).
- **New dossier section:** follow `detailHTML`'s grammar: `.insp-sechead` label, then tiles
  (`.tilerow`/`.tile`), charts (`.chartgrid`/`.chartbox`), or rows (`.relrow`). God controls sit
  in their own block headed by a ✦ label, buttons styled `.abtn` (danger variant for
  destructive), sliders per `.godgrid`.
- **New god action anywhere:** it must (1) be marked ✦, (2) use violet, (3) produce a visible
  consequence — inject a fabricated headline via `injectLocalEvent` so the world *reacts*, and
  (4) toast confirmation. A god action with no narrative echo feels dead.
- **New overlay:** reuse `#overlay`/`#overlayCard` + `.glass`; close on `esc` and backdrop
  click; wire it into the existing key handler, don't add a second listener.
- **New history-scale view:** follow the Annals grammar — serif era/war names with mono tick
  ranges, italic muted epigraphs, `.record` tiles for superlatives. Fabricated history must
  tie back to real delivered events where any exist (real wars get invented names, not the
  other way round), and every superlative should contextualize a number the live UI shows.
- **Buttons say what they do** ("✦ Intervene here", "Resume live ▶", "End this war"), sentence
  case, verb-first. Toasts confirm in the same words the button used.

### A7. Voice

The interface is a wry, omniscient instrument. Chrome copy is dry and precise; the *world's*
copy (headlines, dossier logs, god feedback) has personality: "The gods leave no paper trail.
This happened because you wanted it to." / "Three concurrent forks is plenty, even for a god."
Rules: never apologize, never exclaim, never use filler ("Oops!", "Awesome!"). Humor lives in
god-power and fabricated-data moments only — status/error copy stays straight.

### A8. Hard architectural walls (breaking these is a bug, not a style choice)

1. `app.js` contains **zero simulation logic** — it renders messages and sends commands per
   `docs/frontend-contract.md`. New feature = contract addition first, then engine mock, then UI.
2. `globe.js` is presentation only: in real-engine mode it renders protocol-v2 `worldObjects`
   and never invents or mutates simulation assets. Stylized territory outlines and satellite
   orbit paths may be projected visually; their centers/ownership/count/condition are authoritative.
3. The frontend **never composes simulation headlines**. Local negative-id narration must never
   pretend to be an engine asset event; approximate asset meddling exists only in explicit mock mode.
4. No build step, no dependencies, no CDN, no network. Plain JS IIFEs. It must work from
   `file://`.
5. `[hidden] { display: none !important; }` exists because flex containers defeat the `hidden`
   attribute — never remove it, and prefer `hidden` over inline styles for show/hide.

---

## B. The method (how to be good at this)

What follows is the actual procedure behind this UI. It is not mystique; it is a sequence.

**B1. Design the nouns before the pixels.** The UI is good because the *contract* is good:
events with `parentId` make the trace view possible; per-tick history snapshots make scrubbing
possible; `timelines.A/B` in one frame makes the diff column possible. When asked for a feature,
first ask "what data shape makes this trivial to render?" and extend the contract — the UI then
mostly writes itself. UI pain is usually a data-shape mistake.

**B2. Find the one metaphor and spend everything on it.** This product's metaphor: *time-travel
debugger for history* (and visually: mission-control × broadsheet). One metaphor, executed
everywhere, beats five clever ideas. New features must be phrased inside it — "multiple forks"
became *browser tabs for realities*; god edits became *REWRITE REALITY sliders*. Before
building, write the one-line metaphor for the feature; if you can't, you don't understand it yet.

**B3. Constraints are the style.** No libraries forced the hand-rolled orthographic globe —
which is now the most impressive thing on screen. Density limits forced the tight type scale.
When you hit a constraint, don't route around it with a dependency; ask what distinctive thing
the constraint makes *cheap*.

**B4. Steal semantics from tools people already trust.** The severity left-border is from log
viewers; tabs-with-✕ from browsers; the ribbon from video scrubbers; diff columns from code
review. Users already know these grammars — reusing them buys instant legibility, and novelty
is spent only on the one thing that's genuinely new (causality as navigation).

**B5. Restraint is a budget, not a mood.** Rules of thumb used throughout: one accent meaning
per hue (§A2); one font per voice (§A3); glass only above the world (§A4); motion only as state
change (§A5); the boldest element on any screen should be *the data*, never the chrome. After
building, remove one thing (Chanel's rule) — every pass on this UI deleted at least one border,
label, or effect.

**B6. Fake data belongs only to explicit mock mode.** The approximate offline fallback may use
specific set dressing, but the default real-engine path must label and display only fields the
protocol actually supplies. Never turn an aggregate asset count into invented individual mission
histories, crew manifests, or mutable health.

**B7. Every action gets a visible consequence within one second.** Click → selection state;
real event → feed/globe response; fork → tab appears + feed splits. In explicit mock mode only,
an approximate local action may use a local toast/ping. If you
add an interaction and nothing on screen *changes*, the feature reads as broken even when the
state mutated correctly.

**B8. Verify without a browser, then look anyway.** The discipline used here: `node --check`
every touched JS file; headless smoke tests (stub `window`, `requestAnimationFrame`, canvas)
asserting protocol behavior; then a person looks at real screenshots, because a validator can't
see label collisions or a broken layout. Never ship on "the code looks right."

**B9. Self-critique with the calibration list.** Before calling anything done, check it against
the failure modes that make UIs look templated: same-y cream-serif or black-plus-acid-accent
kits; numbers on every data point; dual axes; color used as decoration; entrance
animations everywhere; copy that sells instead of describes; headers/sections for trivial
content. If your addition resembles what *any* template would produce for *any* dashboard, it's
wrong for this one — re-derive it from the identity sentence in §A1.

---

## Checklist for any `web/` change

- [ ] Contract updated first (if data shape changed) — `docs/frontend-contract.md`
- [ ] Colors used only per §A2 semantics; no new hues
- [ ] Serif/sans/mono per §A3; numbers are `.mono`
- [ ] Glass only if it floats above the world
- [ ] God actions: ✦ + violet + narrative echo + toast
- [ ] Show/hide via `hidden` attribute
- [ ] `node --check` on every touched JS file
- [ ] Smoke test updated/added if protocol behavior changed
- [ ] One accessory removed after it works
