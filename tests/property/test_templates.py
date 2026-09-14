"""Headline template coverage and copy-editing checks (§8.4).

Every narrative kind has >= 4 templates and at least one that needs only the slots every
event has, so rendering can never come up empty. Every template -- general lists, fact
variants and the chaos lines -- is formatted against the slots the renderer really builds
for a fact-rich event, and the result is proof-read mechanically: no unfilled braces, no
"None", no doubled or stray spaces, no "a" before a vowel, no exclamation marks, sane
length. Rendering must be deterministic and independent of PYTHONHASHSEED.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import meddler.engine.tickloop  # noqa: F401  -- populates EVENT_REGISTRY at import
from meddler.engine.events import Event, LedgerEntry, StatDelta
from meddler.engine.model import World, WorldSettings
from meddler.engine.registry import EVENT_REGISTRY
from meddler.engine.worldgen import generate_world
from meddler.text import headlines, names
from meddler.text.headlines import (
    AMBIENT_KINDS,
    BASE_SLOTS,
    CHAOS_TEMPLATES,
    TEMPLATES,
    VARIANTS,
    render,
    template_fields,
)

# The narratively-real kinds emitted directly by systems (thresholds.py / relations.py /
# god.py) that never pass through EVENT_REGISTRY but still need templates.
NON_REGISTRY_KINDS = {
    "FAMINE",
    "RELATION_SHIFT",
    "GOD_EDIT",
    "GOD_RELATION_SHIFT",
    "CONVOY_LOSSES",
    "SECESSION_SETTLEMENT",
}
REQUIRED_KINDS = (set(EVENT_REGISTRY) - AMBIENT_KINDS) | NON_REGISTRY_KINDS

# Longest line a feed card should ever carry, rendered with the longest names worldgen and
# the gazetteer can produce. Typical lines are well under 110.
MAX_LEN = 125

ROOT = Path(__file__).resolve().parents[2]


def _fixture_world() -> World:
    return generate_world(1337, WorldSettings(starting_country_count=3))


def _event(
    world: World,
    kind: str,
    *,
    event_id: int = 0,
    payload: dict[str, float | int | str] | None = None,
    parent_id: int | None = None,
    country2: bool = True,
    is_intervention: bool = False,
    ledger: tuple[LedgerEntry, ...] = (),
    stat_deltas: tuple[StatDelta, ...] = (),
) -> Event:
    codes = [c.code for c in world.countries]
    return Event(
        id=event_id,
        tick=420,
        kind=kind,
        country=codes[0],
        country2=codes[1] if country2 else None,
        parent_id=parent_id,
        depth=0 if parent_id is None else 1,
        is_intervention=is_intervention,
        payload=payload if payload is not None else {},
        ledger=ledger,
        stat_deltas=stat_deltas,
        severity=0,
    )


def _rich_world_and_event(kind: str) -> tuple[World, Event]:
    """A world whose log holds a same-country parent, and an event carrying every fact the
    renderer knows how to read, so every optional slot is available."""
    world = _fixture_world()
    codes = [c.code for c in world.countries]
    parent = world.log.append(
        tick=419,
        kind="DROUGHT" if kind != "DROUGHT" else "FLOOD",
        country=codes[0],
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
    )
    payload: dict[str, float | int | str] = {
        "leader": "Old Guard",
        "commodity": "energy",
        "commodities": "energy,food",
        "rate": 0.18,
        "share": 0.25,
        "ally": codes[2],
        "incumbent": "Tae De",
        "new_leader_name": "Poe Se",
        "old_leader_name": "Guu Dazo",
        "ended_wars_with": f"{codes[1]},{codes[2]}",
        "count": 3,
        "recent_total": 5,
        "asset_class": "power_grid",
        "condition": 0.28,
        "distance_km": 17680.6,
        "chaos_resolved_to": "LOCUST_SWARM",
        "new_country_name": "Free Numoania",
        "inflation": 42.0,
        "field": "stability",
        "before": 34.0,
        "after": 80.0,
    }
    event = _event(
        world,
        kind,
        event_id=len(world.log),
        payload=payload,
        parent_id=parent.id,
        is_intervention=True,
        ledger=(LedgerEntry("transfer", f"{codes[0]}.treasury", "x.households", 4_210_000, "X"),),
        stat_deltas=(StatDelta(target=codes[0], stat="population", delta=-0.042),),
    )
    # Guarantee grain_days is printable for the fixture.
    world.country(codes[0]).grain_stock = world.country(codes[0]).grain_need * 12
    return world, event


def _all_pools(kind: str) -> list[str]:
    pools = list(TEMPLATES[kind])
    for variant in VARIANTS.get(kind, {}).values():
        pools.extend(variant)
    return pools


def _proofread(line: str, where: str) -> None:
    assert "{" not in line and "}" not in line, f"{where}: unfilled slot: {line!r}"
    assert "None" not in line, f"{where}: None leaked: {line!r}"
    assert "  " not in line, f"{where}: double space: {line!r}"
    assert line == line.strip(), f"{where}: stray whitespace: {line!r}"
    assert "!" not in line, f"{where}: exclamation (voice rule A7): {line!r}"
    assert not re.search(r"\b[Aa] [AEIOUaeiou]", line), f"{where}: 'a' before vowel: {line!r}"
    assert not re.search(r"\b[Aa] (8|11|18)\b", line), f"{where}: 'a' before 'an' number: {line!r}"
    assert not re.search(r"\b[Aa]n (?!hour)[^AEIOUaeiou\d\W]", line), (
        f"{where}: 'an' + consonant: {line!r}"
    )
    assert not re.search(r"\b(\w+) \1\b", line, re.IGNORECASE), f"{where}: doubled word: {line!r}"
    assert ".." not in line and " ," not in line and " ." not in line, (
        f"{where}: punctuation: {line!r}"
    )
    assert not line[:1].islower(), f"{where}: lower-case start: {line!r}"
    assert line[-1] in ".", f"{where}: no closing full stop: {line!r}"
    assert len(line) <= MAX_LEN, f"{where}: {len(line)} chars: {line!r}"


# ---- coverage -------------------------------------------------------------------------


def test_every_required_kind_has_at_least_four_templates() -> None:
    for kind in sorted(REQUIRED_KINDS):
        assert kind in TEMPLATES, f"{kind} has no templates"
        assert len(TEMPLATES[kind]) >= 4, f"{kind} has fewer than 4 templates"


def test_no_kind_is_both_templated_and_ambient() -> None:
    assert TEMPLATES.keys() & AMBIENT_KINDS == set()


def test_every_registry_kind_is_accounted_for() -> None:
    # Every registered kind is either templated or explicitly ambient; nothing falls through.
    assert set(EVENT_REGISTRY) - TEMPLATES.keys() - AMBIENT_KINDS == set()


# Kinds a system appends straight to the log, found by reading the engine rather than by
# trusting NON_REGISTRY_KINDS to be kept up to date. REQUIRED_KINDS is derived from
# EVENT_REGISTRY, so it is blind to these by construction -- which is how CONVOY_LOSSES and
# SECESSION_SETTLEMENT each reached a merge with no template and no ambient declaration.
_DIRECT_APPEND = re.compile(r'log\.append\(\s*(?:[^()]*?)kind\s*=\s*"([A-Z_]+)"', re.S)


def _directly_appended_kinds() -> set[str]:
    engine = Path(headlines.__file__).resolve().parents[1] / "engine"
    return {
        match.group(1)
        for path in engine.rglob("*.py")
        for match in _DIRECT_APPEND.finditer(path.read_text(encoding="utf-8"))
    }


def test_every_directly_appended_kind_is_accounted_for() -> None:
    """A kind a system appends itself must still be templated or explicitly ambient.

    `test_every_registry_kind_is_accounted_for` cannot see these: they never enter
    EVENT_REGISTRY, so REQUIRED_KINDS does not contain them and nothing forces a decision
    about them. An undeclared one is silently invisible -- `adapter.event_to_json` returns
    None for any kind outside TEMPLATES -- so the event exists in the log, consumes an id,
    and never reaches a reader. This reads the engine for the truth instead of trusting the
    hand-maintained NON_REGISTRY_KINDS set to have been updated.

    A lint, not a proof: it matches literal `kind="..."` arguments only, so a kind passed
    through a variable would be missed. The `assert appended` guard fails loudly if the
    pattern ever stops matching anything at all.
    """
    appended = _directly_appended_kinds()
    assert appended, "found no log.append(kind=...) calls -- the pattern has drifted"
    undeclared = appended - TEMPLATES.keys() - AMBIENT_KINDS
    assert undeclared == set(), (
        f"kinds appended by a system with neither a template nor an ambient declaration: "
        f"{sorted(undeclared)}. Add a template, or add the kind to AMBIENT_KINDS."
    )


def test_no_extra_templates() -> None:
    assert TEMPLATES.keys() - REQUIRED_KINDS == set()


def test_variants_only_extend_templated_kinds() -> None:
    assert VARIANTS.keys() <= TEMPLATES.keys()
    for kind, variants in VARIANTS.items():
        for name, pool in variants.items():
            assert len(pool) >= 4, f"{kind}/{name} has fewer than 4 templates"


def test_every_kind_can_render_from_base_slots_alone() -> None:
    # The fallback guarantee: whatever optional facts an event lacks, one template still
    # fits. Two-target kinds always carry their second country, so it counts as base there.
    pair = BASE_SLOTS | {"foe", "foe_capital", "foes"}
    for kind, pool in TEMPLATES.items():
        spec = EVENT_REGISTRY.get(kind)
        two_target = kind in ("RELATION_SHIFT", "GOD_RELATION_SHIFT") or (
            spec is not None and spec.targets == 2
        )
        base = pair if two_target else BASE_SLOTS
        assert any(template_fields(t) <= base for t in pool), f"{kind}: no base-only line"


def test_every_template_slot_is_one_the_renderer_can_fill() -> None:
    known: set[str] = set()
    for kind in TEMPLATES:
        world, event = _rich_world_and_event(kind)
        known |= set(headlines._slots(event, world, world.log[event.parent_id or 0]))
    for kind in TEMPLATES:
        for template in _all_pools(kind) + CHAOS_TEMPLATES:
            unknown = template_fields(template) - known
            assert not unknown, f"{kind}: unknown slot(s) {unknown} in {template!r}"


# ---- proof-reading --------------------------------------------------------------------


def test_every_template_proofreads_with_real_slots() -> None:
    for kind in sorted(TEMPLATES):
        world, event = _rich_world_and_event(kind)
        slots = headlines._slots(event, world, world.log[event.parent_id or 0])
        for i, template in enumerate(_all_pools(kind) + CHAOS_TEMPLATES):
            line = headlines._finish(template.format(**slots))
            _proofread(line, f"{kind}[{i}]")


def test_longest_names_still_fit() -> None:
    # Worst case for length: the longest country, leader, town and district names.
    longest_town = max(names._TOWNS, key=len)
    longest = dict(
        country="Kaloustria",
        foe="Mebeustria",
        foes="Mebeustria and Tovaustria",
        ally="Tovaustria",
        breakaway="Upper Kaloustria",
        leader="Kau Tebaro",
        new_leader="Mie Salovo",
        old_leader="Guo Dazobe",
        incumbent="Guo Dazobe",
        ousted="Guo Dazobe",
        leader_title="Chairman",
        city=longest_town,
        capital=longest_town,
        foe_capital=longest_town,
        region=max(names.REGIONS, key=len),
        landmass="Occidenta",
    )
    for kind in sorted(TEMPLATES):
        world, event = _rich_world_and_event(kind)
        slots = headlines._slots(event, world, world.log[event.parent_id or 0])
        slots.update(longest)
        for template in _all_pools(kind) + CHAOS_TEMPLATES:
            line = headlines._finish(template.format(**slots))
            assert len(line) <= MAX_LEN, f"{kind}: {len(line)} chars: {line!r}"


def test_render_proofreads_with_bare_events() -> None:
    # Production-shaped events with no payload at all, one and two targets.
    world = _fixture_world()
    for kind in sorted(TEMPLATES):
        for event_id in range(12):
            for two in (True, False):
                event = _event(world, kind, event_id=event_id, country2=two)
                _proofread(render(event, world), f"{kind}#{event_id}")


# ---- facts ------------------------------------------------------------------------------


def test_missing_facts_never_pad_the_line() -> None:
    world = _fixture_world()
    for kind in sorted(TEMPLATES):
        for event_id in range(12):
            line = render(_event(world, kind, event_id=event_id, country2=False), world)
            assert "the nation" not in line, f"{kind}: {line!r}"
            assert "the leader" not in line, f"{kind}: {line!r}"


def test_peace_names_the_real_partners() -> None:
    world = _fixture_world()
    codes = [c.code for c in world.countries]
    for event_id in range(10):
        event = _event(
            world,
            "PEACE",
            event_id=event_id,
            country2=False,
            payload={"ended_wars_with": f"{codes[1]},{codes[2]}"},
        )
        line = render(event, world)
        if world.country(codes[1]).name in line:
            assert f"{world.country(codes[1]).name} and {world.country(codes[2]).name}" in line


def test_low_inflation_is_never_quoted() -> None:
    world = _fixture_world()
    world.countries[0].inflation = 4.0
    for event_id in range(20):
        line = render(_event(world, "INFLATION_CRISIS", event_id=event_id), world)
        assert "4%" not in line, line


def test_leader_change_uses_the_recorded_successor() -> None:
    world = _fixture_world()
    for event_id in range(12):
        event = _event(
            world,
            "LEADER_CHANGE",
            event_id=event_id,
            payload={"new_leader_name": "Poe Se", "old_leader_name": "Tae De"},
        )
        line = render(event, world)
        assert world.countries[0].leader.name not in line or "Poe Se" in line, line


def test_election_names_the_ousted_incumbent() -> None:
    world = _fixture_world()
    lines = {
        render(
            _event(
                world,
                "ELECTION",
                event_id=i,
                payload={"incumbent_changed": 1, "incumbent": "Tae De"},
            ),
            world,
        )
        for i in range(30)
    }
    assert any("Tae De" in line for line in lines)
    assert not any(world.countries[0].leader.name in line for line in lines)


def test_chaos_speaks_in_the_meddlers_voice() -> None:
    world = _fixture_world()
    event = _event(
        world,
        "LOCUST_SWARM",
        payload={"chaos_resolved_to": "LOCUST_SWARM"},
        is_intervention=True,
    )
    assert "locusts" in render(event, world)


def test_convoy_losses_count_the_ships() -> None:
    world = _fixture_world()
    lines = {
        render(
            _event(
                world,
                "CONVOY_LOSSES",
                event_id=i,
                payload={"count": 3, "recent_total": 7, "commodity": "food", "commodities": "food"},
            ),
            world,
        )
        for i in range(20)
    }
    assert any("three" in line for line in lines)
    assert all("3" not in line for line in lines)


def test_a_single_lost_convoy_reads_in_the_singular() -> None:
    # A lone sinking is reported once it has waited, so a report can speak for one ship.
    # The plural lines ("more convoys are missing than arriving") would be false for it.
    world = _fixture_world()
    for relief, variant in ((0, "single"), (1, "single_relief")):
        lines = set()
        for i in range(20):
            event = _event(
                world,
                "CONVOY_LOSSES",
                event_id=i,
                payload={"count": 1, "recent_total": 1, "commodity": "food",
                         "commodities": "food", "relief": relief},
            )
            slots = headlines._slots(event, world, None)
            allowed = {
                headlines._finish(t.format(**slots))
                for t in VARIANTS["CONVOY_LOSSES"][variant]
                if template_fields(t) <= slots.keys()
            }
            line = render(event, world)
            assert line in allowed, f"{variant}: {line!r}"
            lines.add(line)
        assert len(lines) >= 2


def test_god_edits_read_as_the_meddlers_hand() -> None:
    world = _fixture_world()
    lines = {
        render(
            _event(
                world,
                "GOD_EDIT",
                event_id=i,
                country2=False,
                payload={"field": "inflation", "before": 42.0, "after": 3.0},
                is_intervention=True,
            ),
            world,
        )
        for i in range(20)
    }
    assert any("from 42% to 3%" in line for line in lines)
    assert all("inflation" in line for line in lines)
    peace = {
        render(
            _event(
                world,
                "GOD_RELATION_SHIFT",
                event_id=i,
                payload={"before": -80.0, "after": -10.0, "ended_war": 1},
                is_intervention=True,
            ),
            world,
        )
        for i in range(20)
    }
    assert peace <= {
        headlines._finish(t.format(country=world.countries[0].name, foe=world.countries[1].name))
        for t in VARIANTS["GOD_RELATION_SHIFT"]["peace"]
    }


def test_currency_plurals() -> None:
    assert names.plural("Krona") == "Kronor"
    assert names.plural("Lira") == "Lire"
    assert names.plural("Peso") == "Pesos"


def test_gazetteer_is_stable_per_country() -> None:
    assert names.capital_for("LIE") == names.capital_for("LIE")
    assert len(set(names.country_cities("LIE"))) == 4
    assert names.city_for(7, "LIE") in names.country_cities("LIE")
    assert names.region_for(7, "LIE") in names.country_regions("LIE")


# ---- determinism ----------------------------------------------------------------------


def test_render_is_deterministic_and_spreads_across_templates() -> None:
    world = _fixture_world()
    for kind in ("UNREST", "TREASURY_DRAIN", "COUP", "POLICY_SHIFT", "CRACKDOWN"):
        lines = [render(_event(world, kind, event_id=i), world) for i in range(60)]
        again = [render(_event(world, kind, event_id=i), world) for i in range(60)]
        assert lines == again
        assert len(set(lines)) >= 4, f"{kind}: only {len(set(lines))} distinct lines"


_HASHSEED_SCRIPT = """
import meddler.engine.tickloop
from meddler.engine.events import Event
from meddler.engine.model import WorldSettings
from meddler.engine.worldgen import generate_world
from meddler.text.headlines import TEMPLATES, render
world = generate_world(7, WorldSettings(starting_country_count=3))
a, b = world.countries[0].code, world.countries[1].code
for i, kind in enumerate(sorted(TEMPLATES)):
    e = Event(id=i, tick=i, kind=kind, country=a, country2=b, parent_id=None, depth=0,
              is_intervention=False, payload={}, ledger=(), stat_deltas=(), severity=0)
    print(render(e, world))
