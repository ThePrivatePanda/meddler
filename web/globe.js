/* Meddler — protocol-v2 authoritative orbital view.
 *
 * Real-engine mode renders only bridge-supplied positions, ownership, blocs, lanes,
 * shipments, infrastructure counts/conditions, wars, and recent STRIKE events. Territory
 * shapes (see "territory massing" below) and satellite orbit paths are deterministic visual
 * projections around those facts. Explicit ?engine=mock mode retains a small cosmetic fallback.
 */
"use strict";
(function () {
  const D2R = Math.PI / 180;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hashCode(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i); h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }
  function toVec(latR, lonR) {
    const c = Math.cos(latR);
    return [c * Math.cos(lonR), c * Math.sin(lonR), Math.sin(latR)];
  }
  function vecFromPosition(position) {
    return toVec(position.lat * D2R, position.lon * D2R);
  }
  function slerp(p, q, t) {
    const dot = clamp(p[0] * q[0] + p[1] * q[1] + p[2] * q[2], -1, 1);
    const omega = Math.acos(dot);
    if (omega < 1e-6) return p.slice();
    const sinOmega = Math.sin(omega);
    const a = Math.sin((1 - t) * omega) / sinOmega;
    const b = Math.sin(t * omega) / sinOmega;
    return [a * p[0] + b * q[0], a * p[1] + b * q[1], a * p[2] + b * q[2]];
  }
  function statusFor(condition) {
    if (condition < 0.25) return "CRITICAL";
    if (condition < 0.55) return "DEGRADED";
    return "OPERATIONAL";
  }

  function create(canvas, opts) {
    const ctx = canvas.getContext("2d");
    const onSelect = opts.onSelect || function () {};
    const onDetail = opts.onDetail || function () {};
    const onSelectAsset = opts.onSelectAsset || function () {};
    const onSelectRoute = opts.onSelectRoute || function () {};
    const onZoom = opts.onZoom || function () {};
    const state = {
      mockMode: !!opts.mockMode,
      authoritative: false,
      running: false,
      tps: 1,
      worldTick: 0,
      displayWorldTick: 0,
      orbitTargetTick: 0,
      orbitRate: 0,
      hasWorldObjects: false,
      view: { lat: 22, lon: -20 },
      focusTarget: null,
      zoom: 1,
      zoomTarget: 1,
      viewChosen: false,
      lastInteract: 0,
      countries: {},
      order: [],
      wars: [],
      strikes: [],
      routesSea: [],
      routesAir: [],
      routesRail: [],
      routeIndex: Object.create(null),
      assets: [],
      assetIndex: Object.create(null),
      recentAssetIndex: Object.create(null),
      renderAssets: [],
      pings: [],
      stars: [],
      selected: null,
      selectedAsset: null,
      selectedRoute: null,
      hover: null,
      identity: Object.create(null),
      lossPinged: Object.create(null),
      landDirty: true,
      cssWidth: 0,
      cssHeight: 0,
      w: 0, h: 0, dpr: 1
    };

    function updateCanvasSize(width, height) {
      state.cssWidth = Math.max(1, Math.round(width));
      state.cssHeight = Math.max(1, Math.round(height));
    }
    if (window.ResizeObserver) {
      new ResizeObserver((entries) => {
        const rect = entries[0] && entries[0].contentRect;
        if (rect) updateCanvasSize(rect.width, rect.height);
      }).observe(canvas);
    }
    const initialCanvasRect = canvas.getBoundingClientRect();
    updateCanvasSize(initialCanvasRect.width, initialCanvasRect.height);

    // Identity (name + categorical color from app.js) outlives a country's presence in any
    // one worldObjects payload, so a nation that vanishes on a scrub or exists only in one
    // fork keeps its color when it reappears. Codes never announced via addCountry render in
    // neutral ink until they are (never in selection blue).
    const UNANNOUNCED = "#898781";
    function countryMeta(code) {
      return state.countries[code] || state.identity[code] || { code: code, name: code, color: UNANNOUNCED };
    }

    function addCountry(code, info) {
      info = info || {};
      const known = state.identity[code];
      state.identity[code] = {
        code: code,
        name: info.name || (known && known.name) || code,
        color: info.color || (known && known.color) || UNANNOUNCED
      };
      const existing = state.countries[code];
      if (existing) {
        existing.name = state.identity[code].name;
        existing.color = existing.baseColor = state.identity[code].color;
        labels.dirty = true;
        return;
      }
      const seed = hashCode(code);
      const country = {
        code: code,
        name: state.identity[code].name,
        color: state.identity[code].color,
        baseColor: state.identity[code].color,
        center: null,
        territories: [],
        blocId: null,
        status: "ACTIVE"
      };
      state.countries[code] = country;
      state.order.push(code);
      state.landDirty = true;
      if (!state.mockMode) return;
      const center = [((seed % 120) - 60) * 0.9, ((seed >>> 3) % 360) - 180];
      country.center = center;
      country.territories = [{ center: center, region: "approximate" }];
      regenRoutes();
    }

    function laneKey(raw) {
      return raw.carrier + ":" + raw.origin + ":" + raw.dest;
    }

    function routeObject(raw, extra) {
      const a = vecFromPosition(raw.route.from);
      const b = vecFromPosition(raw.route.to);
      const cross = [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0]
      ];
      const length = Math.hypot(cross[0], cross[1], cross[2]);
      const normal = length > 1e-6
        ? [cross[0] / length, cross[1] / length, cross[2] / length]
        : [0, 0, 0];
      return Object.assign({
        a: a,
        b: b,
        normal: normal,
        origin: raw.origin,
        dest: raw.dest,
        carrier: raw.carrier,
        volume: raw.volume || raw.qty || 0,
        condition: clamp(Number(raw.condition || 0), 0, 1),
        distanceKm: raw.distanceKm == null ? null : Math.max(0, Number(raw.distanceKm))
      }, extra || {});
    }

    function shipmentAsset(shipment, previous, route) {
      const types = { sea: "ship", air: "plane", rail: "train" };
      const authoritativeProgress = clamp(Number(shipment.progress || 0), 0, 1);
      const durationTicks = Number(shipment.arriveTick) - Number(shipment.departTick);
      const scheduledNextProgress = durationTicks > 0
        ? clamp((state.worldTick + 1 - Number(shipment.departTick)) / durationTicks, 0, 1)
        : authoritativeProgress;
      const targetProgress = state.running
        ? Math.max(authoritativeProgress, scheduledNextProgress)
        : authoritativeProgress;
      const canInterpolate = state.running && previous && previous.shipment && previous.renderSlot != null &&
        previous.targetProgress <= targetProgress;
      const displayProgress = canInterpolate
        ? clamp(Number(previous.displayProgress), 0, targetProgress)
        : targetProgress;
      const transitionSeconds = clamp(1.05 / Math.max(0.5, state.tps), 0.14, 2.1);
      return {
        id: shipment.id,
        type: types[shipment.carrier] || "ship",
        authoritative: true,
        shipment: true,
        name: "Shipment " + shipment.id,
        operator: shipment.origin,
        origin: shipment.origin,
        dest: shipment.dest,
        commodity: shipment.commodity,
        qty: shipment.qty,
        carrier: shipment.carrier,
        buyerPool: shipment.buyerPool,
        baseCost: shipment.baseCost,
        tariffDuty: shipment.tariffDuty,
        cost: shipment.cost,
        proceeds: shipment.proceeds,
        dispatchEventId: shipment.dispatchEventId,
        departTick: shipment.departTick,
        arriveTick: shipment.arriveTick,
        progress: authoritativeProgress,
        distanceKm: shipment.distanceKm == null ? null : Math.max(0, Number(shipment.distanceKm)),
        remainingDistanceKm: shipment.remainingDistanceKm == null ? null : Math.max(0, Number(shipment.remainingDistanceKm)),
        targetProgress: targetProgress,
        displayProgress: displayProgress,
        progressRate: Math.max(0, targetProgress - displayProgress) / transitionSeconds,
        renderSlot: previous && previous.renderSlot != null ? previous.renderSlot : null,
        lifecycle: shipment.status || "in_transit",
        terminal: shipment.status === "arrived" || shipment.status === "lost",
        terminalTick: shipment.terminalTick,
        terminalEventId: shipment.terminalEventId,
        ageTicks: shipment.ageTicks,
        retentionTicksRemaining: shipment.retentionTicksRemaining,
        etaTicks: shipment.etaTicks,
        warExposed: !!shipment.warExposed,
        relief: !!shipment.relief,
        condition: clamp(Number(shipment.condition || 0), 0, 1),
        originCondition: clamp(Number(shipment.originCondition || 0), 0, 1),
        destCondition: clamp(Number(shipment.destCondition || 0), 0, 1),
        status: shipment.status === "arrived" ? "ARRIVED" : (shipment.status === "lost" ? "LOST" : statusFor(Number(shipment.condition || 0))),
        route: route,
        screen: null,
        lastLL: previous && previous.lastLL ? previous.lastLL.slice() : null
      };
    }

    function satelliteAsset(code, data) {
      return {
        id: "satellites:" + code,
        type: "sat",
        authoritative: true,
        aggregate: true,
        name: code + " satellite network",
        operator: code,
        count: data.count,
        condition: clamp(Number(data.condition || 0), 0, 1),
        effectiveCapacity: data.effectiveCapacity,
        lastMaintainedTick: data.lastMaintainedTick,
        status: statusFor(Number(data.condition || 0)),
        screen: null,
        lastLL: null
      };
    }

    function rebuildAssetCollections(allowMidRouteFill) {
      const hadAssets = Object.keys(state.assetIndex).length > 0;
      const index = Object.create(null);
      const groups = Object.create(null);
      const satellites = [];
      state.assets.forEach((asset) => {
        index[asset.id] = asset;
        asset.wasRendered = false;
        if (asset.shipment) {
          const key = laneKey(asset);
          if (!groups[key]) groups[key] = [];
          groups[key].push(asset);
        } else satellites.push(asset);
      });
      state.assetIndex = index;

      const rendered = satellites.slice();
      Object.keys(groups).sort().forEach((key) => {
        const group = groups[key];
        const slots = new Array(8).fill(null);
        group.forEach((asset) => {
          const slot = asset.renderSlot == null ? null : Number(asset.renderSlot);
          if (slot != null && Number.isInteger(slot) && slot >= 0 && slot < slots.length && !slots[slot]) slots[slot] = asset;
          else asset.renderSlot = null;
        });
        const mayFill = allowMidRouteFill || !hadAssets;
        const candidates = group
          .filter((asset) => asset.renderSlot == null && (mayFill || asset.progress <= 0.000001))
          .sort((a, b) => a.departTick - b.departTick || hashCode(a.id) - hashCode(b.id) || a.id.localeCompare(b.id));
        let candidateIndex = 0;
        for (let slot = 0; slot < slots.length && candidateIndex < candidates.length; slot++) {
          if (slots[slot]) continue;
          slots[slot] = candidates[candidateIndex++];
          slots[slot].renderSlot = slot;
        }
        slots.forEach((asset, slot) => {
          if (!asset) return;
          asset.visualOffset = (slot - 3.5) * 0.003;
          asset.wasRendered = true;
          rendered.push(asset);
        });
      });
      const selected = state.selectedAsset && index[state.selectedAsset];
      if (selected && !selected.wasRendered) {
        selected.visualOffset = ((hashCode(selected.id) % 7) - 3) * 0.003;
        selected.wasRendered = true;
        rendered.push(selected);
      }
      state.renderAssets = rendered;
    }

    function setWorldObjects(payload) {
      if (!payload || !payload.countries) return;
      state.authoritative = true;
      const nextTick = Number(payload.tick || 0);
      const discontinuity = !state.hasWorldObjects || nextTick < state.worldTick || Math.abs(nextTick - state.worldTick) > 1;
      if (!state.running) {
        state.displayWorldTick = nextTick;
        state.orbitTargetTick = nextTick;
        state.orbitRate = 0;
      } else {
        if (discontinuity) state.displayWorldTick = nextTick;
        state.orbitTargetTick = nextTick + 1;
        const transitionSeconds = clamp(1.05 / Math.max(0.5, state.tps), 0.14, 2.1);
        state.orbitRate = Math.max(0, state.orbitTargetTick - state.displayWorldTick) / transitionSeconds;
      }
      state.worldTick = nextTick;
      state.hasWorldObjects = true;
      const nextCodes = Object.keys(payload.countries).sort();
      const nextCountries = {};
      nextCodes.forEach((code) => {
        const source = payload.countries[code];
        const old = state.identity[code] || countryMeta(code);
        const center = [source.position.lat, source.position.lon];
        // Bloc membership is drawn as a treaty rim around members' coasts, so each nation
        // keeps its own categorical identity color (the one on its WORLD-list chip).
        nextCountries[code] = {
          code: code,
          name: old.name || code,
          color: old.baseColor || old.color || UNANNOUNCED,
          baseColor: old.baseColor || old.color || UNANNOUNCED,
          center: center,
          region: source.region,
          blocId: source.blocId,
          status: source.status,
          territories: (source.territories || []).map((territory) => ({
            center: [territory.position.lat, territory.position.lon],
            region: territory.region
          }))
        };
        if (!nextCountries[code].territories.length) {
          nextCountries[code].territories.push({ center: center, region: source.region });
        }
      });
      state.countries = nextCountries;
      state.order = nextCodes;
      state.landDirty = true;

      state.routesSea = [];
      state.routesAir = [];
      state.routesRail = [];
      state.routeIndex = Object.create(null);
      const routesByLane = Object.create(null);
      (payload.lanes || []).forEach((lane) => {
        const route = routeObject(lane, {
          id: lane.id,
          shipmentCount: lane.shipmentCount,
          shipmentIds: lane.shipmentIds || [],
          recentShipmentCount: lane.recentShipmentCount || 0,
          recentShipmentIds: lane.recentShipmentIds || []
        });
        routesByLane[laneKey(lane)] = route;
        state.routeIndex[route.id] = route;
        if (Number(lane.shipmentCount || 0) <= 0) return;
        if (lane.carrier === "sea") state.routesSea.push(route);
        else if (lane.carrier === "air") state.routesAir.push(route);
        else if (lane.carrier === "rail") state.routesRail.push(route);
      });

      const previousAssets = state.assetIndex;
      const movers = (payload.shipments || []).map((shipment) => {
        const route = routesByLane[laneKey(shipment)] || routeObject(shipment, {});
        return shipmentAsset(shipment, previousAssets[shipment.id], route);
      });
      state.recentAssetIndex = Object.create(null);
      (payload.recentShipments || []).forEach((shipment) => {
        const route = routesByLane[laneKey(shipment)] || routeObject(shipment, {});
        const asset = shipmentAsset(shipment, null, route);
        state.recentAssetIndex[asset.id] = asset;
        // Loss pulse from the authoritative terminal row itself, so it does not depend on
        // SHIPMENT_LOST reaching the feed. One pulse per shipment id, live play only.
        if (asset.lifecycle === "lost" && !discontinuity && Number(shipment.ageTicks || 0) <= 1 && !state.lossPinged[asset.id]) {
          const last = previousAssets[asset.id];
          const vector = routeVector(route, asset.progress, 0);
          state.pings.push({
            ll: last && last.lastLL ? last.lastLL : [Math.asin(clamp(vector[2], -1, 1)), Math.atan2(vector[1], vector[0])],
            color: "#ec835a", t0: performance.now() / 1000, big: true
          });
          state.lossPinged[asset.id] = true;
        }
      });
      const keptPings = Object.create(null);
      Object.keys(state.lossPinged).forEach((id) => {
        if (state.recentAssetIndex[id] || previousAssets[id]) keptPings[id] = true;
      });
      state.lossPinged = keptPings;
      const satellites = [];
      nextCodes.forEach((code) => {
        const satelliteData = payload.countries[code].assets && payload.countries[code].assets.satellites;
        if (satelliteData && satelliteData.count > 0) satellites.push(satelliteAsset(code, satelliteData));
      });
      state.assets = movers.concat(satellites);
      rebuildAssetCollections(discontinuity);
      state.strikes = (payload.strikes || []).map((strike) => ({
        id: strike.id,
        eventId: strike.eventId,
        tick: strike.tick,
        age: strike.age,
        visibleTicks: strike.visibleTicks,
        power: strike.power,
        origin: strike.origin,
        dest: strike.dest,
        a: vecFromPosition(strike.route.from),
        b: vecFromPosition(strike.route.to)
      }));
      if (state.selectedAsset && !assetById(state.selectedAsset)) state.selectedAsset = null;
      if (state.selectedRoute && !state.routeIndex[state.selectedRoute]) state.selectedRoute = null;
      if (state.selected && !state.countries[state.selected]) state.selected = null;
      if (state.hover && state.hover.kind === "country" && !state.countries[state.hover.id]) state.hover = null;
      if (state.hover && state.hover.kind === "route" && !state.routeIndex[state.hover.id]) state.hover = null;
      if (state.hover && state.hover.kind === "asset" && !assetById(state.hover.id)) state.hover = null;
    }

    // Cosmetic objects exist only in the explicitly selected offline mock.
    function regenRoutes() {
      if (state.authoritative || !state.mockMode) return;
      state.routesSea = []; state.routesAir = []; state.routesRail = [];
      state.routeIndex = Object.create(null);
      state.assets = [];
      if (state.order.length < 2) { rebuildAssetCollections(); return; }
      state.order.forEach((code, i) => {
        const from = state.countries[code];
        const to = state.countries[state.order[(i + 1) % state.order.length]];
        if (!from.center || !to.center || from.code === to.code) return;
        const carrier = i % 3 === 0 ? "air" : "sea";
        const route = {
          id: "mock:" + carrier + ":" + from.code + ":" + to.code,
          a: toVec(from.center[0] * D2R, from.center[1] * D2R),
          b: toVec(to.center[0] * D2R, to.center[1] * D2R),
          origin: from.code, dest: to.code, carrier: carrier,
          volume: 1, condition: 1
        };
        state.routeIndex[route.id] = route;
        (carrier === "air" ? state.routesAir : state.routesSea).push(route);
        state.assets.push({
          id: "mock-mover:" + i,
          type: carrier === "air" ? "plane" : "ship",
          authoritative: false,
          name: carrier === "air" ? "Approximate flight" : "Approximate vessel",
          operator: from.code,
          origin: from.code, dest: to.code, commodity: "approximate", qty: 1,
          progress: (i * 0.17) % 1, condition: 1, status: "APPROXIMATE",
          route: route, screen: null, lastLL: null
        });
      });
      rebuildAssetCollections();
    }

    function assetById(id) {
      return state.assetIndex[id] || state.recentAssetIndex[id] || null;
    }

    function assetInfo(asset) {
      if (!asset) return null;
      if (asset.aggregate) {
        return {
          id: asset.id, type: asset.type, name: asset.name, operator: asset.operator,
          authoritative: true, aggregate: true, count: asset.count,
          condition: Math.round(asset.condition * 100), status: asset.status,
          effectiveCapacity: asset.effectiveCapacity,
          lastMaintainedTick: asset.lastMaintainedTick
        };
      }
      const from = countryMeta(asset.origin).name || asset.origin;
      const to = countryMeta(asset.dest).name || asset.dest;
      return {
        id: asset.id, type: asset.type, name: asset.name, operator: asset.operator,
        authoritative: !!asset.authoritative, shipment: !!asset.shipment,
        condition: Math.round(asset.condition * 100), status: asset.status,
        originCondition: Math.round((asset.originCondition == null ? asset.condition : asset.originCondition) * 100),
        destCondition: Math.round((asset.destCondition == null ? asset.condition : asset.destCondition) * 100),
        origin: asset.origin, dest: asset.dest, commodity: asset.commodity,
        qty: asset.qty, progress: asset.progress, carrier: asset.carrier,
        distanceKm: asset.distanceKm, remainingDistanceKm: asset.remainingDistanceKm,
        lifecycle: asset.lifecycle, terminal: !!asset.terminal,
        terminalTick: asset.terminalTick, terminalEventId: asset.terminalEventId,
        ageTicks: asset.ageTicks, retentionTicksRemaining: asset.retentionTicksRemaining,
        etaTicks: asset.etaTicks, warExposed: !!asset.warExposed,
        buyerPool: asset.buyerPool, baseCost: asset.baseCost,
        tariffDuty: asset.tariffDuty, cost: asset.cost, proceeds: asset.proceeds,
        dispatchEventId: asset.dispatchEventId, departTick: asset.departTick,
        arriveTick: asset.arriveTick, relief: asset.relief,
        routeDesc: from + " → " + to
      };
    }

    function routeInfo(route) {
      if (!route) return null;
      const activeIds = route.shipmentIds || [];
      const recentIds = route.recentShipmentIds || [];
      let active = activeIds.map((id) => state.assetIndex[id]).filter(Boolean);
      if (!activeIds.length && !state.authoritative) {
        active = state.assets.filter((asset) => asset.route && asset.route.id === route.id);
      }
      const recent = recentIds.map((id) => state.recentAssetIndex[id]).filter(Boolean);
      const immutableOrder = (a, b) => Number(a.dispatchEventId) - Number(b.dispatchEventId) || a.id.localeCompare(b.id);
      active.sort(immutableOrder);
      recent.sort(immutableOrder);
      const rosterOrder = (a, b) =>
        (a.lifecycle === "arrived" ? 1 : 0) - (b.lifecycle === "arrived" ? 1 : 0) || immutableOrder(a, b);
      const shipments = active.concat(recent).sort(rosterOrder);
      const from = countryMeta(route.origin).name || route.origin;
      const to = countryMeta(route.dest).name || route.dest;
      return {
        id: route.id,
        authoritative: !!state.authoritative,
        carrier: route.carrier,
        origin: route.origin,
        dest: route.dest,
        routeDesc: from + " → " + to,
        volume: route.volume,
        distanceKm: route.distanceKm,
        condition: Math.round(route.condition * 100),
        shipmentCount: route.shipmentCount == null ? active.length : route.shipmentCount,
        recentShipmentCount: route.recentShipmentCount == null ? recent.length : route.recentShipmentCount,
        shipments: shipments.map(assetInfo)
      };
    }

    function applyAsset(id, action) {
      if (state.authoritative || !state.mockMode) return null;
      const asset = assetById(id);
      if (!asset) return null;
      if (action === "degrade") asset.condition = Math.max(0.05, asset.condition - 0.3);
      else if (action === "upgrade" || action === "extend") asset.condition = 1;
      else if (action === "break") asset.condition = 0.2;
      else if (action === "destroy") state.assets = state.assets.filter((item) => item.id !== id);
      asset.status = statusFor(asset.condition);
      rebuildAssetCollections();
      return assetInfo(assetById(id));
    }

    function noteEvent(event) {
      if (!event || event.kind !== "SHIPMENT_LOST") return;
      const shipmentId = event.payload && event.payload.shipment_id;
      if (shipmentId && state.lossPinged[shipmentId]) return;
      if (shipmentId) state.lossPinged[shipmentId] = true;
      const asset = shipmentId ? assetById(shipmentId) : null;
      if (asset && asset.lastLL) {
        state.pings.push({ ll: asset.lastLL, color: "#ec835a", t0: performance.now() / 1000, big: true });
      } else if (event.country && state.countries[event.country]) {
        const center = state.countries[event.country].center;
        state.pings.push({ ll: [center[0] * D2R, center[1] * D2R], color: "#ec835a", t0: performance.now() / 1000, big: true });
      }
    }

    // ---------------------------------------------------------------- projection
    let sinF0 = 0, cosF0 = 1, lam0 = 0, sinL0 = 0, cosL0 = 1, radius = 100, cx = 0, cy = 0;
    const P = new Float64Array(4);
    function beginProject() {
      const lat = state.view.lat * D2R;
      sinF0 = Math.sin(lat); cosF0 = Math.cos(lat);
      lam0 = state.view.lon * D2R;
      sinL0 = Math.sin(lam0); cosL0 = Math.cos(lam0);
      radius = Math.min(state.w, state.h) * 0.45 * state.zoom;
      cx = state.w / 2; cy = state.h / 2;
    }
    // Allocation-free projection of a unit vector at an altitude (1 = surface). Writes the
    // shared scratch P = [screenX, screenY, depth, radial distance in globe radii] and
    // returns it; callers must read P before the next projection.
    function projInto(vx, vy, vz, altitude) {
      const x = vy * cosL0 - vx * sinL0;
      const c = vx * cosL0 + vy * sinL0;
      const y = cosF0 * vz - sinF0 * c;
      P[0] = cx + radius * altitude * x;
      P[1] = cy - radius * altitude * y;
      P[2] = sinF0 * vz + cosF0 * c;
      P[3] = Math.sqrt(x * x + y * y) * altitude;
      return P;
    }
    // An elevated point behind the globe is still visible when it clears the limb.
    function visibleP() { return P[2] > 0.0 || P[3] > 1.0; }
    function proj(latR, lonR, altitude) {
      const cf = Math.cos(latR);
      projInto(cf * Math.cos(lonR), cf * Math.sin(lonR), Math.sin(latR), altitude || 1);
      return [P[0], P[1], P[2], P[3]];
    }
    function projV(vector, altitude) {
      projInto(vector[0], vector[1], vector[2], altitude || 1);
      return [P[0], P[1], P[2], P[3]];
    }
    function invProject(px, py) {
      const x = (px - cx) / radius, y = -(py - cy) / radius;
      const rho = Math.sqrt(x * x + y * y);
      if (rho > 1) return null;
      if (rho < 1e-6) return [state.view.lat * D2R, lam0];
      const c = Math.asin(clamp(rho, -1, 1)), sc = Math.sin(c), cc = Math.cos(c);
      return [
        Math.asin(clamp(cc * sinF0 + (y * sc * cosF0) / rho, -1, 1)),
        lam0 + Math.atan2(x * sc, rho * cc * cosF0 - y * sc * sinF0)
      ];
    }

    function makeStars() {
      const rng = mulberry32(0xBEEF);
      state.stars = [];
      for (let i = 0; i < 110; i++) state.stars.push([rng() * state.w, rng() * state.h, 0.4 + rng() * 1.1, 0.06 + rng() * 0.3]);
    }

    // Static graticule: meridians every 30 degrees plus the equator, as unit vectors once.
    const GRATICULE = (function () {
      const lines = [];
      for (let lon = -180; lon < 180; lon += 30) {
        const pts = [];
        for (let lat = -84; lat <= 84; lat += 4) pts.push(toVec(lat * D2R, lon * D2R));
        lines.push({ pts: pts, equator: false });
      }
      const equator = [];
      for (let lon = -180; lon <= 180; lon += 4) equator.push(toVec(0, lon * D2R));
      lines.push({ pts: equator, equator: true });
      return lines;
    })();

    // ------------------------------------------------------- territory massing
    // Presentation projection only. Every authoritative territory marker becomes a site; its
    // shape is a deterministic function of marker positions, region, and owner:
    //   * a softly irregular disc around the marker (irregularity seeded by the marker's own
    //     position, so annexation recolors a cell but never reshapes it);
    //   * an isthmus toward same-region neighbors (a minimum spanning tree per region),
    //     because the engine defines a region as one landmass (rail-connected);
    //   * clipped to the marker's spherical Voronoi cell, with a strait kept open between
    //     different regions. Nothing here adds, moves, or re-owns a simulation object.
    const COAST_STEPS = 72;
    const STRAIT = 2.2 * D2R;
    const land = { key: "", sites: [], regions: [] };

    function dot3(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
    function regionKeyFor(code, region) {
      return !region || region === "approximate" ? "solo:" + code : region;
    }
    function landSignature() {
      let key = "";
      for (let i = 0; i < state.order.length; i++) {
        const country = state.countries[state.order[i]];
        if (!country) continue;
        key += country.code + "|";
        for (let t = 0; t < country.territories.length; t++) {
          const territory = country.territories[t];
          key += territory.center[0].toFixed(4) + "," + territory.center[1].toFixed(4) + "," + territory.region + ";";
        }
      }
      return key;
    }
    // Rebuilt only when the country/territory set may have changed, and then only if the
    // signature (positions, regions, owners) actually differs.
    function ensureLand() {
      if (!state.landDirty) return;
      state.landDirty = false;
      const key = landSignature();
      if (key === land.key) return;
      land.key = key;
      const sites = [];
      state.order.forEach((code) => {
        const country = state.countries[code];
        if (!country) return;
        country.territories.forEach((territory) => {
          const v = toVec(territory.center[0] * D2R, territory.center[1] * D2R);
          sites.push({
            owner: code,
            region: regionKeyFor(code, territory.region),
            regionName: territory.region,
            v: v,
            seed: hashCode(territory.center[0].toFixed(3) + "," + territory.center[1].toFixed(3)),
            links: [],
            radii: new Float64Array(COAST_STEPS),
            coast: new Float64Array(COAST_STEPS * 3),
            rim: new Float64Array(COAST_STEPS * 3),
            east: null, north: null
          });
        });
      });
      // Per-region minimum spanning tree (Prim) gives every landmass one connected shape.
      const byRegion = Object.create(null);
      sites.forEach((site, index) => { (byRegion[site.region] = byRegion[site.region] || []).push(index); });
      const regions = [];
      Object.keys(byRegion).sort().forEach((regionKey) => {
        const members = byRegion[regionKey];
        const inTree = [members[0]];
        const pending = members.slice(1);
        while (pending.length) {
          let bestA = -1, bestB = -1, bestDot = -2;
          for (let i = 0; i < inTree.length; i++) {
            for (let j = 0; j < pending.length; j++) {
              const d = dot3(sites[inTree[i]].v, sites[pending[j]].v);
              if (d > bestDot) { bestDot = d; bestA = inTree[i]; bestB = j; }
            }
          }
          const joined = pending.splice(bestB, 1)[0];
          sites[bestA].links.push(joined); sites[joined].links.push(bestA);
          inTree.push(joined);
        }
        const sum = [0, 0, 0];
        members.forEach((index) => { sum[0] += sites[index].v[0]; sum[1] += sites[index].v[1]; sum[2] += sites[index].v[2]; });
        const length = Math.hypot(sum[0], sum[1], sum[2]) || 1;
        regions.push({
          key: regionKey,
          name: sites[members[0]].regionName,
          solo: regionKey.indexOf("solo:") === 0,
          v: [sum[0] / length, sum[1] / length, sum[2] / length],
          count: members.length
        });
      });

      sites.forEach((site) => {
        const p = site.v;
        let ex = -p[1], ey = p[0];
        const el = Math.hypot(ex, ey);
        if (el < 1e-6) { ex = 1; ey = 0; } else { ex /= el; ey /= el; }
        const east = [ex, ey, 0];
        const north = [-p[2] * ey, p[2] * ex, p[0] * ey - p[1] * ex];
        site.east = east; site.north = north;
        let nearestSame = Infinity;
        sites.forEach((other) => {
          if (other === site || other.region !== site.region) return;
          nearestSame = Math.min(nearestSame, Math.acos(clamp(dot3(p, other.v), -1, 1)));
        });
        const base = isFinite(nearestSame) ? clamp(nearestSame * 0.45, 6.5 * D2R, 14 * D2R) : 10.5 * D2R;
        const rng = mulberry32(site.seed);
        const phases = [rng() * 6.283, rng() * 6.283, rng() * 6.283, rng() * 6.283];
        const bridges = site.links.map((index) => {
          const q = sites[index].v;
          const pq = dot3(p, q);
          const t = [q[0] - pq * p[0], q[1] - pq * p[1], q[2] - pq * p[2]];
          return { angle: Math.atan2(dot3(t, north), dot3(t, east)), dist: Math.acos(clamp(pq, -1, 1)) };
        });
        const width = clamp(base * 0.42, 2.8 * D2R, 5.5 * D2R);
        for (let k = 0; k < COAST_STEPS; k++) {
          const theta = (k / COAST_STEPS) * Math.PI * 2;
          const ct = Math.cos(theta), st = Math.sin(theta);
          const u = [ct * east[0] + st * north[0], ct * east[1] + st * north[1], ct * east[2] + st * north[2]];
          const wobble = 1 + 0.15 * Math.sin(2 * theta + phases[0]) + 0.1 * Math.sin(3 * theta + phases[1]) +
            0.06 * Math.sin(5 * theta + phases[2]) + 0.035 * Math.sin(9 * theta + phases[3]);
          let r = base * wobble;
          bridges.forEach((bridge) => {
            const phi = theta - bridge.angle;
            if (Math.cos(phi) <= 0) return;
            const reach = Math.min(bridge.dist, (width * (0.85 + 0.3 * wobble - 0.3)) / Math.max(1e-4, Math.abs(Math.sin(phi))));
            if (reach > r) r = reach;
          });
          sites.forEach((other) => {
            if (other === site) return;
            const uq = dot3(u, other.v);
            if (uq <= 0) return;
            let bisector = Math.atan2(1 - dot3(p, other.v), uq);
            if (other.region !== site.region) bisector -= STRAIT;
            if (bisector < r) r = bisector;
          });
          r = Math.max(r, 1.2 * D2R);
          site.radii[k] = r;
          const cr = Math.cos(r), sr = Math.sin(r);
          site.coast[k * 3] = cr * p[0] + sr * u[0];
          site.coast[k * 3 + 1] = cr * p[1] + sr * u[1];
          site.coast[k * 3 + 2] = cr * p[2] + sr * u[2];
          const rr = r + 1.1 * D2R, crr = Math.cos(rr), srr = Math.sin(rr);
          site.rim[k * 3] = crr * p[0] + srr * u[0];
          site.rim[k * 3 + 1] = crr * p[1] + srr * u[1];
          site.rim[k * 3 + 2] = crr * p[2] + srr * u[2];
        }
      });
      land.sites = sites;
      land.regions = regions;
      labels.dirty = true;
    }

    // Trace a surface ring (flat xyz array) into the current path. Points behind the globe
    // are pinned radially to the limb, which is where an edge-on coast would be seen.
    function traceRing(ring) {
      let anyFront = false;
      for (let k = 0; k < ring.length; k += 3) {
        projInto(ring[k], ring[k + 1], ring[k + 2], 1);
        if (P[2] > -0.02) { anyFront = true; break; }
      }
      if (!anyFront) return false;
      ctx.beginPath();
      for (let k = 0; k < ring.length; k += 3) {
        projInto(ring[k], ring[k + 1], ring[k + 2], 1);
        let x = P[0], y = P[1];
        if (P[2] < 0) {
          const dx = x - cx, dy = y - cy, length = Math.sqrt(dx * dx + dy * dy) || 1;
          x = cx + dx / length * radius; y = cy + dy / length * radius;
        }
        if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      return true;
    }

    function warringSet() {
      const set = Object.create(null);
      state.wars.forEach((pair) => { set[pair[0]] = true; set[pair[1]] = true; });
      return set;
    }

    function drawLand(now) {
      ensureLand();
      const atWar = warringSet();
      const hoverCode = state.hover && state.hover.kind === "country" ? state.hover.id : null;
      const hoverBloc = hoverCode && state.countries[hoverCode] ? state.countries[hoverCode].blocId : null;
      const selectedBloc = state.selected && state.countries[state.selected] ? state.countries[state.selected].blocId : null;
      const warAlpha = 0.5 + 0.18 * Math.sin(now * 2.2);
      // Pass 1: treaty rims (bloc members) and war halos sit under the land.
      for (let i = 0; i < land.sites.length; i++) {
        const site = land.sites[i];
        const country = state.countries[site.owner];
        if (!country) continue;
        if (atWar[site.owner] && traceRing(site.rim)) {
          ctx.globalAlpha = warAlpha; ctx.strokeStyle = "#ec835a"; ctx.lineWidth = 2.2; ctx.stroke();
        } else if (country.blocId && traceRing(site.rim)) {
          const lit = country.blocId === hoverBloc || country.blocId === selectedBloc;
          ctx.globalAlpha = lit ? 0.7 : 0.32; ctx.strokeStyle = "#c3c2b7"; ctx.lineWidth = lit ? 1.1 : 0.8;
          ctx.setLineDash([2, 3]); ctx.stroke(); ctx.setLineDash([]);
        }
      }
      // Pass 2: land mass, then identity tint, then coast.
      for (let i = 0; i < land.sites.length; i++) {
        const site = land.sites[i];
        const country = state.countries[site.owner];
        if (!country || !traceRing(site.coast)) continue;
        const hot = site.owner === hoverCode, selected = site.owner === state.selected;
        // Land base: the sea gradient's own hue lifted a step, so the identity tint reads as
        // solid ground rather than tinted water.
        ctx.globalAlpha = 1; ctx.fillStyle = "#1b2126"; ctx.fill();
        ctx.globalAlpha = selected ? 0.42 : (hot ? 0.36 : 0.26); ctx.fillStyle = country.color; ctx.fill();
        ctx.globalAlpha = hot || selected ? 1 : 0.78; ctx.strokeStyle = country.color;
        ctx.lineWidth = hot ? 1.5 : 1; ctx.stroke();
      }
      // Pass 3: selection outline on top of every neighbor's coast (blue ring + ink hairline,
      // so a blue-identity nation still reads as selected).
      if (state.selected) {
        for (let i = 0; i < land.sites.length; i++) {
          const site = land.sites[i];
          if (site.owner !== state.selected || !traceRing(site.coast)) continue;
          ctx.globalAlpha = 1; ctx.strokeStyle = "#3987e5"; ctx.lineWidth = 2.6; ctx.stroke();
          ctx.strokeStyle = "rgba(255,255,255,0.85)"; ctx.lineWidth = 0.7; ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    }

    // ---------------------------------------------------------------- arcs
    // Stroke a sampled curve. `sample(i)` must leave the i-th point in P. Surface curves
    // (peak 1) draw only the near side. Raised curves also draw the stretch that is behind
    // the globe but clears the limb, at reduced alpha so it reads as far side.
    // `segments`, when given, receives every drawn segment as flat x0,y0,x1,y1 numbers.
    function strokeSampled(sample, steps, peak, segments) {
      const back = peak > 1 ? new Path2D() : null;
      ctx.beginPath();
      let mode = 0, px = 0, py = 0;
      for (let i = 0; i <= steps; i++) {
        sample(i);
        const next = P[2] > 0 ? 1 : (back && P[3] > 1 ? 2 : 0);
        if (next === 0) { mode = 0; continue; }
        const path = next === 1 ? ctx : back;
        if (mode === 0) path.moveTo(P[0], P[1]);
        else {
          if (next !== mode) path.moveTo(px, py);
          path.lineTo(P[0], P[1]);
          if (segments) segments.push(px, py, P[0], P[1]);
        }
        mode = next; px = P[0]; py = P[1];
      }
      ctx.stroke();
      if (back) {
        const alpha = ctx.globalAlpha;
        ctx.globalAlpha = alpha * 0.32;
        ctx.stroke(back);
        ctx.globalAlpha = alpha;
      }
    }
    // Great-circle arc between unit vectors, raised to `peak` altitude at its midpoint.
    function strokeArc(a, b, peak, steps) {
      const omega = Math.acos(clamp(dot3(a, b), -1, 1));
      const sinOmega = Math.sin(omega) || 1;
      strokeSampled((i) => {
        const t = i / steps;
        let wa = 1 - t, wb = t;
        if (omega > 1e-6) { wa = Math.sin((1 - t) * omega) / sinOmega; wb = Math.sin(t * omega) / sinOmega; }
        projInto(wa * a[0] + wb * b[0], wa * a[1] + wb * b[1], wa * a[2] + wb * b[2], 1 + (peak - 1) * Math.sin(Math.PI * t));
      }, steps, peak, null);
    }
    function drawArc(a, b, peak, color, width, dash) {
      ctx.strokeStyle = color; ctx.lineWidth = width;
      if (dash) ctx.setLineDash(dash);
      strokeArc(a, b, peak, 48);
      if (dash) ctx.setLineDash([]);
    }

    function routeVector(route, t, visualOffset) {
      const base = slerp(route.a, route.b, t);
      const carrierOffset = route.carrier === "air" ? 0.05 : (route.carrier === "rail" ? -0.025 : 0);
      const offset = (carrierOffset + (visualOffset || 0)) * Math.sin(Math.PI * t);
      const normal = route.normal || [0, 0, 0];
      const vector = [
        base[0] + normal[0] * offset,
        base[1] + normal[1] * offset,
        base[2] + normal[2] * offset
      ];
      const length = Math.hypot(vector[0], vector[1], vector[2]) || 1;
      return [vector[0] / length, vector[1] / length, vector[2] / length];
    }

    // Route polyline in world space depends only on the lane geometry: cache it per route.
    const ROUTE_STEPS = 40;
    function routePolyline(route) {
      if (route.poly) return route.poly;
      const poly = new Float64Array((ROUTE_STEPS + 1) * 3);
      for (let i = 0; i <= ROUTE_STEPS; i++) {
        const v = routeVector(route, i / ROUTE_STEPS, 0);
        poly[i * 3] = v[0]; poly[i * 3 + 1] = v[1]; poly[i * 3 + 2] = v[2];
      }
      route.poly = poly;
      return poly;
    }

    // route.screenSegments is a flat [x0,y0,x1,y1, ...] list of visible segments, reused
    // between frames, used only for lane hit testing.
    function drawRouteArc(route, peak, color, width, dash) {
      ctx.strokeStyle = color; ctx.lineWidth = width;
      if (dash) ctx.setLineDash(dash);
      const poly = routePolyline(route);
      const segments = route.screenSegments || (route.screenSegments = []);
      segments.length = 0;
      strokeSampled((i) => {
        projInto(poly[i * 3], poly[i * 3 + 1], poly[i * 3 + 2], 1 + (peak - 1) * Math.sin(Math.PI * i / ROUTE_STEPS));
      }, ROUTE_STEPS, peak, segments);
      if (dash) ctx.setLineDash([]);
    }

    let laneVolumeMax = 1;
    function nearBlend() { return clamp((state.zoom - 1.05) / 0.9, 0, 1); }
    function routeWidth(route) {
      const near = clamp(0.7 + Math.log1p(Math.max(0, route.volume)) * 0.75, 0.8, 5.5);
      const far = 0.55 + 1.6 * Math.sqrt(clamp(Math.max(0, route.volume) / laneVolumeMax, 0, 1));
      return far + (near - far) * nearBlend();
    }
    function routeAlpha(route) {
      const share = Math.sqrt(clamp(Math.max(0, route.volume) / laneVolumeMax, 0, 1));
      const far = (0.16 + 0.34 * share) * (0.35 + 0.65 * clamp(route.condition, 0, 1));
      const near = 0.12 + 0.55 * clamp(route.condition, 0, 1);
      return far + (near - far) * nearBlend();
    }
    function drawRoute(route, color, peak, dash) {
      const width = routeWidth(route);
      const hot = state.hover && state.hover.kind === "route" && state.hover.id === route.id;
      if (state.selectedRoute === route.id) {
        drawRouteArc(route, peak, "rgba(57,135,229,0.55)", width + 4, null);
      } else if (hot) {
        drawRouteArc(route, peak, "rgba(255,255,255,0.22)", width + 3, null);
      }
      drawRouteArc(route, peak, color.replace("ALPHA", (hot ? 0.9 : routeAlpha(route)).toFixed(2)), width, dash);
    }

    // Shared scratch vector for movers; routeVector() allocates, so the tail uses it sparingly.
    function drawMover(asset, peak, color, dt) {
      const target = asset.targetProgress == null ? Number(asset.progress || 0) : asset.targetProgress;
      if (asset.displayProgress == null) asset.displayProgress = target;
      if (!state.running) asset.displayProgress = target;
      else if (asset.displayProgress < target) {
        const gap = target - asset.displayProgress;
        const transitionSeconds = clamp(1.05 / Math.max(0.5, state.tps), 0.14, 2.1);
        const rate = asset.progressRate == null ? gap / transitionSeconds : asset.progressRate;
        asset.displayProgress += Math.min(gap, dt * rate);
      }
      const shownProgress = clamp(asset.displayProgress, 0, target);
      const vector = routeVector(asset.route, shownProgress, asset.visualOffset || 0);
      const altitude = 1 + (peak - 1) * Math.sin(Math.PI * shownProgress);
      asset.lastLL = [Math.asin(clamp(vector[2], -1, 1)), Math.atan2(vector[1], vector[0])];
      projInto(vector[0], vector[1], vector[2], altitude);
      if (!(peak > 1 ? visibleP() : P[2] > 0.02)) { asset.screen = null; return; }
      const hx = P[0], hy = P[1], farSide = P[2] <= 0 ? 0.4 : 1;
      asset.screen = [hx, hy];
      const blend = nearBlend();
      const size = clamp(1.8 + Math.log1p(Math.max(0, asset.qty || 0)) * 0.8, 2, 7) * (0.55 + 0.45 * blend);
      const degraded = asset.condition < 0.35;
      const fill = degraded ? "#ec835a" : color;
      // A short wake behind the mover (about 2.5 degrees of its own route) shows heading.
      const routeAngle = Math.acos(clamp(dot3(asset.route.a, asset.route.b), -1, 1)) || 1;
      const tailT = Math.max(0, shownProgress - (2.5 * D2R) / routeAngle);
      if (tailT < shownProgress) {
        const tail = routeVector(asset.route, tailT, asset.visualOffset || 0);
        projInto(tail[0], tail[1], tail[2], 1 + (peak - 1) * Math.sin(Math.PI * tailT));
        if (peak > 1 ? visibleP() : P[2] > 0.02) {
          ctx.globalAlpha = 0.3 * (0.3 + 0.7 * asset.condition) * farSide;
          ctx.strokeStyle = fill; ctx.lineWidth = Math.max(0.8, size * 0.7);
          ctx.beginPath(); ctx.moveTo(P[0], P[1]); ctx.lineTo(hx, hy); ctx.stroke();
        }
      }
      ctx.globalAlpha = (0.35 + 0.65 * asset.condition) * farSide;
      ctx.fillStyle = fill;
      ctx.beginPath(); ctx.arc(hx, hy, size, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
      const hot = state.hover && state.hover.kind === "asset" && state.hover.id === asset.id;
      if (state.selectedAsset === asset.id || hot) {
        ctx.strokeStyle = state.selectedAsset === asset.id ? "#3987e5" : "rgba(255,255,255,0.8)";
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.arc(hx, hy, size + 4, 0, Math.PI * 2); ctx.stroke();
        if (state.selectedAsset === asset.id) {
          ctx.fillStyle = "rgba(255,255,255,0.92)"; ctx.font = "600 9px ui-monospace, monospace";
          ctx.textAlign = "left"; ctx.textBaseline = "alphabetic"; ctx.fillText(asset.id, hx + size + 7, hy + 3);
        }
      }
    }

    function satellitePosition(code, index, worldTick) {
      const seed = hashCode(code + ":" + index);
      const inclination = (18 + (seed % 66)) * D2R;
      const node = ((seed >>> 8) % 360) * D2R;
      const phase = ((seed >>> 16) % 360) * D2R + worldTick * (0.08 + (seed % 19) * 0.002);
      return [
        Math.asin(Math.sin(inclination) * Math.sin(phase)),
        node + Math.atan2(Math.cos(inclination) * Math.sin(phase), Math.cos(phase)),
        1.12 + (seed % 5) * 0.025
      ];
    }

    function drawSatelliteNetwork(asset) {
      asset.screen = null;
      for (let i = 0; i < asset.count; i++) {
        const ll = satellitePosition(asset.operator, i, state.displayWorldTick);
        const p = proj(ll[0], ll[1], ll[2]);
        if (!(p[2] > 0 || p[3] > 1.001)) continue;
        if (!asset.screen) { asset.screen = [p[0], p[1]]; asset.lastLL = [ll[0], ll[1]]; }
        ctx.globalAlpha = (0.25 + 0.75 * asset.condition) * (p[2] > 0 ? 1 : 0.5);
        ctx.fillStyle = asset.condition < 0.35 ? "#ec835a" : "rgba(255,255,255,0.95)";
        ctx.beginPath(); ctx.arc(p[0], p[1], 1.6, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
      const hot = state.hover && state.hover.kind === "asset" && state.hover.id === asset.id;
      if ((state.selectedAsset === asset.id || hot) && asset.screen) {
        ctx.strokeStyle = state.selectedAsset === asset.id ? "#3987e5" : "rgba(255,255,255,0.8)";
        ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.arc(asset.screen[0], asset.screen[1], 7, 0, Math.PI * 2); ctx.stroke();
      }
    }

    // ---------------------------------------------------------------- labels
    // Greedy collision-avoided placement. The *choice* of slot is cached and recomputed only
    // when the view moves past a small threshold or the label set changes; positions are
    // re-projected every frame from that choice, so labels track the globe exactly.
    const SERIF = 'Georgia, "Iowan Old Style", "Times New Roman", serif';
    const labels = { dirty: true, key: "", items: [], widths: Object.create(null), at: 0 };
    const LABEL_SLOTS = [[0, -1], [0, 1], [1, 0], [-1, 0]];
    function textWidth(font, text, spacing) {
      const key = font + "|" + spacing + "|" + text;
      let width = labels.widths[key];
      if (width == null) {
        ctx.font = font;
        width = ctx.measureText(text).width + spacing * text.length;
        labels.widths[key] = width;
      }
      return width;
    }
    function labelFontSize() { return clamp(9.5 + (state.zoom - 1) * 1.6, 9.5, 12); }
    function layoutLabels(now) {
      const hoverCode = state.hover && state.hover.kind === "country" ? state.hover.id : "";
      const key = [Math.round(state.view.lat * 2), Math.round(state.view.lon * 2), Math.round(state.zoom * 40),
        state.w, state.h, state.selected || "", hoverCode, state.wars.length, land.key.length, state.order.length].join(":");
      if (!labels.dirty && key === labels.key && now - labels.at < 1.5) return;
      labels.dirty = false; labels.key = key; labels.at = now;
      const atWar = warringSet();
      const size = labelFontSize();
      const countryFont = "600 " + size.toFixed(1) + "px " + SERIF;
      const regionFont = "italic " + (size + 0.5).toFixed(1) + "px " + SERIF;
      const candidates = [];
      state.order.forEach((code) => {
        const country = state.countries[code];
        if (!country || !country.center) return;
        const text = String(country.name || code).toUpperCase();
        candidates.push({
          kind: "country", code: code, text: text, font: countryFont, spacing: 1.4,
          v: toVec(country.center[0] * D2R, country.center[1] * D2R),
          width: textWidth(countryFont, text, 1.4), height: size + 2,
          priority: code === state.selected ? 1000 : (code === hoverCode ? 900 : (atWar[code] ? 400 : 100 + country.territories.length))
        });
      });
      if (state.zoom < 2.6) land.regions.forEach((region) => {
        if (region.solo || !region.name) return;
        const text = String(region.name).toUpperCase();
        candidates.push({
          kind: "region", text: text, font: regionFont, spacing: 4,
          v: region.v, width: textWidth(regionFont, text, 4), height: size + 2, priority: 10
        });
      });
      candidates.sort((a, b) => b.priority - a.priority || (a.text < b.text ? -1 : 1));
      // Region names are faint set dressing laid over the land, so besides other labels they
      // also keep clear of capitals and of the lanes drawn this frame; with no clear spot
      // near the landmass the name is left out rather than printed over the network.
      const marks = [];
      state.order.forEach((code) => {
        const country = state.countries[code];
        if (!country || !country.center) return;
        const v = toVec(country.center[0] * D2R, country.center[1] * D2R);
        projInto(v[0], v[1], v[2], 1);
        if (P[2] > 0.03) marks.push(P[0], P[1]);
      });
      const show = layers();
      const lanes = [];
      if (show.sea) lanes.push.apply(lanes, state.routesSea);
      if (show.air) lanes.push.apply(lanes, state.routesAir);
      if (show.rail) lanes.push.apply(lanes, state.routesRail);
      const placed = [];
      const items = [];
      candidates.forEach((item) => {
        projInto(item.v[0], item.v[1], item.v[2], 1);
        if (P[2] < (item.kind === "region" ? 0.35 : 0.12)) return;
        const x = P[0], y = P[1];
        const region = item.kind === "region";
        const slots = region ? REGION_SLOTS : LABEL_SLOTS;
        for (let s = 0; s < slots.length; s++) {
          const rect = labelRect(item, x, y, slots[s]);
          const margin = region ? REGION_MARGIN : 0;
          let clear = true;
          for (let j = 0; j < placed.length && clear; j++) {
            const other = placed[j];
            if (rect[0] - margin < other[2] && rect[2] + margin > other[0] &&
              rect[1] - margin < other[3] && rect[3] + margin > other[1]) clear = false;
          }
          if (region && clear) clear = regionSpotClear(rect, marks, lanes);
          if (clear) {
            placed.push(rect);
            item.slot = slots[s];
            items.push(item);
            break;
          }
        }
      });
      labels.items = items;
    }
    // Region slots are offsets from the landmass centroid in label widths and label heights:
    // the centroid first, then above and below it, then to either side.
    const REGION_SLOTS = [[0, 0], [0, -1.4], [0, 1.4], [0, -2.8], [0, 2.8], [-0.6, 0], [0.6, 0], [0, -4.2], [0, 4.2]];
    const REGION_MARGIN = 5;
    function segmentHitsRect(x0, y0, x1, y1, rect) {
      // Liang-Barsky clip of the segment against the rectangle.
      const dx = x1 - x0, dy = y1 - y0;
      const p = [-dx, dx, -dy, dy];
      const q = [x0 - rect[0], rect[2] - x0, y0 - rect[1], rect[3] - y0];
      let t0 = 0, t1 = 1;
      for (let i = 0; i < 4; i++) {
        if (p[i] === 0) {
          if (q[i] < 0) return false;
        } else {
          const t = q[i] / p[i];
          if (p[i] < 0) { if (t > t1) return false; if (t > t0) t0 = t; } else { if (t < t0) return false; if (t < t1) t1 = t; }
        }
      }
      return true;
    }
    function regionSpotClear(rect, marks, lanes) {
      // The whole name stays on the sphere, never out in space past the limb.
      const inner = radius * 0.94;
      for (let c = 0; c < 4; c++) {
        const px = rect[c & 1 ? 2 : 0] - cx, py = rect[c & 2 ? 3 : 1] - cy;
        if (px * px + py * py > inner * inner) return false;
      }
      for (let i = 0; i < marks.length; i += 2) {
        if (marks[i] > rect[0] - REGION_MARGIN && marks[i] < rect[2] + REGION_MARGIN &&
          marks[i + 1] > rect[1] - REGION_MARGIN && marks[i + 1] < rect[3] + REGION_MARGIN) return false;
      }
      for (let r = 0; r < lanes.length; r++) {
        const segments = lanes[r].screenSegments;
        if (!segments) continue;
        for (let i = 0; i < segments.length; i += 4) {
          if (segmentHitsRect(segments[i], segments[i + 1], segments[i + 2], segments[i + 3], rect)) return false;
        }
      }
      return true;
    }
    function labelRect(item, x, y, slot) {
      const pad = 3, gap = 7;
      let lx = x - item.width / 2, ly = y - item.height / 2;
      if (item.kind === "region") {
        lx += slot[0] * item.width;
        ly += slot[1] * item.height;
      } else if (item.kind === "country") {
        if (slot[1] < 0) ly = y - gap - item.height;
        else if (slot[1] > 0) ly = y + gap;
        else if (slot[0] > 0) lx = x + gap;
        else lx = x - gap - item.width;
      }
      return [lx - pad, ly - pad, lx + item.width + pad, ly + item.height + pad];
    }
    function drawLabels(now) {
      layoutLabels(now);
      ctx.textBaseline = "middle"; ctx.textAlign = "left";
      ctx.lineJoin = "round";
      const hasSpacing = "letterSpacing" in ctx;
      labels.items.forEach((item) => {
        projInto(item.v[0], item.v[1], item.v[2], 1);
        if (P[2] < 0.05) return;
        const fade = clamp((P[2] - 0.05) / 0.3, 0, 1);
        const rect = labelRect(item, P[0], P[1], item.slot);
        const tx = rect[0] + 3, ty = (rect[1] + rect[3]) / 2;
        ctx.font = item.font;
        if (hasSpacing) ctx.letterSpacing = item.spacing + "px";
        if (item.kind === "region") {
          ctx.globalAlpha = 0.34 * fade * (1 - clamp((state.zoom - 1.9) / 0.7, 0, 1));
          ctx.fillStyle = "#c3c2b7";
          ctx.fillText(item.text, tx, ty);
        } else {
          const emphasis = item.code === state.selected || (state.hover && state.hover.id === item.code);
          ctx.globalAlpha = fade * (emphasis ? 1 : 0.86);
          ctx.strokeStyle = "rgba(13,13,13,0.85)"; ctx.lineWidth = 3;
          ctx.strokeText(item.text, tx, ty);
          ctx.fillStyle = "#ffffff";
          ctx.fillText(item.text, tx, ty);
        }
      });
      if (hasSpacing) ctx.letterSpacing = "0px";
      ctx.globalAlpha = 1;
      ctx.textBaseline = "alphabetic";
    }

    function drawCapitals() {
      const atWar = warringSet();
      state.order.forEach((code) => {
        const country = state.countries[code];
        if (!country || !country.center) return;
        const cf = Math.cos(country.center[0] * D2R);
        projInto(cf * Math.cos(country.center[1] * D2R), cf * Math.sin(country.center[1] * D2R), Math.sin(country.center[0] * D2R), 1);
        if (P[2] <= 0.03) return;
        const fade = clamp(P[2] / 0.25, 0, 1);
        ctx.globalAlpha = fade;
        ctx.fillStyle = "#0d0d0d";
        ctx.beginPath(); ctx.arc(P[0], P[1], 3.4, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = atWar[code] ? "#ec835a" : "#ffffff";
        ctx.beginPath(); ctx.arc(P[0], P[1], 2.1, 0, Math.PI * 2); ctx.fill();
        if (code === state.selected) {
          ctx.strokeStyle = "#3987e5"; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(P[0], P[1], 5.5, 0, Math.PI * 2); ctx.stroke();
        }
      });
      ctx.globalAlpha = 1;
    }

    // ---------------------------------------------------------------- conflict
    function countryVec(code) {
      const country = state.countries[code];
      return country && country.center ? toVec(country.center[0] * D2R, country.center[1] * D2R) : null;
    }
    function drawConflict(now) {
      // Persistent wars: a steady raised arc in the war-status color; slow breathing only.
      state.wars.forEach((pair) => {
        const a = countryVec(pair[0]), b = countryVec(pair[1]);
        if (!a || !b) return;
        const breathe = 0.62 + 0.2 * Math.sin(now * 2.2);
        ctx.lineCap = "round";
        drawArc(a, b, 1.12, "rgba(236,131,90," + (breathe * 0.35).toFixed(3) + ")", 5, null);
        drawArc(a, b, 1.12, "rgba(236,131,90," + breathe.toFixed(3) + ")", 1.6, null);
        ctx.lineCap = "butt";
      });
      // Recent real STRIKE events: dashes march attacker -> target, fading with age, and the
      // target gets an impact ring while the strike is fresh.
      state.strikes.forEach((strike) => {
        const remaining = clamp(1 - strike.age / Math.max(1, strike.visibleTicks), 0.1, 1);
        const width = clamp(1.1 + strike.power * 2.5, 1.1, 3.6);
        ctx.lineDashOffset = -now * 26;
        drawArc(strike.a, strike.b, 1.18, "rgba(208,59,59," + (0.85 * remaining).toFixed(3) + ")", width, [7, 5]);
        ctx.lineDashOffset = 0;
        projInto(strike.b[0], strike.b[1], strike.b[2], 1);
        if (P[2] > 0.02) {
          const cycle = (now * 0.8 + (strike.eventId % 7) * 0.13) % 1;
          ctx.strokeStyle = "#d03b3b";
          ctx.globalAlpha = remaining * (1 - cycle) * 0.9; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(P[0], P[1], 4 + cycle * (10 + 10 * strike.power), 0, Math.PI * 2); ctx.stroke();
          ctx.globalAlpha = remaining; ctx.fillStyle = "#d03b3b";
          ctx.beginPath(); ctx.arc(P[0], P[1], 2.2, 0, Math.PI * 2); ctx.fill();
          ctx.globalAlpha = 1;
        }
      });
    }

    function drawBlocLinks() {
      const focus = state.hover && state.hover.kind === "country" ? state.hover.id : state.selected;
      const country = focus && state.countries[focus];
      if (!country || !country.blocId) return;
      const a = countryVec(focus);
      state.order.forEach((code) => {
        if (code === focus || state.countries[code].blocId !== country.blocId) return;
        const b = countryVec(code);
        if (a && b) drawArc(a, b, 1.04, "rgba(195,194,183,0.55)", 1, [2, 4]);
      });
    }

    // ---------------------------------------------------------------- tooltip
    function quantityText(value) {
      const qty = Math.abs(Number(value || 0));
      if (qty >= 10) return qty.toFixed(0);
      if (qty >= 1) return qty.toFixed(1);
      return qty > 0 ? qty.toPrecision(2) : "0";
    }
    function tooltipLines(hover) {
      if (hover.kind === "country") {
        const country = state.countries[hover.id];
        if (!country) return null;
        const detail = [];
        if (country.region && country.region !== "approximate") detail.push(country.region);
        if (country.territories.length > 1) detail.push(country.territories.length + " territories");
        const foes = [];
        state.wars.forEach((pair) => {
          if (pair[0] === hover.id) foes.push(pair[1]);
          else if (pair[1] === hover.id) foes.push(pair[0]);
        });
        const partners = country.blocId ? state.order.filter((code) => code !== hover.id && state.countries[code].blocId === country.blocId) : [];
        const lines = [{ text: country.name, font: "600 12.5px " + SERIF, color: "#ffffff", tag: country.code }];
        if (detail.length) lines.push({ text: detail.join(" · "), font: "10.5px system-ui, sans-serif", color: "#c3c2b7" });
        if (partners.length) lines.push({ text: "Bloc with " + partners.join(", "), font: "10.5px system-ui, sans-serif", color: "#c3c2b7" });
        if (foes.length) lines.push({ text: "At war with " + foes.join(", "), font: "10.5px system-ui, sans-serif", color: "#c3c2b7", chip: "#ec835a" });
        return lines;
      }
      if (hover.kind === "route") {
        const route = state.routeIndex[hover.id];
        if (!route) return null;
        const count = route.shipmentCount == null ? "" : route.shipmentCount + " in flight · ";
        return [
          { text: route.carrier.toUpperCase() + " LANE", font: "600 10px system-ui, sans-serif", color: "#898781", tag: route.origin + " → " + route.dest },
          { text: count + "volume " + quantityText(route.volume), font: "10.5px ui-monospace, monospace", color: "#c3c2b7" }
        ];
      }
      if (hover.kind === "asset") {
        const asset = assetById(hover.id);
        if (!asset) return null;
        if (asset.aggregate) {
          return [
            { text: asset.name, font: "600 10.5px system-ui, sans-serif", color: "#ffffff" },
            { text: asset.count + " units · condition " + Math.round(asset.condition * 100) + "%", font: "10.5px ui-monospace, monospace", color: "#c3c2b7" }
          ];
        }
        return [
          { text: asset.id, font: "600 10.5px ui-monospace, monospace", color: "#ffffff", tag: (asset.carrier || asset.type || "").toUpperCase() },
          { text: (asset.commodity || "") + " " + quantityText(asset.qty) + " · " + asset.origin + " → " + asset.dest + " · " + Math.round(Number(asset.progress || 0) * 100) + "%", font: "10.5px ui-monospace, monospace", color: "#c3c2b7" }
        ];
      }
      return null;
    }
    function drawTooltip() {
      const hover = state.hover;
      if (!hover || dragging) return;
      const lines = tooltipLines(hover);
      if (!lines) return;
      let width = 0;
      lines.forEach((line) => {
        ctx.font = line.font;
        line.w = ctx.measureText(line.text).width + (line.chip ? 12 : 0);
        if (line.tag) { ctx.font = "10px ui-monospace, monospace"; line.tw = ctx.measureText(line.tag).width; line.w += line.tw + 8; }
        width = Math.max(width, line.w);
      });
      const lineH = 16, boxW = width + 20, boxH = lines.length * lineH + 12;
      let x = hover.x + 14, y = hover.y + 16;
      if (x + boxW > state.w - 6) x = hover.x - 14 - boxW;
      if (y + boxH > state.h - 6) y = hover.y - 12 - boxH;
      x = clamp(x, 6, Math.max(6, state.w - boxW - 6)); y = clamp(y, 6, Math.max(6, state.h - boxH - 6));
      ctx.globalAlpha = 1;
      ctx.fillStyle = "rgba(26,26,25,0.94)";
      ctx.strokeStyle = "rgba(255,255,255,0.16)"; ctx.lineWidth = 1;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x + 0.5, y + 0.5, boxW, boxH, 4); else ctx.rect(x + 0.5, y + 0.5, boxW, boxH);
      ctx.fill(); ctx.stroke();
      ctx.textBaseline = "middle"; ctx.textAlign = "left";
      lines.forEach((line, i) => {
        const ly = y + 6 + lineH * i + lineH / 2;
        let lx = x + 10;
        if (line.chip) {
          ctx.fillStyle = line.chip; ctx.beginPath(); ctx.arc(lx + 3, ly, 3, 0, Math.PI * 2); ctx.fill();
          lx += 12;
        }
        ctx.font = line.font; ctx.fillStyle = line.color; ctx.fillText(line.text, lx, ly);
        if (line.tag) {
          ctx.font = "10px ui-monospace, monospace"; ctx.fillStyle = "#898781";
          ctx.fillText(line.tag, x + boxW - 10 - line.tw, ly);
        }
      });
      ctx.textBaseline = "alphabetic";
    }

    // ---------------------------------------------------------------- frame
    // Open on the side of the world where most territory is, not on empty ocean. Runs once,
    // only before the user has touched the globe; purely a camera choice.
    function chooseInitialView() {
      ensureLand();
      if (!land.sites.length) return;
      state.viewChosen = true;
      if (state.lastInteract > 0 || state.focusTarget) return;
      let best = null, bestScore = -1;
      land.sites.forEach((candidate) => {
        let score = 0;
        land.sites.forEach((site) => { const d = dot3(candidate.v, site.v); if (d > 0) score += d * d; });
        if (score > bestScore) { bestScore = score; best = candidate; }
      });
      const sum = [0, 0, 0];
      land.sites.forEach((site) => {
        const d = dot3(best.v, site.v);
        if (d <= 0) return;
        sum[0] += site.v[0] * d * d; sum[1] += site.v[1] * d * d; sum[2] += site.v[2] * d * d;
      });
      const length = Math.hypot(sum[0], sum[1], sum[2]) || 1;
      state.view.lat = clamp(Math.asin(clamp(sum[2] / length, -1, 1)) / D2R, -30, 30);
      state.view.lon = Math.atan2(sum[1], sum[0]) / D2R;
    }

    function layers() {
      return { sea: true, air: true, rail: state.zoomTarget > 1.6, satellites: state.zoomTarget > 1.05 };
    }

    function draw(now, dt) {
      const width = state.cssWidth, height = state.cssHeight;
      if (!width || !height) return;
      const dpr = window.devicePixelRatio || 1;
      if (state.w !== width || state.h !== height || state.dpr !== dpr) {
        state.w = width; state.h = height; state.dpr = dpr;
        canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); makeStars();
        labels.dirty = true;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
      state.renderAssets.forEach((asset) => { asset.screen = null; });
      state.routesSea.concat(state.routesAir, state.routesRail).forEach((route) => { if (route.screenSegments) route.screenSegments.length = 0; });
      if (state.focusTarget) {
        const lonDelta = ((state.focusTarget[1] - state.view.lon + 540) % 360) - 180;
        const latDelta = state.focusTarget[0] - state.view.lat;
        state.view.lon += lonDelta * 0.09; state.view.lat += latDelta * 0.09;
        if (Math.abs(lonDelta) < 0.4 && Math.abs(latDelta) < 0.4) state.focusTarget = null;
      } else if (now - state.lastInteract > 8 && state.zoom < 2.2 && !state.hover) state.view.lon += dt * 1.6;
      // Wheel zoom eases toward its target instead of jumping.
      if (state.zoom !== state.zoomTarget) {
        state.zoom += (state.zoomTarget - state.zoom) * (1 - Math.exp(-dt * 14));
        if (Math.abs(state.zoomTarget - state.zoom) < 1e-4) state.zoom = state.zoomTarget;
      }
      if (!state.viewChosen) chooseInitialView();
      state.view.lat = clamp(state.view.lat, -80, 80);
      if (!state.running) state.displayWorldTick = state.worldTick;
      else if (state.displayWorldTick < state.orbitTargetTick) {
        state.displayWorldTick += Math.min(state.orbitTargetTick - state.displayWorldTick, dt * state.orbitRate);
      }
      beginProject();
      const show = layers();

      state.stars.forEach((star) => {
        ctx.globalAlpha = star[3]; ctx.fillStyle = "#c3c2b7";
        ctx.fillRect(star[0], star[1], star[2], star[2]);
      });
      ctx.globalAlpha = 1;
      // Atmosphere: a thin ink rim just outside the limb.
      const halo = ctx.createRadialGradient(cx, cy, radius * 0.98, cx, cy, radius * 1.07);
      halo.addColorStop(0, "rgba(195,194,183,0.11)"); halo.addColorStop(1, "rgba(195,194,183,0)");
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(cx, cy, radius * 1.07, 0, Math.PI * 2); ctx.fill();
      const sea = ctx.createRadialGradient(cx - radius * 0.35, cy - radius * 0.4, radius * 0.1, cx, cy, radius);
      sea.addColorStop(0, "#17242f"); sea.addColorStop(1, "#0b1117");
      ctx.fillStyle = sea; ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill();
      GRATICULE.forEach((line) => {
        ctx.strokeStyle = line.equator ? "rgba(255,255,255,0.07)" : "rgba(255,255,255,0.035)"; ctx.lineWidth = 1;
        ctx.beginPath(); let started = false;
        for (let i = 0; i < line.pts.length; i++) {
          const v = line.pts[i];
          projInto(v[0], v[1], v[2], 1);
          if (P[2] <= 0) { started = false; continue; }
          if (!started) { ctx.moveTo(P[0], P[1]); started = true; } else ctx.lineTo(P[0], P[1]);
        }
        ctx.stroke();
      });

      drawLand(now);
      // Limb shading over sea and land alike gives the sphere its depth.
      const limb = ctx.createRadialGradient(cx - radius * 0.2, cy - radius * 0.25, radius * 0.45, cx, cy, radius);
      limb.addColorStop(0, "rgba(0,0,0,0)"); limb.addColorStop(0.75, "rgba(0,0,0,0.12)"); limb.addColorStop(1, "rgba(0,0,0,0.42)");
      ctx.fillStyle = limb; ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill();

      // Trade network. Every active lane is drawn at every zoom: thin and volume-weighted
      // when far, full width and dash pattern when near.
      laneVolumeMax = 1;
      state.routesSea.concat(state.routesAir, state.routesRail).forEach((route) => { laneVolumeMax = Math.max(laneVolumeMax, Number(route.volume) || 0); });
      const blend = nearBlend();
      if (show.sea) state.routesSea.forEach((route) => drawRoute(route, "rgba(195,194,183,ALPHA)", 1, blend > 0.5 ? [3, 4] : null));
      if (show.air) state.routesAir.forEach((route) => drawRoute(route, "rgba(255,255,255,ALPHA)", 1.07, [1, 4]));
      if (show.rail) state.routesRail.forEach((route) => drawRoute(route, "rgba(137,135,129,ALPHA)", 1, [2, 3]));
      drawBlocLinks();
      state.renderAssets.forEach((asset) => {
        if (asset.type === "ship" && show.sea) drawMover(asset, 1, "#c3c2b7", dt);
        else if (asset.type === "plane" && show.air) drawMover(asset, 1.07, "#ffffff", dt);
        else if (asset.type === "train" && show.rail) drawMover(asset, 1, "#898781", dt);
      });

      drawConflict(now);
      drawCapitals();
      if (show.satellites) state.renderAssets.forEach((asset) => {
        if (asset.type === "sat") drawSatelliteNetwork(asset);
      });

      state.pings = state.pings.filter((ping) => now - ping.t0 < 2.2);
      state.pings.forEach((ping) => {
        const p = proj(ping.ll[0], ping.ll[1], 1);
        if (p[2] <= 0) return;
        const age = (now - ping.t0) / 2.2;
        ctx.strokeStyle = ping.color; ctx.globalAlpha = (1 - age) * 0.9;
        ctx.lineWidth = ping.big ? 2 : 1.4; ctx.beginPath();
        ctx.arc(p[0], p[1], radius * (0.02 + (ping.big ? 0.16 : 0.1) * age), 0, Math.PI * 2); ctx.stroke();
      });
      ctx.globalAlpha = 1; ctx.strokeStyle = "rgba(255,255,255,0.12)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.stroke();
      drawLabels(now);
      drawTooltip();
    }

    // ---------------------------------------------------------------- picking
    // Movers are small when far, so the pick radius grows with zoom (8px far, 14px near);
    // otherwise a passing mover would steal hovers from the nation underneath.
    function pickAsset(px, py) {
      const reach = 8 + 6 * nearBlend();
      let best = null, bestDistance = reach * reach;
      state.renderAssets.forEach((asset) => {
        if (!asset.screen) return;
        const dx = asset.screen[0] - px, dy = asset.screen[1] - py;
        const distance = dx * dx + dy * dy;
        if (distance < bestDistance) { bestDistance = distance; best = asset; }
      });
      return best;
    }
    function pointSegmentDistanceSquared(px, py, ax, ay, bx, by) {
      const dx = bx - ax, dy = by - ay;
      const lengthSquared = dx * dx + dy * dy;
      const t = lengthSquared > 0 ? clamp(((px - ax) * dx + (py - ay) * dy) / lengthSquared, 0, 1) : 0;
      const x = ax + dx * t, y = ay + dy * t;
      return (px - x) * (px - x) + (py - y) * (py - y);
    }

    function pickRoute(px, py) {
      let best = null, bestDistance = Infinity;
      const show = layers();
      const routes = [];
      if (show.sea) routes.push.apply(routes, state.routesSea);
      if (show.air) routes.push.apply(routes, state.routesAir);
      if (show.rail) routes.push.apply(routes, state.routesRail);
      routes.forEach((route) => {
        const threshold = 7 + routeWidth(route) / 2;
        const segments = route.screenSegments || [];
        for (let i = 0; i + 3 < segments.length; i += 4) {
          const distance = pointSegmentDistanceSquared(px, py, segments[i], segments[i + 1], segments[i + 2], segments[i + 3]);
          if (distance <= threshold * threshold && distance < bestDistance) {
            bestDistance = distance;
            best = route;
          }
        }
      });
      return best;
    }

    // Country hit test against the drawn shapes: nearest territory marker (its Voronoi cell),
    // then inside that marker's coast, with a few pixels of forgiveness.
    function pickCountry(px, py) {
      const ll = invProject(px, py);
      if (!ll) return null;
      ensureLand();
      const vector = toVec(ll[0], ll[1]);
      let best = null, bestDot = -2;
      land.sites.forEach((site) => {
        const d = dot3(vector, site.v);
        if (d > bestDot) { bestDot = d; best = site; }
      });
      if (!best) return null;
      const distance = Math.acos(clamp(bestDot, -1, 1));
      const theta = Math.atan2(dot3(vector, best.north), dot3(vector, best.east));
      const f = ((theta / (Math.PI * 2)) * COAST_STEPS + COAST_STEPS) % COAST_STEPS;
      const k0 = Math.floor(f) % COAST_STEPS, k1 = (k0 + 1) % COAST_STEPS, w = f - Math.floor(f);
      const coastRadius = best.radii[k0] * (1 - w) + best.radii[k1] * w;
      const forgiveness = 6 / Math.max(1, radius);
      return distance <= coastRadius + forgiveness ? best.owner : null;
    }

    function pickAny(px, py) {
      const asset = pickAsset(px, py);
      if (asset) return { kind: "asset", id: asset.id };
      const route = pickRoute(px, py);
      if (route) return { kind: "route", id: route.id };
      const country = pickCountry(px, py);
      if (country) return { kind: "country", id: country };
      return null;
    }

    let dragging = false, moved = false, lastX = 0, lastY = 0;
    canvas.addEventListener("pointerdown", (event) => {
      dragging = true; moved = false; lastX = event.clientX; lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId); state.lastInteract = performance.now() / 1000; state.focusTarget = null;
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!dragging) {
        const rect = canvas.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
        const hit = pickAny(px, py);
        state.hover = hit ? { kind: hit.kind, id: hit.id, x: px, y: py } : null;
        canvas.style.cursor = hit && hit.kind !== "country" ? "pointer" : (hit ? "pointer" : "grab");
        return;
      }
      const dx = event.clientX - lastX, dy = event.clientY - lastY;
      if (Math.abs(dx) + Math.abs(dy) > 2) { moved = true; state.hover = null; }
      lastX = event.clientX; lastY = event.clientY;
      state.view.lon -= dx * 0.28 / state.zoom; state.view.lat += dy * 0.28 / state.zoom;
      state.lastInteract = performance.now() / 1000;
    });
    canvas.addEventListener("pointerleave", () => { if (!dragging) state.hover = null; });
    canvas.addEventListener("pointerup", (event) => {
      dragging = false; if (moved) return;
      const rect = canvas.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
      const asset = pickAsset(px, py);
      if (asset) { onSelectAsset(asset.id); return; }
      const route = pickRoute(px, py);
      if (route) { onSelectRoute(route.id); return; }
      const country = pickCountry(px, py); if (country) onSelect(country);
    });
    canvas.addEventListener("dblclick", (event) => {
      const rect = canvas.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
      const asset = pickAsset(px, py);
      if (asset) { onSelectAsset(asset.id); return; }
      const route = pickRoute(px, py);
      if (route) { onSelectRoute(route.id); return; }
      const country = pickCountry(px, py); if (country) onDetail(country);
    });
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault(); state.zoomTarget = clamp(state.zoomTarget * Math.exp(-event.deltaY * 0.0011), 0.85, 5);
      state.lastInteract = performance.now() / 1000; onZoom(state.zoomTarget, layers());
    }, { passive: false });

    let previous = performance.now() / 1000, alive = true;
    function frame() {
      if (!alive) return;
      const now = performance.now() / 1000, dt = Math.min(0.1, now - previous); previous = now;
      draw(now, dt); requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);

    return {
      addCountry: addCountry,
      setWorldObjects: setWorldObjects,
      setRunning: function (running, tps) {
        state.running = !!running;
        if (Number(tps) > 0) state.tps = Number(tps);
        if (!state.running) {
          state.displayWorldTick = state.worldTick;
          state.orbitTargetTick = state.worldTick;
          state.orbitRate = 0;
          state.assets.forEach((asset) => {
            if (asset.shipment) asset.displayProgress = asset.progress;
          });
        }
      },
      setWars: function (wars) { state.wars = wars || []; labels.dirty = true; },
      noteEvent: noteEvent,
      setSelected: function (code) { state.selected = code; labels.dirty = true; },
      setSelectedAsset: function (id) {
        state.selectedAsset = id;
        rebuildAssetCollections();
      },
      setSelectedRoute: function (id) { state.selectedRoute = id; },
      ping: function (code, color) {
        const country = state.countries[code];
        if (country && country.center) state.pings.push({ ll: [country.center[0] * D2R, country.center[1] * D2R], color: color, t0: performance.now() / 1000 });
      },
      focus: function (code) {
        const country = state.countries[code]; if (country && country.center) state.focusTarget = country.center.slice();
      },
      getAsset: function (id) { return assetInfo(assetById(id)); },
      getRoute: function (id) { return routeInfo(state.routeIndex[id]); },
      applyAsset: applyAsset,
      getZoom: function () { return state.zoomTarget; },
      getLayers: layers,
      has: function (code) { return !!state.countries[code]; },
      removeCountry: function (code) {
        delete state.countries[code]; state.order = state.order.filter((item) => item !== code);
        state.landDirty = true;
        if (!state.authoritative) regenRoutes();
      },
      destroy: function () { alive = false; }
    };
  }

  window.MeddlerGlobe = { create: create };
})();
