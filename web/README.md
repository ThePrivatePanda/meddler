# Meddler web frontend

The production frontend for the deterministic Meddler engine. It has no build step or external
runtime dependencies and is served by the same process as the WebSocket bridge.

## Run it

From the repository root:

```sh
pip install -e .
meddler serve --seed 1337
# open http://127.0.0.1:7677/
```

The default path uses the real Python engine and bridge protocol v2. `?engine=mock` explicitly
selects the approximate in-browser offline fallback; connection failures never switch to it
silently. `?ws=<url>` overrides the WebSocket endpoint.

## Globe data boundary

`globe.js` is a hand-rolled Canvas 2D orthographic renderer. In real-engine mode,
`setWorldObjects(payload)` consumes authoritative:

- country capital positions, regions, territory centers and ownership;
- endowments, six commodity buckets, infrastructure counts/conditions, and blocs;
- directional carrier-separated lanes and current in-flight volume;
- real in-flight shipment IDs, routes, quantities, progress, settlement fields, and endpoint
  fleet condition; engine shipments remain in flight for at least seven simulation ticks;
- recent real STRIKE routes, separate from persistent war arcs.

Every zoom level shows territories, capitals, collision-avoided nation labels, wars, recent
strikes, and the sea/air trade network with its real in-flight shipments: at default zoom lanes
are thin, alpha-weighted by their share of the largest current lane volume; zooming in widens
them to volume-scaled strokes with carrier dash patterns. Satellites join above 1.05× and rail
above 1.6× (`getLayers()` reports the active set; `onZoom(zoom, layers)` passes it on every
zoom). Volume controls width/size; condition controls fading and degraded color. The trade
network is drawn in neutral ink (sea ink2, air white, rail ink3) so it never borrows a nation's
categorical color; war arcs and belligerent coasts use the serious (war) color, strike arcs and
impact rings the crisis color, and selection is always blue. Hovering any nation, lane, or
mover highlights it and shows a small in-canvas tooltip built only from protocol fields. Visible lanes are selectable; the right inspector paginates the lane's full
stable roster in immutable dispatch order for active and lost rows. Progress never reorders them.
Arrival is the sole exception: the arrived row moves to the bottom for four ticks, then expires;
lost rows stay in their original slot for eight ticks. Live kilometers left refresh on every engine
frame. **Trade Operations** (`V` or the
`⇄ TRADE` topbar button) provides a global paginated view of every active shipment and retained
outcome with carrier/commodity/status/search filters, progress, ETA, war exposure, fleet condition,
settlement details, and unit-safe aggregate statistics. Every row selects that exact shipment.
Real shipment and aggregate satellite dossiers are read-only.

Only two elements are visual projections: stylized territory shapes around authoritative
territory markers and deterministic orbit paths for the authoritative aggregate satellite count.
Territory shapes are a pure function of marker positions, regions, and owners: each marker gets
a softly irregular disc (irregularity seeded by the marker's own position, so an annexation
recolors a cell without reshaping it), joined to same-region markers by an isthmus along a
per-region minimum spanning tree — the engine defines a region as one land-connected landmass —
and clipped to the marker's spherical Voronoi cell, with a strait kept open between regions.
Region names are drawn at each region's marker centroid. Bloc membership is shown as a dotted
treaty rim around members' coasts (and dotted links to partners on hover/selection); every
nation keeps its own categorical color. Mover wakes trail each real shipment along its own
route for a few degrees of arc and carry no extra data. The real
path does not fabricate cities, routes, movers, mission histories, asset mutations, or negative-id
asset headlines. Approximate cosmetic objects and mutations are guarded to explicit mock mode.

Country dossier history charts share a synchronized hover index. Pointer or keyboard navigation shows
the exact sampled tick and all metric values/deltas immediately, then requests authoritative events
only for that sampled interval. Responses carry an exact total and at most six strongest summaries;
the client retains 24 intervals and 1,000 summaries per timeline. Users can inspect an event or jump
PRIME to that tick, and no event-free interval is assigned an invented cause.

## Files

- `app.js` — UI state and protocol-v2 message consumption; no simulation logic.
- `globe.js` — globe projection/rendering and hit testing.
- `realengine.js` — real WebSocket transport with same-origin default and reconnect guidance.
- `engine.js` — optional approximate offline mock only.
- `style.css` / `index.html` — visual system and document shell.

Before visual changes, read `../docs/design-guide.md`. The exact message schema and honesty
boundary live in `../docs/frontend-contract.md`.

## Runtime and asynchronous interaction

`meddler serve` owns one simulation runtime for its process lifetime. Reloads and transient WebSocket
reconnects attach to that same runtime; they do not create a fresh world or implicitly resume a paused
one. The initial `hello`/`status` pair restores tick, playback state, settings, forks, focus/scrub state,
and authoritative history. Stopping the process still ends the ephemeral world: durable process-restart
save/load is not implemented.

Every user-triggered engine command goes through `app.js`'s correlated request dispatcher. The global
status indicator names pending work, contextual trace/dossier/fork overlays show immediate loading
state, duplicate logical requests are suppressed, and correlated errors restore retryable controls.
Settings updates are simulation timeline mutations recorded by the backend, not browser preferences:
scrub, fork, restart, and deterministic replay all observe the change at its recorded tick.

Hot rendering paths avoid synchronous layout reads after DOM writes. Feed cards batch in a document
fragment, ribbon and canvas dimensions come from `ResizeObserver`, scrub pointer traffic is coalesced
to animation frames with one reconstruction in flight, and animation restarts use frame-separated
class changes rather than forced reflow.
