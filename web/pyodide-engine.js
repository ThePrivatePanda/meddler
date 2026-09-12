/* Meddler — in-browser engine transport (static demo only).
 *
 * The hosted demo has no `meddler serve` to talk to, so it runs the real Python engine in
 * the page instead: `pyodide-worker.js` loads Pyodide, unpacks the packaged `meddler`
 * sources, and drives meddler.bridge.runtime.LocalRuntime on its own clock. This file is
 * the page-side half. It exposes the same `{ connect(onMessage), send(cmd) }` surface as
 * `realengine.js` (frontend-contract §1) and hands `app.js` the identical protocol
 * messages the WebSocket would, so nothing downstream knows the difference.
 *
 * Inert unless asked for: it only takes over `MeddlerRealEngine.create` when the page is
 * the demo build (`window.MEDDLER_DEMO`, set by scripts/build_demo.py) or the URL says
 * `?engine=pyodide`. Loaded any other way it defines `MeddlerPyodideEngine` and does
 * nothing else — no network, no DOM. It must load after realengine.js and before app.js.
 *
 * Page-only additions, both outside the protocol: a loading screen while Pyodide boots
 * (it is a ~10 MB download the first time), and a small chip in the top bar reporting the
 * engine's real tick rate and cost, so a slow machine is shown honestly rather than hidden.
 */
