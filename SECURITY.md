# Security

Meddler is a local, single-user application. It has no accounts, no authentication, and no
network features beyond the one server you start yourself. This page explains what that server
exposes so you can run it safely.

## What `meddler serve` exposes

`meddler serve` opens one port (7677 by default) that serves two things:

- the static web UI (read-only files from the bundled `web/` directory, with path-traversal
  protection), and
- a WebSocket at `/ws` that **controls the simulation**: anyone who can connect can pause and
  resume it, scrub history, fork timelines, apply god-mode interventions, change world settings and
  restart the world.

The WebSocket has **no authentication, no authorization, and no origin check**. Treat access to
the port as full control of that Meddler instance.

## Default: loopback only

By default the server binds to `127.0.0.1`, so only programs on your own machine can reach it.
That is the supported configuration.

## `--host 0.0.0.0` and other non-loopback binds

```sh
meddler serve --host 0.0.0.0   # reachable from your whole network
```

This exposes the unauthenticated control socket to every machine that can reach yours: on shared
Wi-Fi, a university or office network, or a host with a public IP, anyone could drive your
simulation and fill its history database. Only bind beyond loopback on a network you trust, and
prefer one of these instead:

- an SSH tunnel: `ssh -L 7677:127.0.0.1:7677 you@host`, then open `http://127.0.0.1:7677/`
  locally;
- a reverse proxy that adds authentication and TLS in front of a loopback-bound server.

Other things to know:

- Traffic is plain HTTP and WebSocket (no TLS).
- A browser page from another site could try to open a WebSocket to `127.0.0.1:7677` while the
  server is running. The impact is limited to controlling the local simulation, but stop the
  server when you are not using it.
- History is written to a temporary SQLite database that is deleted when the server shuts down.

## The hosted demo

The static in-browser demo downloads the Pyodide runtime from its CDN and then runs the engine
inside your browser tab. There is no Meddler server behind it and no control socket to expose.

## Reporting a vulnerability

If you find a security problem, please report it privately rather than opening a public issue:
use GitHub's "Report a vulnerability" button on the [repository's Security
tab](https://github.com/ThePrivatePanda/meddler/security). Include the
version (`meddler --version`), what you did, and what happened. I'll acknowledge the report as
soon as I can and credit you in the changelog if you'd like.

## Supported versions

Only the latest release receives fixes.
