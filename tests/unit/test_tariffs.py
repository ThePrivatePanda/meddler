"""Real tariff policy and economically material trade settlement regressions."""

import pytest

from meddler.engine import cascade, commodities, tariffs
from meddler.engine.model import Bloc, TariffPolicy, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import logistics, tariffs as tariff_system, trade
from meddler.engine.timeline import Timeline
from meddler.engine.worldgen import generate_world


def _world(seed: int = 5):
    world = generate_world(seed, WorldSettings(starting_country_count=2, snapshot_interval=10))
    world.relations = {}
    world.blocs = []
    world.embargoes = []
    world.tariffs = []
    world.shipments = []
    for country in world.countries:
        country.at_war_with = []
        for name in commodities.ORDER:
            country.commodity_output[name] = country.commodity_need[name]
            country.commodity_stock[name] = country.commodity_need[name] * 30.0
        for asset_class in ("naval_fleet", "rail_network", "air_fleet"):
            asset = getattr(country.infrastructure, asset_class)
            asset.count = 1000
            asset.condition = 1.0
    return world


def _countries(world):
    return sorted(world.countries, key=lambda country: country.code)


def _set_gap(importer, exporter, commodity: str, quantity: float = 10.0) -> None:
    importer.commodity_output[commodity] = importer.commodity_need[commodity] - quantity
    exporter.commodity_output[commodity] = exporter.commodity_need[commodity] + quantity


def _currency_total(world, code: str) -> int:
    return sum(world.country(code).pools.values()) + world.fx_pools.get(f"fx:{code}", 0)


def test_policy_specificity_and_intra_bloc_exemption():
    world = _world()
    importer, exporter = _countries(world)
    world.tariffs = [
        TariffPolicy(importer.code, "*", "*", 0.05, 0, 0),
        TariffPolicy(importer.code, "*", "food", 0.10, 0, 1),
        TariffPolicy(importer.code, exporter.code, "*", 0.20, 0, 2),
        TariffPolicy(importer.code, exporter.code, "food", 0.30, 0, 3),
    ]

    assert tariffs.effective_rate(world, importer.code, exporter.code, "food") == 0.30
    assert tariffs.effective_rate(world, importer.code, exporter.code, "energy") == 0.20

    world.blocs = [
        Bloc(id="BLOC1", members=sorted([importer.code, exporter.code]), formed_at_tick=0)
    ]
    assert tariffs.effective_rate(world, importer.code, exporter.code, "food") == 0.0


@pytest.mark.parametrize(
    ("commodity", "pool"),
    [("food", "households"), ("consumer", "households"), ("energy", "corporates")],
)
def test_tariff_duty_is_sector_funded_revenue_and_not_exporter_proceeds(commodity, pool):
    world = _world()
    importer, exporter = _countries(world)
    _set_gap(importer, exporter, commodity)
    world.tariffs = [
        TariffPolicy(importer.code, exporter.code, commodity, 0.25, 0, 0)
    ]
    pool_before = importer.pools[pool]
    treasury_before = importer.pools["treasury"]
    exporter_corporates_before = exporter.pools["corporates"]
    importer_total_before = _currency_total(world, importer.code)
    exporter_total_before = _currency_total(world, exporter.code)

    event = next(
        item
        for item in trade.run(world, Rng(1))
        if item.kind == "SHIPMENT_DISPATCHED" and item.payload["commodity"] == commodity
    )
    shipment = world.shipments[0]

    assert event.payload["buyer_pool"] == pool
    assert shipment.base_cost > 0
    assert shipment.tariff_duty == round(shipment.base_cost * 0.25)
    assert shipment.cost == shipment.base_cost + shipment.tariff_duty
    assert importer.pools[pool] == pool_before - shipment.cost
    assert importer.pools["treasury"] == treasury_before + shipment.tariff_duty
    assert world.fx_pools[f"fx:{importer.code}"] == shipment.base_cost
    assert _currency_total(world, importer.code) == importer_total_before

    world.tick = shipment.arrive_tick
    logistics.run(world, Rng(1))
    assert exporter.pools["corporates"] == exporter_corporates_before + shipment.proceeds
    assert shipment.proceeds != shipment.cost
    assert _currency_total(world, exporter.code) == exporter_total_before


def test_retaliation_has_typed_trigger_and_intrinsic_severity(monkeypatch):
    world = _world()
    importer, exporter = _countries(world)
    world.relations[(importer.code, exporter.code)] = -50.0
    source = cascade.emit_event(
        world,
        Rng(1),
        kind="TARIFF_IMPOSED",
        primary=importer.code,
        secondary=exporter.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"commodity": "food", "rate": 0.2, "reason": "protection"},
    )
    monkeypatch.setattr(Rng, "roll", lambda self, probability: True)

    events = tariff_system.run(world, Rng(2))
    retaliation = next(event for event in events if event.payload.get("reason") == "retaliation")

    assert retaliation.country == exporter.code
    assert retaliation.country2 == importer.code
    assert retaliation.parent_id is None
    assert retaliation.parent_ids == (source.id,)
    assert retaliation.causes[0].role == "trigger"
    assert retaliation.severity == source.severity == 1


def test_impose_and_repeal_replay_exact_policy_state():
    world = _world()
    importer, exporter = _countries(world)
    timeline = Timeline(seed=5, world=world, snapshots={}, rng=Rng(5))

    world.tick = 1
    imposed = cascade.emit_event(
        world,
        timeline.rng,
        kind="TARIFF_IMPOSED",
        primary=importer.code,
        secondary=exporter.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"commodity": "food", "rate": 0.3, "reason": "test"},
    )
    world.tick = 2
    cascade.emit_event(
        world,
        timeline.rng,
        kind="TARIFF_REPEALED",
        primary=importer.code,
        secondary=exporter.code,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={"commodity": "food", "reason": "test"},
    )

    at_imposition = timeline.world_at(1)
    assert tariffs.effective_rate(at_imposition, importer.code, exporter.code, "food") == 0.3
    assert at_imposition.tariffs[0].source_event_id == imposed.id
    assert timeline.world_at(2).tariffs == []