(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var DEFAULT_SEED = 1337; // `meddler serve` default
  var RATE_WINDOW_MS = 10000;

  var STAGES = [
    { id: "runtime", label: "Fetching the Python runtime", note: "Pyodide, about 10 MB the first time; cached after that" },
    { id: "sqlite", label: "Loading SQLite", note: "history lives in an in-memory database in this tab" },
    { id: "engine", label: "Unpacking the engine", note: "the same Python package the local server runs" },
    { id: "world", label: "Generating the world", note: "" },
    { id: "ready", label: "Opening the chronicle", note: "" }
  ];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function injectStyle() {
    if (document.getElementById("pyoStyle")) return;
    var style = el("style");
    style.id = "pyoStyle";
    style.textContent = [
      "#pyoBoot{position:fixed;inset:0;z-index:9000;background:var(--page,#0d0d0d);display:flex;align-items:center;justify-content:center;padding:24px 16px}",
      "#pyoBoot .pb-card{width:100%;max-width:480px}",
      "#pyoBoot .pb-brand{font-family:var(--serif);font-size:16px;letter-spacing:3px;color:var(--ink)}",
      "#pyoBoot .pb-tag{margin-left:8px;font-family:var(--sans);font-size:10px;letter-spacing:4px;color:var(--ink3)}",
      "#pyoBoot .pb-lede{margin-top:18px;font-family:var(--serif);font-style:italic;font-size:15px;line-height:1.5;color:var(--ink2)}",
      "#pyoBoot .pb-body{margin-top:10px;font-size:12px;line-height:1.6;color:var(--ink3)}",
      "#pyoBoot .pb-body code{font-family:var(--mono);font-size:11px;color:var(--ink2)}",
      "#pyoBoot ol{list-style:none;margin-top:22px;border-top:1px solid var(--line)}",
      "#pyoBoot li{display:grid;grid-template-columns:16px 1fr auto;gap:0 10px;align-items:baseline;padding:8px 0;border-bottom:1px solid var(--line)}",
      "#pyoBoot .pb-glyph{font-family:var(--mono);font-size:11px;color:var(--ink3)}",
      "#pyoBoot .pb-label{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--ink3)}",
      "#pyoBoot .pb-note{grid-column:2;font-size:11px;color:var(--ink3);opacity:.8}",
      "#pyoBoot .pb-time{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:10px;color:var(--ink3)}",
      "#pyoBoot li.on .pb-label{color:var(--ink)}",
      "#pyoBoot li.on .pb-glyph{color:var(--ink)}",
      "#pyoBoot li.done .pb-label{color:var(--ink2)}",
      "#pyoBoot li.fail .pb-glyph{color:var(--crit)}",
      "#pyoBoot li.fail .pb-label{color:var(--ink)}",
      "#pyoBoot .pb-error{margin-top:14px;font-size:12px;line-height:1.6;color:var(--ink2)}",
      "#pyoBoot .pb-error .mono{display:block;margin-top:6px;font-size:10.5px;color:var(--ink3);word-break:break-word}",
      ".pyo-chip{display:inline-flex;align-items:center;gap:6px;margin-right:8px;padding:2px 7px;border-radius:3px;background:var(--sunken);color:var(--ink3);font:10px/1.4 var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap;cursor:help}",
      ".pyo-chip b{font-weight:400;color:var(--ink2)}",
      ".pyo-src{margin-right:8px;font:10px/1.4 var(--sans);letter-spacing:1px;text-transform:uppercase;color:var(--ink3);text-decoration:none;border-bottom:1px solid var(--line)}",
      ".pyo-src:hover{color:var(--ink2);border-bottom-color:var(--line-strong)}",
      "#pyoBoot .pyo-src{margin-right:0;text-transform:none;letter-spacing:0;font-size:12px;color:var(--ink2)}",
      ".pyo-chip .pyo-behind{padding:0 4px;border-radius:3px;color:var(--warn);background:rgba(250,178,25,.12)}",
      "@media (max-width:760px){.pyo-chip .pyo-cost{display:none}}"
    ].join("\n");
    document.head.appendChild(style);
  }

  function fmtBytes(n) {
    if (n == null) return "—";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function fmtRate(tps) {
    return tps.toFixed(2);
  }

  // The AGPL asks a network-deployed program to offer its source; the demo links the
  // exact commit it was built from (and the archive of it, when the build shipped one).
  function sourceInfo() {
    var info = window.MEDDLER_DEMO_SOURCE;
    return info && info.url ? info : null;
  }

  function sourceLink(text) {
    var info = sourceInfo();
    if (!info) return null;
    var a = el("a", "pyo-src", text);
    a.href = info.commit ? info.url.replace(/\/$/, "") + "/tree/" + info.commit : info.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = "Meddler is free software under the AGPL-3.0-or-later." +
      (info.commit ? " This demo was built from commit " + info.commit.slice(0, 12) + "." : "") +
      (info.archive ? " A source archive ships next to this page: " + info.archive : "");
    return a;
  }

  // ---------------------------------------------------------------- loading screen
  function BootScreen(seed) {
    injectStyle();
    this.root = el("div");
    this.root.id = "pyoBoot";
    this.root.setAttribute("role", "status");
    this.root.setAttribute("aria-live", "polite");
    var card = el("div", "pb-card");
    var brand = el("div");
    brand.appendChild(el("span", "pb-brand", "MEDDLER"));
    brand.appendChild(el("span", "pb-tag", "IN-BROWSER DEMO"));
    card.appendChild(brand);
    card.appendChild(el("p", "pb-lede", "A history that runs itself, loading."));
    var body = el("p", "pb-body");
    body.innerHTML =
      "The simulation below is the real engine: the Python package <code>meddler serve</code> " +
      "runs, executing in this tab on WebAssembly. Nothing is precomputed or replayed; every " +
      "headline is generated as you watch.";
    card.appendChild(body);
    var info = sourceInfo();
    if (info) {
      var src = el("p", "pb-body");
      src.appendChild(document.createTextNode("Free software under the AGPL-3.0-or-later. "));
      var link = sourceLink(info.commit ? "Source for this build (" + info.commit.slice(0, 12) + ")" : "Source");
      src.appendChild(link);
      src.appendChild(document.createTextNode("."));
      card.appendChild(src);
    }
    this.list = el("ol");
    this.rows = {};
    for (var i = 0; i < STAGES.length; i++) {
      var s = STAGES[i];
      var li = el("li");
      li.appendChild(el("span", "pb-glyph", "·"));
      li.appendChild(el("span", "pb-label", s.label));
      li.appendChild(el("span", "pb-time", ""));
      var note = s.id === "world" ? "seed " + seed : s.note;
      if (note) li.appendChild(el("span", "pb-note", note));
      this.list.appendChild(li);
      this.rows[s.id] = li;
    }
    card.appendChild(this.list);
    this.card = card;
    this.root.appendChild(card);
    this.current = null;
    this.started = 0;
    this.timer = null;
    var self = this;
    var mount = function () { document.body.appendChild(self.root); };
    if (document.body) mount();
    else document.addEventListener("DOMContentLoaded", mount);
  }

  BootScreen.prototype._finishCurrent = function (glyph, cls) {
    if (!this.current) return;
    var li = this.rows[this.current];
    li.className = cls;
    li.querySelector(".pb-glyph").textContent = glyph;
    li.querySelector(".pb-time").textContent = ((performance.now() - this.started) / 1000).toFixed(1) + "s";
  };

  BootScreen.prototype.stage = function (id) {
    if (!this.rows[id] || id === this.current) return;
    this._finishCurrent("✓", "done");
    this.current = id;
    this.started = performance.now();
    var li = this.rows[id];
    li.className = "on";
    li.querySelector(".pb-glyph").textContent = "›";
    var self = this;
    clearInterval(this.timer);
    this.timer = setInterval(function () {
      li.querySelector(".pb-time").textContent = ((performance.now() - self.started) / 1000).toFixed(1) + "s";
    }, 100);
  };

  BootScreen.prototype.fail = function (text) {
    clearInterval(this.timer);
    this._finishCurrent("✕", "fail");
    var box = el("div", "pb-error");
    box.appendChild(document.createTextNode(
      "The engine could not start. The demo needs WebAssembly, Web Workers, and access to " +
      "cdn.jsdelivr.net for the Python runtime. Reload to try again."
    ));
    box.appendChild(el("span", "mono", text));
    this.card.appendChild(box);
  };

  BootScreen.prototype.done = function () {
    clearInterval(this.timer);
    this._finishCurrent("✓", "done");
    this.root.remove();
  };

  // ---------------------------------------------------------------- honesty chip
  function RateChip() {
    this.node = null;
    this.frames = []; // arrival times of recent frames
    this.lastMs = null;
    this.avgMs = null;
    this.historyBytes = null;
    this.heapBytes = null;
    this.frameBytes = null;
    this.running = true;
    this.tps = 1;
  }

  RateChip.prototype.mount = function () {
    if (this.node) return;
    injectStyle();
    var node = el("span", "pyo-chip");
    node.id = "pyoChip";
    var seed = document.getElementById("seedChip");
    if (seed && seed.parentNode) seed.parentNode.insertBefore(node, seed);
    else return;
    var source = sourceLink("source");
    if (source) seed.parentNode.insertBefore(source, node);
    this.node = node;
    this.render();
  };

  RateChip.prototype.frame = function () {
    var now = performance.now();
    this.frames.push(now);
    while (this.frames.length && now - this.frames[0] > RATE_WINDOW_MS) this.frames.shift();
  };

  RateChip.prototype.perf = function (p) {
    this.lastMs = p.ms;
    this.avgMs = this.avgMs == null ? p.ms : this.avgMs * 0.8 + p.ms * 0.2;
    this.frameBytes = p.frameBytes;
    if (p.historyBytes != null) this.historyBytes = p.historyBytes;
    if (p.heapBytes != null) this.heapBytes = p.heapBytes;
    this.render();
  };

  RateChip.prototype.effectiveRate = function () {
    var now = performance.now();
    var recent = this.frames.filter(function (t) { return now - t <= RATE_WINDOW_MS; });
    if (recent.length < 2) return null;
    var span = Math.max(now - recent[0], 1000 / this.tps);
    return (recent.length - 1) / (span / 1000);
  };

  RateChip.prototype.render = function () {
    if (!this.node) return;
    var node = this.node;
    node.textContent = "";
    node.appendChild(document.createTextNode("IN-BROWSER ENGINE"));
    var rate = this.effectiveRate();
    var behind = false;
    if (!this.running) {
      node.appendChild(el("b", null, "paused"));
    } else if (rate == null) {
      node.appendChild(el("b", null, "warming up"));
    } else {
      node.appendChild(el("b", null, fmtRate(rate) + " t/s"));
      behind = rate < this.tps * 0.9 && this.avgMs != null && this.avgMs > 900 / this.tps;
      if (behind) node.appendChild(el("span", "pyo-behind", "of " + fmtRate(this.tps)));
    }
    if (this.avgMs != null) node.appendChild(el("span", "pyo-cost", Math.round(this.avgMs) + " ms/tick"));
    node.title =
      "The real Python engine, running in this tab on WebAssembly (Pyodide).\n" +
      "Measured, not estimated: " +
      (rate == null ? "no rate yet" : fmtRate(rate) + " ticks/s over the last 10 s") +
      " at a requested " + fmtRate(this.tps) + " ticks/s.\n" +
      (this.avgMs == null ? "" : "Each tick costs about " + Math.round(this.avgMs) + " ms of engine time.\n") +
      (behind ? "This machine cannot keep up with the requested speed, so the clock runs as fast as the engine allows. Ticks never queue up.\n" : "") +
      "History database " + fmtBytes(this.historyBytes) + " · WebAssembly memory " + fmtBytes(this.heapBytes) + ".\n" +
      "A reload starts the same seed from tick 0.";
  };

  // ---------------------------------------------------------------- the transport
  function PyodideEngine(options) {
    options = options || {};
    this.seed = options.seed != null ? options.seed : DEFAULT_SEED;
    this.bundle = options.bundle;
    this.worker = null;
    this.out = null;
    this.boot = null;
    this.chip = new RateChip();
    this.booted = false;
  }

  PyodideEngine.prototype.connect = function (onMessage) {
    var self = this;
    this.out = onMessage;
    this.boot = new BootScreen(this.seed);
    if (typeof Worker === "undefined" || location.protocol === "file:") {
      this.boot.stage("runtime");
      this.boot.fail("This page has to be served over http(s); browsers do not run workers from file://.");
      return;
    }
    var workerUrl = new URL("pyodide-worker.js", document.baseURI).href;
    var bundleUrl = new URL(this.bundle || window.MEDDLER_DEMO_BUNDLE || "meddler-engine.zip", document.baseURI).href;
    try {
      this.worker = new Worker(workerUrl);
    } catch (e) {
      this.boot.stage("runtime");
      this.boot.fail(String(e && e.message || e));
      return;
    }
    this.worker.onmessage = function (event) { self._onWorker(event.data); };
    this.worker.onerror = function (event) {
      if (!self.booted && self.boot) self.boot.fail((event && event.message) || "worker failed to load");
    };
    this.worker.postMessage({ kind: "boot", seed: this.seed, bundle: bundleUrl });
  };

  PyodideEngine.prototype._onWorker = function (data) {
    if (!data) return;
    switch (data.kind) {
      case "wire": {
        var msg;
        try {
          msg = JSON.parse(data.data);
        } catch (e) {
          return;
        }
        if (msg.type === "status") {
          // A pause, resume, or speed change starts a fresh measurement window, so the
          // rate shown is never diluted by time the clock was deliberately held.
          if (this.chip.running !== !!msg.running || this.chip.tps !== msg.tps) this.chip.frames = [];
          this.chip.running = !!msg.running;
          this.chip.tps = msg.tps || this.chip.tps;
          this.chip.render();
        } else if (msg.type === "snapshot") {
          this.chip.frames = []; // scrubbing holds the clock too
        } else if (msg.type === "hello") {
          this.chip.tps = msg.tps || this.chip.tps;
        } else if (msg.type === "frame") {
          this.chip.frame();
        }
        if (this.out) this.out(msg);
        if (!this.booted && msg.type === "snapshot") {
          this.booted = true;
          this.boot.done();
          this.chip.mount();
        }
        break;
      }
      case "stage":
        if (this.boot && !this.booted) this.boot.stage(data.id);
        break;
      case "perf":
        this.chip.perf(data);
        break;
      case "fatal":
        if (this.boot) this.boot.fail(data.text);
        break;
      case "capped":
        if (this.out) {
          this.out({
            type: "toast",
            tone: "warn",
            text: "This tab is holding " + fmtBytes(data.historyBytes) + " of history, so the world is " +
              "paused. Press play to keep going, or reload for a fresh one."
          });
        }
        break;
      case "crash":
        if (window.console) console.error("meddler engine error:", data.text);
        if (this.out) this.out({ type: "toast", text: "The engine raised an error and stopped: " + data.text, tone: "warn" });
        break;
    }
  };

  PyodideEngine.prototype.send = function (cmd) {
    // The worker holds commands until the world exists and the first snapshot is out, the
    // same order the socket server gives a fresh connection.
    if (this.worker) this.worker.postMessage({ kind: "cmd", payload: JSON.stringify(cmd) });
  };

  PyodideEngine.prototype.close = function () {
    if (this.worker) this.worker.terminate();
    this.worker = null;
  };

  function seedFromUrl() {
    var raw = params.get("seed");
    if (raw == null || !/^\d{1,9}$/.test(raw)) return DEFAULT_SEED;
    return parseInt(raw, 10);
  }

  window.MeddlerPyodideEngine = {
    create: function (options) {
      return new PyodideEngine(options);
    }
  };

  var wanted = params.get("engine") === "pyodide" || (window.MEDDLER_DEMO === true && !params.get("engine"));
  if (wanted) {
    // app.js asks MeddlerRealEngine for "the real engine". In the demo, the real engine is
    // the one running in this tab. Keep the socket transport reachable for debugging.
    window.MeddlerSocketEngine = window.MeddlerRealEngine;
    window.MeddlerRealEngine = {
      create: function () {
        return new PyodideEngine({ seed: seedFromUrl() });
      }
    };
  }
})();
