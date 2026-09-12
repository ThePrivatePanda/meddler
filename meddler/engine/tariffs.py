"""Real tariff policy primitives.

Leaf-ish engine helper: deterministic policy lookup and import-sector routing. Policy
mutation/events live in systems/tariffs.py; trade only reads through this module.
"""

from __future__ import annotations

from meddler.engine import config
from meddler.engine.model import TariffPolicy, World


def policy_key(policy: TariffPolicy) -> tuple[str, str, str]:
    return policy.importer, policy.exporter, policy.commodity


def buyer_pool(commodity: str) -> str:
    return "households" if commodity in config.HOUSEHOLD_IMPORT_COMMODITIES else "corporates"


def same_bloc(world: World, a: str, b: str) -> bool:
    bloc = world.bloc_of(a)
    return bloc is not None and b in bloc.members


def matching_policy(
    world: World, importer: str, exporter: str, commodity: str
) -> TariffPolicy | None:
    """Most-specific policy wins: bilateral commodity, bilateral blanket, global
    commodity, global blanket. Keys are unique by structural-handler invariant."""
    if same_bloc(world, importer, exporter):
        return None
    by_key = {policy_key(policy): policy for policy in world.tariffs}
    for key in (
        (importer, exporter, commodity),
        (importer, exporter, "*"),
        (importer, "*", commodity),
        (importer, "*", "*"),
    ):
        policy = by_key.get(key)
        if policy is not None:
            return policy
    return None


def effective_rate(world: World, importer: str, exporter: str, commodity: str) -> float:
    policy = matching_policy(world, importer, exporter, commodity)
    return 0.0 if policy is None else policy.rate


def find_policy(
    world: World, importer: str, exporter: str, commodity: str = "*"
) -> TariffPolicy | None:
    return next(
        (
            policy
            for policy in world.tariffs
            if policy.importer == importer
            and policy.exporter == exporter
            and policy.commodity == commodity
        ),
        None,
    )
