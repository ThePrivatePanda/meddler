/* Meddler — protocol-v2 UI layer.
 * The default real-engine path renders authoritative world objects and shipment/asset
 * dossiers. Explicit ?engine=mock remains an approximate offline presentation fallback.
 */
"use strict";
(function () {

  // ---------- helpers ----------
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
  const fmt1 = (n) => (Math.round(n * 10) / 10).toFixed(1);
  // Engine money and output figures run to billions; compact them so tiles, axes and chips
  // stay legible. Small values keep one decimal.
  const fmtBig = (n) => {
    const v = Number(n);
    if (!Number.isFinite(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return (v / 1e3).toFixed(1) + "k";
    return fmt1(v);
  };
  // Axis labels get ~30px: three significant digits at most.
  const fmtAxis = (metric, n) => {
    const v = Number(n);
    if (metric === "fx") return v.toFixed(3);
    const a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toPrecision(3) + "T";
    if (a >= 1e9) return (v / 1e9).toPrecision(3) + "B";
    if (a >= 1e6) return (v / 1e6).toPrecision(3) + "M";
    if (a >= 1e4) return (v / 1e3).toPrecision(3) + "k";
    return fmt1(v);
  };
  const fmtInt = (n) => (Number.isFinite(Number(n)) ? Math.round(Number(n)).toLocaleString("en-US") : "—");

  const CCOLORS = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"];
  const XCOLORS = ["#5ea8a2", "#b07fd6", "#a1a15a", "#c46a9a", "#7f9fd0", "#c98f6a"];
  const ASSET_ICON = { sat: "🛰", ship: "🚢", plane: "✈️", train: "🚆" };
  const ASSET_LABEL = { sat: "SATELLITE", ship: "CARGO VESSEL", plane: "AIRCRAFT", train: "RAIL SERVICE" };
  // Public source for this build (AGPL-3.0-or-later: a hosted copy must offer its source).
  // Set once at release; the help overlay links to it whenever it is non-empty. It must be
  // filled in before the hosted demo is published — AGPL §13 requires network users to be
  // offered the source — but only with the confirmed publish remote, never a guess.
  const SOURCE_URL = "";
  const ROUTE_PAGE_SIZE = 40;
  const TRADE_PAGE_SIZE = 50;
  const EVENT_CACHE_LIMIT = 1000;
  const CHART_INTERVAL_CACHE_LIMIT = 24;
  let chartEventTimer = null;

  // ---------- state ----------
  const S = {
    hello: null,
    order: [],
    byCode: {},
    color: {},
    leaders: {},
    running: false,
    tps: 1,
    liveTick: 0,
    primeLive: 0,
    viewTick: 0,
    // False until a world projection has actually arrived. Until then the page knows the
    // world exists but not what tick it is at, and says so rather than showing t0.
    viewReady: false,
    scrubbed: false,
    forks: [],               // [{id,tick,forkTick,label}]
    focusId: "A",
    stats: { A: null, B: null },
    wars: { A: [], B: [] },
    worldObjects: null,
    spark: {},
    events: {},
    eventsByTimeline: { A: {} },
    eventOrderByTimeline: { A: [] },
    markers: [],
    selEvent: null,
    selEventData: null,
    selTimeline: "A",
    selCountry: null,
    selAsset: null,
    selRoute: null,
    routeInfo: null,
    routePage: 0,
    trace: null,
    impact: null,
    detail: null,
    assetInfo: null,
    terminalAsset: null,
    overlay: null,
    god: { kind: null, country: null, target2: null, atTick: null },
    colorCursor: 0,
    nextLocalId: -1,
    dossierEdited: false,
    checkpoints: [],         // [{id,tick,label,note}] — client-side bookmarks (contract §9)
    ckSeq: 1,
    annals: { tab: "history", country: "ALL", group: "ALL", impactSort: "descendants" },
    annalsImpact: {},
    annalsImpactLoading: {},
    annalsData: {},
    annalsDataLoading: {},
    annalsLate: {},
    trade: { carrier: "ALL", commodity: "ALL", status: "ALL", query: "", page: 0 },
    tradeRowCache: {},
    pendingChartJump: null,
    pendingRequests: {},
    requestSeq: 1,
    tracePending: null,
    loadingLabel: "",
    loadingReturn: null,
    scrubFrame: null,
    scrubClientX: null,
    scrubQueuedTick: null,
    speedQueued: null
  };
  const focusTL = () => (S.focusId === "A" ? "A" : "B");
  const activeTimelineId = () => (S.focusId === "A" ? "A" : S.focusId);

  // Engine selection (M8.2a): the shipped default is the REAL engine over the WebSocket
  // bridge (frontend-contract §2.3.1: "full UI on the real engine, no mock fallback") —
  // start it with `meddler serve --seed 1337`. `?engine=mock` runs the offline in-browser
  // mock (`engine.js`); `?ws=<url>` overrides the bridge URL. If the real engine can't be
  // reached, realengine.js shows a banner telling you to start the server — it does NOT
  // silently fall back to the mock (that fallback is exactly what the contract forbids).
  const engineParams = new URLSearchParams(location.search);
  const isMockEngine = engineParams.get("engine") === "mock";
  const engine =
    isMockEngine
      ? window.MeddlerEngine.create(1337)
      : window.MeddlerRealEngine.create(engineParams.get("ws") || undefined);
  let globe = null;

  function pendingByKey(key) {
    return Object.values(S.pendingRequests).find((entry) => entry.key === key) || null;
  }

  function renderActivity() {
    const indicator = $("activityIndicator");
    const entries = Object.values(S.pendingRequests);
    indicator.hidden = entries.length === 0;
    if (!entries.length) return;
    const latest = entries[entries.length - 1];
    $("activityText").textContent = latest.label + (entries.length > 1 ? " · " + entries.length + " tasks" : "");
  }

  function sendCommand(command, label, key) {
    const requestKey = key || null;
    if (requestKey && pendingByKey(requestKey)) return null;
    const requestId = "ui-" + S.requestSeq++;
    const entry = {
      id: requestId,
      key: requestKey,
      command: command,
      label: label || "Updating world…",
      started: Date.now(),
      slow: false
    };
    S.pendingRequests[requestId] = entry;
    renderActivity();
    setTimeout(() => {
      if (!S.pendingRequests[requestId]) return;
      S.pendingRequests[requestId].slow = true;
      S.pendingRequests[requestId].label = "Still working: " + entry.label;
      renderActivity();
    }, 4000);
    engine.send(Object.assign({}, command, { requestId: requestId }));
    return requestId;
  }

  function completeRequest(message) {
    const requestId = message && message.requestId;
    if (requestId == null || !S.pendingRequests[requestId]) return null;
    const entry = S.pendingRequests[requestId];
    delete S.pendingRequests[requestId];
    document.querySelectorAll('[data-pending-request="' + requestId + '"]').forEach((node) => {
      node.disabled = false;
      node.classList.remove("is-pending");
      delete node.dataset.pendingRequest;
    });
    renderActivity();
    if (entry.key === "worldAt" && S.scrubQueuedTick != null) {
      const tick = S.scrubQueuedTick;
      S.scrubQueuedTick = null;
      requestWorldAt(tick);
    }
    if (entry.key === "speed" && S.speedQueued != null) {
      const tps = S.speedQueued;
      S.speedQueued = null;
      if (tps !== entry.command.tps) requestSpeed(tps);
    }
    return entry;
  }

  function clearPending(reason) {
    if (!Object.keys(S.pendingRequests).length) return;
    S.pendingRequests = {};
    S.tracePending = null;
    S.speedQueued = null;
    document.querySelectorAll("[data-pending-request]").forEach((node) => {
      node.disabled = false;
      node.classList.remove("is-pending");
      delete node.dataset.pendingRequest;
    });
    renderActivity();
    if (reason) toast(reason, "warn");
  }

  function loadingHTML(label) {
    return '<div class="loading-state"><i class="loading-spinner"></i><b>' + esc(label) + '</b><span>This can take longer on deep history. Controls remain responsive.</span></div>';
  }

  function requestWorldAt(tick) {
    const bounded = Math.max(0, Math.min(Math.round(tick), S.primeLive));
    if (pendingByKey("worldAt")) {
      S.scrubQueuedTick = bounded;
      return;
    }
    sendCommand({ cmd: "worldAt", tick: bounded }, "Reconstructing tick " + bounded + "…", "worldAt");
  }

  function assignColor(code, idx) {
    if (idx != null && idx < CCOLORS.length) return CCOLORS[idx];
    const c = XCOLORS[S.colorCursor % XCOLORS.length];
    S.colorCursor++;
    return c;
  }

  // S.order lists every country ever seen, in first-seen order, and never shrinks: colors
  // stay attached to codes, and a nation that un-happens on a scrub or restart comes back in
  // the same slot when history reaches it again. Renderers skip codes absent from the
  // focused timeline's stats.
  function registerCountry(meta, idx) {
    const existing = S.byCode[meta.code];
    if (!existing) {
      // A stable founding ordinal from the engine, when provided, pins the color regardless
      // of which nations were still alive when this client connected.
      if (Number.isInteger(meta.ordinal)) {
        S.color[meta.code] = meta.ordinal < CCOLORS.length ? CCOLORS[meta.ordinal] : XCOLORS[(meta.ordinal - CCOLORS.length) % XCOLORS.length];
      } else {
        S.color[meta.code] = assignColor(meta.code, idx);
      }
      S.spark[meta.code] = { stability: [], inflation: [], fx: [] };
    }
    if (S.order.indexOf(meta.code) < 0) S.order.push(meta.code);
    S.byCode[meta.code] = meta;
    S.leaders[meta.code] = meta.leader;
    if (globe) {
      globe.addCountry(meta.code, {
        name: meta.name, color: S.color[meta.code],
        population: meta.population, cities: meta.cities, parent: meta.parent
      });
    }
  }

  // The bridge sends a roster with every world projection: the countries that exist in that
  // view, with their identity. Older builds send it only in `hello`, so both are tolerated.
  function registerRoster(roster) {
    if (!Array.isArray(roster)) return;
    roster.forEach((entry, index) => {
      if (!entry || !entry.code) return;
      const known = S.byCode[entry.code];
      registerCountry(Object.assign({}, known, entry, { placeholder: false }), index);
    });
  }

  // Keep the globe's country set equal to the countries that exist in `stats` (the focused
  // timeline). A code the client has never been introduced to (e.g. a fork-born nation after
  // a reload) gets a minimal placeholder so it still renders.
  function syncCountries(stats) {
    if (!stats) return;
    Object.keys(stats).forEach((code) => {
      if (!S.byCode[code]) {
        registerCountry({
          code: code, name: code, currency: { name: "local currency", symbol: "¤" },
          leader: { title: "", name: "", traits: [] }, population: stats[code].pop, placeholder: true
        });
      } else if (S.order.indexOf(code) < 0) {
        registerCountry(S.byCode[code]);
      }
    });
    if (!globe) return;
    S.order.forEach((code) => {
      const meta = S.byCode[code];
      if (stats[code] && meta && !globe.has(code)) {
        globe.addCountry(code, { name: meta.name, color: S.color[code], population: meta.population, cities: meta.cities, parent: meta.parent });
      } else if (!stats[code] && globe.has(code) && globe.removeCountry) {
        globe.removeCountry(code);
      }
    });
  }

  // Local bookmark/edit narration only. Engine-driven assets never create local headlines.
  function injectLocalEvent(country, headline) {
    const ev = {
      id: S.nextLocalId--, tick: S.viewTick, kind: "DIVINE MEDDLING", severity: 2,
      country: country || S.order[0], parentId: null, depth: 0,
      intervention: true, headline: headline, payload: {}, ledger: []
    };
    S.events[ev.id] = ev;
    addFeedCard(S.focusId === "A" ? "A" : "B", ev);
    if (globe && S.byCode[ev.country]) globe.ping(ev.country, "#9085e9");
    return ev;
  }

  function shipmentLossEvent(shipmentId) {
    const events = Object.values(S.eventsByTimeline[activeTimelineId()] || {});
    return events
      .filter((event) => event.kind === "SHIPMENT_LOST" && event.payload && event.payload.shipment_id === shipmentId)
      .sort((a, b) => b.tick - a.tick)[0] || null;
  }

  // The bridge derives a recent shipment's fate from the event log and states it in the
  // projection (`recentShipments[].status`), so that is the authority on whether a vessel
  // arrived or sank — the client no longer infers it from a headline kind it may never be
  // sent. The projection's memory is bounded, so the event cache stays as a fallback.
  function recentShipmentRecord(payload, shipmentId) {
    const rows = (payload && payload.recentShipments) || [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i].id === shipmentId) return rows[i];
    }
    return null;
  }

  function terminalShipment(info, payload) {
    const tick = Number(payload.tick || 0);
    const record = recentShipmentRecord(payload, info.id);
    const loss = shipmentLossEvent(info.id);
    if (record && record.status === "lost") {
      return Object.assign({}, info, {
        terminal: true, lifecycle: "lost", status: "LOST",
        terminalTick: Number(record.terminalTick),
        progress: record.progress != null ? record.progress : info.progress,
        // Only offered when this page actually holds the loss event; an id it cannot open
        // would be a dead end rather than a paper trail.
        lossEventId: loss ? loss.id : null
      });
    }
    if (record && record.status === "arrived") {
      return Object.assign({}, info, {
        terminal: true, lifecycle: "arrived", status: "ARRIVED", recordedTerminal: true,
        terminalTick: Number(record.terminalTick), progress: 1
      });
    }
    if (loss && loss.tick <= tick) {
      return Object.assign({}, info, {
        terminal: true, lifecycle: "lost", status: "LOST", terminalTick: loss.tick,
        lossEventId: loss.id
      });
    }
    if (Number.isFinite(Number(info.arriveTick)) && tick >= Number(info.arriveTick)) {
      return Object.assign({}, info, {
        terminal: true, lifecycle: "arrived", status: "ARRIVED", terminalTick: Number(info.arriveTick), progress: 1
      });
    }
    return Object.assign({}, info, {
      terminal: true, lifecycle: "unavailable", status: "NO LONGER IN VIEW", terminalTick: tick
    });
  }

  function applyWorldObjects(payload) {
    if (!payload || !globe) return;
    const selectedBefore = S.selAsset ? (globe.getAsset(S.selAsset) || S.assetInfo || S.terminalAsset) : null;
    S.worldObjects = payload;
    globe.setWorldObjects(payload);
    if (S.selAsset) {
      const current = globe.getAsset(S.selAsset);
      if (current) {
        S.assetInfo = current;
        S.terminalAsset = null;
        globe.setSelectedAsset(S.selAsset);
      } else if (selectedBefore && selectedBefore.shipment) {
        S.terminalAsset = terminalShipment(selectedBefore, payload);
        S.assetInfo = S.terminalAsset;
        globe.setSelectedAsset(null);
      } else {
        S.selAsset = null;
        S.assetInfo = null;
        S.terminalAsset = null;
        globe.setSelectedAsset(null);
      }
      renderInspector();
    }
    if (S.selRoute) {
      const currentRoute = globe.getRoute(S.selRoute);
      if (currentRoute) {
        S.routeInfo = currentRoute;
        const pages = Math.max(1, Math.ceil(currentRoute.shipments.length / ROUTE_PAGE_SIZE));
        S.routePage = Math.min(S.routePage, pages - 1);
        globe.setSelectedRoute(S.selRoute);
      } else {
        S.selRoute = null;
        S.routeInfo = null;
        S.routePage = 0;
        globe.setSelectedRoute(null);
      }
      renderInspector();
    }
    if (S.overlay === "trade") scheduleTradeRefresh();
  }

  // Trade Operations re-renders from each frame's worldObjects. At 4× that is four full
  // rebuilds a second, which swallows clicks (the row under the pointer is replaced between
  // press and release) and closes open dropdowns. Refresh at most once a second, and never
  // while a pointer is held down or a filter dropdown has focus.
  let tradeRefreshedAt = 0;
  let tradeRefreshTimer = null;
  let overlayPointerDown = false;
  function scheduleTradeRefresh() {
    if (S.overlay !== "trade") return;
    const focused = document.activeElement;
    // Like a log viewer, the roster holds still while the pointer rests on it.
    const busy = overlayPointerDown || tradeRosterHover ||
      (focused && focused.tagName === "SELECT" && $("overlayCard").contains(focused));
    const wait = 1000 - (Date.now() - tradeRefreshedAt);
    if (busy || wait > 0) {
      if (tradeRefreshTimer == null) {
        tradeRefreshTimer = setTimeout(() => { tradeRefreshTimer = null; scheduleTradeRefresh(); }, Math.max(250, wait));
      }
      return;
    }
    tradeRefreshedAt = Date.now();
    refreshTradeOverlay(false);
  }
  let tradeRosterHover = false;
  $("overlayCard").addEventListener("pointerover", (ev) => { tradeRosterHover = !!(ev.target.closest && ev.target.closest(".trade-roster")); });
  $("overlayCard").addEventListener("pointerleave", () => { tradeRosterHover = false; });
  $("overlayCard").addEventListener("pointerdown", () => { overlayPointerDown = true; });
  window.addEventListener("pointerup", () => { overlayPointerDown = false; });
  window.addEventListener("pointercancel", () => { overlayPointerDown = false; });

  function onCountryDetail(m) {
    S.loadingReturn = null;
    // A nation first seen only through stats (e.g. annexed before this client connected)
    // has a placeholder name; the dossier carries the real one.
    if (m.meta && S.byCode[m.code] && S.byCode[m.code].placeholder) {
      registerCountry(Object.assign({}, S.byCode[m.code], m.meta, { placeholder: false, leader: m.leader || S.byCode[m.code].leader }));
      renderWorld();
    }
    const timelineId = m.tl || activeTimelineId();
    (m.chartEvents || []).forEach((event) => indexEvent(event, timelineId));
    m.chartIntervals = {};
    m.chartIntervalOrder = [];
    m.chartIntervalRequests = {};
    if (m.worldObject && S.worldObjects) {
      const countries = Object.assign({}, S.worldObjects.countries);
      countries[m.code] = m.worldObject;
      applyWorldObjects(Object.assign({}, S.worldObjects, { countries: countries }));
    }
    S.detail = m;
    S.dossierEdited = false;
    openOverlay("detail");
  }

  function chartIntervalKey(startTick, endTick) {
    return String(startTick) + ":" + String(endTick);
  }

  function onCountryChartEvents(m) {
    const detail = S.detail;
    if (!detail || detail.code !== m.code || (detail.tl || "A") !== (m.tl || "A")) return;
    const key = chartIntervalKey(m.startTick, m.endTick);
    delete detail.chartIntervalRequests[key];
    (m.events || []).forEach((event) => indexEvent(event, m.tl || "A"));
    detail.chartIntervals[key] = {
      events: m.events || [], total: Number(m.total || 0), loaded: true
    };
    detail.chartIntervalOrder = detail.chartIntervalOrder.filter((item) => item !== key);
    detail.chartIntervalOrder.push(key);
    while (detail.chartIntervalOrder.length > CHART_INTERVAL_CACHE_LIMIT) {
      const expired = detail.chartIntervalOrder.shift();
      delete detail.chartIntervals[expired];
    }
    if (S.overlay === "detail") {
      const card = $("overlayCard");
      const chart = card.querySelector(".history-chart");
      const index = chart ? Number(chart.dataset.chartIndex) : -1;
      if (index >= 0 && detail.ticks[index] === Number(m.endTick)) {
        showChartPoint(card, detail, index);
      }
    }
  }

  // ---------- engine messages ----------
  function onMessage(m) {
    const completed = completeRequest(m);
    // God edits on a fork are answered with a snapshot of the *fork* world, which this UI
    // would otherwise read as prime. The next frame already carries the fork's new stats.
    if (m.type === "snapshot" && completed && completed.command && completed.command.tl &&
        completed.command.tl !== "A" && /^god/.test(completed.command.cmd)) {
      applyForkEditSnapshot(m, completed.command.tl);
      return;
    }
    dispatchMessage(m, completed);
    if (S.focusQueued != null && !pendingByKey("focusTimeline")) {
      const next = S.focusQueued;
      S.focusQueued = null;
      requestFocus(next);
    }
  }

  // Fold a god-edit reply for a fork into the fork's view without touching feeds; while
  // paused no frame follows, so this is the only visible consequence of the edit.
  function applyForkEditSnapshot(m, timelineId) {
    if (S.focusId !== timelineId) return;
    S.stats.B = m.stats;
    S.wars.B = m.wars || [];
    if (m.leaders) S.leaders = m.leaders;
    applyWorldObjects(m.worldObjects);
    if (globe) globe.setWars(S.wars.B);
    renderWorld();
    if (S.selCountry || S.selAsset || S.selRoute) renderInspector();
  }

  function dispatchMessage(m, completed) {
    if (m.type === "toast" && completed) {
      if (completed.key && completed.key.startsWith("chartEvents:") && S.detail) {
        S.detail.chartIntervalRequests = {};
      }
      if (completed.key && completed.key.startsWith("annalsImpact:")) {
        const timelineId = completed.key.slice("annalsImpact:".length);
        S.annalsImpactLoading[timelineId] = false;
      }
      if (completed.key && completed.key.startsWith("annals:")) {
        S.annalsDataLoading[completed.key.slice("annals:".length)] = false;
        if (S.overlay === "annals" && S.annals.tab === "history") refreshAnnalsOverlay();
      }
      // A refused `newWorld` (an impossible settings combination) leaves the world running;
      // the pending flag must not survive to reset caches on some later reconnect.
      if (completed.key === "newWorld") S.newWorldPending = false;
      if (S.overlay === "loading") {
        const previous = S.loadingReturn;
        S.tracePending = null;
        S.loadingReturn = null;
        if (previous) openOverlay(previous);
        else closeOverlay();
      }
    }
    switch (m.type) {
      case "hello": onHello(m); break;
      case "status": onStatus(m); break;
      case "snapshot": onSnapshot(m); break;
      case "frame": onFrame(m); break;
      case "trace": S.tracePending = null; S.loadingReturn = null; S.trace = m; openOverlay("trace"); break;
      case "eventImpact":
        if (S.selEvent === m.eventId && S.selTimeline === m.tl) {
          S.impact = m;
          // A cause or loss event may never have been delivered to the feed; the impact
          // reply carries its authoritative summary.
          if (!S.selEventData && m.event) {
            indexEvent(m.event, m.tl);
            S.selEventData = m.event;
          }
          renderInspector();
        }
        break;
      case "annalsImpact": onAnnalsImpact(m); break;
      case "annalsData": onAnnalsData(m); break;
      case "countryDetail": onCountryDetail(m); break;
      case "countryChartEvents": onCountryChartEvents(m); break;
      case "countryAdded": onCountryAdded(m); break;
      case "forkStarted": onForkStarted(m); break;
      case "forkDropped":
      {
        // The bridge names the discarded fork in `id`; older builds replied with `kept`
        // alone, and then the dropped id is the one this client asked for.
        const dropped = m.id || (completed && completed.command ? completed.command.id : null);
        S.refocusAfterStatus = true;
        toast(dropped ? "Timeline " + dropped + " discarded." : "Fork discarded.", "info");
        break;
      }
      case "forkAdopted": onForkAdopted(m); break;
      case "timelineFocus": onTimelineFocus(m); break;
      case "settingsAck": onSettingsAck(m); break;
      case "toast": toast(m.text, m.tone); break;
    }
  }

  function onHello(m) {
    const newWorld = !!S.newWorldPending;
    S.newWorldPending = false;
    const reconnect = !!S.hello && !newWorld;
    S.reconnecting = reconnect;
    if (newWorld) resetWorldCaches(true);
    const hadPending = Object.keys(S.pendingRequests).length > 0;
    clearPending(null);
    if (reconnect) toast(hadPending ? "Reconnected to the engine; unfinished requests were cancelled." : "Reconnected to the engine.", "info");
    if (newWorld) toast("✦ Genesis again. Nothing that happened before happened.", "info");
    S.hello = m;
    S.settings = m.settings || {};
    if (globe && globe.destroy) globe.destroy();
    globe = window.MeddlerGlobe.create($("globe"), {
      mockMode: isMockEngine,
      onSelect: (code) => selectCountry(code, true),
      onDetail: (code) => openDossier(code),
      onSelectAsset: (id) => selectAsset(id),
      onSelectRoute: (id) => selectRoute(id),
      onZoom: updateLayerChips
    });
    (m.countries || []).forEach((c, i) => registerCountry(c, i));
    // The wider hello roster includes nations prime has lost, so their names and colors
    // survive a scrub back into the era they existed in.
    registerRoster(m.roster);
    if (!isMockEngine && m.protocol !== 2) {
      toast("This frontend requires bridge protocol 2.", "warn");
    }
    applyWorldObjects(m.worldObjects);
    $("seedChip").textContent = "seed " + m.seed;
    updateLayerChips(globe.getZoom());
    // The handshake says the world exists; the projection that says what is in it comes
    // next, and for a focused fork that is a large message. Say which one is outstanding.
    if (!S.viewReady) {
      $("worldList").innerHTML = '<p class="hint">Reading the world from the engine…</p>';
      renderClock();
    }
    if (!reconnect && !storedFlag(INTRO_KEY) && !S.overlay) openIntro();
    // Running/paused state is owned by the persistent server runtime and arrives next in
    // the authoritative status message. A reconnect must not silently resume a paused world.
  }

  function onCountryAdded(m) {
    registerCountry(Object.assign({}, m.country, { parent: m.country.parent || m.parent }));
    // A god-mode secession is born in a fork (`tl`) and is announced before forkStarted;
    // only merge its world object into the view that is actually on screen.
    const onScreen = (m.tl || "A") === activeTimelineId();
    if (onScreen && m.worldObject && S.worldObjects) {
      const countries = Object.assign({}, S.worldObjects.countries);
      countries[m.country.code] = m.worldObject;
      applyWorldObjects(Object.assign({}, S.worldObjects, { countries: countries }));
    }
    toast("A nation is born: " + m.country.name + " (" + m.country.code + ")" + (m.tl && m.tl !== "A" ? " on timeline " + m.tl : "") + ".", "info");
    if (globe && onScreen) globe.ping(m.country.code, "#9085e9");
  }

  function onStatus(m) {
    S.running = m.running;
    S.tps = m.tps;
    if (globe && globe.setRunning) globe.setRunning(S.running, S.tps);
    S.forks = m.forks || [];
    const previousFocus = S.focusId;
    S.focusId = m.focus || "A";
    if (S.refocusAfterStatus) {
      // Dropping a fork replies with status only. If the focus moved (to a sibling fork or
      // back to prime), fetch that timeline's full view so feeds, columns and ΔWORLD match.
      S.refocusAfterStatus = false;
      if (previousFocus !== S.focusId) requestFocus(S.focusId, true);
    }
    const activeTimelines = new Set(["A"].concat(S.forks.map((fork) => fork.id)));
    Object.keys(S.eventsByTimeline).forEach((timelineId) => {
      if (activeTimelines.has(timelineId)) return;
      Object.keys(S.eventsByTimeline[timelineId]).forEach((id) => {
        if (S.events[id] === S.eventsByTimeline[timelineId][id]) delete S.events[id];
      });
      delete S.eventsByTimeline[timelineId];
      delete S.eventOrderByTimeline[timelineId];
      delete S.annalsImpact[timelineId];
      delete S.annalsImpactLoading[timelineId];
    });
    $("btnPause").textContent = m.running ? "⏸" : "▶";
    $("btnPause").setAttribute("aria-label", m.running ? "Pause" : "Resume");
    document.querySelectorAll("#speeds button").forEach((b) => {
      b.classList.toggle("on", Number(b.dataset.tps) === m.tps);
      b.setAttribute("aria-pressed", Number(b.dataset.tps) === m.tps ? "true" : "false");
    });
    renderTabs();
  }

  function onSnapshot(m) {
    S.viewReady = true;
    delete S.annalsImpact.A;
    S.annalsImpactLoading.A = false;
    S.refocusAfterStatus = false;
    if (S.reconnecting) {
      // A live snapshot behind the last live tick we saw means the server restarted with a
      // new world: drop everything cached from the old one (ids would collide).
      S.reconnecting = false;
      if (!m.scrubbed && m.tick < S.primeLive) resetWorldCaches();
    }
    // `liveTick` (prime's live edge) is authoritative when present. Older bridge builds sent
    // only `live`, as a boolean on the real engine and as prime's tick in the offline mock; a
    // scrub snapshot then says nothing about live, so the last known edge is kept.
    const liveTick = typeof m.liveTick === "number"
      ? m.liveTick
      : (typeof m.live === "number" ? m.live : (m.live ? m.tick : Math.max(S.primeLive, m.tick)));
    S.liveTick = liveTick;
    S.primeLive = liveTick;
    S.viewTick = m.tick;
    // A snapshot is always PRIME (forks arrive as timelineFocus).
    S.focusId = "A";
    S.scrubbed = m.scrubbed;
    S.stats.A = m.stats;
    S.stats.B = null;
    S.wars.A = m.wars;
    S.leaders = m.leaders;
    if (!S.checkpoints.length) {
      S.checkpoints.push({ id: 0, tick: 0, label: "Genesis", note: "The world as the Chronicle first found it." });
    }
    registerRoster(m.roster);
    // A scrub or restart can un-happen countries born later (or bring them back).
    syncCountries(m.stats);
    if (S.selCountry && !m.stats[S.selCountry]) S.selCountry = null;
    for (const code in m.spark) {
      if (!S.spark[code]) continue;
      S.spark[code] = {
        stability: m.spark[code].stability.slice(),
        inflation: m.spark[code].inflation.slice(),
        fx: m.spark[code].fx.slice()
      };
    }
    m.recentEvents.forEach((e) => indexEvent(e, "A"));
    rebuildFeed("A", m.recentEvents);
    $("colB").hidden = true;
    $("headA").textContent = "CHRONICLE";
    $("deltaStrip").hidden = true;
    rebuildMarkers(m.recentEvents, "A");
    applyWorldObjects(m.worldObjects);
    if (globe) globe.setWars(m.wars);
    renderTabs(); renderWorld(); renderClock(); renderScrubBanner(); drawRibbon();
    if (S.selCountry) renderInspector();
    if (S.pendingChartJump && S.pendingChartJump.tick === m.tick) {
      const pending = S.pendingChartJump;
      S.pendingChartJump = null;
      if (pending.event) {
        indexEvent(pending.event, "A");
        selectEvent(pending.event.id, "A", pending.event);
        jumpToFeedCard(pending.event.id);
      } else {
        toast("Viewing the selected chart point at t" + m.tick + ".", "info");
      }
    }
  }

  function resetWorldCaches(full) {
    clearSelections();
    if (full) {
      // A regenerated genesis is a different world: even the roster of nations is new.
      S.order = [];
      S.byCode = {};
      S.color = {};
      S.spark = {};
      S.colorCursor = 0;
      S.stats = { A: null, B: null };
      S.forks = [];
      ["feedA", "feedB"].forEach((id) => { $(id).innerHTML = ""; });
    }
    S.events = {};
    S.eventsByTimeline = { A: {} };
    S.eventOrderByTimeline = { A: [] };
    S.markers = [];
    S.annalsImpact = {};
    S.annalsImpactLoading = {};
    S.annalsData = {};
    S.annalsDataLoading = {};
    S.annalsLate = {};
    S.checkpoints = [];
    S.primeLive = 0;
    S.liveTick = 0;
    Object.keys(S.spark).forEach((code) => { S.spark[code] = { stability: [], inflation: [], fx: [] }; });
    Object.keys(worldCards).forEach((code) => { delete worldCards[code]; });
    $("worldList").innerHTML = "";
    renderInspector();
  }

  function onTimelineFocus(m) {
    S.viewReady = true;
    S.reconnecting = false;
    S.refocusAfterStatus = false;
    registerRoster(m.rosterA);
    registerRoster(m.rosterB);
    delete S.annalsImpact[m.id];
    S.annalsImpactLoading[m.id] = false;
    S.focusId = m.id;
    // Prime's live edge, which in fork mode runs ahead of the tick on screen. Older builds
    // sent it as `live`; `liveTick` is the name every other projection uses.
    const primeEdge = typeof m.liveTick === "number" ? m.liveTick : m.live;
    if (typeof primeEdge === "number") S.primeLive = primeEdge;
    S.liveTick = m.tick;
    S.viewTick = m.tick;
    S.stats.A = m.statsA;
    S.stats.B = m.statsB;
    S.wars.A = m.warsA;
    S.wars.B = m.warsB;
    S.leaders = m.leadersB;
    syncCountries(m.statsB);
    m.recentA.forEach((e) => indexEvent(e, "A"));
    m.recentB.forEach((e) => indexEvent(e, m.id));
    rebuildFeed("A", m.recentA.filter((e) => e.tick <= m.tick));
    rebuildFeed("B", m.recentB);
    $("colB").hidden = false;
    $("headA").textContent = "TIMELINE A · PRIME";
    $("headB").textContent = "TIMELINE " + m.id + " ✦ " + forkLabel(m.label);
    rebuildMarkers(m.recentB, "B");
    applyWorldObjects(m.worldObjectsB);
    if (globe) globe.setWars(m.warsB);
    S.scrubbed = false;
    renderTabs(); renderWorld(); renderClock(); renderScrubBanner(); drawRibbon();
    if (S.selCountry) renderInspector();
  }

  function rebuildMarkers(recent, tl) {
    S.markers = S.markers.filter((mk) => mk.tl !== tl);
    (recent || []).forEach((e) => {
      if (e.intervention) S.markers.push({ tick: e.tick, kind: "iv", tl: tl });
      else if (e.severity === 2) S.markers.push({ tick: e.tick, kind: "crisis", tl: tl });
    });
  }

  function onFrame(m) {
    S.viewReady = true;
    S.viewTick = m.tick;
    if (typeof m.liveTick === "number") S.primeLive = m.liveTick;
    if (S.focusId === "A") { S.liveTick = m.tick; S.primeLive = Math.max(S.primeLive, m.tick); }
    else S.liveTick = Math.max(S.liveTick, m.tick);
    S.stats.A = m.timelines.A.stats;
    S.wars.A = m.wars || [];
    if (m.timelines.B) {
      S.stats.B = m.timelines.B.stats;
      S.wars.B = m.warsB || [];
    }
    S.leaders = (S.focusId !== "A" && m.leadersB) ? m.leadersB : m.leaders;

    const src = S.stats[focusTL()] || S.stats.A;
    syncCountries(src);
    for (const code in src) {
      const sp = S.spark[code];
      if (!sp) continue;
      sp.stability.push(src[code].stability);
      sp.inflation.push(src[code].inflation);
      sp.fx.push(src[code].fx);
      if (sp.stability.length > 120) { sp.stability.shift(); sp.inflation.shift(); sp.fx.shift(); }
    }

    // In fork mode column A is prime *aligned* to the fork's tick: the bridge reads prime's
    // recorded events at that tick out of the log, so the column replays the history the
    // fork diverged from instead of prime's live edge.
    const eventsA = m.timelines.A.events || [];
    eventsA.forEach((e) => { indexEvent(e, "A"); markEvent(e, "A"); });
    addFeedCards("A", eventsA);
    if (m.timelines.B) {
      const eventsB = m.timelines.B.events || [];
      eventsB.forEach((e) => { indexEvent(e, S.focusId); markEvent(e, "B"); });
      addFeedCards("B", eventsB);
    }

    const focusedObjects = m.timelines.B ? m.timelines.B.worldObjects : m.timelines.A.worldObjects;
    applyWorldObjects(focusedObjects);
    if (globe) globe.setWars(S.wars[focusTL()] || []);
    if (m.diff && S.focusId !== "A") renderDeltaStrip(m.diff);
    renderWorld(); renderClock(); drawRibbon();
    if (S.selCountry || S.selAsset || S.selRoute) renderInspector();
  }

  function indexEvent(e, timelineId) {
    const tl = timelineId || "A";
    if (!S.eventsByTimeline[tl]) S.eventsByTimeline[tl] = {};
    if (!S.eventOrderByTimeline[tl]) S.eventOrderByTimeline[tl] = [];
    if (!Object.prototype.hasOwnProperty.call(S.eventsByTimeline[tl], e.id)) {
      S.eventOrderByTimeline[tl].push(e.id);
    }
    S.eventsByTimeline[tl][e.id] = e;
    S.events[e.id] = e;
    while (S.eventOrderByTimeline[tl].length > EVENT_CACHE_LIMIT) {
      const expiredId = S.eventOrderByTimeline[tl].shift();
      const expired = S.eventsByTimeline[tl][expiredId];
      delete S.eventsByTimeline[tl][expiredId];
      if (S.events[expiredId] === expired) delete S.events[expiredId];
    }
  }
  function markEvent(e, tl) {
    const visible = (S.focusId === "A") ? "A" : "B";
    if (globe && tl === visible) globe.noteEvent(e);
    if (e.intervention) {
      S.markers.push({ tick: e.tick, kind: "iv", tl: tl });
      if (globe && tl === visible) globe.ping(e.country, "#9085e9");
    } else if (e.severity === 2) {
      S.markers.push({ tick: e.tick, kind: "crisis", tl: tl });
      flashCountry(e.country);
      if (globe && tl === visible) globe.ping(e.country, "#d03b3b");
    }
    if (S.markers.length > 2000) S.markers.splice(0, S.markers.length - 2000);
  }

  function updateLayerChips(zoom, layers) {
    $("zoomChip").textContent = zoom.toFixed(1) + "×";
    // The globe owns its level-of-detail rules; older globe builds only report zoom.
    const L = layers || (globe && globe.getLayers ? globe.getLayers() : null);
    $("layerSea").classList.toggle("on", L ? !!L.sea : zoom > 1.25);
    $("layerAir").classList.toggle("on", L ? !!L.air : zoom > 1.25);
    $("layerRail").classList.toggle("on", L ? !!L.rail : zoom > 2.2);
  }

  // ---------- timeline tabs ----------
  // Focus switches are serialized: a click while one is in flight is remembered and sent
  // once the reply lands, so a slow reply never swallows the user's latest choice.
  function requestFocus(id, force) {
    if (pendingByKey("focusTimeline")) { S.focusQueued = id; return; }
    if (!force && id === S.focusId) return;
    sendCommand({ cmd: "focusTimeline", id: id }, "Switching timeline…", "focusTimeline");
  }

  // Fork labels arrive as "<KIND> @ <CODE>"; show the catalog's own name and the nation.
  function forkLabel(raw) {
    const text = String(raw || "");
    const match = /^([A-Z_]+) @ ([A-Z]+)$/.exec(text);
    if (!match) return text;
    const iv = (S.hello ? S.hello.interventions : []).find((item) => item.kind === match[1]);
    return (iv ? iv.label : humanKind(match[1])) + " · " + cname(match[2]);
  }

  function renderTabs() {
    const bar = $("tlbar");
    if (!S.forks.length) { bar.hidden = true; return; }
    bar.hidden = false;
    $("btnNewFork").disabled = S.forks.length >= 3;
    $("btnNewFork").title = S.forks.length >= 3 ? "Three forks are running. Adopt or discard one first." : "Fork a new timeline [g]";
    let h = '<button class="tltab' + (S.focusId === "A" ? " on" : "") + '" data-tl="A">◉ PRIME</button>';
    S.forks.forEach((f) => {
      h += '<span class="tltabwrap' + (S.focusId === f.id ? " on" : "") + '">' +
        '<button class="tltab" data-tl="' + f.id + '" title="' + esc(forkLabel(f.label)) + " · forked at t" + f.forkTick + '">✦ ' + f.id + " · " + esc(forkLabel(f.label).length > 34 ? forkLabel(f.label).slice(0, 33) + "…" : forkLabel(f.label)) + "</button>" +
        '<button class="tlmini adopt" title="Adopt timeline ' + f.id + ' as reality" aria-label="Adopt timeline ' + f.id + ' as reality" data-adopt="' + f.id + '">★</button>' +
        '<button class="tlmini" title="Discard timeline ' + f.id + '" aria-label="Discard timeline ' + f.id + '" data-drop="' + f.id + '">✕</button>' +
        "</span>";
    });
    $("tlTabs").innerHTML = h;
    $("tlTabs").querySelectorAll(".tltab").forEach((b) => {
      b.addEventListener("click", () => requestFocus(b.dataset.tl));
    });
    $("tlTabs").querySelectorAll("[data-drop]").forEach((b) => {
      b.addEventListener("click", () => sendCommand(
        { cmd: "dropFork", id: b.dataset.drop }, "Discarding fork…", "dropFork:" + b.dataset.drop
      ));
    });
    $("tlTabs").querySelectorAll("[data-adopt]").forEach((b) => {
      b.addEventListener("click", () => sendCommand(
        { cmd: "adoptFork", id: b.dataset.adopt }, "Adopting fork…", "adoptFork"
      ));
    });
  }

  function onForkStarted(m) {
    S.viewReady = true;
    if (S.overlay === "loading") closeOverlay();
    S.loadingReturn = null;
    delete S.annalsImpact[m.id];
    S.annalsImpactLoading[m.id] = false;
    S.focusId = m.id;
    S.markers = S.markers.filter((mk) => mk.tl === "A" && mk.tick <= m.tick);
    $("colB").hidden = false;
    $("headA").textContent = "TIMELINE A · PRIME";
    $("headB").textContent = "TIMELINE " + m.id + " ✦ " + forkLabel(m.label);
    registerRoster(m.roster);
    m.sharedRecent.forEach((e) => { indexEvent(e, "A"); indexEvent(e, m.id); });
    rebuildFeed("A", m.sharedRecent);
    rebuildFeed("B", m.sharedRecent);
    ["feedA", "feedB"].forEach((fid) => {
      const li = document.createElement("li");
      li.className = "forkdivider";
      li.textContent = "── FORK POINT · t" + m.tick + " ──";
      $(fid).insertBefore(li, $(fid).firstChild);
    });
    S.scrubbed = false;
    applyWorldObjects(m.worldObjects);
    renderScrubBanner(); renderClock(); drawRibbon();
    toast("Timeline " + m.id + " forked — watching your version unfold.", "info");
  }

  function onForkAdopted(m) {
    toast("Timeline " + m.id + " is now reality. Other drafts dissolved with it.", "info");
  }

  // ---------- clock ----------
  function renderClock() {
    const t = S.viewTick;
    if (!S.viewReady) {
      // A reconnect into a fork waits on a whole timeline projection. Genesis is not a safe
      // guess for where the world is, so the clock stays blank until the engine says.
      $("clockDate").textContent = "Reading the world…";
      $("clockTick").textContent = "t—";
      return;
    }
    $("clockDate").textContent = "Year " + (Math.floor(t / 365) + 1) + " · Day " + ((t % 365) + 1);
    $("clockTick").textContent = "t" + t + (S.focusId !== "A" ? " · " + S.focusId : "");
  }

  // ---------- world pane ----------
  function statusBand(metric, v) {
    if (metric === "stability") {
      if (v >= 60) return "var(--good)";
      if (v >= 35) return "var(--warn)";
      if (v >= 15) return "var(--serious)";
      return "var(--crit)";
    }
    if (v < 4) return "var(--good)";
    if (v < 8) return "var(--warn)";
    if (v < 12) return "var(--serious)";
    return "var(--crit)";
  }

  function sparkSVG(arr, w, h, color, fill) {
    if (!arr || arr.length < 2) return "<svg></svg>";
    let mn = Infinity, mx = -Infinity;
    arr.forEach((v) => { if (v < mn) mn = v; if (v > mx) mx = v; });
    if (mx - mn < 1e-6) { mx += 1; mn -= 1; }
    const pad = (mx - mn) * 0.12; mn -= pad; mx += pad;
    const pts = arr.map((v, i) => {
      const x = (i / (arr.length - 1)) * w;
      const y = h - ((v - mn) / (mx - mn)) * h;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    const last = pts[pts.length - 1].split(",");
    let s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">';
    if (fill) s += '<polygon points="0,' + h + " " + pts.join(" ") + " " + w + "," + h + '" fill="' + color + '" opacity="0.12"/>';
    s += '<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + color + '" stroke-width="2" vector-effect="non-scaling-stroke"/>';
    s += '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="2.6" fill="' + color + '"/>';
    return s + "</svg>";
  }

  function badgesFor(code, st, wars) {
    const b = [];
    if (wars.some((w) => w[0] === code || w[1] === code)) b.push('<span class="badge war">⚔ WAR</span>');
    if (st.inflation > 8) b.push('<span class="badge crit">INFL ' + fmt1(st.inflation) + "%</span>");
    if (st.stability < 35) b.push('<span class="badge crit">UNREST</span>');
    if (st.grainDays < 12) b.push('<span class="badge warn">FAMINE</span>');
    if (st.treasury < 0) b.push('<span class="badge warn">DEBT</span>');
    return b.join("");
  }

  // World cards are keyed by code and updated in place: a card that is rebuilt every frame
  // swallows clicks at high speed (the pressed node vanishes before release) and defeats the
  // meters' width transition, which is the only animation that says "this value moved".
  const worldCards = {};
  function worldCard(code) {
    let el = worldCards[code];
    if (el) return el;
    el = document.createElement("div");
    el.className = "ccard";
    el.dataset.code = code;
    el.id = "card-" + code;
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      ev.preventDefault();
      selectCountry(code);
    });
    el.innerHTML =
      '<div class="crow"><span class="emblem"></span><span class="cname"></span><span class="cfx mono"></span></div>' +
      '<div class="cbadges"></div>' +
      '<div class="meters">' +
      '<div class="meter"><div class="mlabel"><span>STAB</span><span class="mval" data-f="stab"></span></div><div class="mbar"><i data-f="stabbar"></i></div></div>' +
      '<div class="meter"><div class="mlabel"><span>INFL</span><span class="mval" data-f="infl"></span></div><div class="mbar"><i data-f="inflbar"></i></div></div>' +
      "</div>" +
      '<div class="cspark"></div>';
    el._f = {
      emblem: el.querySelector(".emblem"), name: el.querySelector(".cname"), fx: el.querySelector(".cfx"),
      badges: el.querySelector(".cbadges"), spark: el.querySelector(".cspark"),
      stab: el.querySelector('[data-f="stab"]'), stabbar: el.querySelector('[data-f="stabbar"]'),
      infl: el.querySelector('[data-f="infl"]'), inflbar: el.querySelector('[data-f="inflbar"]')
    };
    el._last = {};
    worldCards[code] = el;
    return el;
  }

  function setIfChanged(el, key, value, apply) {
    if (el._last[key] === value) return;
    el._last[key] = value;
    apply(value);
  }

  function renderWorld() {
    const stats = S.stats[focusTL()] || S.stats.A;
    if (!stats) return;
    const wars = S.wars[focusTL()] || [];
    const list = $("worldList");
    let prev = null;
    S.order.forEach((code) => {
      const st = stats[code];
      const c = S.byCode[code];
      if (!st || !c) return;
      const el = worldCard(code);
      const f = el._f;
      const sp = (S.spark[code] ? S.spark[code].stability : []).slice(-60);
      const fxs = S.spark[code] ? S.spark[code].fx : [];
      const fxDelta = fxs.length > 5 ? st.fx - fxs[fxs.length - 6] : 0;
      const arrow = fxDelta > 0.001 ? '<span class="up">▲</span>' : (fxDelta < -0.001 ? '<span class="dn">▼</span>' : "─");
      setIfChanged(el, "color", S.color[code], (v) => { f.emblem.style.setProperty("--c", v); f.emblem.textContent = code; });
      setIfChanged(el, "name", c.name, (v) => { f.name.textContent = v; el.setAttribute("aria-label", v); });
      setIfChanged(el, "fx", esc(c.currency.symbol) + " " + st.fx.toFixed(2) + " " + arrow, (v) => { f.fx.innerHTML = v; });
      setIfChanged(el, "badges", badgesFor(code, st, wars), (v) => { f.badges.innerHTML = v; });
      setIfChanged(el, "stab", fmt1(st.stability), (v) => { f.stab.textContent = v; });
      setIfChanged(el, "stabbar", Math.max(2, st.stability) + "%|" + statusBand("stability", st.stability), (v) => {
        const parts = v.split("|"); f.stabbar.style.width = parts[0]; f.stabbar.style.setProperty("--mc", parts[1]);
      });
      setIfChanged(el, "infl", fmt1(st.inflation) + "%", (v) => { f.infl.textContent = v; });
      setIfChanged(el, "inflbar", Math.max(2, Math.min(100, st.inflation * 6)) + "%|" + statusBand("inflation", st.inflation), (v) => {
        const parts = v.split("|"); f.inflbar.style.width = parts[0]; f.inflbar.style.setProperty("--mc", parts[1]);
      });
      setIfChanged(el, "spark", sparkSVG(sp, 240, 26, "#3987e5", true), (v) => { f.spark.innerHTML = v; });
      el.classList.toggle("sel", S.selCountry === code);
      const expected = prev ? prev.nextSibling : list.firstChild;
      if (expected !== el) list.insertBefore(el, expected);
      prev = el;
    });
    if (prev) {
      while (prev.nextSibling) list.removeChild(prev.nextSibling);
    }
    $("worldTL").hidden = S.focusId === "A";
    $("worldTL").textContent = S.focusId === "A" ? "" : "· TIMELINE " + S.focusId;
  }

  const pendingFlash = {};
  function flashCountry(code) {
    pendingFlash[code] = true;
    requestAnimationFrame(() => {
      for (const k in pendingFlash) {
        const el = document.getElementById("card-" + k);
        if (el) {
          el.classList.remove("flash");
          requestAnimationFrame(() => el.classList.add("flash"));
        }
        delete pendingFlash[k];
      }
    });
  }

  // ---------- chronicle feed ----------
  const humanKind = (kind) => {
    const text = String(kind || "").replace(/^INTERVENE_/, "").replace(/_/g, " ").toLowerCase();
    return text.charAt(0).toUpperCase() + text.slice(1);
  };
  const GLYPH = (e) => e.intervention ? "✦" : (e.severity === 2 ? "⚡" : (e.parentId == null ? "●" : "▸"));

  function eventCardHTML(e, hint) {
    const col = S.color[e.country] || "#3987e5";
    return '<span class="ev-glyph">' + GLYPH(e) + "</span>" +
      '<div class="ev-main"><div class="ev-meta">' +
      '<span class="ev-tick">t' + e.tick + "</span>" +
      '<span class="cchip" style="--c:' + col + '">' + esc(e.country) + "</span>" +
      '<span class="ev-kind">' + esc(String(e.kind).replace("INTERVENE_", "✦ ")) + "</span>" +
      (hint ? '<span class="ev-hint">click to see why</span>' : "") +
      "</div>" +
      '<p class="ev-head">' + esc(e.headline) + "</p></div>";
  }

  function makeCard(e, tl) {
    const li = document.createElement("li");
    const timelineId = tl === "B" ? S.focusId : "A";
    li.className = "ev sev" + e.severity + (e.intervention ? " iv" : "") + (S.selEvent === e.id && S.selTimeline === timelineId ? " sel" : "");
    li.dataset.id = e.id;
    li.dataset.tl = timelineId;
    // First-visit affordance: the first crisis headline says it can be interrogated.
    const hint = !S.whyHintSeen && !S.whyHintShown && e.severity === 2 && !e.intervention;
    if (hint) S.whyHintShown = true;
    li.innerHTML = eventCardHTML(e, hint);
    li.addEventListener("click", () => selectEvent(e.id, timelineId, e));
    li.addEventListener("dblclick", () => requestTrace(e.id, timelineId));
    return li;
  }

  function addFeedCards(tl, events) {
    if (!events.length) return;
    const feed = $("feed" + tl);
    const atTop = feed.scrollTop < 30;
    const beforeHeight = atTop ? 0 : feed.scrollHeight;
    const fragment = document.createDocumentFragment();
    events.forEach((event) => fragment.insertBefore(makeCard(event, tl), fragment.firstChild));
    feed.insertBefore(fragment, feed.firstChild);
    if (!atTop) {
      requestAnimationFrame(() => {
        feed.scrollTop += Math.max(0, feed.scrollHeight - beforeHeight);
      });
    }
    updateFollowPill(feed);
    while (feed.children.length > 150) feed.removeChild(feed.lastChild);
  }

  function addFeedCard(tl, event) {
    addFeedCards(tl, [event]);
  }

  function rebuildFeed(tl, events) {
    const feed = $("feed" + tl);
    feed.innerHTML = "";
    if (tl === "A") S.whyHintShown = false;
    events.slice().reverse().forEach((e) => feed.appendChild(makeCard(e, tl)));
    feed.scrollTop = 0;
  }

  function updateFollowPill(feed) {
    if (feed.id !== "feedA") return;
    $("followPill").hidden = feed.scrollTop < 30;
  }
  $("feedA").addEventListener("scroll", function () { updateFollowPill(this); });
  $("followPill").addEventListener("click", () => {
    $("feedA").scrollTop = 0;
    $("followPill").hidden = true;
  });

  // ---------- selection & inspector ----------
  function clearSelections() {
    S.selEvent = null; S.selEventData = null; S.selCountry = null; S.selAsset = null; S.selRoute = null; S.impact = null;
    S.assetInfo = null; S.terminalAsset = null; S.routeInfo = null; S.routePage = 0;
    document.querySelectorAll(".ev.sel,.ccard.sel").forEach((n) => n.classList.remove("sel"));
    if (globe) { globe.setSelected(null); globe.setSelectedAsset(null); globe.setSelectedRoute(null); }
  }

  function selectEvent(id, timelineId, eventData) {
    if (!S.whyHintSeen) {
      S.whyHintSeen = true;
      storeFlag(HINT_KEY);
      document.querySelectorAll(".ev-hint").forEach((n) => n.remove());
    }
    clearSelections();
    S.selEvent = id;
    S.selTimeline = timelineId || (S.focusId === "A" ? "A" : S.focusId);
    const timelineEvents = S.eventsByTimeline[S.selTimeline] || {};
    S.selEventData = eventData || timelineEvents[id] || S.events[id] || null;
    document.querySelectorAll('.ev[data-id="' + id + '"][data-tl="' + S.selTimeline + '"]').forEach((n) => n.classList.add("sel"));
    renderInspector();
    if (id >= 0) {
      sendCommand(
        { cmd: "eventImpact", eventId: id, tl: S.selTimeline, horizon: 100 },
        "Calculating event impact…",
        "eventImpact:" + S.selTimeline + ":" + id
      );
    }
  }
  function selectCountry(code, fromGlobe) {
    clearSelections();
    S.selCountry = code;
    if (globe) {
      globe.setSelected(code);
      if (!fromGlobe) globe.focus(code);
    }
    renderWorld();
    renderInspector();
  }
  function selectAsset(id, fallbackInfo) {
    const current = globe ? globe.getAsset(id) : null;
    let info = current || fallbackInfo || null;
    if (!info) {
      toast("That shipment has just left the retained operations window.", "info");
      return false;
    }
    if (!current && info.shipment && !info.terminal) {
      info = terminalShipment(info, S.worldObjects || { tick: S.viewTick });
    }
    clearSelections();
    S.selAsset = id;
    S.assetInfo = info;
    S.terminalAsset = current ? null : info;
    if (globe) globe.setSelectedAsset(current ? id : null);
    renderInspector();
    return true;
  }
  function selectRoute(id) {
    clearSelections();
    S.selRoute = id;
    S.routePage = 0;
    if (globe) {
      globe.setSelectedRoute(id);
      S.routeInfo = globe.getRoute(id);
    }
    renderInspector();
  }
  $("worldList").addEventListener("click", (ev) => {
    const card = ev.target.closest(".ccard");
    if (card) selectCountry(card.dataset.code);
  });
  $("worldList").addEventListener("dblclick", (ev) => {
    const card = ev.target.closest(".ccard");
    if (card) openDossier(card.dataset.code);
  });

  // Like the world list, the inspector re-renders on every frame; hold that off while a
  // pointer is pressed inside it so the button under the pointer survives until release.
  let inspectorPointerDown = false;
  let inspectorDeferred = false;
  $("inspBody").addEventListener("pointerdown", () => { inspectorPointerDown = true; });
  // `pointercancel` matters on touch: a press that turns into a scroll never sends
  // `pointerup`, and without this the inspector stays frozen until some later release.
  const releaseInspector = () => {
    inspectorPointerDown = false;
    if (inspectorDeferred) { inspectorDeferred = false; requestAnimationFrame(() => renderInspector()); }
  };
  window.addEventListener("pointerup", releaseInspector);
  window.addEventListener("pointercancel", releaseInspector);

  function renderInspector() {
    const box = $("inspBody");
    if (inspectorPointerDown) { inspectorDeferred = true; return; }
    if (S.selAsset && globe) {
      const info = globe.getAsset(S.selAsset) || S.terminalAsset || S.assetInfo;
      if (info) { S.assetInfo = info; box.innerHTML = inspectorAssetHTML(info); wireInspector(); return; }
    }
    if (S.selRoute && globe) {
      const info = globe.getRoute(S.selRoute) || S.routeInfo;
      if (info) { S.routeInfo = info; box.innerHTML = inspectorRouteHTML(info); wireInspector(); return; }
    }
    if (S.selEvent != null && S.selEventData) { box.innerHTML = inspectorEventHTML(S.selEventData); wireInspector(); return; }
    if (S.selEvent != null) {
      box.innerHTML = '<p class="hint">Fetching event #' + esc(S.selEvent) + " from timeline " + esc(S.selTimeline) + "…</p>";
      return;
    }
    if (S.selCountry) { box.innerHTML = inspectorCountryHTML(S.selCountry); wireInspector(); return; }
    box.innerHTML = '<p class="hint">Select a headline, country, route, or moving object on the globe.<br>Click a lane to list every active shipment on it.<br><kbd>t</kbd> traces an event · <kbd>d</kbd> opens a country dossier.</p>';
  }

  function conditionBar(v) {
    const col = v >= 70 ? "var(--good)" : (v >= 40 ? "var(--warn)" : "var(--crit)");
    return '<div class="mbar" style="margin-top:3px"><i style="width:' + Math.max(2, v) + '%; --mc:' + col + '"></i></div>';
  }

  function shipmentLifecycleHTML(a) {
    if (!a.terminal) return "";
    // Whoever decided this shipment was finished, the projection is the authority on how it
    // ended while the record is still retained.
    const record = recentShipmentRecord(S.worldObjects, a.id);
    const recordedArrival = a.recordedTerminal || !!(record && record.status === "arrived");
    if (a.lifecycle === "lost") {
      const lossEventId = a.terminalEventId != null ? a.terminalEventId : a.lossEventId;
      // Offered only for an event this page can actually open. Some event kinds are not
      // published to the client at all, and a button that leads nowhere is worse than none.
      const held = lossEventId != null &&
        ((S.eventsByTimeline[activeTimelineId()] || {})[lossEventId] || S.events[lossEventId]);
      return '<div class="shipment-terminal lost"><b>GOODS NOT DELIVERED · t' + a.terminalTick + "</b>" +
        "<span>The recorded cost was paid at dispatch. The loss event carries the immediate stability/economic effects; later inventory and trade state show subsequent consequences.</span>" +
        (held ? '<button data-act="ev" data-id="' + lossEventId + '" data-tl="' + activeTimelineId() + '">Inspect loss event</button>' : "") + "</div>";
    }
    if (a.lifecycle === "arrived") {
      // The bridge states an arrival it read from the log; only an inference from the
      // scheduled tick has to hedge.
      return recordedArrival
        ? '<div class="shipment-terminal arrived"><b>DELIVERED · t' +
          (record && record.terminalTick != null ? record.terminalTick : a.terminalTick) + "</b>" +
          "<span>The engine recorded this shipment's arrival at that tick; the goods are in the destination's inventory.</span></div>"
        : '<div class="shipment-terminal arrived"><b>NO LONGER IN FLIGHT · t' + a.terminalTick + "</b>" +
          "<span>The shipment disappeared at or after its scheduled arrival tick, so its lifecycle is complete. No arrival event is fabricated.</span></div>";
    }
    return '<div class="shipment-terminal unavailable"><b>NOT IN THIS RECONSTRUCTED VIEW</b>' +
      "<span>The last observed shipment is not present at this timeline tick. Its mover has been removed from the globe.</span></div>";
  }

  function distanceKmText(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return Math.max(0, Math.round(Number(value))).toLocaleString() + " km";
  }

  function distanceLeftText(value) {
    const distance = distanceKmText(value);
    return distance === "—" ? "Distance unavailable" : distance + " left";
  }

  function inspectorRouteHTML(route) {
    const type = { sea: "ship", air: "plane", rail: "train" }[route.carrier] || "ship";
    const icon = ASSET_ICON[type];
    const shipments = route.shipments || [];
    const activeCount = Number(route.shipmentCount || 0);
    const recentCount = Number(route.recentShipmentCount || 0);
    const pageCount = Math.max(1, Math.ceil(shipments.length / ROUTE_PAGE_SIZE));
    S.routePage = Math.max(0, Math.min(S.routePage, pageCount - 1));
    const start = S.routePage * ROUTE_PAGE_SIZE;
    const page = shipments.slice(start, start + ROUTE_PAGE_SIZE);
    let h = '<div class="insp-title">' + icon + " " + esc(route.routeDesc) + "</div>" +
      '<div class="insp-sub">' + esc(String(route.carrier).toUpperCase()) + " LANE · STABLE SHIPMENT ROSTER</div>" +
      '<table class="stattable"><tr><td>In flight</td><td>' + activeCount + "</td></tr>" +
      "<tr><td>Recent outcomes</td><td>" + recentCount + " · arrivals 4t / losses 8t</td></tr>" +
      "<tr><td>Total quantity</td><td>" + fmt1(route.volume) + " active units</td></tr>" +
      "<tr><td>Route distance</td><td>" + distanceKmText(route.distanceKm) + "</td></tr>" +
      "<tr><td>Endpoint condition</td><td>" + route.condition + "%</td></tr>" +
      "<tr><td>Direction</td><td>" + esc(route.origin + " → " + route.dest) + "</td></tr></table>" +
      conditionBar(route.condition) +
      '<div class="route-roster-head"><span>SHIPMENT ROSTER · DISPATCH ORDER</span><b>' + (shipments.length ? (start + 1) + "–" + (start + page.length) : "0") + " / " + shipments.length + "</b></div>";
    if (!page.length) {
      h += '<div class="logistics-empty">No active or recently completed shipments remain on this lane.</div>';
    } else {
      h += '<div class="route-roster">';
      page.forEach((shipment) => {
        const progress = Math.round(Number(shipment.progress || 0) * 100);
        const lifecycle = shipment.lifecycle || "in_transit";
        const terminal = lifecycle === "arrived" || lifecycle === "lost";
        const terminalMeta = lifecycle === "arrived"
          ? "ARRIVED · t" + shipment.terminalTick + " · clears in " + shipment.retentionTicksRemaining + "t"
          : "LOST · t" + shipment.terminalTick + " · clears in " + shipment.retentionTicksRemaining + "t";
        const liveMeta = distanceLeftText(shipment.remainingDistanceKm) + " · ETA t" + shipment.arriveTick + " · " + shipment.condition + "%";
        h += '<button class="route-shipment' + (terminal ? " terminal " + lifecycle : "") + '" data-act="routeasset" data-id="' + shipment.id + '">' +
          '<span class="route-shipment-icon">' + ASSET_ICON[shipment.type] + "</span>" +
          '<span class="route-shipment-main"><b>' + esc(shipment.id) + "</b><span>" + esc(shipment.commodity) + " · " + fmt1(shipment.qty) + " units</span></span>" +
          '<span class="route-shipment-progress"><b>' + (terminal ? lifecycle.toUpperCase() : progress + "%") + '</b><i><em style="width:' + progress + '%"></em></i></span>' +
          '<span class="route-shipment-meta"><b>' + esc(terminal ? terminalMeta : liveMeta) + "</b> · t" + shipment.departTick + "→t" + shipment.arriveTick + "</span></button>";
      });
      h += "</div>";
    }
    if (pageCount > 1) {
      h += '<div class="route-pager"><button class="abtn" data-act="routepage" data-page="' + (S.routePage - 1) + '"' + (S.routePage === 0 ? " disabled" : "") + ">← Previous</button>" +
        '<span>Page ' + (S.routePage + 1) + " of " + pageCount + "</span>" +
        '<button class="abtn" data-act="routepage" data-page="' + (S.routePage + 1) + '"' + (S.routePage >= pageCount - 1 ? " disabled" : "") + ">Next →</button></div>";
    }
    return h;
  }

  function inspectorAssetHTML(a) {
    const op = S.byCode[a.operator];
    let h = '<div class="insp-title">' + ASSET_ICON[a.type] + " " + esc(a.name) + "</div>" +
      '<div class="insp-sub">' + (a.aggregate ? "AGGREGATE INFRASTRUCTURE" : ASSET_LABEL[a.type]) +
      (op ? ' · <span class="cchip" style="--c:' + S.color[a.operator] + '">' + a.operator + "</span> " + esc(op.name) : "") + "</div>" +
      shipmentLifecycleHTML(a) +
      '<span class="kindchip' + (a.condition < 35 || a.lifecycle === "lost" ? " sev2" : "") + '">' + esc(a.status) + "</span>" +
      '<table class="stattable">' +
      "<tr><td>Condition</td><td>" + a.condition + "%</td></tr>";
    if (a.aggregate) {
      h += "<tr><td>Engine count</td><td>" + a.count + " satellites</td></tr>" +
        "<tr><td>Effective capacity</td><td>" + fmt1(a.effectiveCapacity) + "</td></tr>" +
        "<tr><td>Last maintained</td><td>t" + a.lastMaintainedTick + "</td></tr>";
    } else {
      h += "<tr><td>Shipment id</td><td>" + esc(a.id) + "</td></tr>" +
        "<tr><td>Route</td><td>" + esc(a.routeDesc) + "</td></tr>" +
        "<tr><td>Carrier</td><td>" + esc(a.carrier) + "</td></tr>" +
        "<tr><td>Cargo</td><td>" + esc(a.commodity) + " · " + fmt1(a.qty) + " units</td></tr>" +
        "<tr><td>Progress</td><td>" + Math.round(a.progress * 100) + "% · t" + a.departTick + "→t" + a.arriveTick + "</td></tr>" +
        "<tr><td>Distance left</td><td>" + distanceLeftText(a.remainingDistanceKm) + " / " + distanceKmText(a.distanceKm) + " total</td></tr>" +
        "<tr><td>Buyer pool</td><td>" + esc(a.buyerPool) + "</td></tr>" +
        "<tr><td>Base / duty / total</td><td>" + fmtInt(a.baseCost) + " / " + fmtInt(a.tariffDuty) + " / " + fmtInt(a.cost) + "</td></tr>" +
        "<tr><td>Exporter proceeds</td><td>" + fmtInt(a.proceeds) + "</td></tr>" +
        "<tr><td>Dispatch event</td><td>#" + a.dispatchEventId + "</td></tr>" +
        "<tr><td>Endpoint fleet</td><td>" + a.originCondition + "% / " + a.destCondition + "%</td></tr>" +
        (a.relief ? "<tr><td>Service</td><td>Emergency relief</td></tr>" : "");
    }
    h += "</table>" + conditionBar(a.condition);
    if (a.aggregate || !a.authoritative) {
      h += '<div class="insp-actions"><button class="abtn primary" data-act="assetdossier" data-id="' + a.id + '">' +
        (a.aggregate ? "Inventory details" : "Mock controls") + "</button></div>";
    }
    return h;
  }

  function impactNumber(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const n = Number(value);
    const abs = Math.abs(n);
    if (Number.isInteger(n) && abs >= 1000) return n.toLocaleString();
    if (abs >= 100) return String(Math.round(n));
    return String(Math.round(n * 100) / 100);
  }

  function impactValue(value) {
    if (value == null) return "—";
    if (typeof value === "number" || (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value)))) {
      const n = Number(value);
      return Math.abs(n) >= 1e4 ? fmtBig(n) : impactNumber(n);
    }
    return String(value);
  }

  // A change too small to print as a rounded number still happened; say so rather than "+0".
  function impactDelta(value) {
    const n = Number(value);
    if (n !== 0 && Math.abs(n) < 0.005) return "<0.01";
    return impactValue(value);
  }

  function effectRow(effect, prefix) {
    const delta = effect.delta == null ? "" :
      '<b class="impact-delta ' + (Number(effect.delta) < 0 ? "neg" : "pos") + '">' +
      (Number(effect.delta) > 0 ? "+" : "") + impactDelta(effect.delta) +
      (effect.unit ? " " + esc(effect.unit) : "") + "</b>";
    const transition = effect.before != null || effect.after != null
      ? '<span class="impact-transition">' + esc(impactValue(effect.before)) + " → " + esc(impactValue(effect.after)) + "</span>"
      : "";
    return '<div class="impact-row">' +
      '<span class="impact-label">' + (prefix ? esc(prefix) + " · " : "") + esc(effect.target + " / " + effect.metric) + "</span>" +
      delta + transition + "</div>";
  }

  function impactSection(title, rows, emptyText) {
    let h = '<div class="insp-sechead impact-head">' + title + "</div>";
    if (!rows.length) return h + '<div class="impact-empty">' + esc(emptyText) + "</div>";
    rows.forEach((row) => { h += effectRow(row.effect || row, row.prefix || ""); });
    return h;
  }

  function inspectorEventHTML(e) {
    const c = S.byCode[e.country];
    const impact = S.impact && S.impact.eventId === e.id ? S.impact : null;
    let h = '<span class="kindchip' + (e.intervention ? " iv" : (e.severity === 2 ? " sev2" : "")) + '">' +
      esc(e.kind) + " · sev " + e.severity + "</span>" +
      (e.headline && e.headline !== e.kind
        ? '<p class="insp-headline">' + esc(e.headline) + "</p>"
        : '<p class="insp-headline ambient">' + esc(humanKind(e.kind)) + " <span>· ambient bookkeeping, no headline</span></p>") +
      '<table class="stattable">' +
      "<tr><td>Tick</td><td>t" + e.tick + "</td></tr>" +
      "<tr><td>Country</td><td>" + esc(c ? c.name : e.country) + "</td></tr>" +
      "<tr><td>Timeline</td><td>" + esc(S.selTimeline) + "</td></tr>" +
      "<tr><td>Cascade depth</td><td>" + e.depth + "</td></tr></table>";

    h += '<div class="insp-sechead impact-head">WHY IT HAPPENED</div>';
    const causes = impact ? impact.causes : (e.causes || []);
    if (causes.length) {
      causes.forEach((cause) => {
        h += '<button class="cause-row" data-act="goto" data-id="' + cause.eventId + '">' +
          '<span class="cause-role">' + esc(cause.role || "trigger") + "</span>" +
          '<span>#' + cause.eventId + (cause.headline ? " · " + esc(cause.headline) : "") + "</span>" +
          (cause.detail ? '<small>' + esc(cause.detail) + "</small>" : "") + "</button>";
      });
    } else {
      h += '<div class="impact-empty">' + (e.intervention ? "Divine intervention is the root cause." : "No earlier event is recorded as a cause.") + "</div>";
    }

    const immediate = impact ? impact.immediateEffects : (e.effects || []);
    h += impactSection("IMMEDIATE EFFECTS", immediate.slice(0, 16), "No direct state mutation was recorded.");

    if (!impact && e.id >= 0) {
      h += '<div class="impact-loading">Calculating deduplicated downstream impact…</div>';
    } else if (impact) {
      const downstream = impact.downstreamEffects.slice(0, 16).map((row) => ({
        effect: row.effect,
        prefix: "#" + row.eventId + " " + row.eventKind
      }));
      h += impactSection(
        "DOWNSTREAM EFFECTS · " + impact.descendants.length + " EVENT" + (impact.descendants.length === 1 ? "" : "S"),
        downstream,
        "No descendant effect is recorded within 100 ticks."
      );
      if (impact.downstreamEffects.length > downstream.length) {
        h += '<div class="impact-empty">+' + (impact.downstreamEffects.length - downstream.length) + " more effect records</div>";
      }
      h += impactSection("CUMULATIVE TOTAL · DEDUPLICATED", impact.cumulativeTotals.slice(0, 16), "No numeric effects to total.");

      const hd = impact.horizonDiff || {};
      h += '<div class="insp-sechead impact-head">' + (hd.available ? "VALID HORIZON DIFFERENCE" : "HORIZON DIFFERENCE UNAVAILABLE") + "</div>";
      if (hd.available) {
        h += '<div class="impact-note">Fork − prime at t' + hd.tick + ". " + esc(hd.scope || "") + "</div>";
        h += (hd.effects || []).slice(0, 16).map((effect) => effectRow(effect, "")).join("");
        if (!(hd.effects || []).length) h += '<div class="impact-empty">Fork and prime are identical at this horizon.</div>';
      } else {
        h += '<div class="impact-empty">' + esc(hd.reason || "No genuine aligned fork baseline exists.") + "</div>";
      }
    }

    h += '<div class="insp-actions">' +
      '<button class="abtn primary" data-act="trace" data-id="' + e.id + '">Trace causal DAG</button>';
    h += '<button class="abtn" data-act="country" data-code="' + e.country + '">View ' + esc(c ? c.name : e.country) + "</button></div>";
    return h;
  }

  function countryLogisticsHTML(code) {
    const objects = S.worldObjects || {};
    const country = objects.countries && objects.countries[code];
    if (!country) return "";
    const shipments = objects.shipments || [];
    const lanes = objects.lanes || [];
    const inbound = shipments.filter((item) => item.dest === code);
    const outbound = shipments.filter((item) => item.origin === code);
    const countryLanes = lanes.filter((lane) => lane.origin === code || lane.dest === code);
    const inboundQty = inbound.reduce((sum, item) => sum + Number(item.qty || 0), 0);
    const outboundQty = outbound.reduce((sum, item) => sum + Number(item.qty || 0), 0);
    const worstCondition = countryLanes.length
      ? Math.round(Math.min.apply(null, countryLanes.map((lane) => Number(lane.condition || 0))) * 100)
      : null;
    let h = '<div class="insp-sechead logistics-head">TRADE &amp; LOGISTICS · t' + Number(objects.tick || 0) + "</div>" +
      '<div class="logistics-grid">' +
      '<div><span>INBOUND</span><b>' + inbound.length + " · " + fmt1(inboundQty) + " u</b></div>" +
      '<div><span>OUTBOUND</span><b>' + outbound.length + " · " + fmt1(outboundQty) + " u</b></div>" +
      '<div><span>ACTIVE LANES</span><b>' + countryLanes.length + "</b></div>" +
      '<div><span>WORST CONDITION</span><b>' + (worstCondition == null ? "—" : worstCondition + "%") + "</b></div></div>";
    if (worstCondition != null) h += conditionBar(worstCondition);

    const cargoGroups = {};
    inbound.concat(outbound).forEach((item) => {
      const direction = item.dest === code ? "IN" : "OUT";
      const key = [direction, item.origin, item.dest, item.commodity, item.carrier].join(":");
      if (!cargoGroups[key]) cargoGroups[key] = {
        direction: direction, origin: item.origin, dest: item.dest,
        commodity: item.commodity, carrier: item.carrier, qty: 0, count: 0
      };
      cargoGroups[key].qty += Number(item.qty || 0);
      cargoGroups[key].count += 1;
    });
    const cargos = Object.values(cargoGroups).sort((a, b) => b.qty - a.qty).slice(0, 3);
    if (cargos.length) {
      h += '<div class="logistics-routes">';
      cargos.forEach((item) => {
        h += '<div><span class="route-dir">' + item.direction + "</span><b>" +
          esc(item.origin + "→" + item.dest) + "</b><span>" + esc(item.commodity) + " · " + fmt1(item.qty) + " u · " +
          item.count + " shipment" + (item.count === 1 ? "" : "s") + " · " + esc(item.carrier) + "</span></div>";
      });
      h += "</div>";
    } else {
      h += '<div class="logistics-empty">No shipments are currently in flight.</div>';
    }

    const commodities = Object.entries(country.commodities || {}).sort((a, b) => {
      const ratio = (entry) => Number(entry[1].stock || 0) / Math.max(Number(entry[1].need || 0), 0.0001);
      return ratio(a) - ratio(b);
    }).slice(0, 3);
    if (commodities.length) {
      h += '<div class="commodity-strip"><span>LOWEST STOCK / NEED</span>';
      commodities.forEach(([name, values]) => {
        h += '<b>' + esc(name) + " " + fmt1(values.stock) + " / " + fmt1(values.need) + "</b>";
      });
      h += "</div>";
    }
    return h;
  }

  function inspectorCountryHTML(code) {
    const c = S.byCode[code];
    const stats = S.stats[focusTL()] || S.stats.A;
    const st = stats ? stats[code] : null;
    if (!c || !st) return '<p class="hint">' + esc(cname(code)) + " does not exist on this timeline at t" + S.viewTick + ".</p>";
    const L = S.leaders[code] || c.leader || { title: "", name: "", traits: [] };
    const sp = S.spark[code];
    let h = '<div class="insp-title">' + esc(c.name) + "</div>" +
      '<div class="insp-sub">' + esc(L.title) + " " + esc(L.name) + " · pop " + fmt1(st.pop) + "M" +
      (c.parent ? " · born of " + esc(S.byCode[c.parent] ? S.byCode[c.parent].name : c.parent) : "") + "</div>" +
      '<div class="traitchips">' + L.traits.map((t) => '<span class="trait">' + esc(t) + "</span>").join("") + "</div>" +
      '<table class="stattable">' +
      "<tr><td>Stability</td><td>" + fmt1(st.stability) + "</td></tr>" +
      "<tr><td>Inflation</td><td>" + fmt1(st.inflation) + "%</td></tr>" +
      "<tr><td>GDP / tick</td><td>" + fmtBig(st.gdp) + "</td></tr>" +
      "<tr><td>Grain reserve</td><td>" + fmt1(st.grainDays) + " days</td></tr>" +
      "<tr><td>Exchange rate</td><td>" + esc(c.currency.symbol) + " " + st.fx.toFixed(3) + "</td></tr>" +
      "<tr><td>Treasury</td><td>" + esc(c.currency.symbol) + " " + fmtBig(st.treasury) + "</td></tr></table>";
    if (sp) {
      h += '<div class="sparkblock"><div class="sblabel"><span>STABILITY</span><span>90t</span></div>' + sparkSVG(sp.stability.slice(-90), 300, 34, "#3987e5", true) + "</div>";
      h += '<div class="sparkblock"><div class="sblabel"><span>INFLATION</span><span>90t</span></div>' + sparkSVG(sp.inflation.slice(-90), 300, 34, "#c98500", true) + "</div>";
    }
    h += countryLogisticsHTML(code);
    h += '<div class="insp-actions"><button class="abtn primary" data-act="dossier" data-code="' + code + '">Open full dossier</button></div>';
    const timelineId = activeTimelineId();
    const recents = Object.values(S.eventsByTimeline[timelineId] || {})
      .filter((e) => e.country === code || e.country2 === code)
      .sort((a, b) => b.tick - a.tick || b.id - a.id)
      .slice(0, 12);
    if (recents.length) {
      h += '<div class="insp-sechead">RECENT EVENTS · TIMELINE ' + esc(timelineId) + '</div><ul class="minilist country-events">';
      recents.forEach((e) => {
        const glyph = e.intervention ? "✦" : (e.severity === 2 ? "⚡" : (e.severity === 1 ? "◆" : "·"));
        h += '<li class="country-event sev' + e.severity + (e.intervention ? " iv" : "") + '" data-act="ev" data-id="' + e.id + '" data-tl="' + esc(timelineId) + '">' +
          '<span class="country-event-glyph">' + glyph + '</span><span class="ev-tick">t' + e.tick + "</span><span>" + esc(e.headline) + "</span></li>";
      });
      h += "</ul>";
    }
    return h;
  }

  function wireInspector() {
    $("inspBody").querySelectorAll("[data-act]").forEach((n) => {
      n.addEventListener("click", () => {
        const act = n.dataset.act;
        if (act === "trace") requestTrace(Number(n.dataset.id), S.selTimeline);
        else if (act === "goto") { selectEvent(Number(n.dataset.id), S.selTimeline); jumpToFeedCard(Number(n.dataset.id)); }
        else if (act === "ev") { selectEvent(Number(n.dataset.id), n.dataset.tl || activeTimelineId()); jumpToFeedCard(Number(n.dataset.id)); }
        else if (act === "country") selectCountry(n.dataset.code);
        else if (act === "dossier") openDossier(n.dataset.code);
        else if (act === "assetdossier") openAssetDossier(n.dataset.id);
        else if (act === "routeasset") selectAsset(n.dataset.id);
        else if (act === "routepage") { S.routePage = Number(n.dataset.page); renderInspector(); }
      });
    });
  }

  function jumpToFeedCard(id) {
    const card = document.querySelector('.ev[data-id="' + id + '"]');
    if (card) {
      card.scrollIntoView({ block: "center", behavior: "smooth" });
      card.classList.remove("flashsel");
      requestAnimationFrame(() => card.classList.add("flashsel"));
    }
  }

  // ---------- trace ----------
  function requestTrace(id, timelineId) {
    if (id < 0 && S.events[id]) {
      // locally fabricated event — fabricate the lineage too
      const e = S.events[id];
      S.trace = { selectedId: id, nodes: [{ id: id, tick: e.tick, kind: e.kind, severity: 2, country: e.country, headline: e.headline, intervention: true, d: 0 }], divine: true };
      openOverlay("trace");
      return;
    }
    const tl = timelineId || S.selTimeline || "A";
    const key = "trace:" + tl + ":" + id;
    if (pendingByKey(key)) return;
    S.tracePending = { id: id, tl: tl };
    S.loadingLabel = "Tracing the complete causal DAG…";
    S.loadingReturn = S.overlay;
    const requestId = sendCommand(
      { cmd: "trace", eventId: id, tl: tl },
      "Tracing causal DAG…",
      key
    );
    if (requestId) openOverlay("loading");
  }

  function traceHTML(m) {
    let h = '<div class="ov-title">Causal trace</div>' +
      '<div class="ov-sub">' + (m.divine ? "The gods leave no paper trail. This happened because you wanted it to." :
        "Root cause at top — every ripple below it. Click a row to inspect. Ticks, causes and " +
        "effects are the record; the wording describes each event in today's terms, not the " +
        "sentence the chronicle printed at the time.") + "</div>" +
      '<ul class="trace-rows">';
    m.nodes.forEach((n) => {
      const gcls = n.intervention ? "iv" : (n.d === 0 ? "root" : (n.severity === 2 ? "crisis" : "cons"));
      const glyph = n.intervention ? "✦" : (n.d === 0 ? "●" : (n.severity === 2 ? "⚡" : "▸"));
      const pre = n.d === 0 ? "" : "   ".repeat(n.d - 1) + "└─";
      h += '<li data-id="' + n.id + '"' + (n.id === m.selectedId ? ' class="sel"' : "") + ">" +
        '<span class="tr-pre">' + pre + "</span>" +
        '<span class="tr-glyph ' + gcls + '">' + glyph + "</span>" +
        '<span class="tr-tick">t' + n.tick + "</span>" +
        '<span class="cchip" style="--c:' + (S.color[n.country] || "#3987e5") + '">' + n.country + "</span>" +
        // An event kind the engine writes no prose for arrives as its bare kind; print it
        // the way the feed does rather than showing a raw enum in the causal panel.
        '<span class="tr-head' + (n.headline === n.kind ? " ambient" : "") + '">' +
        esc(n.headline === n.kind ? humanKind(n.kind) : n.headline) + "</span>" +
        (n.d === 0 ? ' <span class="rootbadge">ROOT CAUSE</span>' : "") +
        (n.parentIds && n.parentIds.length > 1 ? ' <span class="sharedbadge">' + n.parentIds.length + " CAUSES</span>" : "") +
        "</li>";
    });
    h += "</ul>" +
      '<div class="ov-actions"><button class="cancel" data-act="close">esc · close</button></div>';
    return h;
  }

  // ---------- asset dossier ----------
  function openAssetDossier(id) {
    if (!globe) return;
    S.assetInfo = globe.getAsset(id) || (S.terminalAsset && S.terminalAsset.id === id ? S.terminalAsset : S.assetInfo);
    if (S.assetInfo) openOverlay("asset");
  }

  function assetDossierHTML(a) {
    const op = S.byCode[a.operator];
    let h = '<div class="dossier-head">' +
      '<span class="emblem big" style="--c:' + (S.color[a.operator] || "#3987e5") + '">' + ASSET_ICON[a.type] + "</span>" +
      '<div><div class="ov-title">' + esc(a.name) + "</div>" +
      '<div class="ov-sub" style="margin-bottom:2px">' + (a.aggregate ? "ENGINE ASSET INVENTORY" : ASSET_LABEL[a.type]) +
      (op ? " · " + esc(op.name) : "") + "</div>" +
      '<div class="ov-sub" style="margin-bottom:0"><span class="kindchip' + (a.condition < 35 || a.lifecycle === "lost" ? " sev2" : "") + '" style="margin:0">' + esc(a.status) + "</span></div></div></div>";
    h += shipmentLifecycleHTML(a);
    h += '<div class="tilerow" style="grid-template-columns: repeat(4, 1fr)">' +
      '<div class="tile"><span class="tlabel">CONDITION</span><span class="tval">' + a.condition + "%</span>" + conditionBar(a.condition) + "</div>";
    if (a.aggregate) {
      h += '<div class="tile"><span class="tlabel">COUNT</span><span class="tval">' + a.count + "</span></div>" +
        '<div class="tile"><span class="tlabel">CAPACITY</span><span class="tval">' + fmt1(a.effectiveCapacity) + "</span></div>" +
        '<div class="tile"><span class="tlabel">MAINTAINED</span><span class="tval">t' + a.lastMaintainedTick + "</span></div>";
    } else {
      h += '<div class="tile"><span class="tlabel">COMMODITY</span><span class="tval" style="font-size:13px">' + esc(a.commodity) + "</span></div>" +
        '<div class="tile"><span class="tlabel">QUANTITY</span><span class="tval">' + fmt1(a.qty) + "</span></div>" +
        '<div class="tile"><span class="tlabel">PROGRESS</span><span class="tval">' + Math.round(a.progress * 100) + "%</span></div>";
    }
    h += "</div>";
    if (a.aggregate) {
      h += '<div class="insp-sechead">AUTHORITATIVE INVENTORY</div><div class="ov-sub">' +
        "The engine owns this count and aggregate condition. Individual glyph orbits are deterministic presentation only; no mission histories or per-satellite health are invented.</div>";
    } else {
      h += '<div class="insp-sechead">SHIPMENT SETTLEMENT</div><table class="stattable">' +
        "<tr><td>Shipment id</td><td>" + esc(a.id) + "</td></tr>" +
        "<tr><td>Route</td><td>" + esc(a.routeDesc) + "</td></tr>" +
        "<tr><td>Carrier</td><td>" + esc(a.carrier) + "</td></tr>" +
        "<tr><td>Buyer pool</td><td>" + esc(a.buyerPool) + "</td></tr>" +
        "<tr><td>Depart / arrive</td><td>t" + a.departTick + " / t" + a.arriveTick + "</td></tr>" +
        "<tr><td>Base / duty / total</td><td>" + fmtInt(a.baseCost) + " / " + fmtInt(a.tariffDuty) + " / " + fmtInt(a.cost) + "</td></tr>" +
        "<tr><td>Exporter proceeds</td><td>" + fmtInt(a.proceeds) + "</td></tr>" +
        "<tr><td>Endpoint condition</td><td>" + a.originCondition + "% / " + a.destCondition + "%</td></tr>" +
        (a.relief ? "<tr><td>Relief</td><td>Emergency shipment</td></tr>" : "") + "</table>";
    }
    if (!a.authoritative) {
      h += '<div class="insp-sechead" style="color:var(--violet)">APPROXIMATE MOCK OBJECT</div>' +
        '<div class="godrow"><button class="abtn" data-god="upgrade">⬆ Upgrade</button>' +
        '<button class="abtn" data-god="degrade">⬇ Degrade</button><button class="abtn" data-god="break">⚠ Break</button>' +
        '<button class="abtn danger" data-god="destroy">✕ Destroy</button></div>';
    } else {
      h += '<div class="ov-sub" style="margin-top:12px">Observed engine state is read-only here. Use a real intervention to change history.</div>';
    }
    h += '<div class="ov-actions"><button class="cancel" data-act="close">esc · close</button></div>';
    return h;
  }

  function assetActionHeadline(a, action) {
    const op = S.byCode[a.operator];
    const opName = op ? op.name : "somebody";
    const n = esc(a.name);
    const H = {
      sat: {
        extend: "✦ Ground control at " + opName + " finds fuel reserves that were never loaded aboard " + n + ".",
        upgrade: "✦ " + n + " wakes up sharper. Its engineers publish a paper they don't understand.",
        degrade: "✦ " + n + " starts drifting. The anomaly review board finds nothing.",
        break: "✦ " + n + " goes dark mid-orbit; " + opName + " blames space weather.",
        destroy: "✦ " + n + " blooms into debris. A thousand telescopes point at nothing."
      },
      ship: {
        extend: "✦ The hull of " + n + " is found freshly painted. Nobody painted it.",
        upgrade: "✦ " + n + " makes record time; her chief engineer takes unearned credit.",
        degrade: "✦ " + n + " reports engine trouble in calm seas.",
        break: "✦ " + n + " is adrift; tugs dispatched from " + opName + ".",
        destroy: "✦ " + n + " vanishes from every chart at once. The sea keeps its counsel."
      },
      plane: {
        extend: "✦ Flight " + n + " passes inspection with parts that have zero hours on them.",
        upgrade: "✦ Flight " + n + " lands early on a tailwind meteorologists cannot find.",
        degrade: "✦ Flight " + n + " diverts with an instrument fault.",
        break: "✦ Flight " + n + " declares an emergency; it will land, barely.",
        destroy: "✦ Flight " + n + " drops off radar. " + opName + " grounds the fleet."
      },
      train: {
        extend: "✦ The " + n + " gets new rails overnight. The old ones are simply gone.",
        upgrade: "✦ The " + n + " runs ahead of a schedule no one rewrote.",
        degrade: "✦ The " + n + " limps between stations, sparks at the wheels.",
        break: "✦ The " + n + " stalls in open country; passengers walk the last miles.",
        destroy: "✦ The " + n + " derails spectacularly. Miraculously — or not — the toll is light."
      }
    };
    return H[a.type][action];
  }

  // ---------- country dossier ----------
  function openDossier(code) {
    const timelineId = S.focusId;
    const key = "countryDetail:" + timelineId + ":" + code;
    if (pendingByKey(key)) return;
    S.loadingLabel = "Opening the full dossier for " + cname(code) + "…";
    S.loadingReturn = S.overlay;
    const requestId = sendCommand(
      { cmd: "countryDetail", code: code, tl: timelineId },
      "Loading " + cname(code) + " dossier…",
      key
    );
    if (requestId) openOverlay("loading");
  }

  function lineChart(ticks, values, w, h, color, metric) {
    if (!values || values.length < 2) return '<svg class="history-chart" data-chart-metric="' + metric + '" viewBox="0 0 ' + w + " " + h + '"></svg>';
    let mn = Infinity, mx = -Infinity;
    values.forEach((v) => { if (v < mn) mn = v; if (v > mx) mx = v; });
    if (mx - mn < 1e-6) { mx += 1; mn -= 1; }
    const pad = (mx - mn) * 0.1;
    const lo = mn - pad, hi = mx + pad;
    const X = (i) => 34 + (i / (values.length - 1)) * (w - 42);
    const Y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (h - 26);
    let pts = "";
    values.forEach((v, i) => { pts += X(i).toFixed(1) + "," + Y(v).toFixed(1) + " "; });
    const cur = values[values.length - 1];
    let s = '<svg class="history-chart" tabindex="0" role="img" data-chart-metric="' + metric + '" data-chart-lo="' + lo + '" data-chart-hi="' + hi + '" data-chart-w="' + w + '" data-chart-h="' + h + '" viewBox="0 0 ' + w + " " + h + '">';
    s += '<line x1="34" y1="' + Y(mx).toFixed(1) + '" x2="' + (w - 8) + '" y2="' + Y(mx).toFixed(1) + '" stroke="rgba(255,255,255,0.07)"/>';
    s += '<line x1="34" y1="' + Y(mn).toFixed(1) + '" x2="' + (w - 8) + '" y2="' + Y(mn).toFixed(1) + '" stroke="rgba(255,255,255,0.07)"/>';
    s += '<text x="30" y="' + (Y(mx) + 3).toFixed(1) + '" text-anchor="end" class="axis">' + fmtAxis(metric, mx) + "</text>";
    s += '<text x="30" y="' + (Y(mn) + 3).toFixed(1) + '" text-anchor="end" class="axis">' + fmtAxis(metric, mn) + "</text>";
    s += '<polygon points="' + X(0).toFixed(1) + "," + (h - 18) + " " + pts + X(values.length - 1).toFixed(1) + "," + (h - 18) + '" fill="' + color + '" opacity="0.10"/>';
    s += '<polyline points="' + pts.trim() + '" fill="none" stroke="' + color + '" stroke-width="2"/>';
    s += '<circle cx="' + X(values.length - 1).toFixed(1) + '" cy="' + Y(cur).toFixed(1) + '" r="3" fill="' + color + '"/>';
    s += '<text x="34" y="' + (h - 4) + '" class="axis">t' + (ticks[0] || 0) + "</text>";
    s += '<text x="' + (w - 8) + '" y="' + (h - 4) + '" text-anchor="end" class="axis">t' + (ticks[ticks.length - 1] || 0) + "</text>";
    s += '<line class="chart-hover-line" x1="34" y1="6" x2="34" y2="' + (h - 17) + '"/>';
    s += '<circle class="chart-hover-dot" cx="34" cy="8" r="4"/>';
    s += '<rect class="chart-hit" x="34" y="0" width="' + (w - 42) + '" height="' + h + '"/>';
    s += "</svg>";
    return s;
  }

  // Fixed scales for bounded stats. GDP and treasury are engine money figures whose magnitude
  // depends on the world, so their sliders span a range around the current value instead of
  // a constant that could erase six orders of magnitude with one touch.
  const GOD_FIELDS = [
    { field: "stability", label: "Stability", min: 1, max: 99, step: 1 },
    { field: "inflation", label: "Inflation %", min: -1, max: 40, step: 0.5 },
    { field: "gdp", label: "GDP / tick", relative: true },
    { field: "pop", label: "Population M", min: 0.3, max: 200, step: 0.5 },
    { field: "treasury", label: "Treasury", relative: true, signed: true },
    { field: "grainDays", label: "Grain days", min: 0, max: 120, step: 1 }
  ];

  function godFieldRange(g, value) {
    if (!g.relative) return { min: g.min, max: g.max, step: g.step };
    const span = Math.max(Math.abs(Number(value) || 0), 1);
    return { min: g.signed ? -span : 0, max: span * 3, step: span / 100 };
  }

  function godFieldText(field, value) {
    const v = Number(value);
    if (field === "gdp" || field === "treasury") return fmtBig(v);
    return fmt1(v) + (field === "inflation" ? "%" : (field === "pop" ? "M" : (field === "grainDays" ? "d" : "")));
  }


  function chartPointInterval(detail, index) {
    const tick = detail.ticks[index];
    return { start: index > 0 ? detail.ticks[index - 1] : tick - 1, end: tick };
  }

  function chartPointEventState(detail, index) {
    const interval = chartPointInterval(detail, index);
    return detail.chartIntervals[chartIntervalKey(interval.start, interval.end)] || null;
  }

  function chartPointEvents(detail, index) {
    if (detail.chartEventMode !== "interval") {
      const interval = chartPointInterval(detail, index);
      return (detail.chartEvents || [])
        .filter((event) => event.tick > interval.start && event.tick <= interval.end)
        .sort((a, b) => b.severity - a.severity || b.tick - a.tick || b.id - a.id);
    }
    const state = chartPointEventState(detail, index);
    return state && state.loaded ? state.events : [];
  }

  function requestChartPointEvents(detail, index) {
    if (detail.chartEventMode !== "interval") return;
    const interval = chartPointInterval(detail, index);
    const key = chartIntervalKey(interval.start, interval.end);
    if (detail.chartIntervals[key] || detail.chartIntervalRequests[key]) return;
    if (chartEventTimer != null) clearTimeout(chartEventTimer);
    chartEventTimer = setTimeout(() => {
      chartEventTimer = null;
      if (S.detail !== detail) return;
      detail.chartIntervalRequests[key] = true;
      sendCommand({
        cmd: "countryChartEvents", code: detail.code, tl: detail.tl || activeTimelineId(),
        startTick: interval.start, endTick: interval.end
      }, "Loading chart evidence…", "chartEvents:" + detail.code + ":" + key);
    }, 80);
  }

  function chartValue(metric, value) {
    return metric === "fx" ? Number(value).toFixed(3) : fmtBig(Number(value));
  }

  function chartHoverHTML(detail, index) {
    const tick = detail.ticks[index];
    const previousTick = index > 0 ? detail.ticks[index - 1] : tick;
    const labels = { stability: "Stability", inflation: "Inflation", gdp: "GDP / tick", fx: "Exchange" };
    const state = detail.chartEventMode === "interval" ? chartPointEventState(detail, index) : null;
    const loading = detail.chartEventMode === "interval" && !(state && state.loaded);
    const events = chartPointEvents(detail, index);
    const total = state && state.loaded ? state.total : events.length;
    let h = '<div class="chart-hover-head"><div><b>t' + tick + '</b><span>' +
      (index > 0 ? "Changes since t" + previousTick : "First recorded point") +
      '</span></div><button class="abtn primary" data-chart-jump="' + index + '"' +
      (loading ? " disabled" : "") + '>Jump to t' + tick + "</button></div>";
    h += '<div class="chart-hover-values">';
    Object.keys(labels).forEach((metric) => {
      const value = detail.series[metric][index];
      const before = index > 0 ? detail.series[metric][index - 1] : value;
      const delta = Number(value) - Number(before);
      h += '<div><span>' + labels[metric] + "</span><b>" + chartValue(metric, value) +
        '</b><small class="' + (delta < 0 ? "neg" : (delta > 0 ? "pos" : "")) + '">' +
        (index > 0 ? (delta > 0 ? "+" : "") + chartValue(metric, delta) : "—") + "</small></div>";
    });
    h += "</div>";
    if (loading) {
      h += '<div class="chart-event-empty">Loading authoritative events recorded in this interval…</div>';
    } else if (!events.length) {
      h += '<div class="chart-event-empty">No recorded event involved this country in this sampled interval. The change may be ordinary system drift; no cause is invented.</div>';
    } else {
      h += '<div class="chart-event-list"><b>RECORDED IN THIS INTERVAL</b>';
      events.forEach((event) => {
        // Ambient bookkeeping events have no headline template; the engine sends the kind
        // twice. Show it once, humanized, instead of echoing the constant.
        const ambient = !event.headline || event.headline === event.kind;
        h += '<button data-chart-event="' + event.id + '"' + (ambient ? ' class="ambient"' : "") + '><span class="ev-tick">t' + event.tick + "</span><i>" +
          (ambient ? "" : esc(event.kind)) + "</i><span>" + esc(ambient ? humanKind(event.kind) : event.headline) + "</span></button>";
      });
      if (total > events.length) h += '<small>+' + (total - events.length) + " more recorded events</small>";
      h += "</div>";
    }
    return h;
  }

  function showChartPoint(card, detail, rawIndex) {
    if (!detail.ticks || !detail.ticks.length) return;
    const index = Math.max(0, Math.min(detail.ticks.length - 1, rawIndex));
    requestChartPointEvents(detail, index);
    card.querySelectorAll(".history-chart").forEach((svg) => {
      const metric = svg.dataset.chartMetric;
      const values = detail.series[metric];
      if (!values || values[index] == null) return;
      const w = Number(svg.dataset.chartW || 380);
      const h = Number(svg.dataset.chartH || 110);
      const lo = Number(svg.dataset.chartLo || 0);
      const hi = Number(svg.dataset.chartHi || 1);
      const x = 34 + (index / Math.max(1, detail.ticks.length - 1)) * (w - 42);
      const y = 8 + (1 - (Number(values[index]) - lo) / Math.max(1e-9, hi - lo)) * (h - 26);
      const line = svg.querySelector(".chart-hover-line");
      const dot = svg.querySelector(".chart-hover-dot");
      if (line) { line.setAttribute("x1", x); line.setAttribute("x2", x); line.classList.add("on"); }
      if (dot) { dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.classList.add("on"); }
      svg.dataset.chartIndex = index;
    });
    const hover = card.querySelector("#chartHoverCard");
    if (hover) {
      hover.innerHTML = chartHoverHTML(detail, index);
      wireChartHoverActions(card, detail, index);
    }
  }

  function jumpToChartPoint(detail, index, event) {
    const tick = detail.ticks[index];
    const timelineId = detail.tl || activeTimelineId();
    if (timelineId !== "A" || S.forks.length) {
      toast("Historical scrubbing is available on PRIME after resolving active forks.", "warn");
      if (event) { closeOverlay(); selectEvent(event.id, timelineId, event); }
      return;
    }
    S.pendingChartJump = { tick: tick, event: event || null };
    closeOverlay();
    requestWorldAt(tick);
  }

  function wireChartHoverActions(card, detail, index) {
    const events = chartPointEvents(detail, index);
    const byId = {};
    events.forEach((event) => { byId[event.id] = event; });
    const jump = card.querySelector("[data-chart-jump]");
    if (jump) jump.addEventListener("click", () => jumpToChartPoint(detail, index, events[0] || null));
    card.querySelectorAll("[data-chart-event]").forEach((button) => {
      button.addEventListener("click", () => {
        const event = byId[Number(button.dataset.chartEvent)];
        if (!event) return;
        closeOverlay();
        selectEvent(event.id, detail.tl || activeTimelineId(), event);
        jumpToFeedCard(event.id);
      });
    });
  }

  function wireHistoryCharts(card, detail) {
    card.querySelectorAll(".history-chart").forEach((svg) => {
      const pointFromEvent = (event) => {
        const rect = svg.getBoundingClientRect();
        const w = Number(svg.dataset.chartW || 380);
        const x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * w;
        return Math.round(((x - 34) / (w - 42)) * (detail.ticks.length - 1));
      };
      svg.addEventListener("pointermove", (event) => showChartPoint(card, detail, pointFromEvent(event)));
      svg.addEventListener("click", (event) => showChartPoint(card, detail, pointFromEvent(event)));
      svg.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Enter") return;
        event.preventDefault();
        const current = Number(svg.dataset.chartIndex || detail.ticks.length - 1);
        if (event.key === "Enter") jumpToChartPoint(detail, current, chartPointEvents(detail, current)[0] || null);
        else showChartPoint(card, detail, current + (event.key === "ArrowLeft" ? -1 : 1));
      });
    });
  }
  function detailHTML(d) {
    const col = S.color[d.code] || "#3987e5";
    const st = d.stats;
    const countryMeta = S.byCode[d.code] || {};
    const worldObject = d.worldObject || (S.worldObjects && S.worldObjects.countries[d.code]);
    const currency = countryMeta.currency || { name: "local currency", symbol: "¤" };
    const bandStyle = (metric, v) => "border-bottom: 2px solid " + statusBand(metric, v);
    let h = '<div class="dossier-head">' +
      '<span class="emblem big" style="--c:' + col + '">' + d.code + "</span>" +
      "<div>" +
      '<div class="ov-title">' + esc(d.meta.name) + "</div>" +
      '<div class="ov-sub" style="margin-bottom:2px">' + esc(d.leader.title) + " " + esc(d.leader.name) +
      " · " + d.leader.traits.map(esc).join(", ") +
      (countryMeta.parent ? ' · <span class="vio">born of ' + esc(S.byCode[countryMeta.parent] ? S.byCode[countryMeta.parent].name : countryMeta.parent) + "</span>" : "") +
      "</div>" +
      '<div class="ov-sub" style="margin-bottom:0">' + esc(currency.name) + " (" + esc(currency.symbol) + ")" +
      " · timeline " + S.focusId + "</div>" +
      "</div></div>";

    h += '<div class="tilerow">' +
      '<div class="tile" style="' + bandStyle("stability", st.stability) + '"><span class="tlabel">STABILITY</span><span class="tval" id="tv-stability">' + fmt1(st.stability) + "</span></div>" +
      '<div class="tile" style="' + bandStyle("inflation", st.inflation) + '"><span class="tlabel">INFLATION</span><span class="tval" id="tv-inflation">' + fmt1(st.inflation) + "%</span></div>" +
      '<div class="tile"><span class="tlabel">GDP / TICK</span><span class="tval" id="tv-gdp">' + fmtBig(st.gdp) + "</span></div>" +
      '<div class="tile"><span class="tlabel">POPULATION</span><span class="tval" id="tv-pop">' + fmt1(st.pop) + "M</span></div>" +
      '<div class="tile"><span class="tlabel">GRAIN</span><span class="tval" id="tv-grainDays">' + fmt1(st.grainDays) + "d</span></div>" +
      '<div class="tile"><span class="tlabel">TREASURY · ' + esc(currency.symbol) + '</span><span class="tval" id="tv-treasury">' + fmtBig(st.treasury) + "</span></div>" +
      "</div>";

    h += '<div class="chartgrid interactive-charts">' +
      '<div class="chartbox"><div class="sblabel"><span>STABILITY</span><span>' + fmt1(st.stability) + '</span></div>' + lineChart(d.ticks, d.series.stability, 380, 110, "#3987e5", "stability") + "</div>" +
      '<div class="chartbox"><div class="sblabel"><span>INFLATION %</span><span>' + fmt1(st.inflation) + '</span></div>' + lineChart(d.ticks, d.series.inflation, 380, 110, "#c98500", "inflation") + "</div>" +
      '<div class="chartbox"><div class="sblabel"><span>GDP / TICK</span><span>' + fmtBig(st.gdp) + '</span></div>' + lineChart(d.ticks, d.series.gdp, 380, 110, "#199e70", "gdp") + "</div>" +
      '<div class="chartbox"><div class="sblabel"><span>EXCHANGE</span><span>' + st.fx.toFixed(3) + '</span></div>' + lineChart(d.ticks, d.series.fx, 380, 110, "#9085e9", "fx") + "</div>" +
      "</div>" +
      '<div id="chartHoverCard" class="chart-hover-card"><div class="chart-hover-hint">Hover any chart to inspect an exact sampled tick, synchronized values, and recorded events since the previous point.</div></div>';

    // wars involving this country (focused timeline)
    const wars = (S.wars[focusTL()] || []).filter((w) => w[0] === d.code || w[1] === d.code);
    if (wars.length) {
      h += '<div class="insp-sechead" style="color:var(--serious)">⚔ ACTIVE WARS</div>';
      wars.forEach((w) => {
        const foe = w[0] === d.code ? w[1] : w[0];
        const fm = S.byCode[foe];
        h += '<div class="relrow"><span class="relname">vs ' + esc(fm ? fm.name : foe) + "</span>" +
          '<span class="spacer"></span>' +
          '<button class="abtn" data-peace="' + foe + '">🕊 End this war</button></div>';
      });
    }

    h += '<div class="dcols"><div>';
    h += '<div class="insp-sechead">AUTHORITATIVE HOLDINGS</div><table class="stattable">';
    if (worldObject) {
      h += "<tr><td>★ Capital</td><td>" + fmt1(worldObject.position.lat) + "°, " + fmt1(worldObject.position.lon) + "°</td></tr>" +
        "<tr><td>Region</td><td>" + esc(worldObject.region) + "</td></tr>" +
        "<tr><td>Territories</td><td>" + worldObject.territories.length + "</td></tr>" +
        "<tr><td>Bloc</td><td>" + esc(worldObject.blocId || "unaligned") + "</td></tr>";
    }
    h += "</table>";
    if (worldObject) {
      h += '<div class="insp-sechead">INFRASTRUCTURE</div><table class="stattable">';
      Object.keys(worldObject.assets).forEach((name) => {
        const asset = worldObject.assets[name];
        h += "<tr><td>" + esc(name.replace(/_/g, " ")) + "</td><td>" + asset.count + " · " + Math.round(asset.condition * 100) + "%</td></tr>";
      });
      h += "</table>";
    }
    if (d.relations.length) {
      h += '<div class="insp-sechead">RELATIONS <span class="godhint">−/+ meddle</span></div>';
      d.relations.forEach((r) => {
        const other = S.byCode[r.code];
        const pct = Math.min(100, Math.abs(r.value));
        h += '<div class="relrow" data-rel="' + r.code + '">' +
          '<span class="relname">' + esc(other ? other.name : r.code) + "</span>" +
          '<button class="relbtn" data-reldown="' + r.code + '">−</button>' +
          '<span class="relbar"><i class="' + (r.value < 0 ? "neg" : "pos") + '" style="width:' + pct + '%"></i></span>' +
          '<button class="relbtn" data-relup="' + r.code + '">+</button>' +
          '<span class="relval mono" id="rv-' + r.code + '">' + Math.round(r.value) + "</span></div>";
      });
    }
    h += "</div><div>";
    h += '<div class="insp-sechead">RECENT HISTORY</div><ul class="minilist">';
    d.recentEvents.slice().reverse().forEach((e) => {
      h += '<li data-act="ev" data-id="' + e.id + '"><span class="ev-tick">t' + e.tick + "</span> " + esc(e.headline) + "</li>";
    });
    h += "</ul></div></div>";

    // god sliders
    h += '<div class="insp-sechead" style="color:var(--violet)">✦ REWRITE REALITY</div><div class="godgrid">';
    GOD_FIELDS.forEach((g) => {
      const v = st[g.field];
      const range = godFieldRange(g, v);
      h += '<div class="godfield"><div class="mlabel"><span>' + g.label + '</span><span class="mval" id="gv-' + g.field + '">' + (g.relative ? fmtBig(v) : fmt1(v)) + "</span></div>" +
        '<input type="range" aria-label="' + esc(g.label) + '" data-god-field="' + g.field + '" min="' + range.min + '" max="' + range.max + '" step="' + range.step + '" value="' + v + '">' +
        "</div>";
    });
    h += "</div>";

    h += '<div class="ov-actions">' +
      '<button class="abtn" data-act="annals" data-code="' + d.code + '">📜 In the Annals</button>' +
      '<button class="cancel" data-act="close">esc · close</button></div>';
    return h;
  }

  // ---------- delta strip ----------
  function renderDeltaStrip(diff) {
    const strip = $("deltaStrip");
    let chips = "";
    S.order.forEach((code) => {
      const d = diff[code];
      if (!d) return;
      // The engine only lists stats that actually differ, so any of the three may be absent.
      const delta = (pair) => (Array.isArray(pair) ? Number(pair[1]) - Number(pair[0]) : 0);
      const ds = delta(d.stability);
      const di = delta(d.inflation);
      const dg = delta(d.gdp);
      if (Math.abs(ds) < 0.8 && Math.abs(di) < 0.4 && Math.abs(dg) < 0.8) return;
      const cell = (label, v, goodWhenUp) => {
        if (Math.abs(v) < (label === "infl" ? 0.4 : 0.8)) return "";
        const good = goodWhenUp ? v > 0 : v < 0;
        return '<span class="' + (good ? "dgood" : "dbad") + '">' + label + " " + (v > 0 ? "+" : "") + fmtBig(v) + "</span>";
      };
      chips += '<span class="dchip"><span class="dcode">' + code + "</span>" +
        cell("stab", ds, true) + cell("infl", di, false) + cell("gdp", dg, true) + "</span>";
    });
    strip.innerHTML = '<span class="dslabel">ΔWORLD · ' + S.focusId + "−A</span>" + (chips || '<span class="dneut mono" style="font-size:10.5px">timelines identical so far…</span>');
    strip.hidden = false;
  }

  // ---------- ribbon ----------
  const ribbon = $("ribbon");
  const rctx = ribbon.getContext("2d");
  let ribbonSize = { width: 0, height: 0 };

  function drawRibbon() {
    const dpr = window.devicePixelRatio || 1;
    const w = ribbonSize.width, h = ribbonSize.height;
    if (!w || !h) return;
    if (ribbon.width !== Math.round(w * dpr) || ribbon.height !== Math.round(h * dpr)) {
      ribbon.width = Math.round(w * dpr);
      ribbon.height = Math.round(h * dpr);
    }
    rctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    rctx.clearRect(0, 0, w, h);
    const forked = S.focusId !== "A";
    const focusFork = S.forks.find((f) => f.id === S.focusId);
    const maxT = Math.max(S.primeLive, S.liveTick, 300);
    const x = (t) => 8 + (t / maxT) * (w - 16);
    const yA = forked ? 16 : h / 2;
    const yB = h - 14;

    rctx.strokeStyle = "rgba(255,255,255,0.14)";
    rctx.lineWidth = 2;
    rctx.beginPath(); rctx.moveTo(8, yA); rctx.lineTo(x(Math.max(S.primeLive, S.viewTick)), yA); rctx.stroke();

    // every fork appears as a diamond at its branch point
    S.forks.forEach((f) => {
      rctx.fillStyle = f.id === S.focusId ? "#9085e9" : "rgba(144,133,233,0.45)";
      const px = x(f.forkTick);
      rctx.beginPath();
      rctx.moveTo(px, yA - 7); rctx.lineTo(px + 5, yA); rctx.lineTo(px, yA + 7); rctx.lineTo(px - 5, yA);
      rctx.closePath(); rctx.fill();
    });

    // checkpoint flags (◈ hollow, blue) sit above the prime line
    S.checkpoints.forEach((ck) => {
      if (ck.tick > maxT) return;
      const px = x(ck.tick);
      rctx.strokeStyle = "#3987e5";
      rctx.lineWidth = 1.4;
      rctx.beginPath();
      rctx.moveTo(px, yA - 10); rctx.lineTo(px + 3.5, yA - 5.5); rctx.lineTo(px, yA - 1); rctx.lineTo(px - 3.5, yA - 5.5);
      rctx.closePath(); rctx.stroke();
    });

    if (forked && focusFork) {
      rctx.strokeStyle = "rgba(144,133,233,0.55)";
      rctx.beginPath(); rctx.moveTo(x(focusFork.forkTick), yA); rctx.lineTo(x(focusFork.forkTick), yB);
      rctx.lineTo(x(S.viewTick), yB); rctx.stroke();
    }

    S.markers.forEach((mk) => {
      const y = (forked && mk.tl === "B") ? yB : yA;
      if (!forked && mk.tl === "B") return;
      if (mk.kind === "crisis") {
        rctx.strokeStyle = "#d03b3b"; rctx.lineWidth = 1.6;
        rctx.beginPath(); rctx.moveTo(x(mk.tick), y - 5); rctx.lineTo(x(mk.tick), y + 5); rctx.stroke();
      } else {
        rctx.fillStyle = "#9085e9";
        const px = x(mk.tick);
        rctx.beginPath();
        rctx.moveTo(px, y - 6); rctx.lineTo(px + 4, y); rctx.lineTo(px, y + 6); rctx.lineTo(px - 4, y);
        rctx.closePath(); rctx.fill();
      }
    });
    const py = forked ? yB : yA;
    const px = x(S.viewTick);
    rctx.strokeStyle = S.scrubbed ? "#fab219" : "#ffffff";
    rctx.lineWidth = 2;
    rctx.beginPath(); rctx.moveTo(px, py - 9); rctx.lineTo(px, py + 9); rctx.stroke();
    rctx.fillStyle = S.scrubbed ? "#fab219" : "#ffffff";
    rctx.beginPath(); rctx.arc(px, py, 3.2, 0, Math.PI * 2); rctx.fill();

    rctx.fillStyle = "#898781";
    rctx.font = "9px ui-monospace, monospace";
    rctx.textAlign = "left"; rctx.fillText("t0", 8, h - 2);
    rctx.textAlign = "right"; rctx.fillText(S.viewReady ? "t" + S.viewTick : "t—", w - 8, h - 2);
  }

  function updateRibbonSize(width, height) {
    const nextWidth = Math.max(1, Math.round(width));
    const nextHeight = Math.max(1, Math.round(height));
    if (ribbonSize.width === nextWidth && ribbonSize.height === nextHeight) return;
    ribbonSize = { width: nextWidth, height: nextHeight };
    drawRibbon();
  }

  function measureRibbon() {
    const rect = ribbon.getBoundingClientRect();
    updateRibbonSize(rect.width, rect.height);
  }

  if (window.ResizeObserver) {
    new ResizeObserver((entries) => {
      const rect = entries[0] && entries[0].contentRect;
      if (rect) updateRibbonSize(rect.width, rect.height);
    }).observe(ribbon);
  }
  measureRibbon();

  let scrubRect = null;
  function tickFromX(clientX) {
    const rect = scrubRect || ribbon.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left - 8) / (rect.width - 16)));
    return Math.round(frac * Math.max(S.primeLive, 300));
  }
  let scrubDragging = false;
  function scheduleScrub(clientX) {
    S.scrubClientX = clientX;
    if (S.scrubFrame != null) return;
    S.scrubFrame = requestAnimationFrame(() => {
      S.scrubFrame = null;
      if (scrubDragging && S.scrubClientX != null) requestWorldAt(tickFromX(S.scrubClientX));
    });
  }
  ribbon.addEventListener("pointerdown", (ev) => {
    if (S.forks.length) { toast("Scrubbing is locked while forks exist — resolve them first.", "warn"); return; }
    scrubDragging = true;
    scrubRect = ribbon.getBoundingClientRect();
    ribbon.setPointerCapture(ev.pointerId);
    requestWorldAt(tickFromX(ev.clientX));
  });
  ribbon.addEventListener("pointermove", (ev) => {
    if (scrubDragging) scheduleScrub(ev.clientX);
  });
  ribbon.addEventListener("pointerup", () => {
    scrubDragging = false;
    scrubRect = null;
    S.scrubClientX = null;
  });
  window.addEventListener("resize", measureRibbon);

  // ---------- scrub banner ----------
  function renderScrubBanner() {
    const b = $("scrubBanner");
    b.hidden = !S.scrubbed;
    if (S.scrubbed) $("scrubText").textContent = "VIEWING PAST · t" + S.viewTick + "  (live t" + S.liveTick + ")";
  }
  $("btnResumeLive").addEventListener("click", () => {
    sendCommand({ cmd: "resumeLive" }, "Returning to live world…", "resumeLive");
  });
  $("btnForkHere").addEventListener("click", () => { openGod(S.viewTick); });

  // ---------- the annals & checkpoints (v1.3) ----------
  // Real-engine mode: every war, date and number in the Annals is derived from events this
  // page has received for the focused timeline; only era names, war titles and epigraphs are
  // the archivists' prose. The fully invented history (antiquity, tolls, outcomes, "ever"
  // records) is kept for the explicit offline mock only (design guide §B6).

  function hash32(s) {
    let x = 2166136261;
    for (let i = 0; i < s.length; i++) { x ^= s.charCodeAt(i); x = Math.imul(x, 16777619); }
    return x >>> 0;
  }
  const pickBy = (arr, h) => arr[h % arr.length];
  const cname = (code) => (S.byCode[code] ? S.byCode[code].name : code);
  const yearDay = (t) => "Y" + (Math.floor(t / 365) + 1) + "·D" + ((t % 365) + 1);
  const ckDate = (t) => "Year " + (Math.floor(t / 365) + 1) + " · Day " + ((t % 365) + 1);

  const ERA_NAMES = {
    calm: ["The Quiet Years", "The Long Calm", "The Years of Small News", "The Slow Season"],
    boom: ["The Long Boom", "The Gilded Stretch", "The Fat Years", "The Age of Plenty"],
    crisis: ["The Unravelling", "The Years of Smoke", "The Great Disorder", "The Hungry Season"],
    war: ["The Burning Years", "The Age of Banners", "The Long Quarrel", "The Iron Season"],
    divine: ["The Age of Meddling", "The Visited Years", "The Season of Portents", "The Second Genesis"]
  };
  const ERA_EPIGRAPHS = {
    calm: "Little happened, and everyone would later claim to miss it.",
    boom: "Money was easy and memory was short.",
    crisis: "Every granary and every promise ran low at once.",
    war: "The maps were redrawn in the usual ink.",
    divine: "Historians agree that something kept touching the scales."
  };
  const ANTIQUITY = [
    { name: "The Founding of the Eight", line: "Eight nations, one bad map, centuries of consequences." },
    { name: "The First Concordat", line: "Signed in good faith and better wine; it held for a generation." },
    { name: "The Comet Year", line: "The harvest failed everywhere at once, and the crowns blamed the sky." }
  ];
  const WAR_EPITHETS = ["the Broken Granary", "the Two Rivers", "the Salt Tariff", "the Counterfeit Crown",
    "the Long Winter", "the Burned Charts", "the Widow's Toll", "the Silent Border"];
  const WAR_OUTCOMES = [
    "$W took the border provinces and the blame.",
    "White peace; the grudge survived intact.",
    "$L ceded two rivers and thirty years of pride.",
    "Ended by treaty, exhaustion, and an unusually hard winter.",
    "$W declared victory; historians declare a draw."
  ];

  function eventGroup(e) {
    if (e.intervention) return "divine";
    const k = String(e.kind);
    if (k === "WAR_DECLARED" || k === "PEACE") return "war";
    if (k === "ELECTION" || k === "SCANDAL" || k === "UNREST" || k === "SECESSION") return "throne";
    if (k === "DROUGHT" || k === "PLAGUE" || k === "EARTHQUAKE" || k === "METEOR" || k === "FAMINE_WARNING") return "disaster";
    if (k === "DEBT_CRISIS" || k === "INFLATION_CRISIS" || k === "GOLDEN_AGE" || k === "INNOVATION") return "economy";
    return "other";
  }
  const ANN_GROUPS = [
    ["ALL", "everything"], ["war", "⚔ wars"], ["throne", "♛ thrones & streets"],
    ["economy", "¤ economy"], ["disaster", "☄ disasters"], ["divine", "✦ acts of god"]
  ];

  // Events received for the focused timeline, plus local narration (negative ids).
  function timelineEvents() {
    const own = Object.values(S.eventsByTimeline[activeTimelineId()] || {});
    const local = Object.values(S.events).filter((e) => e.id < 0);
    return own.concat(local).filter((e) => e.tick <= S.viewTick || e.id < 0);
  }
  function majorEvents() {
    return timelineEvents()
      .filter((e) => e.intervention || e.severity >= 1)
      .sort((a, b) => a.tick - b.tick || a.id - b.id);
  }
  function actsOfGod() {
    return timelineEvents().filter((e) => e.intervention).sort((a, b) => b.tick - a.tick || b.id - a.id);
  }

  // Wars as the record has them: each WAR_DECLARED with its two belligerents, ended by the
  // first PEACE either side fires afterwards (the engine's own annals rule). Wars raging now
  // whose declaration predates what this page holds are listed without a start.
  function recordedWars() {
    const seed = S.hello ? S.hello.seed : 0;
    const evs = timelineEvents().filter((e) => e.id >= 0).sort((a, b) => a.tick - b.tick || a.id - b.id);
    const wars = [];
    evs.forEach((e) => {
      if (e.kind !== "WAR_DECLARED" || !e.country || !e.country2) return;
      const pair = [e.country, e.country2];
      const peace = evs.find((later) => later.tick > e.tick && later.kind === "PEACE" && pair.indexOf(later.country) >= 0);
      wars.push({
        a: e.country, b: e.country2, from: e.tick, to: peace ? peace.tick : null,
        declaredId: e.id, peaceId: peace ? peace.id : null,
        name: warTitle(e.country, e.country2, hash32("w" + seed + e.id))
      });
    });
    (S.wars[focusTL()] || []).forEach((w) => {
      const known = wars.some((x) => x.to == null && ((x.a === w[0] && x.b === w[1]) || (x.a === w[1] && x.b === w[0])));
      if (!known) wars.push({ a: w[0], b: w[1], from: null, to: null, declaredId: null, peaceId: null, name: warTitle(w[0], w[1], hash32("w" + seed + w[0] + w[1])) });
    });
    const raging = new Set((S.wars[focusTL()] || []).map((w) => [w[0], w[1]].sort().join(":")));
    wars.forEach((w) => { w.raging = w.to == null && raging.has([w.a, w.b].sort().join(":")); });
    return wars.reverse();
  }

  function recordedRecords() {
    const recs = [];
    let wi = null, ws = null;
    S.order.forEach((code) => {
      const sp = S.spark[code];
      if (!sp) return;
      sp.inflation.forEach((v) => { if (!wi || v > wi.v) wi = { v: v, code: code }; });
      sp.stability.forEach((v) => { if (!ws || v < ws.v) ws = { v: v, code: code }; });
    });
    if (wi) recs.push({ label: "WORST INFLATION, LIVING MEMORY", value: fmt1(wi.v) + "%", note: cname(wi.code) + " · last 120 ticks" });
    if (ws) recs.push({ label: "LOWEST STABILITY, LIVING MEMORY", value: fmt1(ws.v), note: cname(ws.code) + " · last 120 ticks" });
    const finished = recordedWars().filter((w) => w.from != null && w.to != null);
    if (finished.length) {
      const longest = finished.reduce((best, w) => (w.to - w.from > best.to - best.from ? w : best));
      recs.push({ label: "LONGEST WAR ON RECORD", value: (longest.to - longest.from) + " ticks", note: longest.name });
    }
    const evs = timelineEvents().filter((e) => e.id >= 0);
    const coups = {};
    evs.filter((e) => e.kind === "COUP" && e.country).forEach((e) => { coups[e.country] = (coups[e.country] || 0) + 1; });
    const coupLeader = Object.keys(coups).sort((a, b) => coups[b] - coups[a] || a.localeCompare(b))[0];
    if (coupLeader) recs.push({ label: "MOST COUPS", value: String(coups[coupLeader]), note: cname(coupLeader) });
    const crises = evs.filter((e) => e.severity === 2 && !e.intervention).length;
    recs.push({ label: "CRISES ON RECORD", value: String(crises), note: crises ? "severity-2 headlines in this record" : "not one, so far" });
    const acts = actsOfGod().length;
    recs.push({ label: "ACTS OF GOD", value: String(acts), note: acts ? "and the world has noticed" : "the world remains untouched" });
    return recs;
  }

  function fabricateEras() {
    const seed = S.hello ? S.hello.seed : 0;
    const end = Math.max(isMockEngine ? S.primeLive : 0, S.viewTick, 1);
    const n = Math.max(2, Math.min(6, Math.floor(end / 250) + 2));
    const evs = majorEvents();
    const eras = [];
    const flavorCount = { calm: 0, boom: 0, crisis: 0, war: 0, divine: 0 };
    const rot = hash32("era" + seed) % 4;
    let from = 0;
    for (let i = 0; i < n; i++) {
      const to = (i === n - 1) ? end : Math.round(((i + 1) / n) * end);
      const within = evs.filter((e) => e.tick >= from && (i === n - 1 ? e.tick <= to : e.tick < to));
      let war = 0, crisis = 0, divine = 0;
      within.forEach((e) => {
        if (e.intervention) divine++;
        else if (eventGroup(e) === "war") war++;
        else if (e.severity === 2) crisis++;
      });
      let flavor = "calm";
      if (divine >= 2 && divine >= war && divine >= crisis) flavor = "divine";
      else if (war >= 1 && war >= crisis) flavor = "war";
      else if (crisis >= 3) flavor = "crisis";
      else if (isMockEngine && ((hash32("f" + seed + ":" + i) >>> 3) & 3) === 0) flavor = "boom";
      const idx = flavorCount[flavor]++;
      const name = ERA_NAMES[flavor][(rot + idx) % 4] + (idx >= 4 ? " II" : "");
      eras.push({ name: name, flavor: flavor, from: from, to: to, events: within, current: i === n - 1 });
      from = to;
    }
    return eras;
  }

  function warTitle(a, b, h) {
    return (h & 1)
      ? "The " + cname(a) + "–" + cname(b) + " War"
      : "The War of " + pickBy(WAR_EPITHETS, h >>> 2);
  }

  function fabricateWars() {
    const seed = S.hello ? S.hello.seed : 0;
    const wars = [];
    (S.wars[focusTL()] || []).forEach((w) => {
      const h = hash32("w" + seed + w[0] + w[1]);
      wars.push({
        a: w[0], b: w[1], raging: true,
        name: warTitle(w[0], w[1], h),
        from: Math.max(1, S.viewTick - (60 + (h % 320))), to: null,
        toll: 4 + (h % 90),
        outcome: "No end in sight. Both capitals print victory posters from the same template."
      });
    });
    // every observed PEACE closes a war of record (the rest of it is invented)
    Object.values(S.events).filter((e) => e.kind === "PEACE").sort((a, b) => a.tick - b.tick).forEach((e) => {
      const h = hash32("p" + seed + e.id);
      const others = S.order.filter((c) => c !== e.country);
      const foe = others.length ? pickBy(others, h) : e.country;
      const winFirst = (h >>> 5) & 1;
      wars.push({
        a: e.country, b: foe, raging: false,
        name: warTitle(e.country, foe, h),
        from: Math.max(1, e.tick - (90 + (h % 400))), to: e.tick,
        toll: 9 + (h % 140),
        outcome: pickBy(WAR_OUTCOMES, h >>> 3)
          .replace("$W", cname(winFirst ? e.country : foe))
          .replace("$L", cname(winFirst ? foe : e.country))
      });
    });
    // antiquity — the wars every schoolchild resents memorizing
    for (let i = 0; i < 2 && S.order.length > 1; i++) {
      const h = hash32("aw" + seed + ":" + i);
      const a = S.order[h % S.order.length];
      let b = S.order[(h >>> 3) % S.order.length];
      if (b === a) b = S.order[(h % S.order.length + 1) % S.order.length];
      wars.push({
        a: a, b: b, raging: false, ancient: true,
        name: warTitle(a, b, h), from: null, to: null,
        toll: 20 + (h % 400),
        outcome: pickBy(WAR_OUTCOMES, h >>> 7).replace("$W", cname(a)).replace("$L", cname(b))
      });
    }
    return wars;
  }

  function fabricateRecords(eras) {
    const seed = S.hello ? S.hello.seed : 0;
    const recs = [];
    let wi = null, ws = null;
    S.order.forEach((code) => {
      const sp = S.spark[code];
      if (!sp) return;
      sp.inflation.forEach((v) => { if (!wi || v > wi.v) wi = { v: v, code: code }; });
      sp.stability.forEach((v) => { if (!ws || v < ws.v) ws = { v: v, code: code }; });
    });
    const h = hash32("rec" + seed);
    if (wi) recs.push({ label: "WORST INFLATION, LIVING MEMORY", value: fmt1(wi.v) + "%", note: cname(wi.code) });
    recs.push({
      label: "WORST INFLATION EVER", value: fmt1((wi ? wi.v : 12) * (2.1 + (h % 10) / 10)) + "%",
      note: cname(pickBy(S.order, h)) + ", the Comet Year — never surpassed"
    });
    if (ws) recs.push({ label: "LOWEST STABILITY, LIVING MEMORY", value: fmt1(ws.v), note: cname(ws.code) });
    recs.push({
      label: "LONGEST WAR", value: (3 + (h % 9)) + " years",
      note: "The War of " + pickBy(WAR_EPITHETS, h >>> 2) + ", antiquity"
    });
    recs.push({
      label: "RICHEST TREASURY EVER", value: ((8 + (h % 40)) * 500) + "M",
      note: cname(pickBy(S.order, h >>> 4)) + ", " + (eras.length > 1 ? eras[Math.floor(eras.length / 2)].name : "antiquity")
    });
    const acts = actsOfGod().length;
    recs.push({ label: "ACTS OF GOD", value: String(acts), note: acts ? "and the world has noticed" : "the world remains untouched" });
    return recs;
  }

  function localAnnalsImpactPayload(timelineId, sortBy) {
    const events = Object.values(S.eventsByTimeline[timelineId] || {}).sort((a, b) => a.id - b.id);
    const byId = {};
    const children = {};
    events.forEach((event) => { byId[event.id] = event; });
    events.forEach((event) => {
      const parents = event.parentIds || (event.parentId != null ? [event.parentId] : []);
      parents.forEach((parentId) => {
        if (!children[parentId]) children[parentId] = [];
        children[parentId].push(event.id);
      });
    });
    const leaders = [];
    events.forEach((event) => {
      const direct = (children[event.id] || []).slice().sort((a, b) => a - b);
      if (!direct.length) return;
      const seen = new Set();
      const pending = direct.slice().reverse().map((id) => [id, 1]);
      let generations = 0;
      while (pending.length) {
        const entry = pending.pop();
        if (seen.has(entry[0])) continue;
        seen.add(entry[0]); generations = Math.max(generations, entry[1]);
        (children[entry[0]] || []).slice().reverse().forEach((id) => pending.push([id, entry[1] + 1]));
      }
      const descendants = Array.from(seen).sort((a, b) => a - b).map((id) => byId[id]).filter(Boolean);
      const affected = new Set();
      [event].concat(descendants).forEach((item) => {
        if (item.country) affected.add(item.country);
        if (item.country2) affected.add(item.country2);
      });
      leaders.push({
        event: event,
        directChildren: direct.length,
        descendants: descendants.length,
        generations: generations,
        affectedCountries: Array.from(affected).sort(),
        recordedEffects: [event].concat(descendants).reduce((sum, item) => sum + (item.effects || []).length, 0),
        crisisDescendants: descendants.filter((item) => item.severity >= 2).length,
        lastDescendantTick: descendants.reduce((tick, item) => Math.max(tick, item.tick), event.tick),
        children: direct.slice(0, 6).map((id) => byId[id]).filter(Boolean)
      });
    });
    const fields = {
      descendants: ["descendants", "directChildren", "recordedEffects", "affectedCountries"],
      children: ["directChildren", "descendants", "recordedEffects", "affectedCountries"],
      effects: ["recordedEffects", "descendants", "directChildren", "affectedCountries"],
      countries: ["affectedCountries", "descendants", "directChildren", "recordedEffects"]
    };
    const normalized = fields[sortBy] ? sortBy : "descendants";
    const value = (row, field) => field === "affectedCountries" ? row.affectedCountries.length : row[field];
    leaders.sort((a, b) => {
      for (const field of fields[normalized]) {
        const diff = value(b, field) - value(a, field);
        if (diff) return diff;
      }
      return b.event.severity - a.event.severity || a.event.id - b.event.id;
    });
    return {
      type: "annalsImpact", tl: timelineId, tick: S.viewTick, sortBy: normalized,
      eventCount: events.length, leaders: leaders.slice(0, 30), approximate: true
    };
  }

  function onAnnalsImpact(message) {
    S.annalsImpact[message.tl] = message;
    S.annalsImpactLoading[message.tl] = false;
    (message.leaders || []).forEach((row) => {
      indexEvent(row.event, message.tl);
      (row.children || []).forEach((event) => indexEvent(event, message.tl));
    });
    if (!S.tracePending && S.overlay === "annals" && S.annals.tab === "impact" && activeTimelineId() === message.tl) {
      openOverlay("annals");
    }
  }

  function requestAnnalsImpact(force) {
    const timelineId = activeTimelineId();
    const cached = S.annalsImpact[timelineId];
    if (!force && cached && cached.sortBy === S.annals.impactSort && cached.tick >= S.viewTick) return;
    if (S.annalsImpactLoading[timelineId]) return;
    S.annalsImpactLoading[timelineId] = true;
    if (isMockEngine) {
      onAnnalsImpact(localAnnalsImpactPayload(timelineId, S.annals.impactSort));
    } else {
      sendCommand(
        { cmd: "annalsImpact", tl: timelineId, sortBy: S.annals.impactSort, limit: 30 },
        "Computing timeline impact…",
        "annalsImpact:" + timelineId
      );
    }
  }

  // The engine's own archive (bridge `annals`): eras, wars, records and major events, all
  // derived from the real log. Until a bridge serves it, the tab falls back to deriving what
  // it can from the events this page has received.
  function requestAnnals(force) {
    if (isMockEngine) return;
    const timelineId = activeTimelineId();
    const cached = S.annalsData[timelineId];
    // The archive is a picture of one tick, so a scrub in either direction invalidates it.
    if (!force && cached && cached.tick === S.viewTick) return;
    if (S.annalsDataLoading[timelineId]) return;
    S.annalsDataLoading[timelineId] = true;
    const requestId = sendCommand(
      { cmd: "annals", tl: timelineId },
      "Reading the Annals…",
      "annals:" + timelineId
    );
    if (requestId == null) { S.annalsDataLoading[timelineId] = false; return; }
    setTimeout(() => {
      if (!S.pendingRequests[requestId]) return;
      // A silent request means one of two things this client cannot tell apart: a bridge
      // with no `annals` handler, which never answers, or an engine that is simply taking
      // its time over a long log (measured: 32s on a busy world). So the page stops
      // spinning and shows the record it holds, labelled — and does NOT claim the engine
      // lacks the feature. A late answer still lands and replaces the fallback.
      completeRequest({ requestId: requestId });
      S.annalsDataLoading[timelineId] = false;
      S.annalsLate[timelineId] = true;
      if (S.overlay === "annals" && S.annals.tab === "history") refreshAnnalsOverlay();
    }, 20000);
  }

  function onAnnalsData(m) {
    const timelineId = m.tl || activeTimelineId();
    S.annalsDataLoading[timelineId] = false;
    delete S.annalsLate[timelineId];
    S.annalsData[timelineId] = m;
    // `majorEvents` are summaries: no causes, no ledger, no effects. They are never indexed
    // into the event cache, because that would hollow out a full event the feed already has.
    if (S.overlay === "annals" && S.annals.tab === "history") refreshAnnalsOverlay();
  }

  // Redraw the open Annals in place. Deliberately not `openOverlay`, which would ask the
  // engine for the archive again and, with the clock running, never stop asking.
  function refreshAnnalsOverlay() {
    if (S.overlay !== "annals") return;
    const card = $("overlayCard");
    const scrollTop = card.scrollTop;
    card.innerHTML = annalsHTML();
    wireOverlay();
    card.scrollTop = scrollTop;
  }

  // The archive rows carry only a summary, so an inspector opened from here is seeded with
  // that summary and asks the engine for the rest, exactly as the Impact tab does.
  function annalsDataEvent(timelineId, eventId) {
    const data = S.annalsData[timelineId];
    if (!data) return null;
    return (data.majorEvents || []).find((event) => event.id === eventId) || null;
  }

  const ERA_FLAVOR_BY_KIND = {
    WAR_DECLARED: "war", PEACE: "war", STRIKE: "war",
    DROUGHT: "crisis", PLAGUE: "crisis", EARTHQUAKE: "crisis", METEOR: "crisis",
    FAMINE_WARNING: "crisis", INFLATION_CRISIS: "crisis", DEBT_CRISIS: "crisis",
    UNREST: "crisis", COUP: "crisis", SECESSION: "crisis",
    GOLDEN_AGE: "boom", INNOVATION: "boom", COMMODITY_PRODUCTION: "calm",
    GOD_EDIT: "divine", GOD_RELATION_SHIFT: "divine"
  };

  function annalsDataHTML(data) {
    const A = S.annals;
    const timelineId = data.tl || activeTimelineId();
    const seed = S.hello ? S.hello.seed : 0;
    const majorAll = (data.majorEvents || []).slice().sort((a, b) => a.tick - b.tick || a.id - b.id);
    const match = (e) =>
      (A.country === "ALL" || e.country === A.country || e.country2 === A.country) &&
      (A.group === "ALL" || eventGroup(e) === A.group);
    const unfiltered = A.country === "ALL" && A.group === "ALL";
    let h = annalsFilterRowHTML();
    h += '<div class="annscope">The engine\'s own archive for timeline ' + esc(timelineId) +
      " through t" + data.tick + " · " + (data.majorEventCount || majorAll.length) + " events of severity 1 and up" +
      (data.majorEventCount && data.majorEventCount > majorAll.length ? " (the most recent " + majorAll.length + " shown)" : "") +
      ". Era names and war titles are the archivists' own; every date and number is the engine's.</div>";
    // The filters run over the entries delivered above, and the bridge caps that at the most
    // recent slice of a long record. Say which of the two the filter actually searched, so a
    // nation's tally is never read as its whole history.
    if (!unfiltered) {
      const truncated = data.majorEventCount != null && data.majorEventCount > majorAll.length;
      h += '<div class="annmore" style="margin:-4px 0 10px">' + (truncated
        ? "These filters search the " + majorAll.length + " entries delivered here, not all " +
          data.majorEventCount + " the record holds — a tally below is of this window, not of the whole history."
        : "The record fits in the entries delivered here, so these filters searched all of it.") + "</div>";
    }

    if (unfiltered) {
      h += '<div class="insp-sechead">RECORDS &amp; SUPERLATIVES</div><div class="recordgrid">';
      const records = data.records || {};
      const cards = [];
      if (records.worstInflation) {
        cards.push({ label: "WORST INFLATION ON RECORD", value: fmt1(records.worstInflation.value) + "%", note: cname(records.worstInflation.country) + " · t" + records.worstInflation.tick });
      }
      if (records.longestWar) {
        cards.push({ label: "LONGEST WAR ON RECORD", value: records.longestWar.ticks + " ticks", note: cname(records.longestWar.aggressor) + " vs " + cname(records.longestWar.defender) });
      }
      if (records.mostCoups) {
        cards.push({ label: "MOST COUPS", value: String(records.mostCoups.count), note: cname(records.mostCoups.country) });
      }
      recordedRecords().filter((r) => r.label.indexOf("LIVING MEMORY") >= 0 || r.label === "ACTS OF GOD").forEach((r) => cards.push(r));
      cards.forEach((r) => {
        h += '<div class="record"><span class="rlabel">' + r.label + '</span><span class="rval">' + esc(r.value) +
          '</span><span class="rnote">' + esc(r.note) + "</span></div>";
      });
      h += "</div>";
    }

    if (A.group === "ALL" || A.group === "war") {
      const wars = (data.wars || []).filter((w) => A.country === "ALL" || w.aggressor === A.country || w.defender === A.country);
      h += '<div class="insp-sechead" style="color:var(--serious)">⚔ WARS OF RECORD</div>';
      if (!wars.length) h += '<div class="annmore">Not one war on record. Suspicious.</div>';
      wars.slice().reverse().forEach((w) => {
        const ongoing = w.outcome !== "peace" || w.endTick == null;
        const name = warTitle(w.aggressor, w.defender, hash32("w" + seed + w.aggressor + w.defender + w.startTick));
        h += '<div class="warcard' + (ongoing ? " raging" : "") + '">' +
          '<span class="warname">' + esc(name) + "</span>" +
          '<span class="cchip" style="--c:' + (S.color[w.aggressor] || "#3987e5") + '">' + esc(w.aggressor) + "</span>" +
          '<span class="warvs">vs</span>' +
          '<span class="cchip" style="--c:' + (S.color[w.defender] || "#3987e5") + '">' + esc(w.defender) + "</span>" +
          '<span class="wardates">' + yearDay(w.startTick) + " — " + (w.endTick != null ? yearDay(w.endTick) : "present") +
          (w.ticks != null ? " · " + w.ticks + " ticks" : "") + "</span>" +
          (ongoing ? '<span class="ragingtag">RAGING</span>' : "") +
          '<span class="waroutcome">' + (w.endTick != null ? "Ended in peace at t" + w.endTick + "." : "Still raging at t" + data.tick + ".") + "</span></div>";
      });
    }

    const eras = (data.eras || []);
    const flavorCount = { calm: 0, boom: 0, crisis: 0, war: 0, divine: 0 };
    const rot = hash32("era" + seed) % 4;
    // The engine closes an era ON its boundary event and opens the next one at the same
    // tick, so the windows share an endpoint. Walk the events once, in order, and let each
    // one belong to exactly one era rather than filtering by an overlapping tick range.
    const rowsByEra = eras.map(() => []);
    let eraCursor = 0;
    majorAll.forEach((event) => {
      while (eraCursor < eras.length - 1 && event.tick > eras[eraCursor].endTick) eraCursor++;
      if (eraCursor < eras.length) rowsByEra[eraCursor].push(event);
    });
    let shown = 0;
    eras.forEach((era, index) => {
      const rows = rowsByEra[index].filter(match);
      if (!rows.length && !unfiltered) return;
      shown++;
      const flavor = ERA_FLAVOR_BY_KIND[era.dominantKind] || "calm";
      const idx = flavorCount[flavor]++;
      const name = ERA_NAMES[flavor][(rot + idx) % 4] + (idx >= 4 ? " II" : "");
      h += '<div class="eraband"><span class="eraname">' + esc(name) + "</span>" +
        '<span class="erarange">' + yearDay(era.startTick) + " — " +
        (index === eras.length - 1 ? "present" : yearDay(era.endTick)) + "</span>" +
        '<div class="eraepi">' + ERA_EPIGRAPHS[flavor] + "</div>" +
        '<div class="annmore">' + era.eventCount + " recorded events · mostly " + esc(humanKind(era.dominantKind).toLowerCase()) + "</div>";
      if (rows.length) {
        h += '<ul class="minilist">';
        rows.slice(-14).forEach((e) => {
          h += '<li data-annev="' + e.id + '" data-anntl="' + esc(timelineId) + '"><span class="ev-tick">t' + e.tick + '</span> ' +
            '<span class="cchip" style="--c:' + (S.color[e.country] || "#3987e5") + '">' + esc(e.country || "—") + "</span> " +
            esc(e.headline && e.headline !== e.kind ? e.headline : humanKind(e.kind)) + "</li>";
        });
        h += "</ul>";
        if (rows.length > 14) h += '<div class="annmore">…and ' + (rows.length - 14) + " more entries the archivists kept.</div>";
      }
      h += "</div>";
    });
    if (!shown) {
      h += '<div class="hint" style="padding:20px 4px">' + (unfiltered
        ? "Nothing of consequence has happened yet. Give it a few hundred ticks."
        : "The record holds nothing under this filter" +
          (A.country === "ALL" ? "" : " for " + esc(cname(A.country))) + " yet.") + "</div>";
    }
    return h;
  }

  function annalsImpactHTML() {
    const A = S.annals;
    const timelineId = activeTimelineId();
    const data = S.annalsImpact[timelineId];
    let h = '<div class="impact-annals-intro"><b>CAUSAL IMPACT · TIMELINE ' + esc(timelineId) + "</b>" +
      '<span>Ranked from recorded DAG structure—no weighted or invented impact score. Descendants reached by multiple paths count once.</span></div>';
    h += '<div class="filterrow impact-filters"><select class="annselect" id="annCountry"><option value="ALL">All nations</option>';
    S.order.forEach((code) => {
      h += '<option value="' + code + '"' + (A.country === code ? " selected" : "") + ">" + esc(cname(code)) + "</option>";
    });
    h += "</select>";
    [["descendants", "Most descendants"], ["children", "Most children"], ["effects", "Most effects"], ["countries", "Most nations"]].forEach((item) => {
      h += '<button class="fchip' + (A.impactSort === item[0] ? " on" : "") + '" data-impact-sort="' + item[0] + '">' + item[1] + "</button>";
    });
    h += "</div>";
    if (!data || data.sortBy !== A.impactSort) {
      return h + '<div class="impact-annals-loading">Computing deduplicated descendants across the full timeline…</div>';
    }
    const rows = (data.leaders || []).filter((row) => A.country === "ALL" || row.affectedCountries.includes(A.country));
    const scope = data.scope;
    h += '<div class="impact-annals-scope">' + data.eventCount + " recorded events through t" + data.tick +
      (data.approximate ? " · explicit offline mock cache"
        : (scope ? " · ranked t" + scope.startTick + "–t" + scope.endTick +
            (scope.rankedEvents != null ? " (" + scope.rankedEvents + " ranked)" : "") +
            (scope.complete ? " · the complete timeline" : "")
          : " · complete engine timeline")) + "</div>";
    if (!data.approximate) {
      h += '<div class="annmore" style="margin:-6px 0 10px">Every count below is of what the record' +
        (scope && !scope.complete ? " holds inside that window" : " holds") +
        ". The wording describes each event in today's terms, not the sentence the chronicle " +
        "printed at the time.</div>";
    }
    if (!rows.length) return h + '<div class="hint" style="padding:20px 4px">No event with causal offspring matches this filter yet.</div>';
    rows.forEach((row, index) => {
      const event = row.event;
      h += '<article class="impact-leader sev' + event.severity + (event.intervention ? " iv" : "") + '" data-impactev="' + event.id + '">' +
        '<div class="impact-rank">#' + (index + 1) + '</div><div class="impact-leader-main">' +
        '<div class="impact-leader-meta"><span class="ev-tick">t' + event.tick + '</span><span class="cchip" style="--c:' + (S.color[event.country] || "#3987e5") + '">' + esc(event.country || "WORLD") + "</span>" +
        '<span>' + esc(event.kind) + "</span></div>" +
        '<div class="impact-leader-head' + (!event.headline || event.headline === event.kind ? " ambient" : "") + '">' +
        esc(!event.headline || event.headline === event.kind ? humanKind(event.kind) : event.headline) + "</div>" +
        '<div class="impact-metrics"><span><b>' + row.descendants + "</b> descendants</span><span><b>" + row.directChildren + "</b> children</span><span><b>" + row.generations + "</b> generations</span><span><b>" + row.affectedCountries.length + "</b> nations</span><span><b>" + row.recordedEffects + "</b> effects</span><span><b>" + row.crisisDescendants + "</b> crises</span></div>";
      if (row.children && row.children.length) {
        h += '<div class="impact-offspring"><b>DIRECT OFFSPRING</b>';
        row.children.slice(0, 4).forEach((child) => {
          h += '<span><i>↳ t' + child.tick + "</i> " + esc(child.headline) + "</span>";
        });
        if (row.directChildren > 4) h += '<span class="annmore">+' + (row.directChildren - 4) + " more direct children</span>";
        h += "</div>";
      }
      h += '<div class="impact-leader-actions"><button class="abtn" data-anntrace="' + event.id + '">Trace whole DAG</button><span>cascade through t' + row.lastDescendantTick + "</span></div></div></article>";
    });
    return h;
  }

  function annalsHTML() {
    if (!S.order.length) return '<div class="hint">The world is still forming.</div>';
    const A = S.annals;
    const focusName = A.country !== "ALL" ? cname(A.country) : null;
    let h = '<div class="ov-title">📜 The Annals' + (focusName ? " · " + esc(focusName) : "") + "</div>" +
      '<div class="ov-sub">The long record — eras, wars, ruins and miracles. ' +
      "A number without the history that produced it is just a number.</div>";
    h += '<div class="tlswitch anntabs">' +
      '<button data-anntab="history"' + (A.tab === "history" ? ' class="on"' : "") + ">WORLD HISTORY</button>" +
      '<button data-anntab="impact"' + (A.tab === "impact" ? ' class="on"' : "") + ">⚡ IMPACT</button>" +
      '<button data-anntab="acts"' + (A.tab === "acts" ? ' class="on"' : "") + ">✦ ACTS OF GOD · " + actsOfGod().length + "</button>" +
      "</div>";
    if (A.tab === "impact") h += annalsImpactHTML();
    else if (A.tab === "acts") h += annalsActsHTML();
    else if (!isMockEngine && S.annalsData[activeTimelineId()]) h += annalsDataHTML(S.annalsData[activeTimelineId()]);
    else if (!isMockEngine && S.annalsDataLoading[activeTimelineId()] && !S.annalsLate[activeTimelineId()]) {
      // Never show the page-derived record while the engine's own archive is on its way —
      // the two do not agree, and the real one is authoritative. Once this timeline's
      // archive has been late once, a retry waits behind the fallback instead of putting
      // the reader back on a spinner every time a filter changes.
      h += annalsFilterRowHTML() + loadingHTML("Reading the Annals from the engine…");
    } else {
      if (!isMockEngine && S.annalsLate[activeTimelineId()]) {
        h += '<div class="annscope">The engine has not answered for its archive yet, so what ' +
          "follows is assembled from the events this page has received. It is replaced the " +
          "moment the archive arrives.</div>";
      }
      h += annalsHistoryHTML();
    }
    h += '<div class="ov-actions"><button class="cancel" data-act="close">esc · close</button></div>';
    return h;
  }

  // Nation and category filters, shared by both history renderers so the engine-backed
  // archive is filterable exactly like the page-derived one.
  function annalsFilterRowHTML() {
    const A = S.annals;
    let h = '<div class="filterrow"><select class="annselect" id="annCountry">' +
      '<option value="ALL">All nations</option>';
    S.order.forEach((code) => {
      h += '<option value="' + code + '"' + (A.country === code ? " selected" : "") + ">" + esc(cname(code)) + "</option>";
    });
    h += "</select>";
    ANN_GROUPS.forEach((g) => {
      h += '<button class="fchip' + (A.group === g[0] ? " on" : "") + '" data-group="' + g[0] + '">' + g[1] + "</button>";
    });
    return h + "</div>";
  }

  function annalsHistoryHTML() {
    const A = S.annals;
    const eras = fabricateEras();
    let h = annalsFilterRowHTML();

    const match = (e) =>
      (A.country === "ALL" || e.country === A.country) &&
      (A.group === "ALL" || eventGroup(e) === A.group);
    const unfiltered = A.country === "ALL" && A.group === "ALL";

    if (!isMockEngine) {
      const held = timelineEvents().filter((e) => e.id >= 0);
      const first = held.reduce((t, e) => Math.min(t, e.tick), Infinity);
      h += '<div class="annscope">From the ' + held.length + " events this page has received on timeline " + esc(activeTimelineId()) +
        (held.length ? " (t" + first + "–t" + S.viewTick + ")" : "") +
        ". Era names and war titles are the archivists' own; every date, war and number is from the record.</div>";
    }

    if (unfiltered) {
      h += '<div class="insp-sechead">RECORDS &amp; SUPERLATIVES</div><div class="recordgrid">';
      (isMockEngine ? fabricateRecords(eras) : recordedRecords()).forEach((r) => {
        h += '<div class="record"><span class="rlabel">' + r.label + '</span><span class="rval">' + esc(r.value) +
          '</span><span class="rnote">' + esc(r.note) + "</span></div>";
      });
      h += "</div>";
    }

    if (!isMockEngine && (A.group === "ALL" || A.group === "war")) {
      const wars = recordedWars().filter((w) => A.country === "ALL" || w.a === A.country || w.b === A.country);
      h += '<div class="insp-sechead" style="color:var(--serious)">⚔ WARS OF RECORD</div>';
      if (!wars.length) h += '<div class="annmore">Not one war on record. Suspicious.</div>';
      wars.forEach((w) => {
        const dates = (w.from != null ? yearDay(w.from) : "before this record") + " — " + (w.to != null ? yearDay(w.to) : "present");
        const length = w.from != null ? ((w.to != null ? w.to : S.viewTick) - w.from) + " ticks" : "";
        const peacemaker = w.peaceId != null && S.events[w.peaceId] ? S.events[w.peaceId].country : null;
        const outcome = w.to != null ? (peacemaker ? "Ended when " + cname(peacemaker) + " made peace at t" + w.to + "." : "Ended in peace at t" + w.to + ".")
          : (w.raging ? "Still raging at t" + S.viewTick + "." : "No peace appears in this record.");
        h += '<div class="warcard' + (w.raging ? " raging" : "") + (w.declaredId != null ? '" data-annev="' + w.declaredId : "") + '">' +
          '<span class="warname">' + esc(w.name) + "</span>" +
          '<span class="cchip" style="--c:' + (S.color[w.a] || "#3987e5") + '">' + esc(w.a) + "</span>" +
          '<span class="warvs">vs</span>' +
          '<span class="cchip" style="--c:' + (S.color[w.b] || "#3987e5") + '">' + esc(w.b) + "</span>" +
          '<span class="wardates">' + dates + (length ? " · " + length : "") + "</span>" +
          (w.raging ? '<span class="ragingtag">RAGING</span>' : "") +
          '<span class="waroutcome">' + esc(outcome) + "</span></div>";
      });
    } else if (A.group === "ALL" || A.group === "war") {
      const wars = fabricateWars().filter((w) => A.country === "ALL" || w.a === A.country || w.b === A.country);
      h += '<div class="insp-sechead" style="color:var(--serious)">⚔ WARS OF RECORD</div>';
      if (!wars.length) h += '<div class="annmore">Not one war on record. Suspicious.</div>';
      wars.forEach((w) => {
        h += '<div class="warcard' + (w.raging ? " raging" : "") + '">' +
          '<span class="warname">' + esc(w.name) + "</span>" +
          '<span class="cchip" style="--c:' + (S.color[w.a] || "#3987e5") + '">' + w.a + "</span>" +
          '<span class="warvs">vs</span>' +
          '<span class="cchip" style="--c:' + (S.color[w.b] || "#3987e5") + '">' + w.b + "</span>" +
          '<span class="wardates">' + (w.ancient ? "antiquity" : (yearDay(w.from) + " — " + (w.to != null ? yearDay(w.to) : "present"))) + "</span>" +
          '<span class="wartoll">~' + w.toll + ",000 lives</span>" +
          (w.raging ? '<span class="ragingtag">RAGING</span>' : "") +
          '<span class="waroutcome">' + esc(w.outcome) + "</span></div>";
      });
    }

    if (unfiltered && isMockEngine) {
      h += '<div class="eraband"><span class="eraname">Antiquity</span><span class="erarange">before the Chronicle</span>' +
        '<div class="eraepi">What survives is myth, tax records, and grudges.</div><ul class="minilist">';
      ANTIQUITY.forEach((a) => {
        h += '<li style="cursor:default"><span class="ev-tick">——</span> <b class="annlegend">' + a.name + ".</b> " + a.line + "</li>";
      });
      h += "</ul></div>";
    }
    eras.forEach((era) => {
      const rows = era.events.filter(match);
      if (!rows.length && (!unfiltered || (!isMockEngine && !era.current))) return;
      h += '<div class="eraband"><span class="eraname">' + esc(era.name) + "</span>" +
        '<span class="erarange">' + yearDay(era.from) + " — " + (era.current ? "present" : yearDay(era.to)) + "</span>" +
        '<div class="eraepi">' + ERA_EPIGRAPHS[era.flavor] + "</div>";
      if (rows.length) {
        h += '<ul class="minilist">';
        rows.slice(-14).forEach((e) => {
          h += '<li data-annev="' + e.id + '"><span class="ev-tick">t' + e.tick + '</span> ' +
            '<span class="cchip" style="--c:' + (S.color[e.country] || "#3987e5") + '">' + e.country + "</span> " +
            esc(e.headline) + "</li>";
        });
        h += "</ul>";
        if (rows.length > 14) h += '<div class="annmore">…and ' + (rows.length - 14) + " more entries the archivists kept.</div>";
      } else {
        h += '<div class="annmore">The archives are thin here. Thin archives are happy archives.</div>';
      }
      h += "</div>";
    });
    return h;
  }

  function annalsActsHTML() {
    const acts = actsOfGod();
    if (!acts.length) {
      return '<div class="hint" style="padding:20px 4px">You have not touched the world yet. It shows.<br>Press <kbd>g</kbd> to change that.</div>';
    }
    let h = '<div class="ov-sub" style="margin-top:12px">Every time you touched the scales, newest first.</div><ul class="minilist">';
    acts.forEach((e) => {
      const ripples = e.id < 0 ? "no paper trail" : "";
      h += '<li data-annev="' + e.id + '"><span class="ev-tick">t' + e.tick + '</span> ' +
        '<span class="cchip" style="--c:' + (S.color[e.country] || "#9085e9") + '">' + e.country + "</span> " +
        esc(e.headline) +
        (ripples ? ' <span class="actripple">' + ripples + "</span>" : "") +
        (e.id < 0 ? "" : ' <button class="abtn actrace" data-anntrace="' + e.id + '">Trace the ripples</button>');
      h += "</li>";
    });
    return h + "</ul>";
  }

  function saveCheckpoint(tick) {
    const t = Math.max(1, Math.round(tick != null ? tick : S.viewTick));
    if (S.checkpoints.some((c) => c.tick === t)) { toast("There is already a checkpoint at t" + t + ".", "warn"); return; }
    const era = fabricateEras().find((e) => t >= e.from && t <= e.to);
    S.checkpoints.push({ id: S.ckSeq++, tick: t, label: ckDate(t), note: era ? "during " + era.name : "" });
    S.checkpoints.sort((a, b) => a.tick - b.tick);
    toast("◈ Checkpoint saved — " + ckDate(t) + " (t" + t + ").", "info");
    drawRibbon();
    if (S.overlay === "checkpoints") openOverlay("checkpoints");
  }

  function checkpointsHTML() {
    let h = '<div class="ov-title">◈ Checkpoints</div>' +
      '<div class="ov-sub">Bookmark any moment. Visit it — or begin a new world from it: ' +
      "everything after is forgotten and history rolls fresh dice.</div>";
    h += '<div class="insp-actions" style="margin:2px 0 10px"><button class="abtn primary" data-cksave>◈ Save this moment · t' + S.viewTick + "</button></div>";
    S.checkpoints.forEach((ck) => {
      h += '<div class="ckrow">' +
        '<span class="ckglyph">◈</span>' +
        '<div class="ckmain"><div class="cklabel">' + esc(ck.label) + ' <span class="cktick">t' + ck.tick + "</span></div>" +
        (ck.note ? '<div class="cknote">' + esc(ck.note) + "</div>" : "") + "</div>" +
        '<button class="abtn" data-ckvisit="' + ck.id + '">visit</button>' +
        '<button class="abtn vio" data-ckanew="' + ck.id + '">✦ begin anew</button>' +
        (ck.id === 0 ? "" : '<button class="tlmini" data-ckdrop="' + ck.id + '" title="Forget this checkpoint">✕</button>') +
        "</div>";
    });
    h += '<div class="set-note">Scrub the ribbon into the past first and a checkpoint can sit on any tick that ever was.</div>';
    h += '<div class="ov-actions"><button class="cancel" data-act="close">esc · close</button></div>';
    return h;
  }

  function beginAnew(ck) {
    if (S.forks.length) { toast("Resolve your forks first — a new genesis dissolves nothing quietly.", "warn"); return; }
    closeOverlay();
    sendCommand({ cmd: "restart", tick: ck.tick }, "Beginning anew from tick " + ck.tick + "…", "restart");
    S.checkpoints = S.checkpoints.filter((c) => c.tick <= ck.tick);
    S.markers = S.markers.filter((mk) => mk.tl === "A" && mk.tick <= ck.tick);
    // the erased future must not haunt the Annals
    for (const id in S.events) { if (S.events[id].tick > ck.tick) delete S.events[id]; }
    S.annalsData = {};
    S.annalsDataLoading = {};
    if (S.selEvent != null && !S.events[S.selEvent]) clearSelections();
    if (isMockEngine) {
      injectLocalEvent(null, "✦ GENESIS — the world is wound back to " + ckDate(ck.tick) +
        " and set running again. Only you remember what it grew into the first time.");
    }
    toast("✦ A new history begins from ◈ " + ck.label + ". Only you remember what it grew into the first time.", "info");
    drawRibbon();
  }

  // ---------- world settings (M8.2b) ----------
  // Instrument chrome, NOT a god action: sans labels, mono values, no violet/✦. Sends
  // updateSettings (partial — one changed key) and reflects settingsAck. Restart-required
  // keys are surfaced by the server's `restartRequired`, not hardcoded here.
  function settingsHTML() {
    const s = S.settings || {};
    const toggle = (k, label) =>
      '<label class="set-row"><span>' + label + "</span>" +
      '<input type="checkbox" data-set="' + k + '"' + (s[k] ? " checked" : "") + "></label>";
    const num = (k, label, min, max, step) =>
      '<label class="set-row"><span>' + label + "</span>" +
      '<input type="number" class="mono set-num" data-set="' + k + '" value="' +
      (s[k] != null ? s[k] : "") + '" min="' + min + '" max="' + max + '" step="' + (step || 1) + '"></label>';
    const range = (k, label, min, max, step) =>
      '<label class="set-row"><span>' + label + "</span>" +
      '<span class="set-range"><input type="range" data-set="' + k + '" value="' +
      (s[k] != null ? s[k] : min) + '" min="' + min + '" max="' + max + '" step="' + step + '">' +
      '<b class="mono set-val" data-valfor="' + k + '">' + (s[k] != null ? s[k] : min) + "</b></span></label>";
    return (
      '<button class="ovclose" data-act="close">✕</button>' +
      '<div class="ov-title">⚙ World settings</div>' +
      '<div class="ov-sub">Rules take effect from the current tick and are recorded in the timeline, so scrubbing, forks and replays see each change where it happened.</div>' +
      '<div class="insp-sechead">World rules</div>' +
      toggle("allow_secession", "Allow secession") +
      toggle("allow_conquest", "Allow conquest & annexation") +
      toggle("allow_nukes", "Allow nuclear weapons") +
      toggle("observer_only", "Observer only — no god edits") +
      num("max_countries", "Max countries", 8, 20, 1) +
      num("max_wars_concurrent", "Max concurrent wars", 1, 8, 1) +
      '<div class="insp-sechead">Simulation</div>' +
      range("drama_multiplier", "Drama", 0.1, 3, 0.1) +
      range("cascade_decay", "Cascade decay", 0.3, 0.95, 0.05) +
      num("max_depth", "Max cascade depth", 4, 12, 1) +
      toggle("silent_god_edits", "Silent god edits — no headline") +
      '<div class="insp-sechead">World generation</div>' +
      '<div class="set-note">Stored now. They shape a newly generated world; this one keeps its founding nations, even after Begin anew.</div>' +
      num("starting_country_count", "Starting countries", 3, 20, 1) +
      num("rival_pairs", "Rival pairs", 0, 8, 1) +
      '<div class="insp-actions"><button class="abtn vio" data-newworld>✦ Generate a new world</button>' +
      '<span class="set-note" style="margin:0">Genesis runs again under these rules. This history, its forks and its checkpoints are discarded.</span></div>'
    );
  }

  function onSettingsAck(m) {
    S.settings = m.settings || S.settings;
    if (m.restartRequired && m.restartRequired.length) {
      toast("Saved. " + m.restartRequired.join(", ").replace(/_/g, " ") + " will shape the next generated world, not this one.", "info");
    } else {
      toast("Setting applied.", "info");
    }
  }

  // ---------- global trade operations ----------
  function tradeRisk(shipment) {
    return shipment.status === "in_transit" &&
      (!!shipment.warExposed || Number(shipment.condition || 0) < 0.4);
  }

  function tradeRows() {
    const objects = S.worldObjects || {};
    const immutableOrder = (a, b) => Number(a.dispatchEventId) - Number(b.dispatchEventId) || String(a.id).localeCompare(String(b.id));
    const active = (objects.shipments || []).slice().sort(immutableOrder);
    const recent = (objects.recentShipments || []).slice().sort(immutableOrder);
    const rosterOrder = (a, b) =>
      (a.status === "arrived" ? 1 : 0) - (b.status === "arrived" ? 1 : 0) || immutableOrder(a, b);
    return active.concat(recent).sort(rosterOrder);
  }

  function tradeOperationsHTML() {
    const objects = S.worldObjects || {};
    const stats = objects.tradeStats || {};
    const T = S.trade;
    const rows = tradeRows();
    S.tradeRowCache = {};
    rows.forEach((row) => {
      const info = globe ? globe.getAsset(row.id) : null;
      if (info) S.tradeRowCache[row.id] = info;
    });
    const commodities = Array.from(new Set(rows.map((row) => row.commodity))).sort();
    const query = T.query.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      if (T.carrier !== "ALL" && row.carrier !== T.carrier) return false;
      if (T.commodity !== "ALL" && row.commodity !== T.commodity) return false;
      if (T.status === "RISK" && !tradeRisk(row)) return false;
      if (T.status !== "ALL" && T.status !== "RISK" && row.status !== T.status) return false;
      if (!query) return true;
      const haystack = [row.id, row.origin, row.dest, cname(row.origin), cname(row.dest), row.commodity, row.carrier, row.status].join(" ").toLowerCase();
      return haystack.includes(query);
    });
    const pageCount = Math.max(1, Math.ceil(filtered.length / TRADE_PAGE_SIZE));
    T.page = Math.max(0, Math.min(T.page, pageCount - 1));
    const start = T.page * TRADE_PAGE_SIZE;
    const page = filtered.slice(start, start + TRADE_PAGE_SIZE);
    const number = (value) => Math.round(Number(value || 0)).toLocaleString();
    const pct = (value) => Math.round(Number(value || 0) * 100) + "%";
    const outcomes = Number(stats.arrivalsLast4Ticks || 0) + Number(stats.lossesLast8Ticks || 0);
    let h = '<button class="ovclose" data-act="close">✕</button>' +
      '<div class="trade-title"><div><div class="ov-title">⇄ Trade Operations</div>' +
      '<div class="ov-sub">Every authoritative shipment on timeline ' + esc(activeTimelineId()) + " at t" + S.viewTick +
      " · stable dispatch order · arrivals move to bottom for four ticks; losses hold eight ticks in place</div></div>" +
      '<span class="trade-live' + (tradeRosterHover ? " held" : "") + '"><i></i>' +
      (tradeRosterHover ? "HELD" : (S.running && !S.scrubbed ? "LIVE" : (S.scrubbed ? "HISTORICAL" : "PAUSED"))) + "</span></div>" +
      '<div class="trade-kpis">' +
      '<div><span>IN TRANSIT</span><b>' + number(stats.activeShipments) + "</b><small>shipments</small></div>" +
      '<div><span>CARGO MOVING</span><b>' + number(stats.inTransitQuantity) + "</b><small>units</small></div>" +
      '<div><span>DISTANCE LEFT</span><b>' + number(stats.remainingDistanceKm) + "</b><small>km aggregate</small></div>" +
      '<div><span>ACTIVE LANES</span><b>' + number(stats.activeLanes) + "</b><small>directional</small></div>" +
      '<div class="risk"><span>WAR EXPOSED</span><b>' + number(stats.warExposedShipments) + "</b><small>active shipments</small></div>" +
      '<div class="outcome"><span>RECENT OUTCOMES</span><b>' + number(outcomes) + "</b><small>" + number(stats.arrivalsLast4Ticks) + " arrived · 4t / " + number(stats.lossesLast8Ticks) + " lost · 8t</small></div></div>";
    h += '<div class="trade-carriers">';
    [["sea", "🚢"], ["air", "✈️"], ["rail", "🚆"]].forEach((entry) => {
      const carrier = (stats.byCarrier && stats.byCarrier[entry[0]]) || {};
      h += '<div><span>' + entry[1] + " " + entry[0].toUpperCase() + "</span><b>" + number(carrier.shipments) +
        " moving</b><small>" + number(carrier.quantity) + " units · " + pct(carrier.averageProgress) + " avg progress</small></div>";
    });
    h += "</div>";
    h += '<div class="trade-filters"><label>Carrier<select data-trade-filter="carrier">' +
      '<option value="ALL">All carriers</option>' + [["sea", "Sea"], ["air", "Air"], ["rail", "Rail"]].map((entry) => '<option value="' + entry[0] + '"' + (T.carrier === entry[0] ? " selected" : "") + ">" + entry[1] + "</option>").join("") +
      '</select></label><label>Commodity<select data-trade-filter="commodity"><option value="ALL">All commodities</option>' +
      commodities.map((commodity) => '<option value="' + esc(commodity) + '"' + (T.commodity === commodity ? " selected" : "") + ">" + esc(commodity) + "</option>").join("") +
      '</select></label><label>Status<select data-trade-filter="status">' +
      [["ALL", "All statuses"], ["in_transit", "In transit"], ["RISK", "At risk"], ["arrived", "Arrived · 4t"], ["lost", "Lost · 8t"]].map((entry) => '<option value="' + entry[0] + '"' + (T.status === entry[0] ? " selected" : "") + ">" + entry[1] + "</option>").join("") +
      '</select></label><label class="trade-search">Find<input data-trade-search value="' + esc(T.query) + '" placeholder="ID, nation, route, cargo…"></label>' +
      '<button class="abtn" data-trade-clear>Clear</button></div>';
    h += '<div class="trade-roster-head"><span>GLOBAL SHIPMENT ROSTER · IMMUTABLE DISPATCH ORDER</span><b>' +
      (filtered.length ? (start + 1) + "–" + (start + page.length) : "0") + " / " + filtered.length + "</b></div>";
    if (!page.length) {
      h += '<div class="trade-empty">No shipments match these filters at this tick.</div>';
    } else {
      h += '<div class="trade-roster">';
      page.forEach((shipment) => {
        const status = shipment.status || "in_transit";
        const terminal = status === "arrived" || status === "lost";
        const progress = Math.round(Number(shipment.progress || 0) * 100);
        const condition = Math.round(Number(shipment.condition || 0) * 100);
        const atRisk = tradeRisk(shipment);
        const type = { sea: "ship", air: "plane", rail: "train" }[shipment.carrier] || "ship";
        const eta = terminal
          ? "t" + shipment.terminalTick + " · " + shipment.retentionTicksRemaining + "t retained"
          : "t" + shipment.arriveTick + " · " + shipment.etaTicks + "t remaining";
        const risk = terminal ? "CLOSED" : (shipment.warExposed ? "WAR-EXPOSED" : (condition < 40 ? "FLEET RISK" : "NORMAL"));
        const currency = S.byCode[shipment.dest] && S.byCode[shipment.dest].currency ? S.byCode[shipment.dest].currency.symbol : shipment.dest;
        h += '<button class="trade-row ' + status + (atRisk ? " at-risk" : "") + '" data-tradeasset="' + shipment.id + '">' +
          '<span class="trade-status"><i>' + ASSET_ICON[type] + '</i><b class="status ' + status + '">' + esc(status.replace("_", " ").toUpperCase()) + "</b><small>" + esc(shipment.id) + "</small></span>" +
          '<span class="trade-route"><b>' + esc(cname(shipment.origin)) + " → " + esc(cname(shipment.dest)) + "</b><small>" + esc(shipment.origin + " → " + shipment.dest) + " · " + esc(shipment.carrier.toUpperCase()) + (shipment.relief ? " · RELIEF" : "") + "</small></span>" +
          '<span class="trade-cargo"><b>' + esc(shipment.commodity) + "</b><small>" + fmt1(shipment.qty) + " units</small></span>" +
          '<span class="trade-progress"><b>' + progress + '%</b><i><em style="width:' + progress + '%"></em></i><small>' + distanceLeftText(shipment.remainingDistanceKm) + "</small></span>" +
          '<span class="trade-eta"><b>' + esc(eta) + "</b><small>" + esc(risk) + " · fleet " + condition + "%</small></span>" +
          '<span class="trade-settle"><b>' + esc(currency) + " " + number(shipment.cost) + " total</b><small>base " + number(shipment.baseCost) + " · duty " + number(shipment.tariffDuty) + " · proceeds " + number(shipment.proceeds) + "</small></span>" +
          '<span class="trade-open">Inspect ›</span></button>';
      });
      h += "</div>";
    }
    if (pageCount > 1) {
      h += '<div class="trade-pager"><button class="abtn" data-trade-page="' + (T.page - 1) + '"' + (T.page === 0 ? " disabled" : "") + ">← Previous</button><span>Page " + (T.page + 1) + " of " + pageCount + '</span><button class="abtn" data-trade-page="' + (T.page + 1) + '"' + (T.page >= pageCount - 1 ? " disabled" : "") + ">Next →</button></div>";
    }
    h += '<div class="trade-footnote">Financial values stay per shipment in the destination currency; no invalid cross-currency total is shown. Click any row to inspect that exact shipment.</div>' +
      '<div class="ov-actions"><button class="cancel" data-act="close">esc · close</button></div>';
    return h;
  }

  function refreshTradeOverlay() {
    if (S.overlay !== "trade") return;
    const card = $("overlayCard");
    const focused = document.activeElement;
    let focusSelector = null;
    let selectionStart = null;
    let selectionEnd = null;
    if (focused && card.contains(focused)) {
      if (focused.matches("[data-trade-search]")) {
        focusSelector = "[data-trade-search]";
        selectionStart = focused.selectionStart;
        selectionEnd = focused.selectionEnd;
      } else if (focused.matches("[data-trade-filter]")) {
        focusSelector = '[data-trade-filter="' + focused.dataset.tradeFilter + '"]';
      }
    }
    const scrollTop = card.scrollTop;
    const scrollLeft = card.scrollLeft;
    card.innerHTML = tradeOperationsHTML();
    wireOverlay();
    if (focusSelector) {
      const replacement = card.querySelector(focusSelector);
      if (replacement) {
        replacement.focus({ preventScroll: true });
        if (selectionStart != null && replacement.setSelectionRange) {
          replacement.setSelectionRange(selectionStart, selectionEnd);
        }
      }
    }
    card.scrollTop = scrollTop;
    card.scrollLeft = scrollLeft;
  }

  // ---------- overlays ----------
  function openOverlay(which) {
    const card = $("overlayCard");
    if (S.overlay !== which) card.scrollTop = 0;
    S.overlay = which;
    // Asked for before the first paint, so the tab opens on its loading state instead of
    // flashing the page-derived record it is about to replace.
    if (which === "annals" && S.annals.tab === "impact") requestAnnalsImpact(false);
    if (which === "annals" && S.annals.tab === "history") requestAnnals(false);
    card.className = "glass" + (which === "detail" || which === "asset" || which === "annals" || which === "settings" || which === "loading" ? " detailcard" : "") + (which === "trade" ? " tradecard" : "") + (which === "intro" ? " introcard" : "");
    if (which === "loading") card.innerHTML = loadingHTML(S.loadingLabel || "Working…");
    else if (which === "trace" && S.trace) card.innerHTML = traceHTML(S.trace);
    else if (which === "god") card.innerHTML = godHTML();
    else if (which === "help") card.innerHTML = helpHTML();
    else if (which === "intro") card.innerHTML = introHTML();
    else if (which === "detail" && S.detail) card.innerHTML = detailHTML(S.detail);
    else if (which === "asset" && S.assetInfo) card.innerHTML = assetDossierHTML(S.assetInfo);
    else if (which === "annals") card.innerHTML = annalsHTML();
    else if (which === "trade") card.innerHTML = tradeOperationsHTML();
    else if (which === "settings") card.innerHTML = settingsHTML();
    else if (which === "checkpoints") card.innerHTML = checkpointsHTML();
    const wasHidden = $("overlay").hidden;
    $("overlay").hidden = false;
    card.setAttribute("aria-label", OVERLAY_NAMES[which] || "Dialog");
    wireOverlay();
    if (wasHidden) {
      // Remember where focus was so esc returns the user to it; move focus into the dialog.
      S.overlayReturnFocus = document.activeElement;
      const first = card.querySelector("[data-intro-start]") || card;
      first.focus({ preventScroll: true });
    }
  }
  const OVERLAY_NAMES = {
    loading: "Working", trace: "Causal trace", god: "Intervene", help: "Help", intro: "Introduction",
    detail: "Country dossier", asset: "Asset details", annals: "The Annals", trade: "Trade Operations",
    settings: "World settings", checkpoints: "Checkpoints"
  };
  function closeOverlay() {
    if (S.overlay === "detail" && S.dossierEdited && S.detail) {
      const name = S.detail.meta.name;
      // The real engine records its own GOD_EDIT (or, with silent edits, deliberately nothing);
      // a client-made headline would duplicate or contradict it. Only the mock narrates locally.
      if (isMockEngine) injectLocalEvent(S.detail.code, "✦ Overnight, every ledger in " + name + " disagrees with every historian. The new numbers win.");
      else toast("✦ Every ledger in " + name + " now agrees with you.", "info");
      S.dossierEdited = false;
    }
    if (S.overlay === "intro") storeFlag(INTRO_KEY);
    tradeRosterHover = false;
    S.overlay = null;
    $("overlay").hidden = true;
    const back = S.overlayReturnFocus;
    S.overlayReturnFocus = null;
    if (back && back.focus && document.contains(back)) back.focus({ preventScroll: true });
  }
  $("overlay").addEventListener("click", (ev) => { if (ev.target === $("overlay")) closeOverlay(); });

  function wireOverlay() {
    const card = $("overlayCard");
    card.querySelectorAll('[data-act="close"]').forEach((n) => n.addEventListener("click", closeOverlay));
    card.querySelectorAll("[data-open-intro]").forEach((n) => n.addEventListener("click", () => openOverlay("intro")));
    if (S.overlay === "trace" || S.overlay === "detail") {
      card.querySelectorAll("[data-act='ev'], .trace-rows li").forEach((n) => {
        if (!n.dataset.id) return;
        n.addEventListener("click", () => {
          const id = Number(n.dataset.id);
          closeOverlay();
          selectEvent(id);
          jumpToFeedCard(id);
        });
      });
    }
    if (S.overlay === "detail" && S.detail) {
      const d = S.detail;
      wireHistoryCharts(card, d);
      card.querySelectorAll("[data-act='annals']").forEach((b) => b.addEventListener("click", () => {
        const code = b.dataset.code;
        closeOverlay();
        S.annals = { tab: "history", country: code, group: "ALL", impactSort: S.annals.impactSort || "descendants" };
        openOverlay("annals");
      }));
      card.querySelectorAll("input[data-god-field]").forEach((inp) => {
        const field = inp.dataset.godField;
        inp.addEventListener("input", () => {
          $("gv-" + field).textContent = (field === "gdp" || field === "treasury") ? fmtBig(Number(inp.value)) : fmt1(Number(inp.value));
        });
        inp.addEventListener("change", () => {
          const v = Number(inp.value);
          sendCommand(
            { cmd: "godEdit", code: d.code, field: field, value: v, tl: d.tl || activeTimelineId() },
            "Applying world edit…",
            "godEdit:" + d.code + ":" + field
          );
          d.stats[field] = v;
          const tile = $("tv-" + field);
          if (tile) {
            tile.textContent = godFieldText(field, v);
            if (field === "stability" || field === "inflation") {
              tile.parentElement.style.borderBottom = "2px solid " + statusBand(field, v);
            }
          }
          S.dossierEdited = true;
        });
      });
      card.querySelectorAll("[data-relup],[data-reldown]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const up = btn.dataset.relup != null;
          const other = up ? btn.dataset.relup : btn.dataset.reldown;
          sendCommand(
            { cmd: "godRelation", a: d.code, b: other, delta: up ? 20 : -20, tl: d.tl || activeTimelineId() },
            "Rewriting relations…",
            "godRelation:" + d.code + ":" + other
          );
          const rel = d.relations.find((r) => r.code === other);
          if (rel) {
            rel.value = Math.max(-100, Math.min(100, rel.value + (up ? 20 : -20)));
            $("rv-" + other).textContent = Math.round(rel.value);
            const bar = card.querySelector('[data-rel="' + other + '"] .relbar i');
            if (bar) {
              bar.style.width = Math.min(100, Math.abs(rel.value)) + "%";
              bar.className = rel.value < 0 ? "neg" : "pos";
            }
          }
          S.dossierEdited = true;
        });
      });
      card.querySelectorAll("[data-peace]").forEach((btn) => {
        btn.addEventListener("click", () => {
          sendCommand(
            { cmd: "godPeace", code: d.code, foe: btn.dataset.peace, tl: d.tl || activeTimelineId() },
            "Ending the war…",
            "godPeace:" + d.code + ":" + btn.dataset.peace
          );
          btn.disabled = true;
          btn.textContent = "🕊 done";
          toast("The guns fall silent. Nobody remembers agreeing to it.", "info");
        });
      });
    }
    if (S.overlay === "asset" && S.assetInfo && !S.assetInfo.authoritative) {
      card.querySelectorAll("[data-god]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const action = btn.dataset.god;
          const before = S.assetInfo;
          const after = globe.applyAsset(before.id, action);
          if (after) {
            S.assetInfo = after;
            injectLocalEvent(after.operator, assetActionHeadline(after, action));
            card.innerHTML = assetDossierHTML(after);
            wireOverlay();
            if (S.selAsset === after.id) renderInspector();
          }
        });
      });
    }
    if (S.overlay === "annals") {
      card.querySelectorAll("[data-anntab]").forEach((b) => b.addEventListener("click", () => {
        S.annals.tab = b.dataset.anntab;
        openOverlay("annals");
      }));
      const sel = card.querySelector("#annCountry");
      if (sel) sel.addEventListener("change", () => { S.annals.country = sel.value; openOverlay("annals"); });
      card.querySelectorAll("[data-group]").forEach((b) => b.addEventListener("click", () => {
        S.annals.group = b.dataset.group;
        openOverlay("annals");
      }));
      card.querySelectorAll("[data-impact-sort]").forEach((b) => b.addEventListener("click", () => {
        S.annals.impactSort = b.dataset.impactSort;
        requestAnnalsImpact(true);
        openOverlay("annals");
      }));
      card.querySelectorAll("[data-impactev]").forEach((n) => n.addEventListener("click", (ev) => {
        if (ev.target.closest("button")) return;
        const id = Number(n.dataset.impactev);
        const timelineId = activeTimelineId();
        const data = S.annalsImpact[timelineId];
        const row = data && data.leaders.find((item) => item.event.id === id);
        closeOverlay();
        selectEvent(id, timelineId, row ? row.event : null);
        jumpToFeedCard(id);
      }));
      card.querySelectorAll("[data-annev]").forEach((n) => n.addEventListener("click", () => {
        const id = Number(n.dataset.annev);
        const timelineId = n.dataset.anntl || activeTimelineId();
        // Prefer the full event the page holds; the archive summary is only a seed for one
        // this page has never received, so the inspector is never downgraded.
        const held = (S.eventsByTimeline[timelineId] || {})[id] || S.events[id] || null;
        closeOverlay();
        selectEvent(id, timelineId, held || (n.dataset.anntl ? annalsDataEvent(timelineId, id) : null));
        jumpToFeedCard(id);
      }));
      card.querySelectorAll("[data-anntrace]").forEach((b) => b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        requestTrace(Number(b.dataset.anntrace), activeTimelineId());
      }));
    }
    if (S.overlay === "trade") {
      card.querySelectorAll("[data-trade-filter]").forEach((select) => {
        select.addEventListener("change", () => {
          S.trade[select.dataset.tradeFilter] = select.value;
          S.trade.page = 0;
          refreshTradeOverlay(true);
        });
      });
      const search = card.querySelector("[data-trade-search]");
      if (search) {
        search.addEventListener("input", () => { S.trade.query = search.value; });
        search.addEventListener("change", () => { S.trade.page = 0; refreshTradeOverlay(true); });
        search.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") { S.trade.page = 0; refreshTradeOverlay(true); }
        });
      }
      const clear = card.querySelector("[data-trade-clear]");
      if (clear) clear.addEventListener("click", () => {
        S.trade = { carrier: "ALL", commodity: "ALL", status: "ALL", query: "", page: 0 };
        refreshTradeOverlay(true);
      });
      card.querySelectorAll("[data-trade-page]").forEach((button) => {
        button.addEventListener("click", () => {
          S.trade.page = Number(button.dataset.tradePage);
          refreshTradeOverlay(true);
        });
      });
      card.querySelectorAll("[data-tradeasset]").forEach((row) => {
        row.addEventListener("click", () => {
          const id = row.dataset.tradeasset;
          const fallback = S.tradeRowCache[id] || null;
          if (selectAsset(id, fallback)) closeOverlay();
        });
      });
    }
    if (S.overlay === "settings") {
      const newWorld = card.querySelector("[data-newworld]");
      if (newWorld) newWorld.addEventListener("click", () => {
        // Two-step, because it throws away every recorded tick.
        if (!newWorld.dataset.armed) {
          newWorld.dataset.armed = "1";
          newWorld.textContent = "✦ Yes — discard this history";
          newWorld.classList.add("danger");
          setTimeout(() => {
            if (!newWorld.dataset.armed) return;
            delete newWorld.dataset.armed;
            newWorld.textContent = "✦ Generate a new world";
            newWorld.classList.remove("danger");
          }, 6000);
          return;
        }
        delete newWorld.dataset.armed;
        S.newWorldPending = true;
        const requestId = sendCommand({ cmd: "newWorld" }, "Generating a new world…", "newWorld");
        closeOverlay();
        if (requestId) {
          setTimeout(() => {
            // Only this request is retired, and only if it is still outstanding: a bridge
            // without a `newWorld` handler simply never answers.
            if (!S.pendingRequests[requestId]) return;
            completeRequest({ requestId: requestId });
            S.newWorldPending = false;
            toast("This engine build cannot generate a new world; nothing was changed.", "warn");
          }, 10000);
        }
      });
      card.querySelectorAll("[data-set]").forEach((el) => {
        const key = el.dataset.set;
        const readVal = () =>
          el.type === "checkbox" ? el.checked
          : (el.type === "number" || el.type === "range") ? Number(el.value)
          : el.value;
        if (el.type === "range") {
          el.addEventListener("input", () => {
            const b = card.querySelector('[data-valfor="' + key + '"]');
            if (b) b.textContent = el.value;
          });
        }
        // Send on `change` (fires on release for sliders/number spinners), so dragging a
        // slider doesn't spam the bridge — one updateSettings per settled value.
        el.addEventListener("change", () => {
          const requestId = sendCommand(
            { cmd: "updateSettings", settings: { [key]: readVal() } },
            "Saving world settings…",
            "settings:" + key
          );
          if (requestId) {
            el.disabled = true;
            el.classList.add("is-pending");
            el.dataset.pendingRequest = requestId;
          }
        });
      });
    }
    if (S.overlay === "checkpoints") {
      const ckById = (id) => S.checkpoints.find((c) => c.id === Number(id));
      const save = card.querySelector("[data-cksave]");
      if (save) save.addEventListener("click", () => saveCheckpoint(S.viewTick));
      card.querySelectorAll("[data-ckvisit]").forEach((b) => b.addEventListener("click", () => {
        if (S.forks.length) { toast("Scrubbing is locked while forks exist — resolve them first.", "warn"); return; }
        const ck = ckById(b.dataset.ckvisit);
        if (ck) { closeOverlay(); requestWorldAt(ck.tick); }
      }));
      card.querySelectorAll("[data-ckanew]").forEach((b) => b.addEventListener("click", () => {
        const ck = ckById(b.dataset.ckanew);
        if (ck) beginAnew(ck);
      }));
      card.querySelectorAll("[data-ckdrop]").forEach((b) => b.addEventListener("click", () => {
        S.checkpoints = S.checkpoints.filter((c) => c.id !== Number(b.dataset.ckdrop));
        drawRibbon();
        openOverlay("checkpoints");
      }));
    }
    if (S.overlay === "god") {
      card.querySelectorAll(".ivcard").forEach((n) => n.addEventListener("click", () => {
        S.god.kind = n.dataset.kind;
        card.innerHTML = godHTML();
        wireOverlay();
      }));
      card.querySelectorAll(".cpick").forEach((n) => n.addEventListener("click", () => {
        const grid = n.dataset.grid;
        if (grid === "1") S.god.country = n.dataset.code;
        else S.god.target2 = n.dataset.code;
        card.querySelectorAll('.cpick[data-grid="' + grid + '"]').forEach((x) => x.classList.toggle("on", x === n));
        godRefresh(card);
      }));
      const go = card.querySelector(".go");
      if (go) go.addEventListener("click", () => {
        S.loadingLabel = "Creating and replaying the intervention fork…";
        S.loadingReturn = "god";
        const requestId = sendCommand({
          cmd: "intervene", kind: S.god.kind, country: S.god.country,
          // The bridge requires an explicit fork tick. "Now" is the tick on screen: prime's live
          // tick, the scrubbed tick, or the focused fork's tick (aligned with prime's history).
          atTick: S.god.atTick != null ? S.god.atTick : S.viewTick,
          target2: S.god.target2 || undefined
        }, "Creating intervention fork…", "intervene");
        if (requestId) openOverlay("loading");
      });
      godRefresh(card);
    }
  }
  function needsTwo() {
    const iv = (S.hello ? S.hello.interventions : []).find((x) => x.kind === S.god.kind);
    return iv && iv.targets === 2;
  }
  function godRefresh(card) {
    const go = card.querySelector(".go");
    if (!go) return;
    const ok = S.god.kind && S.god.country && (!needsTwo() || (S.god.target2 && S.god.target2 !== S.god.country));
    go.disabled = !ok;
  }

  function openGod(atTick) {
    if (S.forks.length >= 3) {
      toast("Three concurrent forks is plenty, even for a god. Adopt or discard one first.", "warn");
      return;
    }
    S.god = { kind: null, country: null, target2: null, atTick: atTick != null ? atTick : null };
    openOverlay("god");
  }

  function countryGridHTML(grid, selected, excluded) {
    let h = '<div class="cgrid">';
    S.order.forEach((code) => {
      const c = S.byCode[code];
      const stats = S.stats[focusTL()] || S.stats.A;
      if (!stats || !stats[code]) return;
      const on = selected === code ? " on" : "";
      const dis = excluded === code;
      h += '<button class="cpick' + on + '" data-grid="' + grid + '" data-code="' + code + '"' + (dis ? " disabled" : "") + ">" +
        '<span class="emblem" style="--c:' + S.color[code] + '">' + code + "</span>" +
        '<span class="cname">' + esc(c.name) + "</span></button>";
    });
    return h + "</div>";
  }

  function godHTML() {
    const when = S.god.atTick != null
      ? "t" + S.god.atTick
      : (S.focusId === "A" ? "the present moment (t" + S.viewTick + ")" : "t" + S.viewTick + ", the tick on screen");
    let h = '<div class="ov-title"><span class="vio">✦</span> Do as you will</div>' +
      '<div class="ov-sub">Injects a root event at ' + when +
      " and forks a new timeline from PRIME — up to three drafts of history can run side by side.</div>";
    h += '<div class="ivgrid">';
    (S.hello ? S.hello.interventions : []).forEach((iv) => {
      h += '<button class="ivcard' + (S.god.kind === iv.kind ? " on" : "") + '" data-kind="' + iv.kind + '">' +
        '<span class="ivicon">' + iv.icon + "</span>" +
        '<span class="ivname">' + esc(iv.label) + (iv.targets === 2 ? ' <span class="two">2 nations</span>' : "") + "</span>" +
        '<span class="ivdesc">' + esc(iv.desc) + "</span></button>";
    });
    h += "</div>";
    h += '<div class="insp-sechead" style="margin-top:16px">TARGET</div>' + countryGridHTML("1", S.god.country, null);
    if (needsTwo()) {
      h += '<div class="insp-sechead" style="margin-top:12px">COUNTERPARTY</div>' + countryGridHTML("2", S.god.target2, S.god.country);
    }
    h += '<div class="ov-actions"><button class="cancel" data-act="close">cancel</button>' +
      '<button class="go" disabled>✦ Fork &amp; inject</button></div>';
    return h;
  }

  function helpHTML() {
    return '<div class="ov-title">How to read the Chronicle</div>' +
      '<div class="ov-sub">A zero-player world. You watch, interrogate, and — if you must — intervene. ' +
      '<button class="linkbtn" data-open-intro>Show the introduction</button></div>' +
      '<ul class="helplist">' +
      "<li><kbd>space</kbd>pause / resume the world</li>" +
      "<li><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd><kbd>4</kbd>playback speed</li>" +
      "<li><kbd>←</kbd><kbd>→</kbd>step / scrub one tick</li>" +
      "<li><kbd>⇧←</kbd><kbd>⇧→</kbd>scrub ±25 ticks</li>" +
      "<li><kbd>t</kbd>trace selected event's lineage</li>" +
      "<li><kbd>d</kbd>full dossier of selected country</li>" +
      "<li><kbd>g</kbd>divine intervention (forks a timeline)</li>" +
      "<li><kbd>h</kbd>the Annals — eras, wars of record, records</li>" +
      "<li><kbd>v</kbd>Trade Operations — every active shipment and recent outcome</li>" +
      "<li><kbd>c</kbd>checkpoints — save, visit, begin anew</li>" +
      "<li><kbd>esc</kbd>close overlays / deselect</li>" +
      "<li><kbd>dbl-click</kbd>a nation opens its dossier; a headline, its trace</li>" +
      "</ul>" +
      '<div class="ov-sub" style="margin-top:14px">Glyphs: <b style="color:var(--ink)">●</b> root cause · ' +
      '<b style="color:var(--ink)">▸</b> consequence · <b style="color:var(--crit)">⚡</b> crisis · ' +
      '<b style="color:var(--violet)">✦</b> your intervention · <b style="color:var(--blue)">◈</b> checkpoint.<br>' +
      "The globe: drag to rotate, scroll to zoom. Real shipments and aggregate satellite inventories are " +
      "clickable and read-only; their quantities, routes, progress, and condition come from the engine. Country dossiers have a " +
      '"rewrite reality" panel: sliders for the economy, meddling buttons for relations, a dove for every war. ' +
      "Up to three forked timelines can run at once — switch, discard, or adopt them from the tab bar.</div>" +
      '<div class="ov-actions help-actions">' +
      (SOURCE_URL ? '<a class="linkbtn" href="' + esc(SOURCE_URL) + '" target="_blank" rel="noopener">Source code · AGPL-3.0-or-later</a>' : "") +
      '<button class="cancel" data-act="close">esc · close</button></div>';
  }

  // ---------- first run ----------
  // Browser storage can be missing or throw (private windows, blocked site data); the intro
  // then simply shows once per page load.
  function storedFlag(key) {
    try { return window.localStorage.getItem(key) === "1"; } catch (e) { return false; }
  }
  function storeFlag(key) {
    try { window.localStorage.setItem(key, "1"); } catch (e) { /* not persisted */ }
  }
  const INTRO_KEY = "meddler.introSeen";
  const HINT_KEY = "meddler.whyHintSeen";
  S.whyHintSeen = storedFlag(HINT_KEY);

  function introHTML() {
    return '<div class="intro">' +
      '<div class="intro-brand">MEDDLER</div>' +
      '<p class="intro-lede">A small world that runs itself. Nations trade, feud and come apart on their own, ' +
      "and every headline carries the record of what caused it. You are here to watch. You will not only watch.</p>" +
      '<ol class="intro-verbs">' +
      '<li><b>Watch</b><span><kbd>space</kbd> pause · <kbd>1</kbd>–<kbd>4</kbd> speed</span><em>The Chronicle reports as it happens.</em></li>' +
      '<li><b>Ask why</b><span>click a headline · <kbd>t</kbd> trace</span><em>Causes, effects and the whole lineage, from the record.</em></li>' +
      '<li><b>Rewind</b><span><kbd>←</kbd> or drag the ribbon</span><em>Any tick that ever was, reconstructed exactly.</em></li>' +
      '<li class="vio"><b>✦ Meddle</b><span><kbd>g</kbd> intervene</span><em>Fork history with one act and watch the drafts diverge.</em></li>' +
      "</ol>" +
      '<div class="ov-actions intro-actions"><span class="intro-note">Help <kbd>?</kbd> can show this again</span>' +
      '<button class="abtn primary" data-act="close" data-intro-start>Start watching</button></div></div>';
  }

  function openIntro() {
    openOverlay("intro");
  }

  // ---------- toasts ----------
  function toast(text, tone) {
    const t = document.createElement("div");
    t.className = "toast glass" + (tone === "warn" ? " warn" : "");
    t.textContent = text;
    $("toasts").appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 3600);
    setTimeout(() => t.remove(), 4100);
  }

  // ---------- transport controls ----------
  function requestRunningToggle() {
    const command = S.running ? "pause" : "resume";
    sendCommand({ cmd: command }, command === "pause" ? "Pausing world…" : "Resuming world…", "running");
  }
  // A speed chosen while another setSpeed is in flight is held, not dropped: the latest
  // choice is sent once the pending one lands, so at most one is in flight and one waits.
  function requestSpeed(tps) {
    if (pendingByKey("speed")) {
      S.speedQueued = tps;
      return;
    }
    sendCommand({ cmd: "setSpeed", tps: tps }, "Setting speed to " + tps + "×…", "speed");
  }
  $("btnPause").addEventListener("click", requestRunningToggle);
  document.querySelectorAll("#speeds button").forEach((b) => {
    b.addEventListener("click", () => requestSpeed(Number(b.dataset.tps)));
  });
  $("btnGod").addEventListener("click", () => openGod(S.scrubbed ? S.viewTick : null));
  $("btnNewFork").addEventListener("click", () => openGod(null));
  $("btnHelp").addEventListener("click", () => openOverlay("help"));
  $("btnAnnals").addEventListener("click", () => openOverlay("annals"));
  $("btnTrade").addEventListener("click", () => openOverlay("trade"));
  $("btnCheckpoints").addEventListener("click", () => openOverlay("checkpoints"));
  $("btnSettings").addEventListener("click", () => openOverlay("settings"));
  $("btnCkHere").addEventListener("click", () => saveCheckpoint(S.viewTick));

  // ---------- keyboard ----------
  document.addEventListener("keydown", (ev) => {
    const typing = ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA" || ev.target.tagName === "SELECT";
    if (ev.key === "Tab" && S.overlay) {
      // Keep keyboard focus inside the open dialog.
      const card = $("overlayCard");
      const items = Array.from(card.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])"));
      if (!items.length) { ev.preventDefault(); card.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      if (!card.contains(document.activeElement)) { ev.preventDefault(); first.focus(); }
      else if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
      return;
    }
    if (typing && ev.key !== "Escape") return;
    if (ev.key === "Escape") {
      if (S.overlay) { closeOverlay(); return; }
      clearSelections();
      renderInspector();
      return;
    }
    if (S.overlay) return;
    switch (ev.key) {
      case " ":
        ev.preventDefault();
        requestRunningToggle();
        break;
      case "ArrowLeft": {
        ev.preventDefault();
        if (S.forks.length) break;
        const step = ev.shiftKey ? 25 : 1;
        requestWorldAt((S.scrubbed ? S.viewTick : S.liveTick) - step);
        break;
      }
      case "ArrowRight": {
        ev.preventDefault();
        const step = ev.shiftKey ? 25 : 1;
        if (S.scrubbed) {
          if (S.viewTick + step >= S.liveTick) {
            sendCommand({ cmd: "resumeLive" }, "Returning to live world…", "resumeLive");
          } else requestWorldAt(S.viewTick + step);
        } else if (!S.running) {
          sendCommand({ cmd: "step" }, "Advancing one tick…", "step");
        }
        break;
      }
      case "g": case "G": openGod(S.scrubbed ? S.viewTick : null); break;
      case "t": case "T": if (S.selEvent != null) requestTrace(S.selEvent); break;
      case "d": case "D":
        if (S.selCountry) openDossier(S.selCountry);
        else if (S.selAsset && S.assetInfo && (S.assetInfo.aggregate || !S.assetInfo.authoritative)) openAssetDossier(S.selAsset);
        else if (S.selEvent != null && S.events[S.selEvent]) openDossier(S.events[S.selEvent].country);
        break;
      case "h": case "H": openOverlay("annals"); break;
      case "v": case "V": openOverlay("trade"); break;
      case "c": case "C": openOverlay("checkpoints"); break;
      case "1": requestSpeed(0.5); break;
      case "2": requestSpeed(1); break;
      case "3": requestSpeed(2); break;
      case "4": requestSpeed(4); break;
      case "?": openOverlay("help"); break;
    }
  });

  // ---------- boot ----------
  engine.connect(onMessage);
  drawRibbon();
})();
