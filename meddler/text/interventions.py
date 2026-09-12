"""Intervention palette presentation metadata. docs/frontend-contract.md §3 (`hello.
interventions: [{kind,label,icon,desc}]`).

`EventSpec` deliberately carries no label/icon/desc (engine is text-free, §4.1) -- those
are prose/presentation, exactly like headline templates. This table is keyed by kind and
looked up by the bridge (M6) while iterating `EVENT_REGISTRY` filtered on
`is_intervention`; the KIND LIST itself is always derived from the registry (§12.3.4/
§12.3.8: "clients never hardcode intervention kinds"), never enumerated here or in
bridge/ -- this table only supplies copy for kinds the registry already knows about.

Copy is carried over verbatim from the shipped frontend prototype's own intervention
catalog (web/engine.js, INTERVENTIONS ~lines 58-71) so the UI looks identical the moment
the real engine replaces the mock -- the prototype is normative for UX (PROPOSAL §3.1.6).
"""

from __future__ import annotations

INTERVENTION_META: dict[str, dict[str, str]] = {
    "INTERVENE_DROUGHT": {
        "label": "Scorch the Harvest",
        "icon": "\U0001f335",
        "desc": "Wither the fields. Grain reserves collapse; prices follow.",
    },
    "INTERVENE_ASSASSINATE": {
        "label": "Remove the Leader",
        "icon": "\U0001f5e1️",
        "desc": "The leader meets an abrupt end. Succession is rarely tidy.",
    },
    "INTERVENE_MINT": {
        "label": "Print Money",
        "icon": "\U0001f4b8",
        "desc": "Flood the treasury with fresh currency. Inflation sends regards.",
    },
    "INTERVENE_TAXCUT": {
        "label": "Slash Taxes",
        "icon": "✂️",
        "desc": "The people cheer; the treasury quietly bleeds.",
    },
    "INTERVENE_PLAGUE": {
        "label": "Loose a Plague",
        "icon": "\U0001f9a0",
        "desc": "A sickness with no name. Cities empty; fear spreads faster.",
    },
    "INTERVENE_QUAKE": {
        "label": "Shake the Earth",
        "icon": "\U0001f30b",
        "desc": "A city falls to rubble in a single morning.",
    },
    "INTERVENE_METEOR": {
        "label": "Call Down a Star",
        "icon": "☄️",
        "desc": "You point at the sky. The sky answers. Devastation.",
    },
    "INTERVENE_SECEDE": {
        "label": "Divide the Nation",
        "icon": "\U0001fa93",
        "desc": "Draw a new border through an old country. Two flags by dawn.",
    },
    "INTERVENE_GOLDEN": {
        "label": "Golden Age",
        "icon": "\U0001f3c6",
        "desc": "Bless a nation with fortune, invention, and fat harvests.",
    },
    "INTERVENE_PEACE": {
        "label": "Broker Peace",
        "icon": "\U0001f54a️",
        "desc": "End this country's wars and soothe its grudges.",
    },
    "INTERVENE_WAR": {
        "label": "Ignite a War",
        "icon": "⚔️",
        "desc": "Old hatreds are lit anew. Two nations march by your hand.",
    },
    "INTERVENE_ALLIANCE": {
        "label": "Forge an Alliance",
        "icon": "\U0001f91d",
        "desc": "Bind two nations together in sudden, suspicious friendship.",
    },
    "INTERVENE_EMBARGO": {
        "label": "Impose an Embargo",
        "icon": "\U0001f6a2",
        "desc": "Choke the trade between two nations. Both will feel it.",
    },
    "INTERVENE_INNOVATE": {
        "label": "Spark Innovation",
        "icon": "⚡",
        "desc": "A breakthrough lifts industry and spirits alike.",
    },
    "INTERVENE_CHAOS": {
        "label": "Whisper of Chaos",
        "icon": "\U0001f300",
        "desc": "Roll the dice of history. Even you don't know what happens.",
    },
    "INTERVENE_DESTROY_SATELLITE": {
        "label": "Shoot Down a Satellite",
        "icon": "\U0001f6f0️",
        "desc": "Static fills the sky where an eye used to be.",
    },
    "INTERVENE_NAVAL_BLOCKADE": {
        "label": "Blockade the Coast",
        "icon": "\U0001f6a2",
        "desc": "Warships close the harbor. Nothing sails in or out.",
    },
    "INTERVENE_BLACKOUT": {
        "label": "Sever the Signal",
        "icon": "\U0001f4f4",
        "desc": "The airwaves go silent. No one knows what's happening inside.",
    },
    "INTERVENE_INFRASTRUCTURE_BOOST": {
        "label": "Rebuild in a Night",
        "icon": "\U0001f3d7️",
        "desc": "Cranes and crews appear as if summoned. The damage undoes itself.",
    },
    "INTERVENE_POWER_GRID_FAILURE": {
        "label": "Cut the Power",
        "icon": "\U0001f4a1",
        "desc": "Every light in the country goes out at once.",
    },
}


def meta_for(kind: str) -> dict[str, str]:
    """Fallback keeps the catalog usable even if a new intervention kind is registered
    before its copy is authored here -- never crashes the hello handshake over it."""
    return INTERVENTION_META.get(
        kind, {"label": kind.replace("INTERVENE_", "").title(), "icon": "✨", "desc": ""}
    )
