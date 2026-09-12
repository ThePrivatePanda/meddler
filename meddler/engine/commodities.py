"""The commodity registry: buckets are DATA, not code (v2 spec §3/§12).

Adding a seventh bucket should mean adding a row to config.COMMODITY_ORDER and its
companion tables, not writing a new system. Everything downstream (production, trade,
logistics) iterates this registry.

LAYERING: leaf module -- imports config ONLY. Never model (Country would be a cycle:
model does not import this today, but a later milestone may).
"""

from __future__ import annotations

from dataclasses import dataclass

from meddler.engine import config


@dataclass(frozen=True)
class Commodity:
    """One causal bucket.

    extractive: output comes from TERRITORY (an M9 endowment) rather than from industry.
    This is the distinction that makes conquest economically motivating in M15 -- you can
    capture a country's oil, but you cannot capture its ability to manufacture.
    """

    name: str
    extractive: bool
    endowment_key: str | None  # the config.EXTRACTIVE_COMMODITIES key, extractive only
    inputs: dict[str, float]  # units of input per unit of output; produced only


# food's endowment is "arable" -- the endowment names describe TERRITORY (what the land
# has), the commodity names describe GOODS (what comes off it). They are deliberately not
# the same vocabulary: you have arable land, you produce food.
_ENDOWMENT_KEYS = {
    "food": "arable",
    "energy": "energy",
    "raw_materials": "raw_materials",
}

COMMODITIES: dict[str, Commodity] = {
    name: Commodity(
        name=name,
        extractive=name in _ENDOWMENT_KEYS,
        endowment_key=_ENDOWMENT_KEYS.get(name),
        inputs=dict(config.COMMODITY_INPUTS.get(name, {})),
    )
    for name in config.COMMODITY_ORDER
}

ORDER: tuple[str, ...] = config.COMMODITY_ORDER


def commodity(name: str) -> Commodity:
    """Look up a bucket. Raises KeyError on an unknown name -- a typo'd commodity string
    must fail loudly, not silently produce zero output forever."""
    return COMMODITIES[name]


def extractive_names() -> tuple[str, ...]:
    return tuple(n for n in ORDER if COMMODITIES[n].extractive)


def produced_names() -> tuple[str, ...]:
    return tuple(n for n in ORDER if not COMMODITIES[n].extractive)


def genesis_output(
    name: str,
    *,
    population: float,
    endowments: dict[str, float],
    gdp_tick: float,
    innovation_mult: float,
    education: float,
) -> float:
    """Unconstrained output for one commodity (v2 spec §3's table).

    THE single definition of output. Called by worldgen at genesis AND by
    systems/commodity_production.py every tick -- duplicating these formulas in both would
    let genesis and tick 1 drift apart silently.

    It lives HERE, in the leaf registry, rather than in the production system, precisely so
    both callers can import it without a cycle (worldgen -> system -> model -> ... ). Do not
    move it into the system and reach back with a function-local import.

    Ignores input availability: that is a side-effecting debit the CALLER applies, and this
    must stay a pure function. Consequence: genesis output is unconstrained while tick-1
    output is constrained. That is intentional -- see the plan's "Measured baseline" note.
    """
    spec = commodity(name)
    if spec.extractive:
        assert spec.endowment_key is not None
        return (
            population
            * config.EXTRACTIVE_OUTPUT_PER_CAPITA[name]
            * endowments[spec.endowment_key]
        )
    if name == "manufactured":
        return gdp_tick * config.PRODUCED_GDP_COEFFICIENT[name] * innovation_mult
    if name == "consumer":
        return (
            gdp_tick * config.PRODUCED_GDP_COEFFICIENT[name]
            + population * config.CONSUMER_LABOUR_PER_CAPITA
        )
    if name == "high_tech":
        return (
            gdp_tick
            * config.PRODUCED_GDP_COEFFICIENT[name]
            * innovation_mult
            * (education / config.HIGH_TECH_EDUCATION_REFERENCE)
        )
    raise ValueError(f"no output formula for produced commodity {name!r}")
