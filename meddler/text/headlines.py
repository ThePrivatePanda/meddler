"""Headline rendering: turn an Event into one line of prose (PROPOSAL §6.5, §3.2).

How a line is chosen:

* `TEMPLATES[kind]` is the general list of `str.format`-style strings for a kind.
  `VARIANTS[kind][name]` holds more specific lists that apply only when the event's own
  record supports them (a resource war, an election that ousted the incumbent, a leader
  who took power in a coup, a catalog event fired by the player's own hand). `_variant`
  names the list that applies, if any.
* A template is a candidate only if every slot it names is AVAILABLE for this event.
  Missing facts (no second country, no recorded cause, a number too small to be dramatic)
  are simply absent from the slot map, so a line is never padded with "the nation" or a
  deflating "runaway inflation hits 4%". Every kind keeps at least one template that needs
  only the always-present slots, so rendering can never come up empty (tested).
* The pick is `sha256(kind|event.id)` over the candidates: deterministic, independent of
  PYTHONHASHSEED, and identical whichever world renders the event (the CLI, a fork, a
  replayed view). The text layer never touches the engine's Rng.
* Slots prefer facts RECORDED ON THE EVENT (payload, ledger, stat deltas, the parent
  event's kind) over live world state, because the bridge also renders old events (trace,
  impact, causes) against the current world, where live values have moved on. Live state
  supplies identity (names, titles, currency) and anything not recorded.
* Severity-2 crises tend to open with an all-caps kicker ("FAMINE: ...") authored into
  the template itself; nothing is transformed at render time except capitalising a line
  that opens with a lower-case slot.
* `render` is a pure function of `(event, world)`: no RNG, no wall-clock, no network, no
  LLM. `text/` may import engine types only (§4.1 layering wall).

`AMBIENT_KINDS` are the bookkeeping events with deliberately no templates; the CLI and the
bridge skip any event whose kind is not in `TEMPLATES`, so ambient kinds never print.
"""

from __future__ import annotations

import string
from functools import lru_cache

from meddler.engine.events import Event
from meddler.engine.model import Country, World
from meddler.text import names

# Every-tick bookkeeping events that would flood a headline feed ("Drama, not data",
# PROPOSAL §3.1.2). Deliberately have no templates. Kept explicit so the exclusion is a
# documented, tested decision rather than an implicit gap.
AMBIENT_KINDS: frozenset[str] = frozenset(
    {
        "PRODUCTION",
        # M10: one invisible record per country per tick carrying its commodity stat_deltas.
        "COMMODITY_PRODUCTION",
        "TAX_COLLECTION",
        "TRADE",  # M12: one invisible record per matched bilateral fill
        "TRADE_SETTLE",  # M12: invisible per-country post-trade stock settlement
        "SHIPMENT_DISPATCHED",  # M13: invisible per-shipment departure (structural)
        "SHIPMENT_ARRIVED",  # M13: invisible per-shipment arrival (structural)
        # One line per lost convoy drowned war-heavy feeds (34 of 393 lines on seed 7).
        # The engine now folds them into a periodic CONVOY_LOSSES report per destination,
        # which is what the feed prints instead.
        "SHIPMENT_LOST",
        "FX_DRIFT",
        "INFLATION_UPDATE",
        "STABILITY_UPDATE",
        "INFRASTRUCTURE_MAINTENANCE",
        "RELATION_DECAY",
        "BLOC_COHESION",  # M11: invisible per-bloc relation-propagation bookkeeping
    }
)

# ---------------------------------------------------------------------------------------
# Vocabulary for recorded facts
# ---------------------------------------------------------------------------------------

# Commodity keys (config.COMMODITIES) as nouns, as cargo, and as a prize worth a war.
_COMMODITY_NOUN: dict[str, str] = {
    "food": "grain",
    "energy": "fuel",
    "raw_materials": "ore and timber",
    "manufactured": "machine parts",
    "consumer": "consumer goods",
    "high_tech": "electronics",
}
_CARGO: dict[str, str] = {
    "food": "a grain convoy",
    "energy": "a fuel tanker",
    "raw_materials": "an ore carrier",
    "manufactured": "a freighter of machine parts",
    "consumer": "a container ship of consumer goods",
    "high_tech": "an electronics freighter",
}
_PRIZE: dict[str, str] = {
    "food": "farmland",
    "energy": "oil fields",
    "raw_materials": "mines",
}

# Infrastructure asset classes (payload "asset_class") as nouns.
_ASSET_NOUN: dict[str, str] = {
    "power_grid": "power grid",
    "rail_network": "rail network",
    "naval_fleet": "fleet",
    "air_fleet": "air fleet",
    "satellites": "satellite network",
    "communications": "phone and broadcast network",
}

# GOD_EDIT payload "field" -> (noun, number format or None when the raw value has no
# unit a reader would recognise). Nouns are singular so "rises"/"sinks" agree.
_EDIT_FIELD: dict[str, tuple[str, str | None]] = {
    "stability": ("stability", "{:.0f}"),
    "inflation": ("inflation", "{:.0f}%"),
    "population": ("population", "{:.1f} million"),
    "gdp_tick": ("output", None),
    "treasury": ("treasury", None),
    "grain_stock": ("grain stock", None),
    "exchange_rate": ("exchange rate", None),
}

# The parent event's kind as a noun phrase, for "after {cause}" lines. Only used when the
# parent happened to the same country (see _cause_phrase). Deliberately short and flat:
# the feed already shows the parent itself one click away.
_CAUSE: dict[str, str] = {
    "DROUGHT": "the drought",
    "EARTHQUAKE": "the earthquake",
    "METEOR": "the meteor strike",
    "PLAGUE": "the plague",
    "FLOOD": "the floods",
    "WILDFIRE": "the wildfires",
    "COLD_SNAP": "the freeze",
    "LOCUST_SWARM": "the locusts",
    "VOLCANIC_ERUPTION": "the eruption",
    "PRICE_SPIKE": "the price shock",
    "CURRENCY_SLIDE": "the currency slide",
    "MINT": "the money-printing",
    "TREASURY_DRAIN": "the emergency spending",
    "INFLATION_CRISIS": "the inflation crisis",
    "DEBT_CRISIS": "the debt crisis",
    "CREDIT_FREEZE": "the credit freeze",
    "CAPITAL_FLIGHT": "the capital flight",
    "RECESSION": "the recession",
    "BOOM_BUST": "the bust",
    "COMPANY_COLLAPSE": "the collapse",
    "UNEMPLOYMENT_SPIKE": "the layoffs",
    "GRAIN_LOSS": "the lost harvest",
    "TRADE_HALT": "the trade halt",
    "EMBARGO": "the embargo",
    "ENERGY_SHORTAGE": "the fuel shortage",
    "MATERIALS_SHORTAGE": "the materials shortage",
    "MANUFACTURING_SLUMP": "the factory slump",
    "CONSUMER_SHORTAGE": "the empty shelves",
    "ELECTION": "the election",
    "LEADER_CHANGE": "the change of government",
    "ELECTION_UPSET": "the upset",
    "SCANDAL": "the scandal",
    "CRACKDOWN": "the crackdown",
    "COUP": "the coup",
    "COUP_RISK_UP": "the coup rumours",
    "STABILITY_DROP": "a long souring of the public mood",
    "CIVIL_WAR_RISK": "the militia standoffs",
    "REVOLUTION": "the revolution",
    "ASSASSINATION": "the assassination",
    "PRESS_SUPPRESSION": "the press gag",
    "SECESSION": "the secession",
    "UNREST": "the riots",
    "FAMINE_WARNING": "the grain warnings",
    "FAMINE": "the famine",
    "POPULATION_LOSS": "the losses",
    "REFUGEE_CRISIS": "the refugee exodus",
    "STRIKE": "the air raids",
    "WAR_DECLARED": "the war",
    "OCCUPATION_BEGIN": "the occupation",
    "NAVAL_BLOCKADE": "the blockade",
    "NAVAL_BATTLE": "the sea battle",
    "NUCLEAR_STRIKE": "the nuclear strike",
    "CONVOY_LOSSES": "the lost convoys",
    "POWER_OUTAGE": "the blackouts",
    "RAIL_COLLAPSE": "the rail collapse",
    "COMMS_BLACKOUT": "the communications blackout",
    "PORT_CLOSURE": "the port closures",
    "INFRASTRUCTURE_DAMAGE": "the infrastructure failures",
    "INTERVENE_DROUGHT": "the unnatural drought",
    "INTERVENE_METEOR": "the falling star",
    "INTERVENE_QUAKE": "the earthquake no fault line explains",
    "INTERVENE_PLAGUE": "the nameless sickness",
    "INTERVENE_ASSASSINATE": "the killing",
    "INTERVENE_SECEDE": "the partition",
    "INTERVENE_WAR": "the war nobody ordered",
    "INTERVENE_MINT": "the midnight money",
    "INTERVENE_TAXCUT": "the phantom tax cut",
    "INTERVENE_BLACKOUT": "the silence",
    "INTERVENE_POWER_GRID_FAILURE": "the blackout",
}

# What a god-rolled INTERVENE_CHAOS resolved into, as a noun phrase ("the dice come up
# {chaos}"). Falls back to the kind name for anything newly registered.
_CHAOS_NOUN: dict[str, str] = {
    "INNOVATION": "a breakthrough",
    "GOLDEN_AGE": "a golden age",
    "BOOM_BUST": "a speculative bubble",
    "WAR_DECLARED": "war",
    "WAR_SPARK": "a war scare",
    "NUCLEAR_TEST": "a nuclear test",
    "DROUGHT": "drought",
    "EARTHQUAKE": "an earthquake",
    "METEOR": "a falling star",
    "PLAGUE": "plague",
    "FLOOD": "floods",
    "WILDFIRE": "wildfire",
    "COLD_SNAP": "a killing frost",
    "LOCUST_SWARM": "locusts",
    "VOLCANIC_ERUPTION": "a volcano",
    "SCANDAL": "scandal",
    "PROPAGANDA_CAMPAIGN": "a propaganda blitz",
    "CIVIL_RIGHTS_REFORM": "civil rights reform",
    "ASSASSINATION": "an assassin's bullet",
    "BRAIN_DRAIN": "an exodus of talent",
    "EDUCATION_REFORM": "school reform",
    "EPIDEMIC_FEAR": "a health scare",
    "SPORTS_VICTORY": "a cup final",
}

# Inflation below this reads as ordinary, so no template quotes the number.
_DRAMATIC_INFLATION = 8.0

# ---------------------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------------------

