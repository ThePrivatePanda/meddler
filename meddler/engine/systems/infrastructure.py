"""InfrastructureSystem: maintenance funding, condition degradation, recovery, and
(M7.2) failure-threshold rolls. PROPOSAL §6.1 slot 5, §6.7.2, §6.7.3.

Per-class rate constants (base cost, degradation, instability degradation, recovery)
are named but never numerically specced per class in PROPOSAL -- see config.py's
"InfrastructureSystem" block and docs/design-decisions.md.

Maintenance spending is transferred treasury -> corporates (paying local upkeep
contractors), not burned, keeping money circulating within the tracked pools rather
than vanishing -- consistent with how ProductionSystem/FiscalSystem already treat
corporates as the sector real economic activity flows through.

M14 replaces the flat per-asset maintenance cost with `assets.upkeep_cost` (base_gdp-anchored
-- see that helper and config.INFRA_UPKEEP_GDP_SHARE for the measurements that forced it).
The consequence is the whole point of M14: upkeep is now comparable to tax revenue, revenue
follows stability, so a country in political or economic decline genuinely underfunds its
assets and their condition rots -- which the asset couplings (freight throughput, production
multiplier, trade reach, coordination) then charge it for.

M7.2 adds the failure-threshold rolls (§6.7.3): once a tick's condition math (above)
settles, each asset class below its engine/assets.py FAILURE_THRESHOLDS entry rolls
config.INFRA_FAILURE_ROLL_P with hysteresis (Country.armed, same pattern as systems/
thresholds.py) and fires the mapped EventSpec via the shared cascade path -- so
SATELLITE_FAILURE's own registered consequences (-> COMMS_BLACKOUT -> ...) chain
normally. naval_fleet carries two independent thresholds (FAILURE_THRESHOLDS +
FAILURE_THRESHOLDS_EXTRA), each with its own armed key, since it maps to two failure
kinds (NAVAL_LOSS at 0.4, PORT_CLOSURE at a lower 0.25).
"""

from __future__ import annotations

from meddler.engine import assets, cascade, config, structural
from meddler.engine.events import Event, LedgerEntry, StatDelta, record_structural_effect
from meddler.engine.ledger import Ledger, apply_to_world, round_money
from meddler.engine.model import Country, World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_infra_condition


