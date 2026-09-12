/* Meddler — real engine adapter (M8.2a).
 *
 * The browser-side counterpart to the in-browser mock (`engine.js`). Exposes the exact
 * same surface the mock does — `create(...)` returning `{ connect(onMessage), send(cmd) }`
 * (frontend-contract §1) — but instead of simulating locally it speaks the real JSON/
 * WebSocket protocol to a running `meddler serve` (Python bridge, `meddler/bridge/server.py`,
 * default `ws://127.0.0.1:7677/ws`).
 *
 * The server pushes `hello` -> `status` -> `snapshot(live)` on connect and streams `frame`s
 * as the world advances; every command the UI sends (`{cmd:...}`) is answered with the
 * message(s) `meddler/bridge/commands.py` returns. This adapter is a thin transport: it
 * JSON-encodes outgoing commands and hands each decoded incoming message to `onMessage`,
 * the identical callback the mock drives — so `app.js` is unchanged downstream of `create`.
 *
 * Written to load cleanly under Node (global `WebSocket`, no DOM) as well as the browser,
 * so the adapter's real code path can be smoke-tested headlessly against a live server
 * (see tests/bridge/); DOM touches (the connection banner) are guarded on `document`.
 */
(function () {
  "use strict";

  var glob = typeof window !== "undefined" ? window : globalThis;
  var RECONNECT_MS = 2000;

  // When the page is served by `meddler serve` (http://host:port/), talk to the WebSocket
  // on the SAME origin (ws://host:port/ws) — port-agnostic, no hardcoded port. Only fall
  // back to the fixed default when opened as a bare file:// (no http origin).
  function defaultWsUrl() {
    if (typeof location !== "undefined" && (location.protocol === "http:" || location.protocol === "https:")) {
      return (location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + "/ws";
    }
    return "ws://127.0.0.1:7677/ws";
  }

  function RealEngine(url) {
    this.url = url || defaultWsUrl();
    this.ws = null;
    this.out = null; // onMessage callback (set in connect)
    this.queue = []; // commands sent before the socket is open
    this.open = false;
    this.closedByUs = false;
    this.everOpened = false; // commands queue only before the first connection
    this.outage = false; // true from the first failure until the socket reopens
  }

  // ---- connection banner (browser only; no-op under Node) ----
  RealEngine.prototype._banner = function (show, text) {
    if (typeof document === "undefined") return;
    var el = document.getElementById("engineBanner");
    if (!show) {
      if (el) el.remove();
      return;
    }
    if (!el) {
      // Styled by style.css (#engineBanner); role=status so screen readers hear it once.
      el = document.createElement("div");
      el.id = "engineBanner";
      el.className = "glass";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    el.textContent = text;
  };

  RealEngine.prototype._fail = function (text) {
    this._banner(true, text);
    // The banner stays up while retrying; announce the outage in the toast stack only once.
    if (!this.outage && this.out) this.out({ type: "toast", text: text, tone: "warn" });
    this.outage = true;
  };

  RealEngine.prototype.connect = function (onMessage) {
    this.out = onMessage;
    this._openSocket();
  };

  RealEngine.prototype._openSocket = function () {
    var self = this;
    var ws;
    try {
      ws = new WebSocket(this.url);
    } catch (e) {
      self._fail(
        "Cannot reach the Meddler engine at " + self.url +
        ". Start it with:  meddler serve --seed 1337  — then reload."
      );
      return;
    }
    this.ws = ws;

    ws.onopen = function () {
      self.open = true;
      self.everOpened = true;
      self.outage = false;
      self._banner(false);
      // Flush anything the UI tried to send before the first connection. The server
      // re-sends hello/status/snapshot on every connection, so the UI resynchronizes.
      var q = self.queue;
      self.queue = [];
      for (var i = 0; i < q.length; i++) ws.send(q[i]);
    };

    ws.onmessage = function (ev) {
      var msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return; // ignore malformed frames rather than crash the UI
      }
      if (self.out) self.out(msg);
    };

    ws.onerror = function () {
      // onerror is followed by onclose; surface the guidance there so it isn't shown twice
      // before the socket ever opened.
      if (!self.open && !self.everOpened) {
        self._fail(
          "Cannot reach the Meddler engine at " + self.url +
          ". Start it with  meddler serve  and this page will connect on its own."
        );
      }
    };

    ws.onclose = function () {
      var wasOpen = self.open;
      self.open = false;
      if (self.closedByUs) return;
      if (wasOpen || self.everOpened) {
        self._fail(
          "Connection to the engine lost. Retrying every " + RECONNECT_MS / 1000 +
          " s. If  meddler serve  stopped, start it again; a restarted server begins a new world."
        );
      }
      // Light auto-reconnect: the server re-sends hello/status/snapshot on a fresh
      // connection, so the UI re-initialises cleanly once it comes back.
      setTimeout(function () {
        if (!self.closedByUs) self._openSocket();
      }, RECONNECT_MS);
    };
  };

  RealEngine.prototype.send = function (cmd) {
    var payload = JSON.stringify(cmd);
    if (this.open && this.ws) {
      this.ws.send(payload);
    } else if (!this.everOpened) {
      this.queue.push(payload); // sent on the first open
    }
    // During an outage commands are dropped rather than replayed into whatever world the
    // server holds when it returns; the reconnect handshake clears pending UI requests.
  };

  RealEngine.prototype.close = function () {
    this.closedByUs = true;
    if (this.ws) this.ws.close();
  };

  glob.MeddlerRealEngine = {
    // Mirrors the mock's `create` shape; takes a ws URL instead of a seed (the seed is
    // fixed server-side by `meddler serve --seed`).
    create: function (url) {
      return new RealEngine(url);
    }
  };
})();