TEMPLATES: dict[str, list[str]] = {
    # ---- Natural disasters (exogenous) ----
    "DROUGHT": [
        "DROUGHT grips {country} — the harvest fails across the {region}.",
        "Rivers run thin in {country}; granaries report their worst {season} in memory.",
        "Dust settles over {country} as the long dry withers the fields of the {region}.",
        "DROUGHT: {country}'s breadbasket cracks, and {leader_title} {leader} eyes the empty sky.",
        "The rains skip {country} this {season}; farmers in the {region} plough dust.",
        "DROUGHT in {country}: {capital} rations water and the wells go deeper.",
    ],
    "EARTHQUAKE": [
        "EARTHQUAKE levels half of {city} — {country} digs through the rubble.",
        "The ground opens beneath {city}; {country} declares a national emergency.",
        "EARTHQUAKE rocks {country}; rescue crews race the dark in {city}.",
        "Aftershocks ripple through {country} as {city} counts its dead.",
        "EARTHQUAKE: {city}, in {country}'s {region}, is a field of broken stone by dawn.",
        "{country} shakes for forty seconds; {city} will take years to stand up again.",
        "EARTHQUAKE in {country}: {toll} feared dead as {city} comes down.",
    ],
    "METEOR": [
        "A STAR FALLS on {country} — the night burns and {city} is gone.",
        "METEOR impact scours the {region}; ash rides the wind over {country} for days.",
        "Fire from the sky strikes {country}; where {city} stood there is only a crater.",
        "A falling star gouges {country}'s {region} — the observatories had said nothing.",
        "METEOR: {country} loses {toll} in one white second over the {region}.",
    ],
    "PLAGUE": [
        "PLAGUE stalks {country}: fever wards overflow in {city} and the roads out are watched.",
        "A sickness moves through {country}; markets shutter and the bells in {city} do not stop.",
        "PLAGUE: {country} quarantines {city} as the count of the fallen climbs.",
        "Contagion spreads across {country}'s {region}; {leader_title} {leader} orders the ports sealed.",
        "PLAGUE in {country}: {capital}'s hospitals turn the sick away at the gate.",
        "Curfews and chalk marks on doors — {country} fights a plague through the {season}.",
    ],
    # ---- Economy ----
    "PRICE_SPIKE": [
        "Grain prices leap across {country}; market stalls in {city} empty by noon.",
        "Bread doubles overnight in {country} — households feel the squeeze.",
        "{country}'s traders hoard as prices climb; queues form outside the granaries.",
        "Sticker shock in {country}: staples cost more each morning than the last.",
        "A loaf in {capital} now costs a morning's wages; {leader_title} {leader} blames {scapegoat}.",
        "Price boards in {city} are rewritten twice a day as {country}'s staples soar.",
        "Prices jump in {country} after {cause}; the market in {city} sells out by noon.",
    ],
    "CURRENCY_SLIDE": [
        "The {currency} slides as {country}'s trade deficit widens.",
        "{country}'s {currency} tumbles; exchange desks in {capital} repost rates twice a day.",
        "Confidence wobbles: {country}'s {currency} sheds value against the veri.",
        "A run on the {currency} in {country}; {leader_title} {leader} promises calm.",
        "Visitors to {capital} suddenly feel rich: the {currency} is in free fall.",
        "The {currency} buys less every week, and {country}'s importers feel it first.",
    ],
    "MINT": [
        "{country}'s treasury fires up the presses — fresh {currencies} flood the vaults.",
        "Emergency minting in {country}: new {currencies} printed to cover the bills.",
        "{leader_title} {leader} orders the mint to run all night in {country}.",
        "{country} papers over its shortfall with a wave of brand-new {currencies}.",
        "The presses of {capital} run double shifts; economists reach for the bottle.",
        "{country} mints its way past the bills; the ink is still wet when the {currencies} are spent.",
    ],
    "TREASURY_DRAIN": [
        "{country} unveils an emergency subsidy — {currencies} pour out the treasury door.",
        "Relief spending drains {country}'s coffers; the auditors wince.",
        "{country}'s treasury bleeds as {leader_title} {leader} funds a costly stopgap.",
        "The books thin in {country}: another crisis, another payout.",
        "{country} writes cheques it can barely cover to keep the {region} afloat.",
        "{country}'s rainy-day fund meets a very rainy day.",
        "{country} spends money it does not have cleaning up after {cause}.",
        "The bill for {cause} lands in {capital}; {country}'s treasury pays it, for now.",
        "{leader_title} {leader} signs off another relief package; the finance ministry stops sleeping.",
    ],
    "INFLATION_CRISIS": [
        "INFLATION CRISIS: prices in {country} pass {inflation:.0f}% — {leader_title} {leader} blames {scapegoat}.",
        "Runaway inflation hits {inflation:.0f}% in {country}; {leader} points at {scapegoat}.",
        "At {inflation:.0f}% inflation, {country}'s savers watch their money evaporate.",
        "INFLATION CRISIS grips {country}; wages chase prices and never catch them.",
        "INFLATION CRISIS: {country}'s shops change price tags between customers.",
        "The {currency} loses its grip on prices; shoppers in {city} buy before it gets worse.",
    ],
    "DEBT_CRISIS": [
        "DEBT CRISIS: {country}'s treasury runs dry — creditors circle.",
        "DEBT CRISIS grips {country}; {leader_title} {leader}'s options narrow to bad and worse.",
        "{country} misses its payments; DEBT CRISIS shadows every ministry in {capital}.",
        "DEBT CRISIS: lenders shut the window on {country} as the red ink spreads.",
        "{country} is broke. Officially. Civil servants in {capital} wait on their wages.",
    ],
    "CREDIT_FREEZE": [
        "Lending seizes up in {country}; businesses can't roll their loans.",
        "A credit freeze grips {country} — banks slam the vault doors shut.",
        "{country}'s firms scramble for cash as credit vanishes overnight.",
        "The taps run dry in {country}: no new loans, no relief.",
        "Bankers in {capital} stop answering the phone; {country}'s credit is frozen solid.",
    ],
    "CAPITAL_FLIGHT": [
        "Money flees {country}; the wealthy wire their fortunes abroad.",
        "Capital flight drains {country} as investors head for the exits.",
        "{country}'s ledgers empty overnight — quiet convoys of money leave the {region}.",
        "The smart money abandons {country}; {leader_title} {leader} calls it treason.",
        "Luggage at {capital}'s airport is suspiciously heavy; {country}'s rich are leaving.",
    ],
    "GDP_BOOM": [
        "{country}'s economy surges; exporters can't fill orders fast enough.",
        "Boom times in {country} — output climbs and hiring follows.",
        "{country}'s factories run hot; the {currency} firms on the good news.",
        "Growth roars back in {country}; {leader_title} {leader} takes a bow.",
        "Cranes crowd the skyline of {capital} as {country} rides a boom.",
    ],
    "GDP_TICK_RESTORATION": [
        "{country}'s output steadies after the storm; the numbers stop falling.",
        "Production finds its feet again in {country}.",
        "{country}'s economy exhales — the worst of the slump has passed.",
        "Order returns to {country}'s ledgers as output normalizes.",
        "Night shifts return to the plants of {city}; {country} is working again.",
    ],
    "GOLDEN_AGE": [
        "A golden season for {country}: fat harvests, full ledgers, songs in the streets.",
        "Historians will call this {country}'s golden age — all {leader_title} {leader} touches turns.",
        "{country} basks in fortune; art, trade, and invention flower at once.",
        "Prosperity settles over {country}'s {region} like sunlight; the coffers overflow.",
    ],
    "INNOVATION": [
        "{country}'s engineers unveil a breakthrough — industry hails a new era.",
        "Patent fever in {country}: workshops in {city} race to license the new process.",
        "A clever invention lifts {country}'s output and its mood alike.",
        "{country} leaps ahead; rivals scramble to copy what its labs just built.",
        "A lab in {city} cracks it; {country}'s factories retool within the {season}.",
    ],
    "IMMIGRATION_WAVE": [
        "Newcomers pour into {country}, drawn by work and quiet.",
        "{country}'s cities swell as migrants arrive across the {region}.",
        "A wave of new hands reaches {country}; landlords and hiring managers rejoice.",
        "{country} opens its gates; the population climbs by the week.",
        "New accents fill the markets of {city} as {country} takes in arrivals.",
    ],
    "LABOUR_SHORTAGE": [
        "Help wanted everywhere in {country}: too few hands, too much work.",
        "A labour shortage bites {country}; wages rise but shelves stay empty.",
        "{country}'s employers plead for workers; the hiring halls stand empty.",
        "Fields and factories idle in {country} for want of labour.",
        "Every shop window in {city} wants staff; {country} is short of hands.",
    ],
    # ---- Commodities (M10, v2 spec §3) ----
    "ENERGY_SHORTAGE": [
        "Power rationing hits {country} as fuel reserves run thin.",
        "{country}'s grid strains; factories in the {region} cut shifts to save fuel.",
        "Rolling blackouts spread across {country} — {leader_title} {leader} appeals for calm.",
        "{country} scrambles for fuel as its stockpile nears empty.",
        "Queues at every pump in {capital}; {country}'s fuel tanks are running dry.",
        "Street lights go dark in {city} to stretch {country}'s last fuel reserves.",
    ],
    "MATERIALS_SHORTAGE": [
        "{country}'s stockyards run bare — raw materials just aren't coming in.",
        "A materials crunch hits {country}; builders and mills alike stall.",
        "{country} scours the {region} for ore and timber that no longer arrive.",
        "Supply lines thin in {country}: the warehouses show it before the workers do.",
        "Half-built towers stand idle in {capital}; {country} has run out of steel.",
    ],
    "MANUFACTURING_SLUMP": [
        "MANUFACTURING SLUMP: {country}'s factories fall idle across the {region}.",
        "{country}'s assembly lines slow to a crawl — inputs simply aren't there.",
        "MANUFACTURING SLUMP grips {country}; {leader_title} {leader} calls it temporary.",
        "Output collapses at {country}'s plants as the shortage finally bites.",
        "The factory whistles of {city} fall silent; {country}'s industry has nothing to build with.",
        "MANUFACTURING SLUMP: {country}'s plants go quiet after {cause}.",
    ],
    "CONSUMER_SHORTAGE": [
        "Shelves empty across {country} as consumer goods run short.",
        "{country}'s shoppers queue for basics that used to sit in stock.",
        "A shortage of everyday goods spreads through {country}'s {region}.",
        "{country} rations consumer goods; {leader_title} {leader} promises restocking.",
        "Soap, shoes and batteries vanish from the shops of {city}.",
    ],
    "TECH_STAGNATION": [
        "{country}'s labs go quiet — the innovation pipeline runs dry.",
        "TECH STAGNATION: {country} falls behind as high-tech output stalls.",
        "{country}'s engineers make do with less; research budgets thin across the {region}.",
        "The cutting edge dulls in {country}; {leader_title} {leader} blames the shortages.",
        "{country}'s chip plants in {city} stand idle for want of parts.",
    ],
    # ---- Politics ----
    "ELECTION": [
        "{country} goes to the polls; the count runs late into the night.",
        "Ballot boxes close across {country}'s {region}; the tally runs late.",
        "Voters crowd the halls of {country}; every seat is contested.",
        "Election day in {country}: queues snake around the polling stations of {capital}.",
        "{country} votes in the {season} rain; the result will take a day to count.",
    ],
    "ELECTION_UPSET": [
        "Upset in {country}: voters turn on the government and topple the favourite.",
        "{country}'s establishment reels as an outsider seizes the vote.",
        "Shock result in {country} — the polls got it wrong and the {leader_title} is out.",
        "{country} throws the incumbents out; {capital} wakes to a new order.",
        "Nobody in {capital} predicted it: {country}'s voters deliver an upset.",
    ],
    "LEADER_CHANGE": [
        "{new_leader} takes power in {country}, promising a different road.",
        "A new face in {country}'s palace: {new_leader} is sworn in before a wary crowd.",
        "{country} changes hands at the top; {new_leader} inherits the mess.",
        "Power passes in {country} — {new_leader} steps up as the old guard fades.",
        "{new_leader} succeeds {old_leader} as {leader_title} of {country}.",
        "{old_leader} is out and {new_leader} is in; {country} braces for new policies.",
        "Power changes hands in {country}; the portraits in {capital} are replaced by Friday.",
    ],
    "SCANDAL": [
        "Leaked ledgers embarrass {leader_title} {leader}; {country}'s papers smell blood.",
        "Scandal engulfs {country}: missing funds trace to the {leader_title}'s inner circle.",
        "{country} reels as {leader}'s dealings hit the front pages.",
        "A whiff of corruption becomes a stench in {country}; {leader} denies everything.",
        "A villa in the {region}, a numbered account, a very nervous {leader} — {country} has a scandal.",
        "{leader_title} {leader}'s brother-in-law is suddenly very rich; {country}'s press asks how.",
    ],
    "CRACKDOWN": [
        "{leader_title} {leader} orders a crackdown; {country}'s squares fall silent — for now.",
        "Curfews descend on {country}: the government answers protest with police lines.",
        "{country} clamps down hard; dissenters vanish into the paperwork.",
        "Riot police flood the streets of {capital} on {leader}'s command.",
        "Armoured cars ring {capital}'s squares as {country} cracks down.",
        "After {cause}, {leader_title} {leader} answers with batons; {city} is under curfew.",
        "{country} meets {cause} with mass arrests; the prisons of {capital} fill up.",
    ],
    "COUP": [
        "COUP in {country} — officers seize the broadcast tower in {capital} before dawn.",
        "COUP: {country}'s government falls in a night; tanks idle outside the assembly.",
        "The army moves against {leader_title} {leader}; {country} holds its breath.",
        "COUP in {country}: by morning the {leader_title} is gone and generals hold the palace.",
        "COUP: the state broadcaster in {capital} plays martial music on a loop.",
        "COUP in {country}: the generals cite {cause} and take the palace in {capital}.",
        "Tanks in {capital}: after {cause}, {country}'s army decides it knows best.",
        "COUP: {ousted} is bundled out of the palace in {capital} before dawn.",
    ],
    "STABILITY_DROP": [
        "The mood sours in {country}; patience with {leader_title} {leader} thins.",
        "Cracks show in {country}'s calm as confidence slips.",
        "{country} grows restless; trust in the government erodes by the day.",
        "Unease spreads through {country}'s {region} — the ground feels less solid.",
        "Grumbling in the cafés of {capital}: {country}'s patience is wearing through.",
        "{country} is on edge after {cause}; confidence in {leader_title} {leader} slips.",
    ],
    "CIVIL_WAR_RISK": [
        "CIVIL WAR looms over {country}; rival factions arm in the {region}.",
        "CIVIL WAR RISK: {country} fractures as {leader_title} {leader} loses the provinces.",
        "The centre cannot hold in {country} — talk in {capital} turns to open revolt.",
        "CIVIL WAR RISK grips {country}; militias muster where the state has withdrawn.",
        "Checkpoints no government ordered appear across the {region}; {country} teeters.",
        "CIVIL WAR RISK: {city} flies a flag {capital} does not recognise.",
        "After {cause}, {country}'s {region} stops taking orders from {capital}.",
    ],
    # Organic SECESSION is declarative: a stability shock, no country created (see the TODO in
    # engine/kinds/politics.py). These lines therefore report a separatist CRISIS and must never
    # name or announce a new nation -- the {breakaway} slot would invent one that exists nowhere
    # in the roster, on the globe or in the stats. Only INTERVENE_SECEDE really creates a state,
    # and its own templates below may say so because the engine puts the real name on the payload.
    "SECESSION": [
        "SECESSION: the {region} declares itself no longer part of {country}.",
        "Separatists raise their own flag over {country}'s {region}; {capital} calls it rebellion.",
        "{country} loses the {region}: tax collectors are turned back at the line.",
        "SECESSION: {leader_title} {leader} denies from {capital} that {country} has lost anything.",
        "The {region} answers to no one now. No map has changed, and both sides know it.",
    ],
    "SECESSION_SETTLEMENT": [
        "{country} opens its books: a treasury, a currency of its own, and a first payroll to meet.",
        "The settlement is signed — {share_pct}% of {foe}'s money, output and stock crosses the new border.",
        "{country} banks its opening balances in {currency}; {foe}'s ledgers are {share_pct}% lighter.",
        "Clerks divide {foe}'s accounts: {country} carries out {share_pct}% and starts counting.",
        "{country} inherits its share of {foe}'s reserves and discovers what independence costs.",
    ],
    # ---- Social ----
    "UNREST": [
        "Bread riots erupt in {country} as anger boils over — {leader_title} {leader} blames {scapegoat}.",
        "UNREST spreads through {country}'s cities as patience runs out.",
        "Crowds fill the squares of {capital}; chants call for {leader}'s resignation.",
        "UNREST: {country} burns at the edges while {leader} points at {scapegoat}.",
        "UNREST in {country}: barricades go up in {city} and do not come down.",
        "Strikes, marches, broken windows — {country}'s {season} of discontent has begun.",
        "UNREST: {city} riots over {cause}; {leader_title} {leader} blames {scapegoat}.",
        "Anger over {cause} brings {country} into the streets; {capital} smells of tear gas.",
        "UNREST in {country}: the anger over {cause} reaches the gates of the palace.",
    ],
    "FAMINE_WARNING": [
        "FAMINE WARNING: {country}'s reserves fall below {grain_days} days of grain.",
        "FAMINE WARNING — {country} rations bread; officials whisper the word no one prints.",
        "Granaries empty across {country}; {grain_days} days of grain stand between it and hunger.",
        "FAMINE WARNING: the {region} of {country} tightens its belt as the stores run low.",
        "Ration cards return to {capital}: {country} is down to its last sacks of grain.",
        "FAMINE WARNING in {country}: after {cause}, the granaries of {city} echo.",
    ],
    "FAMINE": [
        "FAMINE takes hold in {country} — the granaries are bare and the lines are long.",
        "FAMINE: hunger walks {country}'s {region}; {leader_title} {leader} pleads for aid.",
        "The reserves are gone: FAMINE grips {country} across the {region}.",
        "FAMINE stalks {country}; the markets of {city} hold nothing this {season}.",
        "FAMINE in {country}: soup lines stretch for blocks in {capital}.",
        "FAMINE: after {cause}, {country} has nothing in the granaries and nothing in the fields.",
    ],
    "POPULATION_LOSS": [
        "The toll mounts in {country} — the {region} empties as thousands are lost.",
        "POPULATION LOSS: {country} counts its dead and its departed alike.",
        "{country} shrinks under the weight of catastrophe; whole towns go quiet.",
        "Grief settles over {country} as the population falls hard.",
        "{country} loses {toll}; the {region} will not look the same again.",
        "The census takers of {country} cross out {toll} names after {cause}.",
        "{toll} gone: after {cause}, empty houses line the {region} of {country}.",
    ],
    "REFUGEE_CRISIS": [
        "REFUGEE CRISIS: columns stream out of {country} toward any open border.",
        "Displaced thousands flee {country}; the camps in the {region} overflow.",
        "REFUGEE CRISIS grips {country} as families abandon the {region} with what they can carry.",
        "The roads out of {country} choke with refugees; neighbours bar the gates.",
        "REFUGEE CRISIS: {city} empties in a week as {country}'s people take to the roads.",
        "REFUGEE CRISIS: fleeing {cause}, {country}'s families walk toward any open border.",
    ],
    "INFRASTRUCTURE_DAMAGE": [
        "Bridges and rail fail across {country}; the {region} is cut off.",
        "{country}'s infrastructure buckles — power flickers and the trains stop.",
        "Repair crews swarm {country} as roads and grids give way.",
        "The lights dim over {country}'s {region}; ageing works finally fail.",
        "A bridge in {city} closes 'until further notice' — {country} is fraying.",
        "After {cause}, {country}'s roads, lines and bridges are in pieces.",
    ],
    # ---- Military / diplomatic ----
    "STRIKE": [
        "STRIKE: {foe} hits {country}; sirens rise across the {region}.",
        "Missiles from {foe} hammer {country} — power and rail lines buckle near {city}.",
        "A conventional strike from {foe} tears into {country}'s infrastructure.",
        "Explosions light up {city} as {foe} opens another front from the sky.",
        "{foe}'s bombers reach {country}'s {region}; the depots there burn through the night.",
        "Air-raid shelters fill in {capital} as {foe} strikes {country} again.",
        "{foe} strikes {country} from {distance} away; the {region} learns what range means.",
    ],
    "WAR_DECLARED": [
        "WAR: {country} declares war on {foe} — border guns speak by nightfall.",
        "WAR erupts between {country} and {foe}; markets convulse across {landmass}.",
        "{country} marches on {foe}; the drums of WAR drown out the diplomats.",
        "WAR DECLARED: {leader_title} {leader} of {country} orders the columns toward {foe}.",
        "Ambassadors are handed their coats in {capital}: {country} is at WAR with {foe}.",
        "WAR: {country} and {foe} end a long cold peace in a single afternoon.",
        "WAR fever sweeps {country}; its generals are handed maps and asked to choose.",
    ],
    "CONVOY_LOSSES": [
        "The sea lanes to {country} turn deadly: {count_word} cargo runs lost of late.",
        "{country}'s harbourmasters post the losses: {count_word} convoys that never came.",
        "Insurers stop writing policies for {country}; {count_word} cargo runs have gone down.",
        "Another bad week at sea for {country} — {count_word} ships lost, {commodities} with them.",
        "The docks at {city} wait for convoys that are not coming; {country} has lost {count_word}.",
        "Raiders prowl the lanes to {country}; {recent_word} cargo runs lost this {season}.",
        "{country} loses {count_word} convoys to the war at sea, most of them carrying {commodity}.",
        "The war at sea reaches {country}: more convoys are missing than arriving.",
    ],
    "PEACE": [
        "Guns fall silent: {country} and {foes} sign an armistice.",
        "PEACE between {country} and {foes} — weary capitals exhale.",
        "{country} and {foes} lay down arms; the {region} dares to hope.",
        "A treaty ends the war: {country} and {foes} shake hands over the ruins.",
        "Bells ring in {capital}: {country}'s war with {foes} is over.",
        "Negotiators emerge blinking into the daylight — {country} and {foes} are at peace.",
        "PEACE comes to {country}; its generals finally stand down.",
    ],
    "OCCUPATION_BEGIN": [
        "OCCUPATION: {foe} rolls into {country}; the flag over {capital} changes.",
        "OCCUPATION BEGINS — {foe}'s troops garrison {country} as {leader_title} {leader} flees.",
        "{country} falls under {foe}'s boot; checkpoints rise across the {region}.",
        "OCCUPATION: {foe} takes {country}, and the old government scatters.",
        "{foe}'s tanks park in the squares of {capital}; {country} is an occupied land.",
        "OCCUPATION: {country}'s ministries in {capital} now answer to {foe}.",
    ],
    "RELATION_SHIFT": [
        "Ties between {country} and {foe} shift; envoys read the new mood carefully.",
        "The temperature changes between {country} and {foe} — analysts take note.",
        "{country} and {foe} recalibrate; the balance across {landmass} tilts.",
        "Envoys shuttle between {capital} and {foe_capital}; {country} and {foe} are recalculating.",
        "{country}'s foreign ministry redrafts its briefing on {foe}, then redrafts it again.",
    ],
    # ---- Interventions (the meddler's own hand) ----
    "INTERVENE_DROUGHT": [
        "The sky refuses {country}. Fields crack. No one can explain the weather.",
        "An impossible dry falls upon {country}, as if willed from above.",
        "Rain forgets {country} entirely; the {region} turns to dust overnight.",
        "Something withholds the rain from {country}, and the harvest dies.",
        "The clouds over {country} part and simply stay parted. Meteorologists resign.",
        "It does not rain in {country}. It is not going to. Someone has decided.",
    ],
    "INTERVENE_METEOR": [
        "You point at the sky above {country}. The sky answers.",
        "A star is plucked and dropped on {country}; history will call it chance.",
        "Fire falls on {country} out of a clear night — {city} simply ceases to be.",
        "The heavens open over {country}, and the {region} is unmade.",
        "Astronomers in {capital} spot it an hour out: time enough to pray, not to leave.",
        "A rock the size of a cathedral finds {city}, in {country}, with suspicious precision.",
        "A falling star takes {toll} of {country}'s people. The sky offers no apology.",
    ],
    "INTERVENE_QUAKE": [
        "The earth beneath {country} is told to move. {city} pays the price.",
        "At a gesture, the ground buckles beneath {country}.",
        "{country} shudders on command; {city} folds into itself.",
        "A tremor no fault line explains splits {country}'s {region}.",
        "Seismographs in {capital} draw a straight line up, then give up. {city} is gone.",
        "{country}'s geologists insist the {region} is stable. The {region} disagrees.",
    ],
    "INTERVENE_PLAGUE": [
        "A sickness with no name wakes in {country}. The first cough is in {city}.",
        "Something sleeps no longer beneath {country}; fever spreads by market day.",
        "Contagion arrives in {country} from nowhere, and {city} falls quiet.",
        "A pestilence unlisted in any book creeps through {country}'s {region}.",
        "The doctors of {capital} have never seen this before. That is rather the point.",
        "Patient zero in {city} has been nowhere and met no one. The fever does not care.",
    ],
    "INTERVENE_ASSASSINATE": [
        "{leader_title} {leader} of {country} is dead. The official story satisfies no one.",
        "A single shot in {capital}; {leader} does not finish the speech.",
        "{country}'s {leader_title} falls to an unseen hand; the palace locks its doors.",
        "{leader} of {country} is struck down — no assassin is ever found.",
        "{leader_title} {leader} dies in a locked room in {capital}. The investigation is brief.",
        "{country} buries its {leader_title}; the inquest rules it an act of fate, which is close.",
    ],
    "INTERVENE_SECEDE": [
        "Old maps of {country} burn; by morning there is a border, and {breakaway} exists.",
        "A line is drawn through {country}. It grows customs posts. {breakaway} is born.",
        "{country} is divided by an unseen will — {breakaway} raises its flag at dawn.",
        "Overnight {breakaway} splits from {country}; no minister recalls signing it.",
        "The {region} wakes up foreign. {breakaway} has a flag, an anthem, and no idea why.",
        "Cartographers in {capital} are told to redraw {country}. The ink decides for them.",
    ],
    "INTERVENE_WAR": [
        "Something whispers in two capitals at once. {country} marches on {foe}.",
        "Old hatred between {country} and {foe} is lit like dry straw.",
        "{country} and {foe} go to war on no order anyone will claim.",
        "A sudden fury seizes {country}; its armies turn on {foe} overnight.",
        "{leader_title} {leader} wakes certain that {foe} must fall, and cannot say why.",
        "The diplomats of {capital} and {foe_capital} find their letters rewritten. War follows.",
    ],
    "INTERVENE_PEACE": [
        "Old enemies of {country} wake strangely forgiving; doves circle {capital}.",
        "A sudden thaw: {country}'s feuds dissolve like morning frost.",
        "{country} and {foes} sheathe their swords for reasons neither can name.",
        "Peace settles on {country} out of a clear sky; the generals stand down.",
        "{country}'s generals forget why they were angry with {foes}. The war ends by lunch.",
        "The guns over {country} fall silent mid-volley. The treaty turns up later, signed.",
    ],
    "INTERVENE_ALLIANCE": [
        "{country} and {foe} wake as friends and cannot quite say why.",
        "Two flags fly side by side: {country} and {foe}, bound by an unseen hand.",
        "{country} and {foe} sign a sudden pact; old maps get nervous.",
        "An alliance no diplomat brokered binds {country} to {foe} by dawn.",
        "{leader_title} {leader} embraces {foe}'s envoy like a long-lost brother. Aides are alarmed.",
        "A treaty between {country} and {foe} is found already signed, in a hand nobody knows.",
    ],
    "INTERVENE_EMBARGO": [
        "Every ship between {country} and {foe} finds its papers suddenly wrong.",
        "Trade between {country} and {foe} simply stops; no one signed anything.",
        "The ports of {country} close to {foe} on an order no clerk remembers.",
        "Commerce between {country} and {foe} freezes overnight, as if by decree.",
        "Customs officers in {capital} refuse {foe}'s cargo and cannot explain the feeling.",
        "The docks between {country} and {foe} fall quiet. The paperwork insists it was always so.",
    ],
    "INTERVENE_MINT": [
        "{country}'s mints run all night on orders no clerk remembers signing.",
        "Crates of crisp {currencies} appear in {country}'s vaults; the auditors are on holiday.",
        "The presses in {country} roar to life untended; fresh {currencies} spill out.",
        "Money multiplies in {country}'s treasury by an unseen hand.",
        "Fresh {currencies} materialise in {country}'s treasury. The serial numbers are all consecutive.",
        "{country} is richer this morning. Prices will be along shortly to correct that.",
    ],
    "INTERVENE_TAXCUT": [
        "{country} slashes taxes overnight; crowds cheer a decree no minister drafted.",
        "Tax collectors in {country} are sent home; the treasury holds its breath.",
        "A phantom edict cuts {country}'s taxes to the bone — the {leader_title} looks baffled.",
        "Taxes vanish across {country} at a stroke no one owns.",
        "{leader_title} {leader} takes credit for a tax cut nobody in {capital} wrote.",
        "The tax office in {capital} finds its own doors locked. {country} celebrates, briefly.",
    ],
    "INTERVENE_GOLDEN": [
        "Fortune leans over {country} and smiles; everything ripens at once.",
        "A golden age is decreed for {country} — by whom, no scholar can say.",
        "{country} blooms overnight: full granaries, firm coin, and calm streets.",
        "Blessing pours over {country}'s {region} from an unseen source.",
        "Everything in {country} goes right at once. Its astrologers are insufferable about it.",
        "{leader_title} {leader} is hailed a genius for a golden age {leader} did nothing to earn.",
    ],
    "INTERVENE_INNOVATE": [
        "A forgotten notebook surfaces in {country}; its diagrams change everything.",
        "Genius strikes {country} from a clear sky; the patent office queues around the block.",
        "{country}'s workshops crack a problem overnight that stumped them for years.",
        "A breakthrough with no inventor lifts {country}'s industry at a stroke.",
        "An engineer in {city} wakes with the answer and no memory of the question.",
        "{country}'s labs publish a paper none of them remember writing. It works.",
    ],
    "INTERVENE_CHAOS": [
        "A die of history rolls over {country}; even the hand that threw it waits to see.",
        "Something unpredictable is loosed upon {country}; the {region} braces.",
        "Chaos is whispered into {country} — no one, not even fate, knows what comes.",
        "An unnamed force stirs {country}; the ordinary rules go quiet.",
    ],
    # ---- M7.1 additions: natural ----
    "FLOOD": [
        "FLOOD waters rise across {country}; the {region} disappears under brown water.",
        "{country}'s rivers break their banks — roads to the {region} wash out overnight.",
        "The lowlands of {country} flood; families in {city} move what they can to higher ground.",
        "FLOOD: {country} declares its river towns a disaster zone.",
        "Boats row down the main street of {city} as FLOOD waters swamp {country}.",
    ],
    "WILDFIRE": [
        "Wildfire races through the {region} of {country}; the sky over {city} turns orange.",
        "{country} fights a wildfire it cannot yet contain — smoke reaches {city}.",
        "Dry winds carry fire across {country}'s {region}; crews are stretched thin.",
        "A wildfire breaks out in {country}; evacuation orders reach {city} by nightfall.",
        "Ash falls like snow on {capital} as wildfires burn through {country}'s {region}.",
    ],
    "COLD_SNAP": [
        "A brutal cold snap grips {country}; pipes freeze across the {region}.",
        "{country} shivers through a freeze — fields and power lines both suffer.",
        "The cold comes hard to {country}; {city} burns its furniture to stay warm.",
        "Frost blankets {country}'s {region}; farmers count what's already lost.",
        "Ice closes the harbour at {city} as a deep freeze settles over {country}.",
    ],
    "LOCUST_SWARM": [
        "LOCUST SWARM descends on {country} — the {region} strips bare in a single day.",
        "A black cloud of locusts crosses into {country}; what's green disappears behind it.",
        "{country}'s harvest vanishes under a swarm no one saw coming.",
        "LOCUSTS: {country}'s fields are stripped to the stalk within hours.",
        "LOCUST SWARM: the sky over {city} goes dark at noon, and {country}'s harvest with it.",
    ],
    "VOLCANIC_ERUPTION": [
        "VOLCANIC ERUPTION shakes {country} — ash falls over {city} for a second day.",
        "The mountain above {city} wakes; {country} evacuates the {region} at dawn.",
        "ERUPTION in {country}: lava reaches the outskirts of {city} by evening.",
        "{country}'s long-dormant peak erupts without warning; the {region} goes dark with ash.",
        "ERUPTION: {country} loses {toll} as the mountain above {city} comes apart.",
    ],
    # ---- M7.1 additions: economic ----
    "BOOM_BUST": [
        "{country}'s markets swing wild — a boom no one trusts to last.",
        "Fortunes are made overnight in {country}; the smart money is already leaving.",
        "{country} rides a speculative wave; economists mutter about the landing.",
        "Boom fever grips {capital}'s exchange — {leader_title} {leader} says nothing will spoil it.",
        "BOOM AND BUST: {country}'s bubble inflates, wobbles, and pops within the {season}.",
    ],
    "GDP_TICK_REDUCTION": [
        "Output slows across {country}; the ledgers show it before the streets do.",
        "{country}'s economy cools — factories run short shifts across the {region}.",
        "Growth stalls in {country}; {leader_title} {leader} calls it a pause, not a retreat.",
        "{country}'s output slips again; the quarter closes worse than the last.",
        "Fewer lorries on the roads out of {city}: {country}'s economy is slowing.",
        "{country}'s output sags under {cause}; the {region}'s mills cut a shift.",
    ],
    "RECESSION": [
        "RECESSION grips {country} — shopfronts empty across the {region}.",
        "{country} slides into recession; {leader_title} {leader} promises it will pass.",
        "The downturn in {country} deepens; hiring freezes spread through the {region}.",
        "RECESSION: {country}'s economy contracts for a second straight season.",
        "'To let' signs bloom along the high streets of {capital}; {country} is in RECESSION.",
    ],
    "COMPANY_COLLAPSE": [
        "A pillar of {country}'s economy folds overnight, taking jobs with it.",
        "{country}'s largest employer in the {region} shuts its doors for good.",
        "Collapse in {country}: a household-name firm goes under, owing everyone.",
        "{country} wakes to news that one of its biggest companies has failed.",
        "The {city} works of {country}'s proudest firm are padlocked by the receivers.",
    ],
    "UNEMPLOYMENT_SPIKE": [
        "Unemployment spikes across {country}; queues form outside labour offices.",
        "{country} sheds jobs fast — the {region} feels it first.",
        "Layoffs ripple through {country}; {leader_title} {leader} faces angry crowds.",
        "{country}'s jobless numbers jump; economists call it the worst in years.",
        "The dole queue in {city} wraps round the block as {country} sheds jobs.",
        "After {cause}, {country}'s employers stop hiring and start counting.",
    ],
    "GRAIN_LOSS": [
        "{country} loses grain stores it can't easily replace.",
        "A blow to {country}'s harvest leaves granaries thinner than planned.",
        "{country}'s grain reserves take a hit; officials quietly recalculate the winter.",
        "Losses mount in {country}'s fields; the {region}'s stockpiles shrink.",
        "{country} loses a harvest to {cause}; the silos of the {region} stand half empty.",
    ],
    "TRADE_HALT": [
        "Trade grinds to a halt for {country}; ships sit idle in harbour.",
        "{country}'s trade routes freeze overnight — warehouses fill, shelves empty.",
        "Commerce stalls across {country} as goods stop moving through the {region}.",
        "{country} watches its trade partners vanish one by one this {season}.",
    ],
    "EMBARGO": [
        "EMBARGO: {country} slams its ports shut to {foe}; both coasts fall quiet.",
        "{country} cuts all trade with {foe} — the docks empty within a day.",
        "An embargo severs {country} from {foe}; merchants on both sides scramble.",
        "{country} and {foe} stop trading overnight; the embargo holds, for now.",
    ],
    "TARIFF_IMPOSED": [
        "TARIFFS: {country} raises duties on goods from {foe}; importers brace for higher prices.",
        "{country} puts a new tariff wall around trade with {foe}.",
        "New import duties in {country} target {foe}; merchants recalculate every contract.",
        "{leader_title} {leader} orders tariffs on {foe}, promising protection at home.",
    ],
    "TARIFF_REPEALED": [
        "{country} lifts its tariffs on {foe}; the border price falls overnight.",
        "A trade thaw: {country} repeals import duties aimed at {foe}.",
        "{country} dismantles its tariff wall against {foe} as relations improve.",
        "Merchants cheer as {country} ends its duties on goods from {foe}.",
    ],
    "IMMIGRATION_INCENTIVE": [
        "{country} opens its doors wider, offering land and coin to newcomers.",
        "{leader_title} {leader} launches a drive to bring new hands into {country}.",
        "{country} advertises abroad for settlers; the {region} needs the labour.",
        "New incentives from {country} aim to draw workers from across the border.",
    ],
    # ---- M7.1 additions: political ----
    "POLICY_SHIFT": [
        "{country} quietly changes course; the {region} adjusts.",
        "A shift in policy from {leader_title} {leader} reshapes {country}'s priorities.",
        "{country}'s government reverses an old position without much fanfare.",
        "New rules take hold in {country} as {leader_title} {leader} tries a different tack.",
        "A white paper from {capital} quietly rewrites {country}'s plans for the {region}.",
        "After {cause}, {capital} tears up its programme and starts again.",
        "{leader_title} {leader} answers {cause} with a new programme and a long speech.",
    ],
    "COUP_RISK_UP": [
        "Whispers of a coup circle {country}'s officer corps.",
        "{country}'s garrisons grow restless; {leader_title} {leader} doubles the guard.",
        "Tension rises in {country}'s barracks — the {region} feels the unease.",
        "Rumours of mutiny reach {leader_title} {leader} in {capital}; loyalty is tested.",
        "Colonels in {capital} start dining together a great deal. {leader} notices.",
    ],
    "REVOLUTION": [
        "REVOLUTION sweeps {country} — the old order falls in a single week.",
        "{country}'s streets belong to the crowd now; the palace in {capital} stands empty.",
        "REVOLUTION: {country}'s government collapses as the {region} rises as one.",
        "The uprising in {country} succeeds where a dozen protests failed.",
        "REVOLUTION: the palace in {capital} is thrown open and {country} starts over.",
        "REVOLUTION in {country}: after {cause}, the crowd takes {capital}.",
        "REVOLUTION: {ousted} flees {capital} as {country}'s crowds take the palace.",
    ],
    "GOVT_TYPE_CHANGE": [
        "{country} rewrites its own charter; the shape of power changes overnight.",
        "A new system of rule takes hold in {country} after weeks of upheaval.",
        "{country}'s government is reborn under a different name and a different crown.",
        "The {region} watches as {country} formally changes how it is governed.",
    ],
    "REFERENDUM": [
        "{country} goes to the ballot on a question that will not wait.",
        "A referendum in {country} draws the {region} to the polls in record numbers.",
        "{country} puts its future to a vote; {leader_title} {leader} awaits the count.",
        "The people of {country} decide a question the government could not.",
    ],
    "PROPAGANDA_CAMPAIGN": [
        "{leader_title} {leader} floods {country}'s papers with a message of unity.",
        "A campaign of reassurance rolls out across {country}'s broadcasts.",
        "{country}'s presses run overtime on {leader_title} {leader}'s orders.",
        "Posters and slogans blanket {country} as the government gets ahead of the story.",
        "A forty-foot portrait of {leader} goes up in {capital}; {country} is told all is well.",
    ],
    "ASSASSINATION": [
        "ASSASSINATION: {country}'s {leader_title} is killed in broad daylight.",
        "{country} reels — its {leader_title} is dead, and no one has claimed it yet.",
        "A single shot in {capital} ends a {leader_title}'s rule over {country}.",
        "ASSASSINATION shakes {country}; the succession is already contested.",
        "ASSASSINATION in {capital}: {country} wakes without a {leader_title}.",
        "ASSASSINATION: {leader_title} {ousted} of {country} is shot dead in {capital}.",
        "{ousted} is dead; {country} reels, and the succession is already contested.",
    ],
    "PRESS_SUPPRESSION": [
        "{country}'s papers go quiet under new orders from the palace.",
        "Censors move through {country}'s newsrooms; the {region} hears only what's approved.",
        "{leader_title} {leader} tightens the leash on {country}'s press.",
        "Reporters in {country} choose their words carefully this week — or don't print at all.",
        "The morning papers in {capital} carry the weather, the football, and nothing else.",
        "{country}'s censors black out every word about {cause}.",
    ],
    "CIVIL_RIGHTS_REFORM": [
        "{country} widens its freedoms; {leader_title} {leader} calls it overdue.",
        "New protections take effect across {country}, cheered in the {region}.",
        "{country}'s reformers win a real victory — rights expand, not contract.",
        "{leader_title} {leader} signs sweeping reforms into law across {country}.",
    ],
    # ---- M7.1 additions: military/diplomatic ----
    "WAR_SPARK": [
        "Tempers flare along {country}'s border; officers reach for their maps.",
        "{country}'s war councils meet through the night as old grudges resurface.",
        "Something in the {region} tips {country} toward the edge of war.",
        "{leader_title} {leader} of {country} is heard using the word 'war' aloud.",
        "Reservists in {country} get their call-up letters; nobody in {capital} says why.",
    ],
    "ALLIANCE": [
        "{country} and {foe} sign a pact of friendship; old maps get nervous.",
        "An alliance is sworn between {country} and {foe} — rivals take note.",
        "{country} binds itself to {foe} in a ceremony neither side calls temporary.",
        "ALLIANCE: {country} and {foe} pledge mutual defence across {landmass}.",
        "Flags of {country} and {foe} fly together over {capital}; an alliance is born.",
    ],
    "ALLIANCE_BROKEN": [
        "The pact between {country} and {foe} lies in tatters.",
        "{country} walks away from its alliance with {foe}; both capitals go quiet.",
        "Years of friendship end as {country} and {foe} formally part ways.",
        "{country} renounces its treaty with {foe} — {landmass} recalculates its loyalties.",
    ],
    "ANNEXATION": [
        "ANNEXATION: {country} is formally absorbed into {foe}, its flag lowered for the last time.",
        "{foe} completes its annexation of {country}; the old borders are erased.",
        "{country} ceases to exist as a nation — {foe} claims its territory outright.",
        "ANNEXATION: {foe} folds {country}'s lands, people, and ledgers into its own.",
    ],
    "LIBERATION_WAR": [
        "{country} takes up arms to drive {foe} out of occupied territory.",
        "A war of liberation begins as {country} marches to reclaim what {foe} took.",
        "{country}'s exiled government calls the people to arms against {foe}'s occupation.",
        "LIBERATION: {country} opens a new front against {foe} to win back its land.",
    ],
    "OCCUPATION_END": [
        "The occupiers leave {country}; the old flag rises over {capital} again.",
        "{country} marks the end of occupation with quiet, exhausted celebration.",
        "After long years, {country} governs itself again.",
        "The last garrison withdraws from {country}; the {region} breathes out.",
    ],
    "NAVAL_BLOCKADE": [
        "{country}'s warships seal {foe}'s harbours; nothing moves by sea.",
        "NAVAL BLOCKADE: {country} chokes {foe}'s coastline, port by port.",
        "{foe}'s ships sit trapped in harbour as {country}'s fleet holds the line.",
        "{country} tightens a naval blockade around {foe}; supplies begin to run thin.",
    ],
    "NAVAL_BATTLE": [
        "Fleets from {country} and {foe} clash off the coast; the sea takes its toll.",
        "NAVAL BATTLE: {country} and {foe} trade broadsides through the night.",
        "{country}'s navy engages {foe}'s in open water — both sides claim the day.",
        "Warships from {country} meet {foe}'s at sea; the wreckage washes ashore for days.",
    ],
    "ARMS_DEAL": [
        "{country} and {foe} quietly finalize a shipment of weapons.",
        "A deal moves crates from {country}'s armouries to {foe}'s garrisons.",
        "{country} sells arms to {foe}; neither government confirms the terms.",
        "Ledgers in {country} and {foe} both show an unusual transfer this week.",
    ],
    "PROXY_WAR": [
        "{country} bankrolls {foe}'s side of a war it isn't officially fighting.",
        "Money and arms flow quietly from {country} into {foe}'s conflict.",
        "{country} funds a distant war through {foe}, hoping no one asks too loudly.",
        "A proxy war deepens as {country} keeps {foe} supplied from a safe distance.",
    ],
    "CEASEFIRE": [
        "Guns fall quiet between {country} and {foe} — for now.",
        "A fragile ceasefire holds between {country} and {foe} after a hard week.",
        "{country} and {foe} agree to a pause; neither trusts it to last.",
        "CEASEFIRE: {country} and {foe} step back from the brink, briefly.",
    ],
    "TREATY": [
        "{country} and {foe} put their signatures to a lasting treaty.",
        "A formal treaty binds {country} and {foe} after months of negotiation.",
        "{country} and {foe} settle their differences on paper, at last.",
        "TREATY signed: {country} and {foe} close the book on an old dispute.",
    ],
    "TRADE_BOOST": [
        "New trade routes open between {country} and {foe}; both treasuries feel it.",
        "Commerce surges between {country} and {foe} after the treaty takes hold.",
        "{country}'s merchants find eager new partners in {foe}.",
        "Trade between {country} and {foe} climbs to levels no one predicted.",
    ],
    "RESISTANCE_MOVEMENT": [
        "A resistance movement takes root in occupied {country}.",
        "Underground cells organize across {country}'s {region}, waiting for their moment.",
        "{country}'s occupiers find their orders quietly disobeyed, then openly defied.",
        "Resistance grows in {country}; the garrison in {capital} sleeps less easily.",
    ],
    "NUCLEAR_TEST": [
        "NUCLEAR TEST: {country} detonates a device in the {region}, and the world takes notice.",
        "{country} announces a successful nuclear test; capitals everywhere recalculate.",
        "A flash in the {region} confirms it — {country} has the bomb now.",
        "NUCLEAR TEST shakes {country}'s remote {region}; the fallout is diplomatic as much as physical.",
    ],
    "NUCLEAR_STRIKE": [
        "NUCLEAR STRIKE: {country} unleashes the unthinkable on {foe}.",
        "{foe} is struck by {country}'s arsenal; {foe_capital} will not recover soon.",
        "NUCLEAR STRIKE: a single order from {country} levels part of {foe}.",
        "{country} crosses the line no one thought would be crossed, striking {foe} directly.",
    ],
    # ---- M7.1 additions: infrastructure ----
    "SATELLITE_FAILURE": [
        "SATELLITE FAILURE: {country} loses its eye in the sky without warning.",
        "{country}'s satellite network goes dark; ground crews scramble for answers.",
        "Contact is lost with {country}'s satellites — the silence is unsettling.",
        "{country}'s orbital assets fail in sequence; no one yet knows why.",
        "SATELLITE FAILURE: {country}'s constellation is down to {condition_pct}% and falling.",
    ],
    "COMMS_BLACKOUT": [
        "COMMS BLACKOUT grips {country}; the {region} is cut off overnight.",
        "{country} goes dark — broadcasts fail across the {region} all at once.",
        "A total communications blackout leaves {country} unable to coordinate.",
        "BLACKOUT: {country}'s networks fail together, and rumour fills the silence.",
        "COMMS BLACKOUT: {capital} cannot reach {city}, and {city} has stopped trying.",
    ],
    "MEDIA_SUPPRESSION": [
        "With the networks down, {country}'s remaining media falls silent too.",
        "{country}'s papers stop printing what they can't verify — which is everything.",
        "Suppression spreads through {country}'s newsrooms as the blackout drags on.",
        "{country} hears less and less official news each day the silence continues.",
    ],
    "NAVAL_LOSS": [
        "{country}'s fleet suffers losses it can't quickly replace.",
        "Ships of {country}'s navy are lost — the harbour at {city} looks emptier.",
        "{country} counts hulls missing from its fleet after a hard week at sea.",
        "Naval losses mount for {country}; shipyards in the {region} work overtime.",
        "NAVAL LOSS: {country}'s fleet limps into {city} at {condition_pct}% strength.",
    ],
    "PORT_CLOSURE": [
        "PORT CLOSURE: {country}'s harbours shut down, stranding cargo on the docks.",
        "{country} closes its ports; ships queue outside {city} with nowhere to unload.",
        "{country}'s main port grinds to a halt, and the backlog grows by the day.",
        "Trade through {country} stalls as its harbours close their gates.",
    ],
    "SUPPLY_DISRUPTION": [
        "Supply lines into {country} break down; shelves in {city} thin out fast.",
        "{country}'s distribution networks falter, and shortages follow quickly.",
        "Disruption spreads through {country}'s supply chains this week.",
        "{country} struggles to move goods from port to market as delays pile up.",
    ],
    "RAIL_COLLAPSE": [
        "RAIL COLLAPSE: {country}'s tracks fail across the {region}.",
        "{country}'s rail network buckles; freight sits stranded for miles.",
        "A stretch of {country}'s railway gives way, severing the {region} from {capital}.",
        "{country}'s trains stop running as the rail network fails outright.",
        "RAIL COLLAPSE: the line from {capital} to {city} is closed; freight waits in sidings.",
    ],
    "GRAIN_TRANSPORT_FAILURE": [
        "With the rails down, {country}'s grain can't reach the granaries.",
        "{country}'s harvest sits rotting at the depot — there's no way to move it.",
        "Grain transport collapses across {country}; the {region} watches stockpiles stall.",
        "{country}'s food supply chain breaks at the worst possible moment.",
    ],
    "POWER_OUTAGE": [
        "POWER OUTAGE: {country} goes dark from {city} to the {region}.",
        "{country}'s grid fails; factories and homes alike lose power at once.",
        "Blackout across {country} — the power grid gives out under the strain.",
        "{country} scrambles to restore electricity as the outage drags into a second day.",
        "POWER OUTAGE: {capital} runs on candles; {country}'s grid is down to {condition_pct}%.",
    ],
    "INDUSTRY_SHUTDOWN": [
        "With the lights out, {country}'s factories fall silent too.",
        "Industry across {country} grinds to a halt as the outage continues.",
        "{country}'s production lines sit idle, workers sent home indefinitely.",
        "{country}'s industrial output stalls completely under the power failure.",
    ],
    "INFRASTRUCTURE_RESTORED": [
        "{country} brings its infrastructure back online, piece by piece.",
        "Repair crews in {country} finish faster than anyone expected.",
        "{country}'s damaged networks hum back to life across the {region}.",
        "A quiet success: {country} restores what was broken without much fuss.",
    ],
    # ---- M7.1 additions: social ----
    "STABILITY_UP": [
        "Calm settles over {country}; the {region} exhales.",
        "{country} finds its footing again after a difficult stretch.",
        "A rare good week for {country} — confidence ticks upward.",
        "{leader_title} {leader} enjoys a quiet moment as {country} steadies itself.",
    ],
    "EDUCATION_DECLINE": [
        "{country}'s schools lose funding, and the {region} will feel it for years.",
        "Classrooms empty across {country} as education spending is cut.",
        "{country}'s universities warn of a coming decline in standards.",
        "Fewer students finish school in {country} this year than last.",
    ],
    "EDUCATION_REFORM": [
        "{country} invests heavily in its schools and universities.",
        "A new curriculum takes hold across {country}'s classrooms.",
        "{leader_title} {leader} makes education the centrepiece of {country}'s agenda.",
        "{country} opens new schools across the {region}, betting on the next generation.",
    ],
    "BRAIN_DRAIN": [
        "{country}'s brightest keep leaving for opportunities elsewhere.",
        "A quiet exodus of talent drains {country}'s universities and labs.",
        "{country} watches its engineers and scholars board ships for other shores.",
        "The {region}'s best minds are choosing to leave {country}, not stay.",
    ],
    "CULTURAL_RENAISSANCE": [
        "A cultural renaissance blooms in {country}; the theatres of {city} fill nightly.",
        "{country} enjoys an unexpected flowering of art, music, and ideas.",
        "Something is stirring in {country}'s cities — call it a golden mood.",
        "{country}'s artists and thinkers find each other, and the {region} notices.",
    ],
    "EPIDEMIC_FEAR": [
        "Fear of contagion empties {country}'s markets and streets alike.",
        "{country} grows cautious as rumours of sickness spread faster than facts.",
        "Public life in {country} slows as the {region} braces for the worst.",
        "{country}'s cities grow quiet as people choose to stay indoors.",
    ],
    "SPORTS_VICTORY": [
        "{country} erupts in celebration after a stunning sporting win.",
        "{city} throws an impromptu holiday after {country}'s team wins the cup.",
        "{country}'s flags fly high tonight — a hard-fought win lifts the whole nation.",
        "{leader_title} {leader} basks in {country}'s unexpected sporting triumph.",
        "An underdog side from {city} wins it all; {country} forgets its troubles for a night.",
    ],
    # ---- M7.1 additions: §6.7.4 intervention kinds ----
    # No "✦" in the text: the feed card already marks interventions with the glyph.
    "INTERVENE_DESTROY_SATELLITE": [
        "A signal blinks out over {country}. Someone, somewhere, gave the order.",
        "{country} loses a satellite with no explanation offered.",
        "The sky over {country} loses one more watching eye — deliberately.",
        "Static replaces signal above {country}. No accident, everyone suspects.",
        "One of {country}'s satellites blooms into debris. Its operators blame space weather.",
    ],
    "INTERVENE_NAVAL_BLOCKADE": [
        "Warships appear off {foe}'s coast overnight, sent by {country}'s hand.",
        "{country} seals {foe}'s harbours on a word from on high.",
        "{foe}'s ports go quiet — {country}'s blockade needs no declaration.",
        "A silent fleet closes around {foe}, dispatched by {country}.",
        "{country}'s admirals find themselves off {foe}'s coast and decide to stay.",
    ],
    "INTERVENE_BLACKOUT": [
        "Every signal into {country} dies at once. No storm, no accident.",
        "{country} goes dark on command — the silence is total and immediate.",
        "Someone reaches into {country}'s networks and simply switches them off.",
        "{country}'s airwaves fall silent, cut by a hand no one can see.",
        "The last broadcast from {capital} is the weather forecast. Then nothing.",
    ],
    "INTERVENE_INFRASTRUCTURE_BOOST": [
        "{country}'s broken infrastructure repairs itself overnight, impossibly fast.",
        "Cranes and crews appear in {country} as if summoned. The damage undoes itself.",
        "{country} wakes to find its ruins rebuilt by unseen hands.",
        "A night passes, and {country}'s infrastructure is whole again.",
        "The bridges of {city} are standing again. The engineers are taking the credit.",
    ],
    "INTERVENE_POWER_GRID_FAILURE": [
        "Every light in {country} dies at the same instant.",
        "{country}'s grid fails on command — no storm, no warning.",
        "Someone cuts {country}'s power from a place no one can find.",
        "Darkness falls over {country} all at once, and it isn't natural.",
        "{capital} goes dark mid-sentence. The grid engineers find nothing wrong.",
    ],
    # ---- Direct god edits (dossier sliders and relation buttons; engine/god.py) ----
    "GOD_EDIT": [
        "The numbers in {country} are rewritten overnight. The archives quietly agree.",
        "{country} wakes to find its own statistics edited. Nobody signed off.",
        "A correction is issued for {country}'s reality. There is no appeal.",
        "History's accountant visits {country} and leaves the books different.",
    ],
    "GOD_RELATION_SHIFT": [
        "Someone rewrites the file on {country} and {foe}. Both foreign ministries adopt it.",
        "The feeling between {country} and {foe} is adjusted from above, without consultation.",
        "{country} and {foe} find their mutual opinion edited overnight.",
        "Envoys between {capital} and {foe_capital} read new instructions nobody sent.",
    ],
}


