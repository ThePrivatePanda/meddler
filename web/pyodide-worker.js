/* Meddler — in-browser engine host (static demo only).
 *
 * Runs the real Python engine under Pyodide inside this Web Worker, so a slow tick never
 * blocks the page. It plays the part `meddler serve` plays for the socket client: it owns
 * one Session (via meddler.bridge.runtime.LocalRuntime), sends hello -> status -> snapshot
 * on attach, answers every command with the bridge's own replies, and advances the world
 * on a wall-clock timer that mirrors ServerRuntime._tick_loop:
 *
 *   - sleep 1/tps first; paused or scrubbed -> sleep 1/tps again and skip;
 *   - advance prime (then the focused fork), post the frame;
 *   - next wait = max(0, 1/tps - elapsed), start-to-start, re-reading tps every tick.
 *
 * The worker is single-threaded, so commands are handled strictly between ticks and a
 * slow tick delays the next one rather than piling up behind it.
 *
 * Messages to the page:
 *   {kind:"wire", data:"<json>"}   one protocol message, byte-identical to the socket's
 *                                  (the runtime returns newline-separated batches; each
 *                                  line is posted as its own wire message, in order)
 *   {kind:"stage", id, detail}     boot progress
 *   {kind:"perf", ...}             tick timing and memory, for the demo's honesty chip
 *   {kind:"fatal", text}           boot failed; nothing else will follow
 *   {kind:"crash", text}           the engine raised; the clock has stopped
 *   {kind:"capped", historyBytes}  history hit the memory cap; the world was paused
 */
/* global importScripts, loadPyodide */
"use strict";

var PYODIDE_VERSION = "0.29.3";
var PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v" + PYODIDE_VERSION + "/full/";
var ENGINE_DIR = "/home/pyodide/engine";

var py = null;
var runtime = null; // PyProxy of meddler.bridge.runtime.LocalRuntime
var pending = []; // commands that arrived before the first snapshot went out
var ticks = 0;
var capped = false;
// About 7,000 ticks of history at current engine output (~0.15 MB per tick).
var HISTORY_CAP_BYTES = 1024 * 1024 * 1024;

function post(message) {
  self.postMessage(message);
}

function stage(id, detail) {
  post({ kind: "stage", id: id, detail: detail || "" });
}

function sendLines(encoded) {
  if (!encoded) return;
  var lines = encoded.split("\n");
  for (var i = 0; i < lines.length; i++) post({ kind: "wire", data: lines[i] });
}

function historyBytes() {
  var total = 0;
  try {
    var names = py.FS.readdir("/tmp");
    for (var i = 0; i < names.length; i++) {
      if (names[i].indexOf("meddler-history-") !== 0) continue;
      total += py.FS.stat("/tmp/" + names[i]).size;
    }
  } catch (e) {
    return null;
  }
  return total;
}

function heapBytes() {
  try {
    return py._module.HEAP8.buffer.byteLength;
  } catch (e) {
    return null;
  }
}

function schedule(seconds) {
  setTimeout(tick, Math.max(0, seconds * 1000));
}

function tick() {
  var started = performance.now();
  var frame;
  try {
    frame = runtime.tick_json();
  } catch (err) {
    // An engine exception ends the clock, as it would end the server's ticker; say so
    // instead of freezing silently.
    post({ kind: "crash", text: String((err && err.message) || err) });
    return;
  }
  if (frame === undefined || frame === null) {
    schedule(runtime.idle_delay());
    return;
  }
  // Today a tick returns one frame, but the runtime may batch messages that have to
  // reach the client before it, so post every line it hands back.
  sendLines(frame);
  var elapsed = (performance.now() - started) / 1000;
  ticks += 1;
  var perf = { kind: "perf", ms: elapsed * 1000, frameBytes: frame.length };
  if (ticks % 10 === 1) {
    perf.historyBytes = historyBytes();
    perf.heapBytes = heapBytes();
    if (!capped && perf.historyBytes != null && perf.historyBytes > HISTORY_CAP_BYTES) {
      // The history database lives in this tab's memory and only grows. Past the cap,
      // pause the world through the ordinary command path (clients see a normal status)
      // and tell the page why. Resuming is allowed; it is the viewer's memory.
      capped = true;
      handle(JSON.stringify({ cmd: "pause" }));
      post({ kind: "capped", historyBytes: perf.historyBytes });
    }
  }
  post(perf);
  schedule(runtime.delay(elapsed));
}

function handle(payload) {
  try {
    sendLines(runtime.handle_json(payload));
  } catch (err) {
    post({ kind: "crash", text: String((err && err.message) || err) });
  }
}

async function fetchEngine(url) {
  var response = await fetch(url);
  if (!response.ok) {
    throw new Error("engine bundle " + url + " answered HTTP " + response.status);
  }
  return response.arrayBuffer();
}

async function boot(options) {
  try {
    stage("runtime", "Pyodide " + PYODIDE_VERSION);
    importScripts(PYODIDE_BASE + "pyodide.js");
    py = await loadPyodide({ indexURL: PYODIDE_BASE });

    stage("sqlite");
    // The history store is an ordinary file-backed SQLite database; here the file lives
    // in Pyodide's in-memory filesystem.
    await py.loadPackage(["sqlite3"], { messageCallback: function () {} });

    stage("engine");
    var bundle = await fetchEngine(options.bundle);
    py.unpackArchive(bundle, "zip", { extractDir: ENGINE_DIR });
    py.runPython(
      "import sys\n" +
      "sys.dont_write_bytecode = True\n" +
      "sys.path.insert(0, " + JSON.stringify(ENGINE_DIR) + ")\n" +
      "from meddler.bridge.runtime import LocalRuntime\n"
    );

    stage("world", "seed " + options.seed);
    var LocalRuntime = py.globals.get("LocalRuntime");
    runtime = LocalRuntime.callKwargs(options.seed, { start_running: true });
    LocalRuntime.destroy();

    stage("ready");
    sendLines(runtime.connect_json());
    var queued = pending;
    pending = null;
    for (var i = 0; i < queued.length; i++) handle(queued[i]);
    schedule(runtime.idle_delay());
  } catch (err) {
    post({ kind: "fatal", text: String((err && err.message) || err) });
  }
}

self.onmessage = function (event) {
  var data = event.data || {};
  if (data.kind === "boot") {
    boot(data);
  } else if (data.kind === "cmd") {
    if (pending) pending.push(data.payload);
    else handle(data.payload);
  }
};