def _roll_failure(
    world: World, rng: Rng, country: Country, asset_class: str, threshold: float, kind: str
) -> Event | None:
    """One hysteresis-gated failure roll for one (asset_class, threshold) pair. Returns
    the fired Event, or None if not armed/not triggered/the roll missed."""
    key = f"infra_failure:{asset_class}:{kind}"
    condition = getattr(country.infrastructure, asset_class).condition
    armed = country.armed
    if armed.get(key, False):
        if condition > threshold + config.INFRA_FAILURE_RECOVERY_MARGIN:
            armed[key] = False
        return None
    if condition >= threshold:
        return None
    if not rng.roll(config.INFRA_FAILURE_ROLL_P):
        return None
    armed[key] = True
    return cascade.emit_event(
        world,
        rng,
        kind=kind,
        primary=country.code,
        secondary=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"asset_class": asset_class, "condition": condition},
    )


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in world.living_countries():
        maintenance_cost = assets.upkeep_cost(country)

        ledger: list[LedgerEntry] = []
        if maintenance_cost > 0:
            treasury_available = country.pools.get("treasury", 0)
            affordable = min(
                maintenance_cost, treasury_available * config.INFRA_MAINTENANCE_SHARE_CAP
            )
            # Clamped: treasury can go NEGATIVE (trade/logistics deliberately let a poor
            # country import into debt), and an unclamped negative ratio would make the
            # degradation term -(1 - ratio) * RATE scale with the DEBT -- at ratio -5 that is
            # -0.085/tick, 8.5x the documented floor. Not currently reachable (import spend is
            # tiny), but it becomes reachable the moment trade prices are scaled up.
            maintenance_ratio = max(0.0, min(1.0, affordable / maintenance_cost))
            paid = round_money(affordable)
            if paid > 0:
                Ledger.transfer(
                    ledger,
                    f"{country.code}.treasury",
                    f"{country.code}.corporates",
                    paid,
                    country.code,
                )
        else:
            maintenance_ratio = 1.0
            paid = 0

        instability_term = max(0.0, (50 - country.stability) / 50)

        stat_deltas: list[StatDelta] = []
        for cls in config.INFRA_ASSET_CLASSES:
            asset = getattr(country.infrastructure, cls)
            delta = 0.0
            if maintenance_ratio < 1.0:
                delta -= (1 - maintenance_ratio) * config.INFRA_DEGRADATION_RATE
            delta -= instability_term * config.INFRA_INSTABILITY_DEGRADATION_RATE
            delta += maintenance_ratio * config.INFRA_RECOVERY_RATE
            new_condition = max(0.0, min(1.0, asset.condition + delta))
            actual_delta = new_condition - asset.condition
            apply_infra_condition(world, stat_deltas, country.code, cls, actual_delta)

        event = world.log.append(
            tick=world.tick,
            kind="INFRASTRUCTURE_MAINTENANCE",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={
                "maintenance_cost": maintenance_cost,
                "paid": paid,
                "maintenance_ratio": maintenance_ratio,
            },
            ledger=tuple(ledger),
            stat_deltas=tuple(stat_deltas),
            severity=0,
        )
        for entry in event.ledger:
            apply_to_world(world, entry)
        events.append(event)

        # M7.2: failure-threshold rolls, read against THIS tick's just-updated
        # condition (sorted for determinism, matching every other system's iteration).
        for cls, (threshold, kind) in sorted(assets.FAILURE_THRESHOLDS.items()):
            failure = _roll_failure(world, rng, country, cls, threshold, kind)
            if failure is not None:
                events.append(failure)
        for cls, (threshold, kind) in sorted(assets.FAILURE_THRESHOLDS_EXTRA.items()):
            failure = _roll_failure(world, rng, country, cls, threshold, kind)
            if failure is not None:
                events.append(failure)
    return events


# --- God-mode infrastructure interventions -------------------------------------------------
#
# These interventions name a specific asset class ("destroy a satellite", "blockade the
# ports", "cut the grid"), which a spec's uniform `infra_all` delta cannot express. Each
# handler mutates the named class, records every realised change on the payload after
# clamping (the war._apply_strike pattern), and `_replay_infra_intervention` re-applies
# exactly those numbers without RNG. Failure kinds the intervention already delivered as
# its own consequence is marked armed, so the organic failure roll does not announce the
# same outage a second time -- that one kind only, never every threshold the class crosses.

# kind -> (asset class to disable, the failure kind the spec's own ConsequenceRule fires).
_INTERVENTION_CAPS: dict[str, tuple[str, str]] = {
    "INTERVENE_NAVAL_BLOCKADE": ("naval_fleet", "PORT_CLOSURE"),
    "INTERVENE_BLACKOUT": ("communications", "COMMS_BLACKOUT"),
    "INTERVENE_POWER_GRID_FAILURE": ("power_grid", "POWER_OUTAGE"),
}


def _failure_keys(asset_class: str, condition: float, delivered: str) -> list[str]:
    """The armed key for the one outage this intervention has already announced, if the
    capped condition would have tripped it.

    Only that one. A blockade drops naval_fleet below both of its thresholds, but it
    announces PORT_CLOSURE alone; arming NAVAL_LOSS as well would silence a wrecked fleet's
    losses until its condition recovered -- suppressing news nobody ever heard.
    """
    for table in (assets.FAILURE_THRESHOLDS, assets.FAILURE_THRESHOLDS_EXTRA):
        entry = table.get(asset_class)
        if entry is not None and entry[1] == delivered and condition < entry[0]:
            return [f"infra_failure:{asset_class}:{entry[1]}"]
    return []