# Fact-specific template lists. Each applies only when `_variant` says the event's own
# record supports it; otherwise the general TEMPLATES list is used.
VARIANTS: dict[str, dict[str, list[str]]] = {
    "WAR_DECLARED": {
        "resource": [
            "WAR: {country} marches on {foe} for its {prize} — hunger for {commodity} outruns diplomacy.",
            "{country} declares WAR on {foe}; the prize, everyone in {capital} knows, is the {prize}.",
            "Short of {commodity} and out of patience, {country} sends its columns into {foe}.",
            "WAR DECLARED: {leader_title} {leader} of {country} wants {foe}'s {prize}, and will take them.",
            "{country}'s generals are handed maps of {foe}'s {prize}; WAR follows within the day.",
            "WAR over {commodity}: {country} crosses into {foe} while the price is still rising.",
        ],
        "defense": [
            "Honouring its pact with {ally}, {country} declares WAR on {foe}.",
            "{country} joins {ally}'s war against {foe}; the alliance holds under fire.",
            "WAR: {country}'s treaty with {ally} comes due, and its armies turn on {foe}.",
            "True to its word, {country} marches beside {ally} against {foe}.",
            "An attack on {ally} is an attack on {country}: WAR with {foe} is declared.",
        ],
    },
    "ELECTION": {
        "ousted": [
            "{country} votes {incumbent} out; the concession speech in {capital} is short.",
            "Voters in {country} turn on {incumbent}; the ballots are not close.",
            "ELECTION in {country}: {incumbent}'s party is swept from office.",
            "{country}'s voters have had enough of {incumbent}, and say so at the polls.",
            "The count in {country} ends {incumbent}'s time in power.",
            "{incumbent} loses {country}; the removal vans reach {capital} before the results do.",
            "{country} votes for change; the count runs late and the government does not survive it.",
        ],
        "returned": [
            "{country} re-elects {incumbent}; the victory party in {capital} runs late.",
            "Voters in {country} return {incumbent} for another term, with fewer cheers than last time.",
            "{incumbent} survives the vote in {country}; the opposition cries foul.",
            "{country} sticks with {incumbent}; the opposition blames the {season} weather.",
            "Turnout is low and the result unsurprising — {country} keeps {incumbent}.",
            "{incumbent} wins again in {country}, mostly by not losing.",
        ],
    },
    "LEADER_CHANGE": {
        "elected": [
            "{new_leader} is sworn in as {leader_title} of {country}, promising a different road.",
            "{country}'s new {leader_title}, {new_leader}, moves into the palace in {capital}.",
            "{new_leader} takes the oath in {country}; {old_leader}'s portraits come down.",
            "Power passes peacefully in {country}: {old_leader} out, {new_leader} in.",
            "{new_leader} takes office in {capital} with a mandate and no majority.",
        ],
        "seized": [
            "The generals' choice, {new_leader}, now runs {country}; {old_leader} is nowhere to be seen.",
            "{new_leader} emerges from the barracks as {country}'s new {leader_title}.",
            "{country} wakes under {new_leader}; nobody voted for this.",
            "{new_leader} is named {leader_title} of {country} by the men with the guns.",
            "{new_leader} takes {capital} after {cause}; the old cabinet is under arrest.",
        ],
        "succession": [
            "{new_leader} takes the oath in {capital}, beside {old_leader}'s empty chair.",
            "{country} has a new {leader_title}: {new_leader}, sworn in before the funeral.",
            "{new_leader} succeeds the murdered {old_leader}; the security detail is doubled.",
            "After {cause}, {new_leader} inherits {country} and an armoured car.",
        ],
    },
    "RELATION_SHIFT": {
        "pact": [
            "Envoys of {country} and {foe} toast the new pact late into the night.",
            "The ink dries on the pact between {country} and {foe}; joint exercises are planned.",
            "{country}'s parliament ratifies the alliance with {foe} by a comfortable margin.",
            "Trade and troops begin to flow between new allies {country} and {foe}.",
        ],
        "rupture": [
            "Recriminations fly between {country} and {foe} after the pact's collapse.",
            "{country} and {foe} expel each other's military attachés.",
            "The broken alliance leaves {country} and {foe} eyeing each other warily.",
            "Former allies {country} and {foe} trade blame in duelling press conferences.",
        ],
        "fading": [
            "The friendship between {country} and {foe} is fading into mere politeness.",
            "{country} and {foe} still shake hands, but they no longer mean it as much.",
            "Once close, {country} and {foe} now keep a careful distance.",
            "Fewer state dinners between {capital} and {foe_capital} this {season}.",
            "{country}'s envoy to {foe} is recalled for 'consultations' and not replaced.",
        ],
        "detente": [
            "{country} and {foe} step back from open hostility; envoys meet again.",
            "The worst of the feud between {country} and {foe} burns out.",
            "{country} and {foe} are no longer enemies, merely rivals; the border guns fall quiet.",
            "A back channel opens between {capital} and {foe_capital}; nobody admits it.",
        ],
        "thaw": [
            "A cautious thaw between {country} and {foe} — nobody calls it friendship yet.",
            "Tension between {country} and {foe} eases; trade talks resume.",
            "{country} and {foe} lower the temperature; hardliners in {capital} grumble.",
            "{country} reopens its consulate in {foe_capital}, quietly.",
        ],
        "cooling": [
            "Frost settles between {country} and {foe}; trade talks are shelved.",
            "A chill between {country} and {foe}: state visits are cancelled without explanation.",
            "{country} and {foe} trade pointed communiqués; the rivalry sharpens.",
            "{country}'s diplomats stop smiling when {foe} is mentioned.",
        ],
        "warming": [
            "{country} and {foe} discover they like each other after all.",
            "Warm words between {country} and {foe} harden into genuine friendship.",
            "{country} rolls out the red carpet for {foe}'s envoys — twice in one {season}.",
            "A friendly turn between {country} and {foe}; the frontier opens a little wider.",
        ],
    },
    "TARIFF_IMPOSED": {
        "commodity": [
            "TARIFFS: {country} slaps a duty of {rate_pct}% on {commodity} from {foe}.",
            "{country} taxes {foe}'s {commodity} at the border — {rate_pct}%, effective at once.",
            "{foe}'s {commodity} now pays {rate_pct}% at {country}'s border; importers groan.",
            "{leader_title} {leader} walls off {country}'s {commodity} market from {foe}: {rate_pct}%.",
        ],
        "retaliation": [
            "Tit for tat: {country} answers {foe}'s tariffs with {rate_pct}% on its {commodity}.",
            "{country} retaliates, taxing {foe}'s {commodity} at {rate_pct}%; the trade war widens.",
            "RETALIATION: {country} matches {foe}'s duties with its own on {commodity}.",
            "{country} hits back at {foe} with tariffs on {commodity}; merchants on both sides wince.",
        ],
    },
    "TARIFF_REPEALED": {
        "commodity": [
            "{country} drops its duty on {foe}'s {commodity}; the border price falls overnight.",
            "{commodity} from {foe} flows into {country} tariff-free again.",
            "A trade thaw: {country} repeals its tariff on {foe}'s {commodity}.",
            "{country} scraps the {commodity} tariff on {foe}; importers celebrate quietly.",
        ],
    },
    "CONVOY_LOSSES": {
        "relief": [
            "Relief bound for {country} goes to the bottom: {count_word} convoys lost.",
            "{country}'s aid shipments are being sunk faster than they sail; {count_word} gone.",
            "Raiders make no distinction off {country}: {count_word} cargo runs lost, relief among them.",
            "Aid for {country} is now a target; {recent_word} convoys lost this {season}.",
        ],
        # A report can speak for one ship: a lone sinking is reported once it has waited.
        "single": [
            "A convoy bound for {country} is lost at sea; the war has found its shipping lanes.",
            "{country} counts a cargo run that never came in; the lanes are no longer safe.",
            "A ship carrying {commodity} to {country} goes down in the war zone.",
            "Raiders sink a convoy on the lanes to {country}; insurers raise their rates.",
            "Another convoy bound for {country} is sunk, {recent_word} lost this {season}.",
        ],
        "single_relief": [
            "Relief bound for {country} goes to the bottom with the convoy carrying it.",
            "Raiders sink an aid convoy on its way to {country}.",
            "The relief ship {country} was waiting for will not arrive; the war sank it.",
            "{country}'s aid convoy is lost at sea, and with it the relief {city} was promised.",
        ],
    },
    "PEACE": {
        "divine": [
            "{country} and {foes} stop fighting at noon precisely. Neither side called the halt.",
            "{country} and {foes} lay down arms mid-battle and wander home, puzzled.",
            "A peace nobody negotiated ends {country}'s war with {foes}; the diplomats bow anyway.",
            "Every general in {country} wakes up tired of the war with {foes}. It ends by lunch.",
        ],
    },
    "GOD_EDIT": {
        "up": [
            "Someone raises {country}'s {edit_field} from {edit_from} to {edit_to}. The ledgers agree, retroactively.",
            "{country}'s {edit_field} rises overnight on nobody's authority. The statisticians check twice.",
            "A hand from outside history nudges {country}'s {edit_field} upward. {capital} does not complain.",
            "{country}'s {edit_field} is revised upward by a clerk who does not exist.",
        ],
        "down": [
            "Someone lowers {country}'s {edit_field} from {edit_from} to {edit_to}. The ledgers agree, retroactively.",
            "{country}'s {edit_field} sinks overnight for no reason the ministries can find.",
            "A hand from outside history presses down on {country}'s {edit_field}. {capital} feels it by lunch.",
            "{country}'s {edit_field} is revised downward by a clerk who does not exist.",
        ],
    },
    "GOD_RELATION_SHIFT": {
        "peace": [
            "{country} and {foe} forget their war mid-sentence. The ceasefire is already signed.",
            "Someone edits the grudge between {country} and {foe} out of existence. The war goes with it.",
            "The war between {country} and {foe} is struck from the record, and then from the field.",
            "{country}'s generals receive new orders about {foe}: stop. Nobody can find who sent them.",
        ],
        "warmer": [
            "{country} and {foe} feel unaccountably fond of each other this morning.",
            "Relations between {country} and {foe} warm by decree; neither foreign ministry wrote it.",
            "Someone smooths things over between {capital} and {foe_capital}. Both claim the credit.",
            "{country} can no longer remember why it disliked {foe}.",
        ],
        "colder": [
            "{country} and {foe} wake up nursing a grudge neither can source.",
            "Relations between {country} and {foe} sour overnight; envoys check their own letters twice.",
            "A chill is poured between {capital} and {foe_capital} from somewhere above both.",
            "{country} suddenly finds {foe} insufferable. Its diplomats cannot say why.",
        ],
    },
    "INTERVENE_SECEDE": {
        "fizzled": [
            "An unseen hand offers {country}'s {region} its independence. The {region} declines.",
            "A border tries to draw itself through {country} and runs out of ink.",
            "{country} almost splits in two overnight. By morning the idea has passed.",
            "The {region} of {country} feels briefly, strangely foreign. It passes.",
        ],
    },
}

