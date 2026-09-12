from hypothesis import given
from hypothesis import strategies as st

from meddler.engine.events import LedgerEntry
from meddler.engine.ledger import Ledger, round_money

POOLS = ["ELB.treasury", "ELB.households", "ELB.corporates"]
CURRENCY = "ELB"

transfer_op = st.tuples(
    st.sampled_from(POOLS),
    st.sampled_from(POOLS),
    st.integers(min_value=0, max_value=1_000_000),
).filter(lambda t: t[0] != t[1])

mint_or_burn_op = st.tuples(
    st.sampled_from(["mint", "burn"]),
    st.sampled_from(POOLS),
    st.integers(min_value=0, max_value=1_000_000),
)


def _apply_transfer(
    pools: dict[str, int], entries: list[LedgerEntry], src: str, dst: str, amount: int
) -> None:
    Ledger.transfer(entries, src, dst, amount, CURRENCY)
    Ledger.apply(pools, entries[-1])


@given(st.lists(transfer_op, max_size=200))
def test_invariant_a_conservation_under_pure_transfers(ops: list[tuple[str, str, int]]) -> None:
    pools = {p: 0 for p in POOLS}
    entries: list[LedgerEntry] = []
    total_before = sum(pools.values())
    for src, dst, amount in ops:
        _apply_transfer(pools, entries, src, dst, amount)
    assert sum(pools.values()) == total_before
    assert all(isinstance(v, int) for v in pools.values())


@given(st.lists(transfer_op, max_size=100), st.lists(mint_or_burn_op, max_size=50))
def test_invariant_a_total_changes_only_by_mint_burn(
    transfers: list[tuple[str, str, int]], mint_burns: list[tuple[str, str, int]]
) -> None:
    pools = {p: 0 for p in POOLS}
    entries: list[LedgerEntry] = []
    for src, dst, amount in transfers:
        _apply_transfer(pools, entries, src, dst, amount)

    expected_delta = 0
    for kind, pool, amount in mint_burns:
        if kind == "mint":
            Ledger.mint(entries, pool, amount, CURRENCY)
            expected_delta += amount
        else:
            Ledger.burn(entries, pool, amount, CURRENCY)
            expected_delta -= amount
        Ledger.apply(pools, entries[-1])

    assert sum(pools.values()) == expected_delta


@given(
    st.lists(
        st.one_of(
            transfer_op.map(lambda t: ("transfer",) + t),
            mint_or_burn_op,
        ),
        max_size=200,
    )
)
def test_invariant_b_replay_reproduces_pools(ops: list[tuple]) -> None:
    pools_live = {p: 0 for p in POOLS}
    entries: list[LedgerEntry] = []
    for op in ops:
        kind = op[0]
        if kind == "transfer":
            _, src, dst, amount = op
            Ledger.transfer(entries, src, dst, amount, CURRENCY)
        elif kind == "mint":
            _, pool, amount = op
            Ledger.mint(entries, pool, amount, CURRENCY)
        else:
            _, pool, amount = op
            Ledger.burn(entries, pool, amount, CURRENCY)
        Ledger.apply(pools_live, entries[-1])

    pools_replay: dict[str, int] = {}
    for entry in entries:
        Ledger.apply(pools_replay, entry)

    all_pools = set(pools_live) | set(pools_replay)
    for p in all_pools:
        assert pools_live.get(p, 0) == pools_replay.get(p, 0)
    assert all(isinstance(v, int) for v in pools_replay.values())


def test_round_money_is_int_and_banker_rounds() -> None:
    assert isinstance(round_money(10.4), int)
    assert round_money(10.4) == 10
    assert round_money(10.5) == 10  # banker's rounding: half rounds to even
    assert round_money(11.5) == 12
    assert round_money(-10.5) == -10
