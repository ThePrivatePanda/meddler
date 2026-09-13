"""Systems iterate `World.living_countries()`, never the raw roster.

An annexed or dissolved country stays in `World.countries`: replay, diffs, snapshots and the
client's stable roster indices all need the whole record. Guards against simulating it were
added one system at a time, each after its omission had reached the feed, and a survey of
the engine still found production minting GDP and fiscal collecting tax for annexed
countries every tick. The accessor makes the safe iteration the default; the source scan
below makes the unsafe one something a new system has to be allowlisted for. It sees
attribute reads only: `getattr(world, "countries")` would pass it unnoticed.

ONE TEST PER SYSTEM, for the reason tests/unit/test_annexed_not_simulated.py gives: an
aggregate test passes with most of the systems fixed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from meddler.engine import tickloop  # noqa: F401 -- import populates EVENT_REGISTRY
from meddler.engine.model import CountryStatus, World, WorldSettings
from meddler.engine.rng import Rng
from meddler.engine.systems import commodity_production, fiscal, fx, production, secession
from meddler.engine.worldgen import generate_world

ENGINE = Path(__file__).resolve().parents[2] / "meddler" / "engine"

#: Every read of `.countries` the engine may make, by (file, enclosing function), with the
#: reason that site needs the countries that have left the world as well as those in it.
ALLOWED_RAW_READS: dict[tuple[str, str], str] = {
    ("model.py", "country"): "lookup by code; replay and history resolve departed countries",
    ("model.py", "living_countries"): "the accessor itself",
    ("diff.py", "diff"): "compares two worlds' full records, including who left",
    ("diff.py", "impact_diff"): "compares two worlds' full records, including who left",
    ("systems/secession.py", "_choose_homeland"): "a departed country's land is still taken",
    ("systems/secession.py", "_secede"): "roster cap and name/code uniqueness span every country",
    ("systems/secession.py", "_apply"): "appends the new country to the roster",
    ("timeline.py", "_aux_state"): "snapshot state covers every country so a restore is exact",
    ("timeline.py", "_apply_aux_state"): "restores that snapshot state",
    ("timeline.py", "_record_stats"): "display history keeps a departed country's last values",
    ("timeline.py", "restart"): "trims the roster to the countries born by the restart tick",
}


def _raw_reads() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(ENGINE.rglob("*.py")):
        rel = path.relative_to(ENGINE).as_posix()

        def visit(node: ast.AST, function: str, rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                name = (
                    child.name
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else function
                )
                if isinstance(child, ast.Attribute) and child.attr == "countries":
                    found.add((rel, name))
                visit(child, name)

        visit(ast.parse(path.read_text()), "<module>")
    return found


def test_no_engine_code_reads_the_raw_roster_outside_the_allowlist() -> None:
    unexpected = sorted(_raw_reads() - ALLOWED_RAW_READS.keys())
    assert unexpected == [], (
        "iterate world.living_countries(); a site that genuinely needs departed countries "
        "belongs in ALLOWED_RAW_READS with its reason"
    )


def test_the_allowlist_names_only_sites_that_still_exist() -> None:
    assert sorted(ALLOWED_RAW_READS.keys() - _raw_reads()) == []


def _world_with_an_annexed_country() -> tuple[World, str]:
    world = generate_world(1337, WorldSettings(starting_country_count=4))
    gone = world.countries[0]
    gone.status = CountryStatus.ANNEXED
    gone.population = 0.0
    for name in list(gone.commodity_need):
        gone.commodity_need[name] = 0.0
        gone.commodity_output[name] = 0.0
        gone.commodity_stock[name] = 0.0
    return world, gone.code


def _emitted_for(events: list, code: str) -> list:
    return [e for e in events if e.country == code or e.country2 == code]


def test_living_countries_leaves_out_only_the_annexed_and_dissolved() -> None:
    world = generate_world(1337, WorldSettings(starting_country_count=5))
    annexed, dissolved, occupied, occupier, _ = world.countries
    annexed.status = CountryStatus.ANNEXED
    dissolved.status = CountryStatus.DISSOLVED
    occupied.status = CountryStatus.OCCUPIED
    occupied.occupied_by = occupier.code

    codes = [c.code for c in world.living_countries()]

    assert set(codes) == {c.code for c in world.countries} - {annexed.code, dissolved.code}
    assert codes == sorted(codes), "systems depend on a fixed, code-sorted order"


def test_production_mints_nothing_for_an_annexed_country() -> None:
    # Annexation zeroes population but not base_gdp, so the old loop kept growing the
    # ledger of a nation with no people, every tick, with no headline to show it.
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    assert gone.base_gdp > 0
    before = gone.gdp_tick
    assert _emitted_for(production.run(world, Rng(1)), code) == []
    assert gone.gdp_tick == before


def test_fiscal_collects_no_tax_from_an_annexed_country() -> None:
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    gone.gdp_tick = 1_000_000.0
    gone.tax_rate = 0.3
    pools_before = dict(gone.pools)
    assert _emitted_for(fiscal.run(world, Rng(1)), code) == []
    assert gone.pools == pools_before


def test_fx_does_not_move_an_annexed_currency() -> None:
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    gone.inflation = 50.0  # enough drift that a simulated currency would move
    before = gone.exchange_rate
    assert _emitted_for(fx.run(world, Rng(1)), code) == []
    assert gone.exchange_rate == before


def test_commodity_production_leaves_an_annexed_country_alone() -> None:
    world, code = _world_with_an_annexed_country()
    gone = world.country(code)
    gone.commodity_output["food"] = 5.0
    assert _emitted_for(commodity_production.run(world, Rng(1)), code) == []
    assert gone.commodity_output["food"] == 5.0


def test_secession_counts_only_living_countries() -> None:
    world, _code = _world_with_an_annexed_country()
    assert secession._live_count(world) == len(world.countries) - 1