# A god-rolled INTERVENE_CHAOS fires a catalog kind flagged as an intervention and tagged
# with payload "chaos_resolved_to". These lines replace the catalog kind's own voice.
CHAOS_TEMPLATES: list[str] = [
    "The dice of history land on {country}. They come up {chaos}.",
    "You shake the world over {country} to see what falls out. It is {chaos}.",
    "Chaos is loosed on {country} and chooses {chaos}. It could have been worse.",
    "Something rolls the bones over {country}; {chaos} it is.",
    "{country} draws a card from the bottom of the deck: {chaos}.",
]


# ---------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------

_FORMATTER = string.Formatter()

# Slots every event has, whatever its record. Tests use this to prove each kind can render.
BASE_SLOTS: frozenset[str] = frozenset(
    {
        "country",
        "code",
        "leader",
        "leader_title",
        "currency",
        "currencies",
        "symbol",
        "city",
        "capital",
        "region",
        "landmass",
        "season",
        "year",
        "scapegoat",
        "breakaway",
    }
)

# Kinds whose headline names the leader who was in office when the event fired, which only
# the payload can say reliably: the successor may already be in office when this renders.
_OUSTING_KINDS = frozenset({"ASSASSINATION", "REVOLUTION", "COUP"})


@lru_cache(maxsize=None)
def template_fields(template: str) -> frozenset[str]:
    """The slot names a template uses (format spec ignored)."""
    return frozenset(
        field.split(".", 1)[0].split("[", 1)[0]
        for _, field, _, _ in _FORMATTER.parse(template)
        if field
    )


