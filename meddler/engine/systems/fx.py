"""FxSystem: update exchange rates from trade balances. PROPOSAL §6.1 slot 3, §6.2.

trade_balance_norm and drift_from_inflation are named in §6.2's formula
("exchange_rate *= 1 + 0.002*tanh(trade_balance_norm) + drift_from_inflation") but
neither is itself formulated. Chosen here, following the review-endorsed principle
that systems read persistent Country/World fields rather than ephemeral same-tick
hand-offs from TradeSystem:

trade_balance_norm = (grain_need - grain_output) / grain_need
    Positive for a deficit country (weakening pressure), negative for a surplus
    country (strengthening pressure) -- signed so the literal "+0.002*tanh(...)" term
    produces the economically sensible direction: a deficit country's exchange_rate
    (local units per veri) rises, i.e. its currency weakens.

drift_from_inflation = FX_INFLATION_DRIFT_COEFFICIENT * (inflation / 100)
    Higher inflation weakens a currency (raises exchange_rate); see config.py.

See docs/design-decisions.md for the full writeup of both choices.
"""

from __future__ import annotations

from math import tanh

from meddler.engine import config
from meddler.engine.events import Event, StatDelta
from meddler.engine.model import CountryStatus, World
from meddler.engine.rng import Rng
from meddler.engine.stats import apply_country_stat


def run(world: World, rng: Rng) -> list[Event]:
    events: list[Event] = []
    for country in sorted(world.countries, key=lambda c: c.code):
        if country.status in (CountryStatus.ANNEXED, CountryStatus.DISSOLVED):
            continue
        # A country whose population has reached zero has a food need of zero too
        # (commodity_production._target_need scales need with population), and dividing by
        # it crashed the tick. Newly reachable: occupation drains population, and occupation
        # only became organically reachable when the war drain was rewritten -- nothing sets
        # CountryStatus.DISSOLVED, so a nation that dies stays ACTIVE and keeps being
        # simulated. Zero need means no food imbalance, which is the same reading
        # systems/trade.py and systems/war.py already take of a zero need; this brings the
        # third site into line with them rather than inventing a rule for it.
        need = country.grain_need
        trade_balance_norm = (need - country.grain_output) / need if need > 0 else 0.0
        drift_from_inflation = config.FX_INFLATION_DRIFT_COEFFICIENT * (country.inflation / 100)
        multiplier = (
            1 + config.FX_TRADE_DRIFT_COEFFICIENT * tanh(trade_balance_norm) + drift_from_inflation
        )
        new_rate = country.exchange_rate * multiplier

        stat_deltas: list[StatDelta] = []
        apply_country_stat(
            world, stat_deltas, country.code, "exchange_rate", new_rate - country.exchange_rate
        )

        event = world.log.append(
            tick=world.tick,
            kind="FX_DRIFT",
            country=country.code,
            country2=None,
            parent_id=None,
            depth=0,
            is_intervention=False,
            payload={"exchange_rate": new_rate, "trade_balance_norm": trade_balance_norm},
            stat_deltas=tuple(stat_deltas),
            severity=0,
        )
        events.append(event)
    return events
