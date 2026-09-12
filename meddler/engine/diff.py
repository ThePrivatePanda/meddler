"""Per-country world diff for the ΔWORLD strip. PROPOSAL §3.5, docs/frontend-contract.md
(`frame.diff`: `{CODE:{stability:[a,b],inflation:[a,b],gdp:[a,b]}}`).

Compares two worlds (typically prime's world at a tick vs a fork's world at the same
tick, §4.4) on the three headline stats the §3.5 mock UI's ΔWORLD strip shows
(stability/inflation/gdp) for every country code present in BOTH worlds. Only countries
where at least one of the three actually differs appear in the result -- M5.2's own
acceptance bar, "fork with no intervention ⇒ zero diff at every tick," means
`diff(a, b) == {}` in that case, not a dict full of `[x, x]` pairs.
"""

from __future__ import annotations

from typing import Any

from meddler.engine.model import World

# (Country attribute name, contract-facing key). "gdp" per the contract; the engine's own
# live figure is gdp_tick (§5.1/§6.2) -- this is the one place that translation happens,
# since M5.2 ties this function directly to the contract's own diff shape.
_DIFF_STATS = (("stability", "stability"), ("inflation", "inflation"), ("gdp_tick", "gdp"))


def diff(world_a: World, world_b: World) -> dict[str, Any]:
    """Per-country `{code: {"stability": [a, b], "inflation": [a, b], "gdp": [a, b]}}`
    for every country present in both worlds where at least one of the three differs."""
    countries_b = {c.code: c for c in world_b.countries}
    result: dict[str, Any] = {}
    for country_a in world_a.countries:
        country_b = countries_b.get(country_a.code)
        if country_b is None:
            continue
        entry: dict[str, list[float]] = {}
        for attr, contract_key in _DIFF_STATS:
            a_value = getattr(country_a, attr)
            b_value = getattr(country_b, attr)
            if a_value != b_value:
                entry[contract_key] = [a_value, b_value]
        if entry:
            result[country_a.code] = entry
    return result


def impact_diff(world_a: World, world_b: World) -> list[dict[str, Any]]:
    """Normalized fork-minus-prime differences for event horizon inspection."""
    from meddler.engine import assets, config

    countries_b = {country.code: country for country in world_b.countries}
    rows: list[dict[str, Any]] = []

    def add(code: str, metric: str, before: float | int, after: float | int, unit: str = "") -> None:
        if before != after:
            rows.append(
                {
                    "target": code,
                    "metric": metric,
                    "before": before,
                    "after": after,
                    "delta": after - before,
                    "unit": unit,
                }
            )

    for country_a in sorted(world_a.countries, key=lambda country: country.code):
        country_b = countries_b.get(country_a.code)
        if country_b is None:
            continue
        for attr in ("population", "stability", "inflation", "gdp_tick"):
            add(country_a.code, attr, getattr(country_a, attr), getattr(country_b, attr))
        add(
            country_a.code,
            "treasury",
            country_a.pools.get("treasury", 0),
            country_b.pools.get("treasury", 0),
            country_a.code,
        )
        for commodity in config.COMMODITY_ORDER:
            add(
                country_a.code,
                f"commodity:stock:{commodity}",
                country_a.commodity_stock[commodity],
                country_b.commodity_stock[commodity],
            )
        for asset_class in assets.all_asset_classes():
            add(
                country_a.code,
                f"infra:{asset_class}",
                assets.get_asset(country_a.infrastructure, asset_class).condition,
                assets.get_asset(country_b.infrastructure, asset_class).condition,
            )
    return rows