def _payload_str(event: Event, key: str) -> str | None:
    value = event.payload.get(key)
    return value if isinstance(value, str) and value else None


def _payload_num(event: Event, key: str) -> float | None:
    value = event.payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _country(world: World, code: str | None) -> Country | None:
    if not code:
        return None
    try:
        return world.country(code)
    except KeyError:
        return None


def _country_name(world: World, code: str | None) -> str | None:
    if not code:
        return None
    found = _country(world, code)
    return found.name if found is not None else code


def _parent(event: Event, world: World) -> Event | None:
    if event.parent_id is None:
        return None
    try:
        return world.log[event.parent_id]
    except (IndexError, KeyError, ValueError):
        return None


def _cause_phrase(event: Event, parent: Event | None) -> str | None:
    """The parent's kind as a noun phrase, when the parent happened to this country."""
    if parent is None or event.country is None:
        return None
    if event.country not in (parent.country, parent.country2):
        return None
    if parent.kind == event.kind:
        return None  # "unrest after the riots" says nothing
    return _CAUSE.get(parent.kind)


def _population_toll(event: Event) -> str | None:
    """The population the event cost its primary country, from its recorded stat deltas
    (population is in millions). None when nothing worth printing was lost."""
    lost_millions = -sum(
        d.delta for d in event.stat_deltas if d.target == event.country and d.stat == "population"
    )
    lost = lost_millions * 1_000_000
    if lost >= 1_000_000:
        return f"{lost_millions:.1f} million".replace(".0 million", " million")
    if lost >= 10_000:
        return f"some {round(lost, -3):,.0f}"
    if lost >= 1_000:
        return f"some {round(lost, -2):,.0f}"
    return None


