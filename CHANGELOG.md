# Changelog

All notable changes to Meddler are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is below 1.0, the
bridge protocol may still change between minor releases.

## [Unreleased]

### Engine

- An annexed country leaves the world completely: it no longer produces, collects tax, votes or
  receives queued events, and annexation detaches it from its bloc, tariffs, embargoes and
  occupations.
- God-mode interventions and direct edits refuse a country that has left the world.
- A departed country no longer holds one of the `max_countries` slots a secession needs.
- Assassinations now happen on their own in unstable countries; before, they could only follow
  an occupation's resistance movement and almost never fired.
- Revolutions can follow political collapse, not only famine: a civil-war risk can overturn the
  order if the country is still in unrest when it comes due.
- A change of ruler moves where a country's stability settles, by the new leader's character,
  while pulling back toward the nation's own founding temperament, so countries wander without
  losing who they are. A revolution resets the temperament halfway toward that founding draw.
- A drought or locust swarm takes at most a quarter of a country's food output. A fixed-size
  crop shock used to erase a small producer's farms for good, leaving it to starve for the rest
  of the run.
- Short history reads (the chronicle frame, shipment history) go through the tick index, and a
  cause lookup no longer stalls on a long history (3.3 s to 0.04 s at one measured tick).

### Web UI

- A speed or pause/resume key pressed while a previous request is still pending takes effect
  instead of being dropped.
- Region names on the globe stay clear of lanes, capitals and the globe's edge.
- The in-browser mock engine opens the dossier, causal trace and fork views instead of leaving
  them on a loading spinner.

### Chronicle

- Every sunk convoy reaches the chronicle: a lone loss is reported once it has waited 24 ticks,
  lost relief at the next report, so no sinking goes untold.
- A god-rolled chaos intervention names what it became in words for every kind it can resolve
  into.

### Tooling

- The linters (ruff, mypy, import-linter) are pinned, so CI judges the tree the same way a local
  checkout does.

## [0.1.0] - 2026-09-11

The first public release.

### Engine

- A deterministic, fixed-tick simulation of a small world of countries: the same seed produces a
  byte-identical event log on every run, pinned by a golden-master test.
- A money-conserving ledger across each country's treasury, households and corporates, with
  per-country currencies, exchange rates, inflation and fiscal policy.
- Politics and conflict: elections, coups, occupation and liberation, wars fought over
  resources, distance-gated strikes, annexation with conquest economics, and structural alliances
  and blocs.
- `INTERVENE_SECEDE` creates a real breakaway country -- a quarter of its parent's people,
  output, assets and money, its own currency, hostile to its parent -- and it replays
  identically under scrub, fork and adoption. Secession never happens on its own; without an
  intervention it is only a stability penalty.
- A spatial world: every country has a position, a region and commodity endowments; countries
  never overlap, and distance shapes trade, strikes and logistics.
- Six commodities with production and demand, bilateral trade driven by GDP, relations and
  distance, and real shipments by sea, air and rail that stay in transit for at least seven ticks
  and can be lost to war.
- Tariff policy with bloc exemptions, retaliation and repeal; shipments settle duty, buyer cost and
  exporter proceeds separately.
- A data-driven registry of event kinds with a causal consequence cascade: every event records its
  triggers, contributors and context, forming a deduplicated causal DAG.
- Impact analysis that separates immediate from downstream effects, deduplicates shared
  descendants and totals numeric effects over a tick horizon.
- Time travel: any past tick can be reconstructed read-only from snapshots plus recorded deltas,
  without re-running a system or touching the dice.
- Forks and god-mode interventions (droughts, mints, assassinations, plagues, quakes, meteor
  strikes and more). A fork keeps the prime timeline's
  exact dice, so every difference is attributable to the intervention. Up to three forks can run
  side by side, and any one can be adopted as the new prime.
- File-backed history in SQLite: events, causal edges, RNG states and statistics are stored once,
  forks share prefixes, and server memory stays roughly flat over long runs.
- The Annals: eras, wars of record, superlatives and a ranking of events by their real causal
  impact.

### Web UI

- A hand-drawn orthographic globe showing countries, territory, blocs, trade lanes, shipments,
  strikes and satellites, all taken from engine state — rebuilt for this release with clearer
  cartography, readable lanes and movers at every zoom level, and selection that reaches the exact
  ship, plane or train.
- A broadsheet-style chronicle of headlines, a country inspector, full-history dossier charts with
  synchronized hover and jump-to-cause, and a causal trace view.
- A timeline ribbon for scrubbing, a ΔWORLD strip comparing forks with prime, checkpoints, a Trade
  Operations view of every shipment in transit, and live world settings.
- No build step and no runtime dependencies; an optional in-browser mock (`?engine=mock`) for a
  look without the engine.

### Licensing

- Released as free software under the GNU Affero General Public License, version 3 or later.
  Copyright (C) 2026 Parth Mittal.

### Tooling

- `meddler serve` hosts the UI and the WebSocket engine on one port (loopback by default), with
  `--open` to launch a browser and a clear error when the port is taken.
- `meddler run --headless` prints the deterministic headline log; `--trace` prints the causal tree
  of one event.
- `python -m meddler` works as an alias, and `meddler --version` reports the installed version.
- The frontend ships inside the wheel, so a regular `pip install .` serves the UI;
  `MEDDLER_WEB_DIR` points the server at another copy.
- Continuous integration: tests and a golden-master check against an installed wheel on Python
  3.11 to 3.13; lint, types, import-layering contracts, JavaScript syntax and the slow test group
  on 3.12.
- A scripted capture of screenshots and a demo GIF (`scripts/capture/`).

[Unreleased]: https://github.com/ThePrivatePanda/meddler/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ThePrivatePanda/meddler/releases/tag/v0.1.0
