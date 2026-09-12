"""Money movement. PROPOSAL §4.5. Money is integer minor units; no floats in pools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from meddler.engine.events import LedgerEntry

if TYPE_CHECKING:
    from meddler.engine.model import World


def round_money(x: float) -> int:
    """The ONE money-rounding helper. Banker's rounding to integer minor units. §4.5."""
    return int(round(x))


class Ledger:
    """Every money movement goes through here so Invariants A/B hold. Entries live on the event."""

    @staticmethod
    def transfer(
        entries: list[LedgerEntry], src_pool: str, dst_pool: str, amount: int, currency: str
    ) -> None:
        entries.append(
            LedgerEntry(
                kind="transfer",
                src_pool=src_pool,
                dst_pool=dst_pool,
                amount=amount,
                currency=currency,
            )
        )

    @staticmethod
    def mint(entries: list[LedgerEntry], pool: str, amount: int, currency: str) -> None:
        entries.append(
            LedgerEntry(kind="mint", src_pool=None, dst_pool=pool, amount=amount, currency=currency)
        )

    @staticmethod
    def burn(entries: list[LedgerEntry], pool: str, amount: int, currency: str) -> None:
        entries.append(
            LedgerEntry(kind="burn", src_pool=pool, dst_pool=None, amount=amount, currency=currency)
        )

    @staticmethod
    def apply(pools: dict[str, int], entry: LedgerEntry) -> None:
        """Apply one entry's delta to a flat pool-name -> balance dict. Generic
        utility; does not know about World/Country structure (see apply_to_world)."""
        if entry.src_pool is not None:
            pools[entry.src_pool] = pools.get(entry.src_pool, 0) - entry.amount
        if entry.dst_pool is not None:
            pools[entry.dst_pool] = pools.get(entry.dst_pool, 0) + entry.amount


def resolve_pool(world: "World", pool_name: str) -> tuple[dict[str, int], str]:
    """Map a global ledger pool name to its backing dict and local key. §4.5.

    "<CODE>.<name>" -> that country's own pools dict (bare "<name>" key, §5.1).
    "fx:<pair>" -> world.fx_pools (the whole string is the key; not country-owned).
    """
    if pool_name.startswith("fx:"):
        return world.fx_pools, pool_name
    code, _, local = pool_name.partition(".")
    return world.country(code).pools, local


def apply_to_world(world: "World", entry: LedgerEntry) -> None:
    """Apply one ledger entry's delta directly to World state (a country's own pools,
    or a fx: bookkeeping pool). Used both by the system that creates the entry (so the
    log and world state never diverge) and by Timeline.world_at's replay (§4.4), which
    must reproduce identical pools without re-running systems."""
    if entry.src_pool is not None:
        pools, key = resolve_pool(world, entry.src_pool)
        pools[key] = pools.get(key, 0) - entry.amount
    if entry.dst_pool is not None:
        pools, key = resolve_pool(world, entry.dst_pool)
        pools[key] = pools.get(key, 0) + entry.amount