def _variant(event: Event, parent: Event | None) -> str | None:
    """Name the VARIANTS list this event's own record supports, if any."""
    kind = event.kind
    parent_kind = parent.kind if parent is not None else None
    if kind == "WAR_DECLARED":
        if event.payload.get("cause") == "resource" and _payload_str(event, "commodity"):
            return "resource"
        if event.payload.get("cause") == "defense" and _payload_str(event, "ally"):
            return "defense"
    elif kind == "ELECTION":
        changed = event.payload.get("incumbent_changed")
        if changed in (True, 1):
            return "ousted"
        if changed in (False, 0):
            return "returned"
    elif kind == "LEADER_CHANGE":
        if parent_kind in ("ELECTION", "ELECTION_UPSET"):
            return "elected"
        if parent_kind in ("COUP", "REVOLUTION"):
            return "seized"
        if parent_kind in ("ASSASSINATION", "INTERVENE_ASSASSINATE"):
            return "succession"
    elif kind == "RELATION_SHIFT":
        if parent_kind in ("ALLIANCE", "INTERVENE_ALLIANCE"):
            return "pact"
        if parent_kind == "ALLIANCE_BROKEN":
            return "rupture"
        threshold = _payload_num(event, "threshold")
        relation = _payload_num(event, "relation")
        if threshold is None or relation is None:
            return None
        if relation > threshold:  # rising through the line
            if threshold < -45.0:
                return "detente"
            if threshold < 0.0:
                return "thaw"
            return "warming"
        if threshold > 0.0:  # falling through the line
            return "fading"
        return "cooling"
    elif kind == "TARIFF_IMPOSED":
        if _payload_str(event, "commodity") in _COMMODITY_NOUN and _payload_num(event, "rate"):
            if event.payload.get("reason") == "retaliation":
                return "retaliation"
            return "commodity"
    elif kind == "TARIFF_REPEALED":
        if _payload_str(event, "commodity") in _COMMODITY_NOUN:
            return "commodity"
    elif kind == "PEACE":
        if event.is_intervention:
            return "divine"
    elif kind == "CONVOY_LOSSES":
        relief = event.payload.get("relief") in (True, 1)
        if _payload_num(event, "count") == 1:
            return "single_relief" if relief else "single"
        if relief:
            return "relief"
    elif kind == "INTERVENE_SECEDE":
        if event.payload.get("seceded") in (False, 0):
            return "fizzled"
    elif kind in ("GOD_EDIT", "GOD_RELATION_SHIFT"):
        if event.payload.get("ended_war") in (True, 1):
            return "peace"
        before, after = _payload_num(event, "before"), _payload_num(event, "after")
        if before is None or after is None or before == after:
            return None
        if kind == "GOD_RELATION_SHIFT":
            return "warmer" if after > before else "colder"
        if _payload_str(event, "field") in _EDIT_FIELD:
            return "up" if after > before else "down"
    return None


