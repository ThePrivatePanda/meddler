"""Fork lineage and runtime-born countries -- the two protocol behaviours that depend on
engine fixes landing alongside this branch.

Kept in its own file on purpose: `pub/bridge` alone cannot make these pass. They need
`Timeline.world_at` replaying from the snapshot's event boundary, forks inheriting prime's
earlier snapshots (so an adopted fork can be scrubbed before its own branch point), and
`INTERVENE_SECEDE` creating a real country. The bridge-side behaviour they pin -- history
that survives adoption, and `countryAdded` before any projection carrying the new code --
is what the frontend's fork/adopt loop reads.
"""

from __future__ import annotations

from typing import Any

from meddler.bridge import adapter, commands
from meddler.bridge.session import Session, new_session
from meddler.engine import tickloop  # noqa: F401 -- import populates EVENT_REGISTRY


def _largest(session: Session) -> str:
    world = session.multiverse.prime.world
    return max(world.countries, key=lambda c: (c.population, c.code)).code


def _types(replies: list[dict[str, Any]]) -> list[str]:
    return [message["type"] for message in replies]


def test_adopted_fork_keeps_the_history_it_branched_from() -> None:
    """The signature loop: fork in the past, adopt it, then scrub back BEFORE the fork.
    The adopted timeline owns prime's pre-fork history, so those ticks still reconstruct."""
    session = new_session(seed=5)
    try:
        for _ in range(60):
            session.multiverse.prime.advance()
        fork_tick = 34
        started = commands.handle(
            session,
            {
                "cmd": "intervene",
                "kind": "INTERVENE_DROUGHT",
                "country": _largest(session),
                "atTick": fork_tick,
            },
        )
        fork_id = next(m["id"] for m in started if m["type"] == "forkStarted")
        for _ in range(4):
            commands.advance_one_tick(session)

        adopted = commands.handle(session, {"cmd": "adoptFork", "id": fork_id})
        assert _types(adopted) == ["forkAdopted", "snapshot", "status"]

        before = commands.handle(session, {"cmd": "worldAt", "tick": fork_tick - 12})
        assert _types(before)[0] == "snapshot", before
        assert before[0]["tick"] == fork_tick - 12
        at_fork = commands.handle(session, {"cmd": "worldAt", "tick": fork_tick})
        assert at_fork[0]["tick"] == fork_tick
        genesis = commands.handle(session, {"cmd": "worldAt", "tick": 0})
        assert genesis[0]["tick"] == 0
    finally:
        session.close()


def test_secede_intervention_announces_the_new_country_before_any_projection() -> None:
    session = new_session(seed=5)
    try:
        for _ in range(12):
            session.multiverse.prime.advance()
        parent = _largest(session)
        prime_codes = {c.code for c in session.multiverse.prime.world.countries}

        replies = commands.handle(
            session,
            {"cmd": "intervene", "kind": "INTERVENE_SECEDE", "country": parent, "atTick": 10},
        )
        kinds = _types(replies)
        assert kinds[0] == "countryAdded", kinds
        assert kinds.index("forkStarted") > 0

        added = replies[0]
        fork_id = added["tl"]
        code = added["country"]["code"]
        child = session.multiverse.forks[fork_id].world.country(code)
        assert added["parent"] == parent
        assert added["country"]["name"] == child.name
        assert added["country"]["bornAt"] == child.born_at_tick
        assert added["worldObject"]["code"] == code
        assert code not in prime_codes

        fork_started = replies[kinds.index("forkStarted")]
        assert code in fork_started["worldObjects"]["countries"]
        assert code in {entry["code"] for entry in fork_started["roster"]}

        # Focusing the fork re-states the roster, so a client that reconnected mid-fork can
        # still name the new nation.
        focus = commands.handle(session, {"cmd": "focusTimeline", "id": fork_id})
        assert focus[0]["type"] == "timelineFocus"
        assert code in {entry["code"] for entry in focus[0]["rosterB"]}
        assert code not in {entry["code"] for entry in focus[0]["rosterA"]}

        # After adoption it is part of prime: hello, snapshots and scrubbing all carry it.
        adopted = commands.handle(session, {"cmd": "adoptFork", "id": fork_id})
        snapshot = adopted[1]
        assert code in snapshot["stats"]
        assert code in {entry["code"] for entry in snapshot["roster"]}
        hello = adapter.hello_message(session)
        assert code in {c["code"] for c in hello["countries"]}
        roster = {entry["code"]: entry for entry in hello["roster"]}
        assert roster[code]["parent"] == parent
        assert roster[code]["ordinal"] == len(roster) - 1  # secessions append after genesis
        assert [entry["ordinal"] for entry in hello["roster"]] == list(range(len(roster)))

        # Scrubbing back before the split shows the old roster; forward again shows the new.
        before = commands.handle(session, {"cmd": "worldAt", "tick": 9})[0]
        assert code not in before["stats"]
        assert code not in {entry["code"] for entry in before["roster"]}
        after = commands.handle(session, {"cmd": "worldAt", "tick": 10})[0]
        assert code in after["stats"]
        assert code in {entry["code"] for entry in after["roster"]}
    finally:
        session.close()


def test_ordinary_interventions_announce_no_country() -> None:
    session = new_session(seed=5)
    try:
        for _ in range(3):
            session.multiverse.prime.advance()
        code = session.multiverse.prime.world.countries[0].code
        replies = commands.handle(
            session,
            {"cmd": "intervene", "kind": "INTERVENE_MINT", "country": code, "atTick": 2},
        )
        assert "countryAdded" not in _types(replies)
    finally:
        session.close()