"""


@pytest.mark.parametrize("seeds", [("0", "4242")])
def test_render_is_independent_of_python_hash_seed(seeds: tuple[str, str]) -> None:
    outputs = []
    for seed in seeds:
        result = subprocess.run(
            [sys.executable, "-c", _HASHSEED_SCRIPT],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT), "PATH": ""},
        )
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
    assert outputs[0].count("\n") == len(TEMPLATES)


def test_two_target_kinds_without_a_foe_still_read_as_prose() -> None:
    # A chaos-resolved WAR_DECLARED fires with no second country; the fallback line must
    # still be a sentence, not "Viavia: war declared."
    world = _fixture_world()
    line = render(_event(world, "STRIKE", country2=False), world)
    _proofread(line, "STRIKE/no-foe")
    assert "strike" not in line.lower()


def test_render_tolerates_missing_country() -> None:
    # render must never raise even if the country code is absent from the world.
    world = _fixture_world()
    event = _event(world, "UNREST")
    ghost = Event(
        id=1,
        tick=1,
        kind="UNREST",
        country="ZZZ",
        country2=None,
        parent_id=None,
        depth=0,
        is_intervention=False,
        payload={},
        ledger=(),
        stat_deltas=(),
        severity=2,
    )
    assert render(event, world)
    assert render(ghost, world)
