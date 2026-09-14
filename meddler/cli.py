"""Command-line entry point. PROPOSAL §3.6 (headless mode) / implementation-spec M3.3.

The `run` subcommand is engine+text only: it must never import meddler.bridge or anything
under web/ (§4.1 layering wall). A linear headless print run needs no snapshots or forking,
so it drives tickloop.tick(world, rng) directly rather than through Timeline (same rationale
as tests/unit/test_cascade.py's linear run).
"""

from __future__ import annotations

import argparse
import errno
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from meddler.engine import tickloop
from meddler.engine.events import Event
from meddler.engine.model import World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.worldgen import generate_world
from meddler.text.headlines import TEMPLATES, render


def _run_headless(seed: int, ticks: int, trace_id: int | None) -> int:
    """Simulate `ticks` ticks from `seed`, printing one headline line per narrative event.

    Line format (§3.6): ``t0412 [ELB] Bread riots erupt…``. Events whose kind has no
    template (ambient bookkeeping) are skipped. If `trace_id` is given, an ancestry tree
    for that event is printed after the run."""
    # SIGTERM (`kill`, `timeout`) would end the process without closing the history file;
    # raising SystemExit instead unwinds through the `finally` below.
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _exit_on_sigterm)
    world = generate_world(seed, WorldSettings())
    try:
        rng = Rng(seed)
        for _ in range(ticks):
            _, events = tickloop.tick(world, rng)
            for event in events:
                if event.kind not in TEMPLATES:
                    continue
                code = event.country or "---"
                print(f"t{event.tick:04d} [{code}] {render(event, world)}")

        if trace_id is not None:
            _print_trace(world, trace_id)
        return 0
    finally:
        world.log.close()
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _exit_on_sigterm(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


def _print_trace(world: World, trace_id: int) -> None:
    """Print a plain-text ancestry tree for `trace_id`: root at top, the traced event
    marked, indented by cascade depth.

    Scope note (implementation-spec M3.3): this is deliberately minimal inline logic, not a
    general trace module -- engine/trace.py is M4.1's job. It walks parent_id up to the root,
    then walks the log forward gathering every descendant whose parent chain reaches that
    root, and prints them depth-indented in id order."""
    log = world.log
    if trace_id < 0 or trace_id >= len(log):
        print(f"trace: no event with id {trace_id}")
        return

    # Walk up to the root cause of the traced event.
    root = log[trace_id]
    while root.parent_id is not None:
        root = log[root.parent_id]

    # Collect the root and every descendant (any event whose parent chain reaches root.id).
    in_tree: set[int] = {root.id}
    members: list[Event] = [root]
    for event in log:
        if event.id == root.id:
            continue
        pid = event.parent_id
        while pid is not None:
            if pid in in_tree:
                in_tree.add(event.id)
                members.append(event)
                break
            pid = log[pid].parent_id

    print(f"trace of event {trace_id} (root cause: event {root.id}):")
    for event in members:
        indent = "  " * event.depth
        marker = " <-- traced" if event.id == trace_id else ""
        country = event.country or "---"
        print(f"{indent}#{event.id} t{event.tick:04d} [{country}] {event.kind}{marker}")


def _version() -> str:
    """The installed distribution's version; source checkouts run via PYTHONPATH may have
    no metadata at all."""
    try:
        return package_version("meddler")
    except PackageNotFoundError:
        return "unknown (not installed)"


def _browser_url(host: str, port: int) -> str:
    """URL a local browser should open: wildcard binds are reached via loopback."""
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def _port_problem(host: str, port: int) -> str | None:
    """Try binding host:port before the (slow-ish) world generation so a busy port fails
    fast with a readable message. The server still handles the race if it loses the port
    in between."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.create_server((host, port), family=family):
            return None
    except OSError as exc:
        return _describe_bind_error(exc, host, port)


def _describe_bind_error(exc: OSError, host: str, port: int) -> str:
    if exc.errno == errno.EADDRINUSE:
        return (
            f"port {port} on {host} is already in use (is another `meddler serve` running?). "
            f"Stop it, or pick another port: meddler serve --port {port + 1}"
        )
    if exc.errno == errno.EACCES:
        return f"not allowed to listen on {host}:{port}; try a port above 1024"
    return f"cannot listen on {host}:{port}: {exc.strerror or exc}"


def _open_browser_when_ready(url: str, host: str, port: int, stop: threading.Event) -> None:
    """Open `url` once the server answers HTTP (world generation happens first)."""
    probe = _browser_url(host, port)
    deadline = time.monotonic() + 120.0
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(probe, timeout=1.0):
                break
        except OSError:  # includes URLError: not listening yet
            stop.wait(0.25)
    else:
        return
    if not stop.is_set():
        webbrowser.open(url)


def _serve(seed: int, host: str, port: int, open_browser: bool) -> int:
    # Local imports: `run` (headless) must never import bridge/ (§4.1, M3.3's own
    # constraint) -- keeping these inside the `serve` path means cli.py's module-level
    # import graph stays bridge-free for the `run` path.
    from meddler.bridge.server import WebDirNotFound, resolve_web_dir
    from meddler.bridge.server import run as serve_run

    try:
        resolve_web_dir()
    except WebDirNotFound as exc:
        print(f"meddler: error: {exc}", file=sys.stderr)
        return 1

    problem = _port_problem(host, port)
    if problem is not None:
        print(f"meddler: error: {problem}", file=sys.stderr)
        return 1

    stop = threading.Event()
    if open_browser:
        url = _browser_url(host, port)
        threading.Thread(
            target=_open_browser_when_ready, args=(url, host, port, stop), daemon=True
        ).start()
    try:
        serve_run(seed, host, port)
    except OSError as exc:
        print(f"meddler: error: {_describe_bind_error(exc, host, port)}", file=sys.stderr)
        return 1
    finally:
        stop.set()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="meddler",
        description="A deterministic zero-player world simulator with a time-travel UI.",
    )
    parser.add_argument("--version", action="version", version=f"meddler {_version()}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser(
        "run",
        help="headless simulation to stdout",
    )
    run.add_argument("--headless", action="store_true", help="print headlines, no UI")
    run.add_argument("--seed", type=int, default=1337, help="world seed (default: %(default)s)")
    run.add_argument("--ticks", type=int, default=1000, help="ticks to simulate (default: %(default)s)")
    run.add_argument(
        "--trace", type=int, default=None, metavar="EVENT_ID",
        help="after the run, print the causal tree of this event",
    )

    serve = sub.add_parser(
        "serve",
        help="serve the web UI and the engine on one port",
    )
    serve.add_argument(
        "--seed", type=int, default=1337,
        help="world seed; the same seed always produces the same history (default: %(default)s)",
    )
    serve.add_argument(
        "--host", default="127.0.0.1",
        help="interface to bind (default: %(default)s); anything but loopback exposes an "
        "unauthenticated control socket, see SECURITY.md",
    )
    serve.add_argument("--port", type=int, default=7677, help="HTTP + WebSocket port (default: %(default)s)")
    serve.add_argument(
        "--open", action="store_true", help="open the UI in your default browser once ready"
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        return _run_headless(args.seed, args.ticks, args.trace)

    if args.command == "serve":
        return _serve(args.seed, args.host, args.port, args.open)

    parser.print_help()
    return 0