def _slots(event: Event, world: World, parent: Event | None) -> dict[str, object]:
    """Every slot value available for this event. A slot that is absent (not None --
    absent) marks the templates that name it as ineligible."""
    code = event.country
    slots: dict[str, object] = {
        "code": code or "",
        "country": code or "the nation",
        "leader": "the leader",
        "leader_title": "leader",
        "currency": "currency",
        "currencies": "banknotes",
        "symbol": "",
        "city": names.city_for(event.id, code),
        "capital": names.capital_for(code),
        "region": names.region_for(event.id, code),
        "landmass": "the region",
        "season": names.season_for(event.tick, world.settings.ticks_per_year),
        "year": names.year_for(event.tick, world.settings.ticks_per_year),
    }
    fired_leader = _payload_str(event, "leader")  # the leader in office when it fired
    traits: list[str] = []
    country = _country(world, code)
    if country is not None:
        traits = list(country.leader.traits)
        slots.update(
            country=country.name,
            leader=fired_leader or country.leader.name,
            leader_title=country.leader.title,
            currency=country.currency_name,
            currencies=names.plural(country.currency_name),
            symbol=country.currency_symbol,
            landmass=country.region,
        )
        if country.grain_need > 0:
            days = country.grain_stock / country.grain_need
            if days >= 1.0:
                slots["grain_days"] = names.cardinal(int(days))
        inflation = _payload_num(event, "inflation")
        if inflation is None:
            inflation = country.inflation
        if inflation >= _DRAMATIC_INFLATION:
            slots["inflation"] = inflation
    slots["scapegoat"] = names.scapegoat_for(event.id, traits)
    breakaway = _payload_str(event, "new_country_name")
    slots["breakaway"] = breakaway or names.breakaway_for(event.id, str(slots["country"]))
    if fired_leader is not None and event.kind in _OUSTING_KINDS:
        slots["ousted"] = fired_leader

    foe = _country_name(world, event.country2)
    if foe is not None:
        slots["foe"] = foe
        slots["foe_capital"] = names.capital_for(event.country2)

    # PEACE-style events name everyone the war ended with, recorded on the payload.
    ended = _payload_str(event, "ended_wars_with")
    if ended:
        slots["foes"] = names.join_names(
            [name for part in ended.split(",") if (name := _country_name(world, part))]
        )
    elif foe is not None:
        slots["foes"] = foe

    cause = _cause_phrase(event, parent)
    if cause is not None:
        slots["cause"] = cause
    toll = _population_toll(event)
    if toll is not None:
        slots["toll"] = toll

    commodity = _payload_str(event, "commodity")
    if commodity in _COMMODITY_NOUN:
        slots["commodity"] = _COMMODITY_NOUN[commodity]
        slots["cargo"] = _CARGO[commodity]
        if commodity in _PRIZE:
            slots["prize"] = _PRIZE[commodity]
    commodities = _payload_str(event, "commodities")
    if commodities:
        nouns = [_COMMODITY_NOUN[c] for c in commodities.split(",") if c in _COMMODITY_NOUN]
        if nouns:
            slots["commodities"] = names.join_names(nouns)
    rate = _payload_num(event, "rate")
    if rate is not None and rate > 0:
        slots["rate_pct"] = f"{rate * 100:.0f}"
    share = _payload_num(event, "share")
    if share is not None and 0.0 < share < 1.0:
        slots["share_pct"] = f"{share * 100:.0f}"
    ally = _country_name(world, _payload_str(event, "ally"))
    if ally is not None:
        slots["ally"] = ally

    incumbent = _payload_str(event, "incumbent")
    if incumbent is None and event.kind == "ELECTION" and country is not None:
        if event.payload.get("incumbent_changed") in (False, 0):
            incumbent = country.leader.name  # unchanged, so the live name is the right one
    if incumbent is not None:
        slots["incumbent"] = incumbent
    new_leader = _payload_str(event, "new_leader_name")
    old_leader = _payload_str(event, "old_leader_name")
    if old_leader is None and fired_leader is not None and fired_leader != new_leader:
        old_leader = fired_leader
    if new_leader is not None:
        slots["new_leader"] = new_leader
    if old_leader is not None:
        slots["old_leader"] = old_leader

    count = _payload_num(event, "count")
    if count is not None and count >= 2:
        slots["count_word"] = names.cardinal(int(count))
    recent = _payload_num(event, "recent_total")
    if recent is not None and recent >= 2:
        slots["recent_word"] = names.cardinal(int(recent))

    asset_class = _payload_str(event, "asset_class")
    condition = _payload_num(event, "condition")
    if asset_class in _ASSET_NOUN and condition is not None and 0.0 < condition < 1.0:
        slots["asset"] = _ASSET_NOUN[asset_class]
        slots["condition_pct"] = f"{condition * 100:.0f}"
    distance = _payload_num(event, "distance_km")
    if distance is not None and distance >= 500:
        slots["distance"] = f"{round(distance, -2):,.0f} km"

    edited = _payload_str(event, "field")
    if event.kind == "GOD_EDIT" and edited in _EDIT_FIELD:
        noun, number = _EDIT_FIELD[edited]
        slots["edit_field"] = noun
        before, after = _payload_num(event, "before"), _payload_num(event, "after")
        if number is not None and before is not None and after is not None:
            slots["edit_from"] = number.format(before)
            slots["edit_to"] = number.format(after)

    chaos = _payload_str(event, "chaos_resolved_to")
    if chaos is not None and event.is_intervention:
        slots["chaos"] = _CHAOS_NOUN.get(chaos, chaos.replace("_", " ").lower())
    return slots


