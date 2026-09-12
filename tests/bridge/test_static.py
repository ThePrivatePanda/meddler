"""M8.2a follow-up: `meddler serve` also serves the web/ frontend over HTTP on the same
port as the WebSocket, so the user visits a URL instead of opening index.html by hand.
These exercise the static-serving helpers directly (pure functions, no running server)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from meddler.bridge import server
from meddler.bridge.server import WS_PATH, _process_request, _static_response


def test_root_serves_index_html() -> None:
    resp = _static_response("/")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    assert b"<" in resp.body and len(resp.body) > 0


def test_js_served_with_js_content_type() -> None:
    resp = _static_response("/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["Content-Type"]


def test_missing_file_is_404() -> None:
    assert _static_response("/no-such-file.js").status_code == 404


def test_path_traversal_is_forbidden() -> None:
    # Must not escape web/ to reach repo files like pyproject.toml.
    assert _static_response("/../pyproject.toml").status_code == 403
    assert _static_response("/../../etc/passwd").status_code == 403


@dataclass
class _FakeRequest:
    path: str


def test_ws_path_is_not_intercepted_by_static() -> None:
    # `/ws` must return None so the WebSocket handshake proceeds; everything else is static.
    assert _process_request(None, _FakeRequest(WS_PATH)) is None  # type: ignore[arg-type]
    assert _process_request(None, _FakeRequest("/ws?foo=1")) is None  # type: ignore[arg-type]
    index = _process_request(None, _FakeRequest("/"))  # type: ignore[arg-type]
    assert index is not None and index.status_code == 200


def test_app_contains_event_impact_and_dag_inspector_contract() -> None:
    body = _static_response("/app.js").body.decode("utf-8")
    for required in (
        'cmd: "eventImpact"',
        'case "eventImpact"',
        "WHY IT HAPPENED",
        "IMMEDIATE EFFECTS",
        "DOWNSTREAM EFFECTS",
        "CUMULATIVE TOTAL · DEDUPLICATED",
        "VALID HORIZON DIFFERENCE",
        "parentIds",
    ):
        assert required in body


def test_protocol_v2_frontend_consumes_only_authoritative_real_engine_globe_objects() -> None:
    globe = _static_response("/globe.js").body.decode("utf-8")
    app = _static_response("/app.js").body.decode("utf-8")

    for required in (
        "function setWorldObjects(payload)",
        "payload.countries",
        "payload.lanes",
        "payload.shipments",
        "payload.strikes",
        "source.territories",
        "source.blocId",
        "satelliteData.count",
        "shipment.id",
        "id: shipment.id",
        "if (state.authoritative || !state.mockMode) return;",
        "if (state.authoritative || !state.mockMode) return null;",
        'event.kind !== "SHIPMENT_LOST"',
    ):
        assert required in globe

    for forbidden in ("SHIP_NAMES", "SAT_DEFS", "fakeAssetLog"):
        assert forbidden not in globe

    for required in (
        "function applyWorldObjects(payload)",
        "globe.setWorldObjects(payload)",
        "m.worldObjects",
        "m.worldObjectsB",
        "m.timelines.A.worldObjects",
        "m.timelines.B.worldObjects",
        "m.worldObject",
        "globe.noteEvent(e)",
        "Shipment id",
        "AUTHORITATIVE INVENTORY",
        "Observed engine state is read-only here",
        "S.assetInfo && !S.assetInfo.authoritative",
    ):
        assert required in app


def test_authoritative_globe_motion_and_inspector_lifecycle_contract() -> None:
    globe = _static_response("/globe.js").body.decode("utf-8")
    app = _static_response("/app.js").body.decode("utf-8")
    style = _static_response("/style.css").body.decode("utf-8")

    for required in (
        "setRunning: function (running, tps)",
        "state.worldTick",
        "targetProgress",
        "displayProgress",
        "previousAssets[shipment.id]",
        "Math.min(gap",
        "routesByLane",
        "state.assetIndex",
        "state.renderAssets",
        "new Array(8).fill(null)",
        "previous.renderSlot",
        "asset.progress <= 0.000001",
        "scheduledNextProgress",
        "progress: authoritativeProgress",
        "state.displayWorldTick",
        "state.orbitTargetTick",
        "state.orbitRate",
        "function routeVector(route, t, visualOffset)",
        'route.carrier === "air" ? 0.05',
        "asset.visualOffset",
    ):
        assert required in globe
    assert "satellitePosition(asset.operator, i, state.displayWorldTick)" in globe
    assert "satellitePosition(asset.operator, i, state.worldTick)" not in globe
    assert "satellitePosition(asset.operator, i, now)" not in globe
    assert "onSelectAsset(asset.id, true)" not in globe

    for required in (
        "function terminalShipment(info, payload)",
        "function shipmentLossEvent(shipmentId)",
        'lifecycle: "lost"',
        'lifecycle: "arrived"',
        'lifecycle: "unavailable"',
        "globe.setSelectedAsset(null)",
        "S.terminalAsset",
        "if (S.selCountry || S.selAsset || S.selRoute) renderInspector()",
        "globe.setRunning(S.running, S.tps)",
        "function selectAsset(id, fallbackInfo)",
        "Dispatch event",
        "S.eventsByTimeline[timelineId]",
        "e.country === code || e.country2 === code",
        ".slice(0, 12)",
        "countryLogisticsHTML(code)",
        "No arrival event is fabricated",
    ):
        assert required in app
    assert "if (openFull) openAssetDossier(id)" not in app

    for required in (
        ".shipment-terminal.lost",
        ".shipment-terminal.arrived",
        ".country-event.sev0",
        ".country-event.sev1",
        ".country-event.sev2",
        ".country-event.iv",
        ".logistics-grid",
    ):
        assert required in style


def test_annals_impact_view_uses_authoritative_timeline_qualified_causal_metrics() -> None:
    app = _static_response("/app.js").body.decode("utf-8")
    style = _static_response("/style.css").body.decode("utf-8")

    for required in (
        'case "annalsImpact"',
        'cmd: "annalsImpact"',
        'data-anntab="impact"',
        "function annalsImpactHTML()",
        "function requestAnnalsImpact(force)",
        "activeTimelineId()",
        "directChildren",
        "descendants",
        "generations",
        "affectedCountries",
        "recordedEffects",
        "crisisDescendants",
        "DIRECT OFFSPRING",
        "Trace whole DAG",
        "no weighted or invented impact score",
        "complete engine timeline",
    ):
        assert required in app

    for required in (
        ".impact-annals-intro",
        ".impact-leader",
        ".impact-metrics",
        ".impact-offspring",
        ".impact-leader.sev2",
        ".impact-leader.iv",
    ):
        assert required in style


def test_stable_route_rosters_and_global_trade_operations_contract() -> None:
    globe = _static_response("/globe.js").body.decode("utf-8")
    app = _static_response("/app.js").body.decode("utf-8")
    index = _static_response("/index.html").body.decode("utf-8")
    style = _static_response("/style.css").body.decode("utf-8")

    for required in (
        "onSelectRoute",
        "state.routeIndex",
        "state.selectedRoute",
        "route.screenSegments",
        "function pointSegmentDistanceSquared",
        "function pickRoute(px, py)",
        "onSelectRoute(route.id)",
        "setSelectedRoute: function (id)",
        "getRoute: function (id)",
        "distanceKm: shipment.distanceKm",
        "remainingDistanceKm: shipment.remainingDistanceKm",
        "distanceKm: asset.distanceKm, remainingDistanceKm: asset.remainingDistanceKm",
        "state.recentAssetIndex",
        "recentShipmentIds: lane.recentShipmentIds || []",
        "if (Number(lane.shipmentCount || 0) <= 0) return",
        "Number(a.dispatchEventId) - Number(b.dispatchEventId)",
        'a.lifecycle === "arrived" ? 1 : 0',
        "const shipments = active.concat(recent).sort(rosterOrder)",
        "shipments: shipments.map(assetInfo)",
    ):
        assert required in globe
    assert "a.progress - b.progress" not in globe

    for required in (
        "onSelectRoute: (id) => selectRoute(id)",
        "function selectRoute(id)",
        "function inspectorRouteHTML(route)",
        "ROUTE_PAGE_SIZE = 40",
        "route.shipments || []",
        'data-act="routeasset"',
        'data-act="routepage"',
        "STABLE SHIPMENT ROSTER",
        "SHIPMENT ROSTER · DISPATCH ORDER",
        "retentionTicksRemaining",
        "function distanceLeftText(value)",
        "distanceLeftText(shipment.remainingDistanceKm)",
        "function tradeOperationsHTML()",
        'a.status === "arrived" ? 1 : 0',
        "return active.concat(recent).sort(rosterOrder)",
        "GLOBAL SHIPMENT ROSTER · IMMUTABLE DISPATCH ORDER",
        "TRADE_PAGE_SIZE = 50",
        "arrivalsLast4Ticks",
        "Arrived · 4t",
        'data-trade-filter="carrier"',
        "data-tradeasset",
        "function refreshTradeOverlay()",
        "replacement.focus({ preventScroll: true })",
        "replacement.setSelectionRange",
        "S.tradeRowCache[row.id] = info",
        "if (selectAsset(id, fallback)) closeOverlay()",
        "refreshTradeOverlay(false)",
        "applyWorldObjects(focusedObjects)",
        "S.selCountry || S.selAsset || S.selRoute",
    ):
        assert required in app
    assert "return active.concat(recent);" not in app
    assert "const shipments = active.concat(recent);" not in globe
    assert "if (!force && focused" not in app

    for required in ('id="btnTrade"', "Trade Operations [v]", "<kbd>v</kbd> trade"):
        assert required in index

    for required in (
        ".route-roster",
        ".route-shipment",
        ".route-shipment-progress",
        ".route-shipment-meta b",
        ".route-shipment.terminal.arrived",
        ".route-shipment.terminal.lost",
        "#overlayCard.tradecard",
        ".trade-kpis",
        ".trade-filters",
        ".trade-row",
        ".trade-row.at-risk",
        "@media (max-width: 700px)",
        ".route-pager",
    ):
        assert required in style


def test_country_history_charts_support_tick_event_hover_and_causal_jump() -> None:
    app = _static_response("/app.js").body.decode("utf-8")
    style = _static_response("/style.css").body.decode("utf-8")

    for required in (
        'class="history-chart"',
        'data-chart-metric="',
        "function chartPointInterval(detail, index)",
        "function chartPointEvents(detail, index)",
        "function requestChartPointEvents(detail, index)",
        'cmd: "countryChartEvents"',
        "CHART_INTERVAL_CACHE_LIMIT",
        "EVENT_CACHE_LIMIT",
        "eventOrderByTimeline",
        'case "countryChartEvents"',
        "function showChartPoint(card, detail, rawIndex)",
        'card.querySelectorAll(".history-chart")',
        "function wireHistoryCharts(card, detail)",
        'svg.addEventListener("pointermove"',
        'event.key !== "ArrowLeft"',
        "function chartHoverHTML(detail, index)",
        "No recorded event involved this country",
        'data-chart-jump="',
        "function jumpToChartPoint(detail, index, event)",
        "requestWorldAt(tick)",
        "S.pendingChartJump",
        'selectEvent(pending.event.id, "A", pending.event)',
        "(m.chartEvents || []).forEach",
    ):
        assert required in app

    for required in (
        ".history-chart",
        ".chart-hover-line",
        ".chart-hover-dot",
        ".chart-hover-card",
        ".chart-hover-values",
        ".chart-event-list",
    ):
        assert required in style


def test_all_engine_commands_use_correlated_progress_and_contextual_loading() -> None:
    app = _static_response("/app.js").body.decode("utf-8")
    mock = _static_response("/engine.js").body.decode("utf-8")
    index = _static_response("/index.html").body.decode("utf-8")
    style = _static_response("/style.css").body.decode("utf-8")

    for required in (
        "function sendCommand(command, label, key)",
        "function completeRequest(message)",
        "function renderActivity()",
        'requestId: requestId',
        'case "trace": S.tracePending = null',
        'S.loadingLabel = "Tracing the complete causal DAG…"',
        'openOverlay("loading")',
        '"countryDetail:" + timelineId + ":" + code',
        '"annalsImpact:" + timelineId',
        '"settings:" + key',
        '"intervene"',
        'if (!S.tracePending && S.overlay === "annals"',
    ):
        assert required in app
    assert app.count("engine.send(") == 1
    assert 'id="activityIndicator"' in index
    assert 'role="status"' in index
    assert "this.requestId = cmd.requestId" in mock
    assert "msg.requestId = this.requestId" in mock
    assert "#activityIndicator" in style
    assert ".loading-state" in style
    assert ".loading-spinner" in style


def test_hot_render_paths_avoid_synchronous_forced_layout_reads() -> None:
    app = _static_response("/app.js").body.decode("utf-8")
    globe = _static_response("/globe.js").body.decode("utf-8")

    for forbidden in ("offsetHeight", "offsetWidth", "clientWidth", "clientHeight"):
        assert forbidden not in app
    assert "canvas.clientWidth" not in globe
    assert "canvas.clientHeight" not in globe
    for required in (
        "function addFeedCards(tl, events)",
        "document.createDocumentFragment()",
        "function scheduleScrub(clientX)",
        "requestAnimationFrame(() =>",
        "new ResizeObserver",
        "ribbonSize.width",
    ):
        assert required in app
    assert "state.cssWidth" in globe
    assert "new ResizeObserver" in globe


# --- web/ directory resolution (packaging) ----------------------------------------------
# A non-editable `pip install .` has no repository checkout next to the package, so the
# frontend ships inside the wheel as meddler/web. These pin the lookup order:
# $MEDDLER_WEB_DIR -> packaged copy -> checkout, with a loud error when nothing is found.


def _fake_web(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return root


def test_web_dir_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = _fake_web(tmp_path / "custom")
    monkeypatch.setenv(server.WEB_DIR_ENV, str(custom))
    assert server.resolve_web_dir() == custom.resolve()


def test_web_dir_env_override_that_is_missing_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.WEB_DIR_ENV, str(tmp_path / "nope"))
    with pytest.raises(server.WebDirNotFound, match=server.WEB_DIR_ENV):
        server.resolve_web_dir()


def test_web_dir_prefers_packaged_copy_over_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packaged = _fake_web(tmp_path / "site" / "meddler" / "web")
    checkout = _fake_web(tmp_path / "repo" / "web")
    monkeypatch.delenv(server.WEB_DIR_ENV, raising=False)
    monkeypatch.setattr(
        server, "_web_dir_candidates", lambda: [("packaged", packaged), ("checkout", checkout)]
    )
    assert server.resolve_web_dir() == packaged.resolve()


def test_web_dir_falls_back_to_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = _fake_web(tmp_path / "repo" / "web")
    monkeypatch.delenv(server.WEB_DIR_ENV, raising=False)
    monkeypatch.setattr(
        server,
        "_web_dir_candidates",
        lambda: [("packaged", tmp_path / "missing"), ("checkout", checkout)],
    )
    assert server.resolve_web_dir() == checkout.resolve()


def test_web_dir_not_found_names_every_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(server.WEB_DIR_ENV, raising=False)
    monkeypatch.setattr(
        server,
        "_web_dir_candidates",
        lambda: [("packaged", tmp_path / "a"), ("checkout", tmp_path / "b")],
    )
    with pytest.raises(server.WebDirNotFound) as info:
        server.resolve_web_dir()
    message = str(info.value)
    assert str(tmp_path / "a") in message and str(tmp_path / "b") in message
    assert server.WEB_DIR_ENV in message


def test_checkout_candidate_is_the_repository_web_dir() -> None:
    # Running from a source tree must serve the top-level web/ (what the other tests read).
    candidates = dict(server._web_dir_candidates())
    assert candidates["checkout"] == Path(__file__).resolve().parents[2] / "web"
    assert server.WEB_DIR is not None and (server.WEB_DIR / "index.html").is_file()
