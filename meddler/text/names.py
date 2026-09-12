"""Prose flavor words and small grammar helpers for headline rendering (PROPOSAL §6.5).

This module is text-layer content only: scapegoats, place names, calendar phrases,
breakaway-state names, and the little bits of English (plurals, articles, lists)
that headlines need. It is deliberately disjoint from `engine/worldgen.py`, which owns the
*identity* generation (country/currency/leader names that become real world state). See
docs/design-decisions.md ("Name/currency/leader generation lives in engine/worldgen.py,
not text/names.py").

Every selection here is a pure function of stable inputs (an event id, a country code, a
tick) hashed with SHA-256 -- never Python's `hash()` (salted per process) and never an Rng
draw. `text/` must consume zero randomness (§4.3.4). No engine imports.

Places are keyed on the country CODE, not the event id: each country owns a small, fixed
gazetteer (a capital, three towns, three districts), so its headlines keep naming the same
places. Town names are compounded from two short lists so that two countries rarely share
a town, which a flat list of a few dozen names could not avoid.
"""

from __future__ import annotations

import hashlib

# Who the leader blames when things go wrong, keyed by personality trait. One entry for
# every trait in worldgen.TRAITS (paranoid, reformist, corrupt, populist, technocrat,
# warhawk, frugal, flamboyant). Tone mirrors web/engine.js's SCAPEGOAT, wording is ours.
SCAPEGOAT: dict[str, str] = {
    "paranoid": "foreign agents",
    "reformist": "the entrenched old guard",
    "corrupt": "a hostile press",
    "populist": "the coastal elites",
    "technocrat": "global supply shocks",
    "warhawk": "enemy saboteurs",
    "frugal": "reckless spendthrifts",
    "flamboyant": "envious rivals",
}

# Town names are ROOT + ENDING ("Ash" + "combe"). The engine models no cities, so these
# are pure flavor; see country_cities().
_TOWN_ROOTS: tuple[str, ...] = (
    "Ash", "Brenn", "Karst", "Merr", "Dun", "Oust", "Tall", "Vell", "Sarn", "Est",
    "Kolv", "Grel", "Pall", "Ord", "Carr", "Holl", "Tarr", "Vess", "Drev", "Soll",
    "Korr", "Fall", "Quill", "Nar", "Stav", "Harr", "Uld", "Mirr", "Bast", "Rav",
    "Senn", "Zarn", "Pell", "Lun", "Istr", "Amber", "Cald", "Orl", "Wend", "Thorn",
)  # fmt: skip
_TOWN_ENDINGS: tuple[str, ...] = (
    "an", "combe", "haven", "mouth", "ford", "wick", "moor", "reach", "ridge", "hollow",
    "gate", "mere", "stead", "fen", "holm", "dale", "ton", "burg", "ness", "port",
    "field", "cross", "well", "ley",
)  # fmt: skip

# Districts for "across the ___" phrasing. Deliberately uniform GRAMMATICAL NUMBER (all
# singular) -- several templates use {region} as a subject ("the {region} feels/watches"),
# and a mixed-number list makes half of those wrong ("the eastern provinces feels").
REGIONS: list[str] = [
    "eastern province",
    "low valley",
    "amber coast",
    "high steppe",
    "middle delta",
    "northern march",
    "river country",
    "old heartland",
    "salt coast",
    "southern plain",
    "iron hill country",
    "western frontier",
    "lake district",
    "green interior",
    "copper basin",
    "long peninsula",
    "upper plateau",
    "fen country",
    "borderland",
    "grain belt",
    "pine country",
    "cape district",
]

# Prefixes fused with a parent country's name to fabricate a breakaway state for SECESSION
# when the engine does not name a real child country.
BREAKAWAY_PREFIXES: list[str] = ["North", "New", "Free", "Upper", "West", "Old", "Lower"]

# Tick-derived calendar. 1 tick = 1 day (PROPOSAL §4.2); matches the UI's "Year N · Day D".
# The year is split into four equal seasons starting with spring at tick 0.
SEASONS: tuple[str, ...] = ("spring", "summer", "autumn", "winter")