def _candidates(event: Event, parent: Event | None, slots: dict[str, object]) -> list[str]:
    available = slots.keys()
    pools: list[list[str]] = []
    if "chaos" in slots:
        pools.append(CHAOS_TEMPLATES)
    variant = _variant(event, parent)
    if variant is not None:
        pools.append(VARIANTS[event.kind][variant])
    pools.append(TEMPLATES[event.kind])
    for pool in pools:
        usable = [t for t in pool if template_fields(t) <= available]
        if usable:
            return usable
    # Reached only if an event arrives without the facts its kind always carries (e.g. a
    # two-target kind with no second country). The feed card shows the kind itself, so this
    # line says the rest and never raises.
    return ["Word reaches {capital}: {country} is caught up in something no one will name."]


def _finish(line: str) -> str:
    """Capitalise a line that opens with a lower-case slot ("{cause} brings ...")."""
    return line[:1].upper() + line[1:]


def render(event: Event, world: World) -> str:
    """Render one headline for `event` against `world`.

    Deterministic (§4.3.4): the candidate list depends only on the event's record and the
    world, and the pick is a SHA-256 of the kind and event id. Consumes no RNG, no
    wall-clock, no network."""
    parent = _parent(event, world)
    slots = _slots(event, world, parent)
    candidates = _candidates(event, parent, slots)
    template = candidates[names.stable_index(f"{event.kind}|{event.id}", len(candidates))]
    return _finish(template.format(**slots))