def _set_condition(event: Event, country: Country, asset_class: str, new: float) -> None:
    asset = assets.get_asset(country.infrastructure, asset_class)
    before = asset.condition
    after = max(0.0, min(1.0, new))
    asset.condition = after
    event.payload[f"infra_delta_{asset_class}"] = after - before
    record_structural_effect(
        event,
        target=country.code,
        metric=f"infra:{asset_class}",
        before=before,
        after=after,
        delta=after - before,
    )


def _arm(event: Event, country: Country, keys: list[str]) -> None:
    for key in keys:
        country.armed[key] = True
    if keys:
        event.payload["armed_keys"] = ",".join(keys)


def _cap_class(world: World, rng: Rng, event: Event) -> None:
    """Knock one asset class down to the disabled condition. The blockade targets the
    SECOND country (the blockaded one); blackout and grid failure target the first."""
    asset_class, delivered = _INTERVENTION_CAPS[event.kind]
    code = event.country2 if event.kind == "INTERVENE_NAVAL_BLOCKADE" else event.country
    if code is None:
        return
    country = world.country(code)
    event.payload["infra_target"] = code
    current = assets.get_asset(country.infrastructure, asset_class).condition
    capped = min(current, config.INTERVENTION_DISABLED_CONDITION)
    _set_condition(event, country, asset_class, capped)
    _arm(event, country, _failure_keys(asset_class, capped, delivered))


def _destroy_satellite(world: World, rng: Rng, event: Event) -> None:
    """One satellite is removed from the constellation; the rest keep their condition."""
    if event.country is None:
        return
    country = world.country(event.country)
    asset = country.infrastructure.satellites
    event.payload["infra_target"] = country.code
    removed = 1 if asset.count > 0 else 0
    asset.count -= removed
    event.payload["satellites_count_delta"] = -removed
    record_structural_effect(
        event,
        target=country.code,
        metric="infra:satellites:count",
        before=asset.count + removed,
        after=asset.count,
        delta=-removed,
    )


def _restore_infrastructure(world: World, rng: Rng, event: Event) -> None:
    """Every asset class is repaired to at least the restored condition."""
    if event.country is None:
        return
    country = world.country(event.country)
    event.payload["infra_target"] = country.code
    for asset_class in assets.all_asset_classes():
        current = assets.get_asset(country.infrastructure, asset_class).condition
        _set_condition(event, country, asset_class, max(current, config.INTERVENTION_RESTORED_CONDITION))


def _replay_infra_intervention(world: World, event: Event) -> None:
    code = event.payload.get("infra_target")
    if not isinstance(code, str):
        return
    country = world.country(code)
    for asset_class in assets.all_asset_classes():
        delta = event.payload.get(f"infra_delta_{asset_class}")
        if isinstance(delta, (int, float)):
            assets.get_asset(country.infrastructure, asset_class).condition += float(delta)
    count_delta = event.payload.get("satellites_count_delta")
    if isinstance(count_delta, (int, float)):
        country.infrastructure.satellites.count += int(count_delta)
    armed = event.payload.get("armed_keys")
    if isinstance(armed, str) and armed:
        for key in armed.split(","):
            country.armed[key] = True


for _kind in _INTERVENTION_CAPS:
    structural.register_structural(_kind, _cap_class)
structural.register_structural("INTERVENE_DESTROY_SATELLITE", _destroy_satellite)
structural.register_structural("INTERVENE_INFRASTRUCTURE_BOOST", _restore_infrastructure)
for _kind in (*_INTERVENTION_CAPS, "INTERVENE_DESTROY_SATELLITE", "INTERVENE_INFRASTRUCTURE_BOOST"):
    structural.register_replay(_kind, _replay_infra_intervention)