_CARDINALS: tuple[str, ...] = (
    "no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve",
)  # fmt: skip
_ORDINALS: tuple[str, ...] = (
    "zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth",
)  # fmt: skip

# Currency names whose English plural is not "+s".
_IRREGULAR_PLURALS: dict[str, str] = {"Krona": "Kronor", "Lira": "Lire"}


def _digest(key: str) -> bytes:
    return hashlib.sha256(key.encode("utf-8")).digest()


def stable_index(key: str, length: int) -> int:
    """Deterministic index in [0, length) for a string key. SHA-256, so the result is the
    same in every process regardless of PYTHONHASHSEED. Pure; consumes no RNG."""
    if length <= 0:
        return 0
    return int.from_bytes(_digest(key)[:4], "big") % length


def _hash_index(event_id: int, length: int) -> int:
    return stable_index(str(event_id), length)


def _distinct_picks(key: str, pool: list[str], count: int) -> list[str]:
    """`count` distinct entries of `pool`, chosen by walking a hash of `key`."""
    picks: list[str] = []
    salt = 0
    while len(picks) < min(count, len(pool)):
        candidate = pool[stable_index(f"{key}#{salt}", len(pool))]
        if candidate not in picks:
            picks.append(candidate)
        salt += 1
    return picks


_TOWNS: list[str] = [root + ending for root in _TOWN_ROOTS for ending in _TOWN_ENDINGS]


def country_cities(code: str) -> list[str]:
    """A country's gazetteer: capital first, then three towns. Stable for a code."""
    return _distinct_picks(f"city:{code}", _TOWNS, 4)


def country_regions(code: str) -> list[str]:
    """A country's three named districts. Stable for a code."""
    return _distinct_picks(f"region:{code}", REGIONS, 3)


def capital_for(code: str | None) -> str:
    """The capital's name (the first entry of the gazetteer)."""
    return country_cities(code or "")[0]


def city_for(event_id: int, code: str | None = None) -> str:
    """A town in `code`'s gazetteer (capital included), stable for an event id."""
    cities = country_cities(code or "")
    return cities[_hash_index(event_id, len(cities))]


def region_for(event_id: int, code: str | None = None) -> str:
    """One of `code`'s districts, stable for an event id."""
    regions = country_regions(code or "")
    return regions[_hash_index(event_id, len(regions))]


def breakaway_for(event_id: int, country_name: str) -> str:
    """A fabricated breakaway-state name (e.g. 'North Veronia'), stable for an event id."""
    prefix = BREAKAWAY_PREFIXES[_hash_index(event_id, len(BREAKAWAY_PREFIXES))]
    return f"{prefix} {country_name}"


def scapegoat_for(event_id: int, traits: list[str]) -> str:
    """Pick one of the leader's traits (by event id) and return who they blame."""
    if not traits:
        return "shadowy forces"
    trait = traits[event_id % len(traits)]
    return SCAPEGOAT.get(trait, "shadowy forces")


def season_for(tick: int, ticks_per_year: int = 365) -> str:
    """The season a tick falls in, spring first."""
    per_year = max(4, ticks_per_year)
    day = tick % per_year
    return SEASONS[min(3, day * 4 // per_year)]


def year_for(tick: int, ticks_per_year: int = 365) -> int:
    """The 1-based calendar year a tick falls in (the UI's "Year N")."""
    return tick // max(1, ticks_per_year) + 1


def cardinal(n: int) -> str:
    """'three', or digits past twelve."""
    if 0 <= n < len(_CARDINALS):
        return _CARDINALS[n]
    return str(n)


def ordinal(n: int) -> str:
    """'third', or '14th' past ten."""
    if 0 <= n < len(_ORDINALS):
        return _ORDINALS[n]
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def plural(word: str) -> str:
    """English plural for the currency names worldgen can produce (and a sane default)."""
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def article(phrase: str) -> str:
    """'a' or 'an' for the phrase that follows (by first letter; good enough for our
    vocabulary, which has no silent-h or 'uni-' words)."""
    return "an" if phrase[:1].lower() in "aeiou" else "a"


def join_names(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]
