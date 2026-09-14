/* Meddler — mock engine (v1.1).
 * Implements docs/frontend-contract.md in-browser so the UI can be judged with
 * fake-but-plausible data. Countries are DYNAMIC (secession creates new ones).
 * Swap for a WebSocket adapter with the same { connect(onMessage), send(cmd) }
 * surface to drive the real Python engine.
 */
"use strict";
(function () {

  // ---------- deterministic-ish RNG ----------
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // ---------- static world flavor ----------
  const BASE_COUNTRIES = [
    { code: "FRD", name: "Freedonia", currency: { name: "Freed", symbol: "₣" }, leader: { title: "Chancellor", name: "Vex Marlow", traits: ["technocrat", "frugal"] }, population: 41.2 },
    { code: "ELB", name: "Elbonia", currency: { name: "Crown", symbol: "₤" }, leader: { title: "President", name: "Grum Hadler", traits: ["populist", "corrupt"] }, population: 18.4 },
    { code: "KRV", name: "Kravonia", currency: { name: "Mark", symbol: "₭" }, leader: { title: "Premier", name: "Oksana Drell", traits: ["paranoid", "warhawk"] }, population: 33.7 },
    { code: "ZUB", name: "Zubrowka", currency: { name: "Zloty", symbol: "Ƶ" }, leader: { title: "King", name: "Ludvig IX", traits: ["flamboyant", "corrupt"] }, population: 9.1 },
    { code: "OST", name: "Ostrova", currency: { name: "Orn", symbol: "Ø" }, leader: { title: "Directrix", name: "Halla Norn", traits: ["technocrat", "reformist"] }, population: 27.9 },
    { code: "MER", name: "Meridia", currency: { name: "Sol", symbol: "☉" }, leader: { title: "Consul", name: "Ira Solano", traits: ["warhawk", "populist"] }, population: 22.3 },
    { code: "TAR", name: "Tarquinia", currency: { name: "Lira", symbol: "₮" }, leader: { title: "Doge", name: "Salvo Rinaldi", traits: ["corrupt", "flamboyant"] }, population: 14.6 },
    { code: "NOV", name: "Novaria", currency: { name: "Nova", symbol: "₦" }, leader: { title: "Premier", name: "Ashe Kovac", traits: ["reformist", "populist"] }, population: 30.5 }
  ];
  const START = {
    FRD: { stability: 78, inflation: 2.1, gdp: 128, grainDays: 55, fx: 1.21, treasury: 4200 },
    ELB: { stability: 46, inflation: 6.8, gdp: 62, grainDays: 24, fx: 0.44, treasury: 420 },
    KRV: { stability: 64, inflation: 3.4, gdp: 95, grainDays: 72, fx: 2.03, treasury: 2100 },
    ZUB: { stability: 58, inflation: 4.1, gdp: 74, grainDays: 40, fx: 0.87, treasury: 900 },
    OST: { stability: 71, inflation: 2.8, gdp: 110, grainDays: 35, fx: 1.55, treasury: 3100 },
    MER: { stability: 52, inflation: 5.0, gdp: 81, grainDays: 48, fx: 0.93, treasury: 640 },
    TAR: { stability: 61, inflation: 3.9, gdp: 88, grainDays: 30, fx: 1.02, treasury: 1500 },
    NOV: { stability: 69, inflation: 2.4, gdp: 102, grainDays: 44, fx: 1.34, treasury: 2600 }
  };
  const CITIES = {
    FRD: ["Marlowton", "Port Verity", "Grand Falls", "Ashford"],
    ELB: ["Grumsk", "Lower Bort", "Kneedeep", "Salt Hollow"],
    KRV: ["Drellgrad", "Vostok Yar", "Kolvo", "Zhelezny"],
    ZUB: ["Zubrow", "Pilsna", "Vranik"],
    OST: ["Nornhavn", "Kysten", "Aurvik", "Sundmark"],
    MER: ["Solano City", "Alta Mesa", "Puerto Bravo"],
    TAR: ["Rinaldi", "Porto Vecchio", "Santa Lira", "Colletta"],
    NOV: ["Kovacsted", "New Harbor", "Frostmere"]
  };
  const RIVALS = { "KRV|OST": -68, "ELB|FRD": -45, "MER|TAR": -52, "ZUB|NOV": -20 };
  const ELECTION_PERIOD = 120;
  const SPEEDS = [0.5, 1, 2, 4];

  const INTERVENTIONS = [
    { kind: "INTERVENE_DROUGHT", label: "Scorch the Harvest", icon: "\u{1F335}", targets: 1, desc: "Wither the fields. Grain reserves collapse; prices follow." },
    { kind: "INTERVENE_ASSASSINATE", label: "Remove the Leader", icon: "\u{1F5E1}️", targets: 1, desc: "The leader meets an abrupt end. Succession is rarely tidy." },
    { kind: "INTERVENE_MINT", label: "Print Money", icon: "\u{1F4B8}", targets: 1, desc: "Flood the treasury with fresh currency. Inflation sends regards." },
    { kind: "INTERVENE_TAXCUT", label: "Slash Taxes", icon: "✂️", targets: 1, desc: "The people cheer; the treasury quietly bleeds." },
    { kind: "INTERVENE_PLAGUE", label: "Loose a Plague", icon: "\u{1F9A0}", targets: 1, desc: "A sickness with no name. Cities empty; fear spreads faster." },
    { kind: "INTERVENE_QUAKE", label: "Shake the Earth", icon: "\u{1F30B}", targets: 1, desc: "A city falls to rubble in a single morning." },
    { kind: "INTERVENE_METEOR", label: "Call Down a Star", icon: "☄️", targets: 1, desc: "You point at the sky. The sky answers. Devastation." },
    { kind: "INTERVENE_SECEDE", label: "Divide the Nation", icon: "\u{1FA93}", targets: 1, desc: "Draw a new border through an old country. Two flags by dawn." },
    { kind: "INTERVENE_GOLDEN", label: "Golden Age", icon: "\u{1F3C6}", targets: 1, desc: "Bless a nation with fortune, invention, and fat harvests." },
    { kind: "INTERVENE_PEACE", label: "Broker Peace", icon: "\u{1F54A}️", targets: 1, desc: "End this country's wars and soothe its grudges." },
    { kind: "INTERVENE_WAR", label: "Ignite a War", icon: "⚔️", targets: 2, desc: "Old hatreds are lit anew. Two nations march by your hand." },
    { kind: "INTERVENE_ALLIANCE", label: "Forge an Alliance", icon: "\u{1F91D}", targets: 2, desc: "Bind two nations together in sudden, suspicious friendship." },
    { kind: "INTERVENE_EMBARGO", label: "Impose an Embargo", icon: "\u{1F6A2}", targets: 2, desc: "Choke the trade between two nations. Both will feel it." },
    { kind: "INTERVENE_INNOVATE", label: "Spark Innovation", icon: "⚡", targets: 1, desc: "A breakthrough lifts industry and spirits alike." },
    { kind: "INTERVENE_CHAOS", label: "Whisper of Chaos", icon: "\u{1F300}", targets: 1, desc: "Roll the dice of history. Even you don't know what happens." }
  ];

  const SCAPEGOAT = {
    paranoid: "foreign spies", populist: "the elites", corrupt: "the free press",
    warhawk: "enemy saboteurs", technocrat: "supply-chain shocks", reformist: "the old guard",
    frugal: "reckless spenders", flamboyant: "jealous rivals"
  };
  const TRAITS = Object.keys(SCAPEGOAT);
  const NEW_LEADERS = ["Mira Voss", "Dario Klenn", "Petra Malin", "Otto Brask", "Livia Marn", "Casimir Holt", "Freya Dunst", "Ilya Rennik", "Sable Quist", "Tomas Verel", "Anka Reyes", "Bruno Falk"];
  const SPLIT_PREFIX = ["North", "New", "Free", "Upper", "West"];

  const SEVERITY = {
    DROUGHT: 2, PRICE_SPIKE: 1, CURRENCY_SLIDE: 1, MINT: 1, TREASURY_DRAIN: 1,
    INFLATION_CRISIS: 2, UNREST: 2, CRACKDOWN: 1, COUP: 2, ELECTION: 1, LEADER_CHANGE: 1,
    SCANDAL: 1, INNOVATION: 1, GDP_BOOM: 1, WAR_DECLARED: 2, PEACE: 1,
    FAMINE_WARNING: 2, DEBT_CRISIS: 2, PLAGUE: 2, EARTHQUAKE: 2, METEOR: 2,
    SECESSION: 2, GOLDEN_AGE: 1, EMBARGO: 1, ALLIANCE: 1
  };
  // interventions are always crises visually
  INTERVENTIONS.forEach((iv) => { SEVERITY[iv.kind] = 2; });

  // Headline templates. ctx: c country, cur currency, l leader, sg scapegoat,
  // v payload, foe other country, nl new leader, city, child new-nation name.
  const T = {
    DROUGHT: [
      (x) => `DROUGHT strikes the ${x.c} breadbasket — harvest fails across the ${x.region}.`,
      (x) => `Rivers run thin in ${x.c}; granaries report their worst season in living memory.`,
      (x) => `Dust over ${x.c}: crops wither as the long dry tightens its grip.`
    ],
    PRICE_SPIKE: [
      (x) => `Grain prices spike ${x.v.pct}% across ${x.c}; market stalls empty by noon.`,
      (x) => `Bread at ${x.v.pct}% over winter prices — ${x.c} households feel the squeeze.`,
      (x) => `${x.c} traders hoard grain as prices leap ${x.v.pct}%.`
    ],
    CURRENCY_SLIDE: [
      (x) => `The ${x.cur} slides ${x.v.pct}% as ${x.c}'s trade deficit widens.`,
      (x) => `${x.c}'s ${x.cur} tumbles ${x.v.pct}%; exchange desks post new rates twice a day.`,
      (x) => `Confidence wobbles: the ${x.cur} sheds ${x.v.pct}% against the veri.`
    ],
    MINT: [
      (x) => `${x.c}'s treasury fires up the presses — ${x.v.amt}M fresh ${x.cur}s enter circulation.`,
      (x) => `Emergency minting in ${x.c}: ${x.v.amt}M ${x.cur}s printed to cover obligations.`
    ],
    TREASURY_DRAIN: [
      (x) => `${x.c} unveils an emergency grain subsidy — ${x.v.amt}M ${x.cur}s out the treasury door.`,
      (x) => `Relief spending drains ${x.v.amt}M from ${x.c}'s coffers.`
    ],
    INFLATION_CRISIS: [
      (x) => `INFLATION CRISIS: ${x.c} passes ${x.v.inflation}% — ${x.l.title} ${x.l.name} blames ${x.sg}.`,
      (x) => `Prices in ${x.c} now rise faster than wages; ${x.l.name} points the finger at ${x.sg}.`
    ],
    UNREST: [
      (x) => `Bread riots erupt in ${x.c} — ${x.l.title} ${x.l.name} blames ${x.sg}.`,
      (x) => `Crowds fill the squares of ${x.c}; chants call for ${x.l.name}'s resignation.`,
      (x) => `UNREST spreads through ${x.c}'s cities as patience runs out.`
    ],
    CRACKDOWN: [
      (x) => `${x.l.title} ${x.l.name} orders a crackdown; ${x.c}'s squares fall silent — for now.`,
      (x) => `Curfews in ${x.c}: the government answers protest with police lines.`
    ],
    COUP: [
      (x) => `COUP in ${x.c} — officers seize the broadcast tower before dawn.`,
      (x) => `${x.c}'s government falls in a night; tanks idle outside the assembly.`
    ],
    ELECTION: [
      (x) => x.v.retained ? `${x.c} votes: ${x.l.title} ${x.l.name} clings to power by a thin margin.`
        : `${x.c} heads to the polls — and the count spells trouble for the incumbent.`,
      (x) => x.v.retained ? `Ballot boxes close in ${x.c}; ${x.l.name} survives another term.`
        : `Upset in ${x.c}: voters turn on the government.`
    ],
    LEADER_CHANGE: [
      (x) => `${x.nl} takes power in ${x.c}, promising "a different road".`,
      (x) => `A new face in ${x.c}'s palace: ${x.nl} sworn in before a wary crowd.`
    ],
    SCANDAL: [
      (x) => `Leaked ledgers embarrass ${x.l.title} ${x.l.name}; ${x.c}'s papers smell blood.`,
      (x) => `Scandal in ${x.c}: missing funds traced to the ${x.l.title}'s inner circle.`
    ],
    INNOVATION: [
      (x) => `${x.c} engineers unveil a breakthrough — industry hails a new era.`,
      (x) => `Patent fever in ${x.c}: workshops race to license the new process.`
    ],
    GDP_BOOM: [
      (x) => `${x.c}'s economy surges; exporters can't fill orders fast enough.`,
      (x) => `Boom times in ${x.c} — output climbs and hiring follows.`
    ],
    WAR_DECLARED: [
      (x) => `WAR: ${x.c} declares war on ${x.foe} — border guns speak by nightfall.`,
      (x) => `${x.c} and ${x.foe} are at WAR; markets convulse across the region.`
    ],
    PEACE: [
      (x) => `Guns fall silent: ${x.c} and ${x.foe} sign an armistice.`,
      (x) => `PEACE between ${x.c} and ${x.foe} — weary capitals exhale.`
    ],
    FAMINE_WARNING: [
      (x) => `FAMINE WARNING: ${x.c}'s reserves fall below ${x.v.days} days of grain.`,
      (x) => `${x.c} rations bread; officials whisper the word no one prints.`
    ],
    DEBT_CRISIS: [
      (x) => `DEBT CRISIS: ${x.c}'s treasury runs dry — creditors circle.`,
      (x) => `${x.c} misses payments; the ${x.l.title}'s options narrow to bad and worse.`
    ],
    PLAGUE: [
      (x) => `PLAGUE in ${x.c}: fever wards overflow in ${x.city}; the roads out are watched.`,
      (x) => `A sickness moves through ${x.c}; markets close, church bells don't stop.`
    ],
    EARTHQUAKE: [
      (x) => `EARTHQUAKE levels half of ${x.city} — ${x.c} digs through the rubble.`,
      (x) => `The ground opens under ${x.city}; ${x.c} declares a national emergency.`
    ],
    METEOR: [
      (x) => `A STAR FALLS on ${x.c} — the night sky burns and ${x.city} is gone.`,
      (x) => `Impact in ${x.c}: a falling star scours the ${x.region}; ash rides the wind for days.`
    ],
    SECESSION: [
      (x) => `SECESSION: the ${x.region} breaks from ${x.c} — the flag of ${x.child} rises by dawn.`,
      (x) => `${x.c} splits in two; ${x.child} declares independence and mans the border.`
    ],
    GOLDEN_AGE: [
      (x) => `A golden season for ${x.c}: fat harvests, full ledgers, and songs in the streets.`,
      (x) => `Historians will call this ${x.c}'s golden age — everything the ${x.l.title} touches turns.`
    ],
    EMBARGO: [
      (x) => `${x.c} slams its ports shut to ${x.foe}; the docks fall quiet on both coasts.`,
      (x) => `EMBARGO: trade between ${x.c} and ${x.foe} stops at the border, ships turn back.`
    ],
    ALLIANCE: [
      (x) => `${x.c} and ${x.foe} sign a pact of friendship; old maps get nervous.`,
      (x) => `An alliance is sworn between ${x.c} and ${x.foe} — rivals take note.`
    ],
    INTERVENE_DROUGHT: [
      (x) => `✦ The sky refuses ${x.c}. Fields crack. Nobody can explain the weather.`,
      (x) => `✦ An impossible dry falls upon ${x.c}, as if willed from above.`
    ],
    INTERVENE_ASSASSINATE: [
      (x) => `✦ ${x.l.title} ${x.l.name} of ${x.c} is dead. The official story satisfies no one.`,
      (x) => `✦ A single shot in ${x.c}; ${x.l.name} does not finish the speech.`
    ],
    INTERVENE_MINT: [
      (x) => `✦ ${x.c}'s mints run all night on orders no clerk remembers signing.`,
      (x) => `✦ Crates of crisp ${x.cur}s appear in ${x.c}'s vaults. The auditors are on holiday.`
    ],
    INTERVENE_TAXCUT: [
      (x) => `✦ ${x.c} slashes taxes overnight; crowds cheer a decree no minister drafted.`,
      (x) => `✦ Tax collectors in ${x.c} are sent home. The treasury holds its breath.`
    ],
    INTERVENE_PLAGUE: [
      (x) => `✦ A sickness with no name wakes in ${x.c}. The first cough is in ${x.city}.`,
      (x) => `✦ Something sleeps no longer beneath ${x.c}; the fever spreads by market day.`
    ],
    INTERVENE_QUAKE: [
      (x) => `✦ The earth under ${x.c} is told to move. ${x.city} pays the price.`,
      (x) => `✦ At your gesture the ground buckles beneath ${x.c}.`
    ],
    INTERVENE_METEOR: [
      (x) => `✦ You point at the sky above ${x.c}. The sky answers.`,
      (x) => `✦ A star is plucked and dropped on ${x.c}. History will call it chance.`
    ],
    INTERVENE_SECEDE: [
      (x) => `✦ Old maps of ${x.c} burn. By morning there is a border where none was, and ${x.child} exists.`,
      (x) => `✦ You draw a line through ${x.c}. The line grows customs posts. ${x.child} is born.`
    ],
    INTERVENE_GOLDEN: [
      (x) => `✦ Fortune leans over ${x.c} and smiles. Everything ripens at once.`,
      (x) => `✦ A golden age is decreed for ${x.c} — by whom, no scholar can say.`
    ],
    INTERVENE_PEACE: [
      (x) => `✦ Old enemies of ${x.c} wake strangely forgiving. Doves circle the capital.`,
      (x) => `✦ A sudden thaw: ${x.c}'s feuds dissolve like morning frost.`
    ],
    INTERVENE_WAR: [
      (x) => `✦ Something whispers in two capitals at once. ${x.c} marches on ${x.foe}.`,
      (x) => `✦ Old hatred between ${x.c} and ${x.foe} is lit like dry straw.`
    ],
    INTERVENE_ALLIANCE: [
      (x) => `✦ ${x.c} and ${x.foe} wake as friends and cannot quite say why.`,
      (x) => `✦ Two flags fly side by side: ${x.c} and ${x.foe}, bound by an unseen hand.`
    ],
    INTERVENE_EMBARGO: [
      (x) => `✦ Every ship between ${x.c} and ${x.foe} finds its papers suddenly wrong.`,
      (x) => `✦ Trade between ${x.c} and ${x.foe} simply… stops. No one signed anything.`
    ],
    INTERVENE_INNOVATE: [
      (x) => `✦ A forgotten notebook surfaces in ${x.c} — its diagrams change everything.`,
      (x) => `✦ Genius strikes ${x.c} from a clear sky; the patent office queues around the block.`
    ]
  };
  const REGIONS = ["Eastern Reach", "Low Valleys", "Amber Coast", "High Steppe", "Middle Delta"];
  const CHAOS_POOL = ["DROUGHT", "SCANDAL", "INNOVATION", "PLAGUE", "EARTHQUAKE", "GOLDEN_AGE", "METEOR", "SECESSION"];

  // ---------- engine ----------
  function FakeEngine(seed) {
    this.seed = seed >>> 0;
    this.out = null;
    this.nextId = 1;
    this.tps = 1;                     // deliberately slow — 1 day per second
    this.running = false;
    this.timer = null;
    this.scrubbed = false;
    this.wasRunning = true;
    this.eventIndex = {};             // id -> { ev, tl }
    this.meta = {};                   // code -> static-ish country meta (global)
    BASE_COUNTRIES.forEach((c) => {
      this.meta[c.code] = {
        code: c.code, name: c.name, currency: c.currency, leader: c.leader,
        population: c.population, cities: CITIES[c.code].slice(), parent: null
      };
    });
    this.A = this._newTimeline("A", mulberry32(this.seed));
    this.forks = [];                  // up to 3 concurrent fork timelines
    this.focusId = "A";
  }

  FakeEngine.prototype._fork = function (id) {
    return this.forks.find((f) => f.id === id) || null;
  };
  FakeEngine.prototype._focusedFork = function () {
    return this.focusId === "A" ? null : this._fork(this.focusId);
  };
  FakeEngine.prototype._forkSummaries = function () {
    return this.forks.map((f) => ({ id: f.id, tick: f.tick, forkTick: f.forkTick, label: f.label }));
  };

  FakeEngine.prototype._newTimeline = function (id, rng) {
    const stats = {}, leaders = {}, armed = {};
    const codes = BASE_COUNTRIES.map((c) => c.code);
    BASE_COUNTRIES.forEach((c) => {
      stats[c.code] = Object.assign({}, START[c.code], { pop: c.population, taxCutUntil: -1 });
      leaders[c.code] = { title: c.leader.title, name: c.leader.name, traits: c.leader.traits.slice() };
      armed[c.code] = { infl: true, stab: true, famine: true, debt: true };
    });
    return {
      id: id, rng: rng, tick: 0, codes: codes, stats: stats, leaders: leaders,
      relations: Object.assign({}, RIVALS),
      wars: [], queue: [], events: [], children: {}, history: [], armed: armed
    };
  };

  // ---- public surface (contract §1) ----
  FakeEngine.prototype.connect = function (onMessage) {
    this.out = onMessage;
    for (let i = 0; i < 120; i++) this._advance(this.A);
    this.A.frameEvents = []; // warm-up events arrive via the snapshot, not a frame
    this._emit({
      type: "hello", protocol: 1, seed: this.seed, tps: this.tps,
      countries: this.A.codes.map((c) => this.meta[c]),
      interventions: INTERVENTIONS
    });
    this._emit(this._snapshotMsg(this.A.tick));
    this._setRunning(true);
  };

  FakeEngine.prototype.send = function (cmd) {
    this.requestId = cmd.requestId;
    try {
      switch (cmd.cmd) {
        case "pause": this._setRunning(false); break;
        case "resume":
          if (this.scrubbed) this._leaveScrub(true);
          this._setRunning(true); break;
        case "setSpeed":
          if (SPEEDS.indexOf(cmd.tps) >= 0) this.tps = cmd.tps;
          if (this.running) { this._stopTimer(); this._startTimer(); }
          this._status(); break;
        case "step": this._stepOnce(); break;
        case "worldAt": this._worldAt(cmd.tick); break;
        case "restart": this._restart(cmd.tick); break;
        case "resumeLive": this._leaveScrub(this.wasRunning); break;
        case "trace": this._trace(cmd.eventId); break;
        case "intervene": this._intervene(cmd.kind, cmd.country, cmd.atTick, cmd.target2); break;
        case "dropFork": this._dropFork(cmd.id); break;
        case "adoptFork": this._adoptFork(cmd.id); break;
        case "focusTimeline": this._focusTimeline(cmd.id); break;
        case "godEdit": this._godEdit(cmd.code, cmd.field, cmd.value); break;
        case "godRelation": this._godRelation(cmd.a, cmd.b, cmd.delta); break;
        case "godPeace": this._godPeace(cmd.code, cmd.foe); break;
        case "countryDetail": this._countryDetail(cmd.code, cmd.tl); break;
        default: this._emit({ type: "toast", text: "Unknown command: " + cmd.cmd, tone: "warn" });
      }
    } finally {
      this.requestId = null;
    }
  };

  // ---- plumbing ----
  // Replies are delivered on a later microtask, as a socket would deliver them. Delivering
  // inside send() let a reply open its overlay before the caller had put up its loading
  // overlay, which then covered the result for good.
  FakeEngine.prototype._emit = function (msg) {
    if (this.requestId != null && msg.requestId == null) msg.requestId = this.requestId;
    const out = this.out;
    if (out) Promise.resolve().then(function () { out(msg); });
  };
  FakeEngine.prototype._status = function () {
    this._emit({
      type: "status", running: this.running, tps: this.tps,
      focus: this.focusId, forks: this._forkSummaries()
    });
  };
  FakeEngine.prototype._setRunning = function (on) {
    this.running = on;
    this._stopTimer();
    if (on) this._startTimer();
    this._status();
  };
  FakeEngine.prototype._startTimer = function () {
    const self = this;
    this.timer = setInterval(function () { self._tickInterval(); }, 1000 / this.tps);
  };
  FakeEngine.prototype._stopTimer = function () {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  };
  FakeEngine.prototype._tickInterval = function () {
    this._advance(this.A);
    this.forks.forEach((f) => this._advance(f));
    this._emitFrame();
  };
  FakeEngine.prototype._stepOnce = function () {
    if (this.scrubbed) return;
    this._advance(this.A);
    this.forks.forEach((f) => this._advance(f));
    this._emitFrame();
  };

  // ---- frame assembly ----
  FakeEngine.prototype._emitFrame = function () {
    const A = this.A, B = this._focusedFork();
    const frame = { type: "frame", tick: B ? B.tick : A.tick, focus: this.focusId, timelines: {} };
    if (!B) {
      frame.timelines.A = { stats: this._round(A, A.stats), events: A.frameEvents || [] };
      frame.wars = A.wars.map((w) => [w.a, w.b]);
      frame.leaders = A.leaders;
    } else {
      const tau = B.tick;
      const hist = A.history[tau] || A.history[A.history.length - 1];
      frame.timelines.A = {
        stats: this._roundStats(hist.stats, hist.wars),
        events: (tau === B.forkTick) ? [] : A.events.filter((e) => e.tick === tau)
      };
      frame.timelines.B = { stats: this._round(B, B.stats), events: B.frameEvents || [] };
      frame.wars = hist.wars.map((w) => [w.a, w.b]);
      frame.warsB = B.wars.map((w) => [w.a, w.b]);
      frame.leaders = hist.leaders;
      frame.leadersB = B.leaders;
      const diff = {};
      B.codes.forEach((code) => {
        const a = hist.stats[code], b = B.stats[code];
        if (!a || !b) return;
        diff[code] = { stability: [a.stability, b.stability], inflation: [a.inflation, b.inflation], gdp: [a.gdp, b.gdp] };
      });
      frame.diff = diff;
    }
    A.frameEvents = [];
    this.forks.forEach((f) => { f.frameEvents = []; });
    this._emit(frame);
  };
  FakeEngine.prototype._round = function (TL, stats) {
    const wars = TL.wars.map((w) => [w.a, w.b]);
    return this._roundStats(stats, wars.map((p) => ({ a: p[0], b: p[1] })));
  };
  FakeEngine.prototype._roundStats = function (stats, wars) {
    const out = {};
    for (const code in stats) {
      const s = stats[code];
      out[code] = {
        stability: Math.round(s.stability * 10) / 10,
        inflation: Math.round(s.inflation * 10) / 10,
        gdp: Math.round(s.gdp * 10) / 10,
        grainDays: Math.round(s.grainDays * 10) / 10,
        fx: Math.round(s.fx * 1000) / 1000,
        treasury: Math.round(s.treasury),
        pop: Math.round(s.pop * 10) / 10,
        war: wars.some((w) => w.a === code || w.b === code)
      };
    }
    return out;
  };

  // ---- simulation core ----
  FakeEngine.prototype._advance = function (TL) {
    TL.tick++;
    TL.frameEvents = TL.frameEvents || [];
    const rng = TL.rng;

    TL.codes.forEach((code) => {
      const s = TL.stats[code];
      const atWar = TL.wars.some((w) => w.a === code || w.b === code);
      const shortage = s.grainDays < 15;
      s.inflation += (2 - s.inflation) * 0.012 + (rng() - 0.5) * 0.10 + (shortage ? 0.07 : 0) + (atWar ? 0.03 : 0);
      s.inflation = clamp(s.inflation, -1, 40);
      s.stability += (s.inflation > 8 ? -0.35 : (s.inflation < 4 ? 0.09 : 0))
        + (s.grainDays < 10 ? -0.5 : 0) + (rng() - 0.5) * 0.35 + (atWar ? -0.28 : 0);
      s.stability = clamp(s.stability, 1, 99);
      s.gdp *= 1 + ((s.stability - 50) / 50) * 0.0007 + (rng() - 0.5) * 0.002 - (atWar ? 0.0012 : 0);
      s.grainDays += (rng() - 0.5) * 1.3 + (s.grainDays < 45 ? 0.28 : -0.05);
      s.grainDays = clamp(s.grainDays, 0, 120);
      s.fx *= 1 + (2 - s.inflation) * 0.0004 + (rng() - 0.5) * 0.0022;
      s.pop *= 1.00002;
      const taxMult = TL.tick < s.taxCutUntil ? 0.62 : 1.0;
      s.treasury += Math.round(s.gdp * 0.09 * taxMult * 10) / 10 - 8.4 - (atWar ? 5.5 : 0);
    });

    // thresholds (with hysteresis re-arm)
    TL.codes.forEach((code) => {
      const s = TL.stats[code], arm = TL.armed[code];
      if (arm.infl && s.inflation > 8.5) { arm.infl = false; this._fire(TL, "INFLATION_CRISIS", code, null, 0, { inflation: Math.round(s.inflation * 10) / 10 }); }
      else if (!arm.infl && s.inflation < 6) arm.infl = true;
      if (arm.stab && s.stability < 33) { arm.stab = false; this._fire(TL, "UNREST", code, null, 0, {}); }
      else if (!arm.stab && s.stability > 45) arm.stab = true;
      if (arm.famine && s.grainDays < 12) { arm.famine = false; this._fire(TL, "FAMINE_WARNING", code, null, 0, { days: Math.round(s.grainDays) }); }
      else if (!arm.famine && s.grainDays > 25) arm.famine = true;
      if (arm.debt && s.treasury < 0) { arm.debt = false; this._fire(TL, "DEBT_CRISIS", code, null, 0, {}); }
      else if (!arm.debt && s.treasury > 400) arm.debt = true;
    });

    // exogenous rolls (tuned sparse — the world should breathe slowly)
    if (rng() < 0.006) this._fire(TL, "DROUGHT", this._pick(TL), null, 0, {});
    if (rng() < 0.004) this._fire(TL, "SCANDAL", this._pick(TL), null, 0, {});
    if (rng() < 0.0035) this._fire(TL, "INNOVATION", this._pick(TL), null, 0, {});
    if (rng() < 0.002) this._fire(TL, "PLAGUE", this._pick(TL), null, 0, {});
    if (rng() < 0.002) this._fire(TL, "EARTHQUAKE", this._pick(TL), null, 0, {});
    if (rng() < 0.0012) this._fire(TL, "GOLDEN_AGE", this._pick(TL), null, 0, {});
    if (rng() < 0.0002) this._fire(TL, "METEOR", this._pick(TL), null, 0, {});
    if (rng() < 0.003) {
      const pair = this._hostilePair(TL);
      if (pair && !TL.wars.length) this._fire(TL, "WAR_DECLARED", pair[0], null, 0, { foe: pair[1] });
    }
    TL.wars.forEach((w) => {
      if (TL.tick - w.since > 90 && rng() < 0.012) this._fire(TL, "PEACE", w.a, null, 0, { foe: w.b });
    });

    // elections on a calendar
    TL.codes.forEach((code, i) => {
      if (TL.tick > 0 && (TL.tick + i * 17) % ELECTION_PERIOD === 0) {
        const s = TL.stats[code];
        const pChange = s.stability < 45 ? 0.65 : 0.3;
        this._fire(TL, "ELECTION", code, null, 0, { retained: rng() > pChange });
      }
    });

    // scheduled consequences
    const due = TL.queue.filter((q) => q.due <= TL.tick);
    TL.queue = TL.queue.filter((q) => q.due > TL.tick);
    due.forEach((q) => {
      if (q.depth <= 6 && TL.stats[q.country]) this._fire(TL, q.kind, q.country, q.parentId, q.depth, q.payload || {});
    });

    // snapshot for scrubbing / fork alignment
    TL.history[TL.tick] = {
      stats: JSON.parse(JSON.stringify(TL.stats)),
      wars: TL.wars.map((w) => ({ a: w.a, b: w.b, since: w.since })),
      leaders: JSON.parse(JSON.stringify(TL.leaders))
    };
  };

  FakeEngine.prototype._pick = function (TL) {
    return TL.codes[Math.floor(TL.rng() * TL.codes.length)];
  };
  FakeEngine.prototype._hostilePair = function (TL) {
    const keys = Object.keys(TL.relations).sort();
    for (const k of keys) {
      if (TL.relations[k] < -60) {
        const pair = k.split("|");
        if (TL.stats[pair[0]] && TL.stats[pair[1]]) return pair;
      }
    }
    return null;
  };
  FakeEngine.prototype._sched = function (TL, due, kind, country, parentId, depth, payload) {
    TL.queue.push({ due: due, kind: kind, country: country, parentId: parentId, depth: depth, payload: payload });
  };

  // ---- secession: creates a NEW country ----
  FakeEngine.prototype._secede = function (TL, code, rng) {
    const parentMeta = this.meta[code];
    let childCode = null;
    for (let i = 0; i < 26 && !childCode; i++) {
      const cand = "N" + code.slice(0, 1) + String.fromCharCode(65 + Math.floor(rng() * 26));
      if (!this.meta[cand]) childCode = cand;
    }
    if (!childCode) return null;
    const prefix = SPLIT_PREFIX[Math.floor(rng() * SPLIT_PREFIX.length)];
    const childName = prefix + " " + parentMeta.name;
    const ps = TL.stats[code];
    const share = 0.32 + rng() * 0.14;

    const childMeta = {
      code: childCode, name: childName,
      currency: { name: parentMeta.currency.name, symbol: parentMeta.currency.symbol },
      leader: null, population: Math.round(ps.pop * share * 10) / 10,
      cities: [prefix + "ton", "Fort " + prefix, parentMeta.cities[parentMeta.cities.length - 1]],
      parent: code
    };
    const childLeader = this._genLeader(rng, "Provisional Chair");
    childMeta.leader = childLeader;
    this.meta[childCode] = childMeta;

    TL.codes.push(childCode);
    TL.stats[childCode] = {
      stability: 55, inflation: ps.inflation + 1, gdp: ps.gdp * share,
      grainDays: ps.grainDays * 0.8, fx: ps.fx * 0.9,
      treasury: Math.round(ps.treasury * 0.3), pop: ps.pop * share, taxCutUntil: -1
    };
    TL.leaders[childCode] = childLeader;
    TL.armed[childCode] = { infl: true, stab: true, famine: true, debt: true };
    // the rump state
    ps.gdp *= (1 - share); ps.pop *= (1 - share);
    ps.treasury = Math.round(ps.treasury * 0.7);
    ps.stability = clamp(ps.stability + 8, 1, 99); // the boil is lanced
    TL.relations[[code, childCode].sort().join("|")] = -70;

    this._emit({ type: "countryAdded", tl: TL.id, country: childMeta, parent: code, tick: TL.tick });
    return childMeta;
  };
  FakeEngine.prototype._genLeader = function (rng, title) {
    const name = NEW_LEADERS[Math.floor(rng() * NEW_LEADERS.length)];
    const t1 = TRAITS[Math.floor(rng() * TRAITS.length)];
    let t2 = TRAITS[Math.floor(rng() * TRAITS.length)];
    if (t2 === t1) t2 = TRAITS[(TRAITS.indexOf(t1) + 3) % TRAITS.length];
    return { title: title, name: name, traits: [t1, t2] };
  };

  // fire an event: apply effects, render headline, schedule children
  FakeEngine.prototype._fire = function (TL, kind, code, parentId, depth, payload, forceIv) {
    const rng = TL.rng;
    const s = TL.stats[code];
    if (!s) return null;
    const meta = this.meta[code];
    const id = this.nextId++;
    const t = TL.tick;
    const ledger = [];
    payload = payload || {};
    let newLeaderName = null;
    let childMeta = null;

    switch (kind) {
      case "DROUGHT": case "INTERVENE_DROUGHT":
        s.grainDays *= 0.45;
        this._sched(TL, t + 3 + Math.floor(rng() * 6), "PRICE_SPIKE", code, id, depth + 1, { pct: 120 + Math.floor(rng() * 140) });
        break;
      case "PRICE_SPIKE":
        s.inflation += 1.6;
        if (rng() < 0.75) this._sched(TL, t + 4 + Math.floor(rng() * 7), "CURRENCY_SLIDE", code, id, depth + 1, { pct: 5 + Math.floor(rng() * 7) });
        if (rng() < 0.5) this._sched(TL, t + 2 + Math.floor(rng() * 5), "TREASURY_DRAIN", code, id, depth + 1, { amt: Math.round(Math.max(50, s.treasury * 0.08)) });
        break;
      case "CURRENCY_SLIDE":
        s.fx *= 1 - (payload.pct || 8) / 100;
        s.inflation += 0.8;
        break;
      case "MINT": case "INTERVENE_MINT": {
        const amt = kind === "MINT" ? 2500 : 5000;
        s.treasury += amt; s.inflation += kind === "MINT" ? 2.2 : 2.8;
        payload.amt = amt;
        ledger.push({ from: "veri:mint", to: code + ".treasury", amount: amt, currency: code });
        if (rng() < 0.9) this._sched(TL, t + 3 + Math.floor(rng() * 6), "CURRENCY_SLIDE", code, id, depth + 1, { pct: 6 + Math.floor(rng() * 6) });
        break;
      }
      case "TREASURY_DRAIN":
        s.treasury -= payload.amt || 100;
        ledger.push({ from: code + ".treasury", to: code + ".households", amount: payload.amt || 100, currency: code });
        break;
      case "INFLATION_CRISIS":
        if (rng() < 0.7) this._sched(TL, t + 4 + Math.floor(rng() * 9), "UNREST", code, id, depth + 1, {});
        break;
      case "UNREST": {
        s.stability -= 9;
        const traits = TL.leaders[code].traits;
        if (s.stability < 15 && rng() < 0.15) this._sched(TL, t + 5 + Math.floor(rng() * 10), "SECESSION", code, id, depth + 1, {});
        else if (rng() < (s.stability < 25 ? 0.5 : 0.15)) this._sched(TL, t + 6 + Math.floor(rng() * 14), "COUP", code, id, depth + 1, {});
        else if (rng() < ((traits.includes("warhawk") || traits.includes("paranoid")) ? 0.5 : 0.2))
          this._sched(TL, t + 3 + Math.floor(rng() * 5), "CRACKDOWN", code, id, depth + 1, {});
        break;
      }
      case "CRACKDOWN": s.stability += 4; break;
      case "COUP": case "INTERVENE_ASSASSINATE":
        if (kind === "INTERVENE_ASSASSINATE") {
          s.stability -= 15;
          if (rng() < 0.8) this._sched(TL, t + 4 + Math.floor(rng() * 8), "UNREST", code, id, depth + 1, {});
        }
        this._sched(TL, t + 1 + Math.floor(rng() * 2), "LEADER_CHANGE", code, id, depth + 1, {});
        break;
      case "ELECTION":
        if (!payload.retained) this._sched(TL, t + 1, "LEADER_CHANGE", code, id, depth + 1, {});
        break;
      case "LEADER_CHANGE": {
        const nl = this._genLeader(rng, TL.leaders[code].title);
        newLeaderName = nl.name;
        TL.leaders[code] = nl;
        s.stability += 6;
        payload.newLeader = newLeaderName;
        break;
      }
      case "SCANDAL":
        s.stability -= 6;
        if (rng() < 0.3) this._sched(TL, t + 5 + Math.floor(rng() * 10), "UNREST", code, id, depth + 1, {});
        break;
      case "INNOVATION": case "INTERVENE_INNOVATE":
        s.gdp *= kind === "INNOVATION" ? 1.05 : 1.07;
        this._sched(TL, t + 6 + Math.floor(rng() * 9), "GDP_BOOM", code, id, depth + 1, {});
        break;
      case "GDP_BOOM": s.gdp *= 1.04; s.stability += 3; break;
      case "WAR_DECLARED": {
        const foe = payload.foe;
        if (!TL.stats[foe]) break;
        TL.wars.push({ a: code, b: foe, since: t });
        s.stability -= 8; TL.stats[foe].stability -= 8;
        TL.relations[[code, foe].sort().join("|")] = -90;
        break;
      }
      case "PEACE": case "INTERVENE_PEACE": {
        const ended = TL.wars.filter((w) => w.a === code || w.b === code);
        TL.wars = TL.wars.filter((w) => w.a !== code && w.b !== code);
        ended.forEach((w) => {
          const other = w.a === code ? w.b : w.a;
          payload.foe = payload.foe || other;
          if (TL.stats[other]) TL.stats[other].stability += 6;
          TL.relations[[code, other].sort().join("|")] = kind === "PEACE" ? -20 : 10;
        });
        if (kind === "INTERVENE_PEACE") {
          Object.keys(TL.relations).forEach((k) => {
            if (k.indexOf(code) >= 0) TL.relations[k] = Math.min(60, TL.relations[k] + 40);
          });
        }
        s.stability += kind === "PEACE" ? 6 : 5;
        if (!payload.foe) payload.foe = null;
        break;
      }
      case "FAMINE_WARNING":
        s.stability -= 4;
        if (rng() < 0.5) this._sched(TL, t + 3 + Math.floor(rng() * 8), "UNREST", code, id, depth + 1, {});
        break;
      case "DEBT_CRISIS":
        this._sched(TL, t + 2 + Math.floor(rng() * 4), "MINT", code, id, depth + 1, {});
        break;
      case "INTERVENE_TAXCUT":
        s.stability += 7; s.taxCutUntil = t + 150;
        if (rng() < 0.7) this._sched(TL, t + 8 + Math.floor(rng() * 10), "GDP_BOOM", code, id, depth + 1, {});
        break;
      case "PLAGUE": case "INTERVENE_PLAGUE":
        s.stability -= 10; s.pop *= 0.965; s.gdp *= 0.96;
        if (rng() < 0.6) this._sched(TL, t + 6 + Math.floor(rng() * 10), "UNREST", code, id, depth + 1, {});
        if (rng() < 0.4) this._sched(TL, t + 4 + Math.floor(rng() * 6), "TREASURY_DRAIN", code, id, depth + 1, { amt: Math.round(Math.max(80, s.treasury * 0.1)) });
        break;
      case "EARTHQUAKE": case "INTERVENE_QUAKE":
        s.gdp *= 0.95; s.stability -= 6;
        this._sched(TL, t + 2 + Math.floor(rng() * 4), "TREASURY_DRAIN", code, id, depth + 1, { amt: Math.round(Math.max(100, s.treasury * 0.12)) });
        break;
      case "METEOR": case "INTERVENE_METEOR":
        s.gdp *= 0.82; s.stability -= 18; s.pop *= 0.94; s.grainDays *= 0.6;
        this._sched(TL, t + 3 + Math.floor(rng() * 5), "PRICE_SPIKE", code, id, depth + 1, { pct: 200 + Math.floor(rng() * 150) });
        if (rng() < 0.7) this._sched(TL, t + 8 + Math.floor(rng() * 10), "UNREST", code, id, depth + 1, {});
        break;
      case "SECESSION": case "INTERVENE_SECEDE":
        childMeta = this._secede(TL, code, rng);
        if (!childMeta) return null;
        payload.child = childMeta.name;
        payload.childCode = childMeta.code;
        break;
      case "GOLDEN_AGE": case "INTERVENE_GOLDEN":
        s.stability += 8; s.gdp *= 1.05; s.grainDays += 25; s.inflation = Math.max(1, s.inflation - 1.5);
        this._sched(TL, t + 10 + Math.floor(rng() * 12), "GDP_BOOM", code, id, depth + 1, {});
        break;
      case "INTERVENE_WAR":
        this._sched(TL, t + 1, "WAR_DECLARED", code, id, depth + 1, { foe: payload.foe });
        break;
      case "EMBARGO": case "INTERVENE_EMBARGO": {
        const foe = payload.foe;
        if (TL.stats[foe]) {
          TL.relations[[code, foe].sort().join("|")] = -50;
          s.gdp *= 0.975; TL.stats[foe].gdp *= 0.975;
          if (rng() < 0.5) this._sched(TL, t + 5 + Math.floor(rng() * 8), "CURRENCY_SLIDE", foe, id, depth + 1, { pct: 4 + Math.floor(rng() * 5) });
        }
        break;
      }
      case "ALLIANCE": case "INTERVENE_ALLIANCE": {
        const foe = payload.foe;
        if (TL.stats[foe]) {
          TL.wars = TL.wars.filter((w) => !((w.a === code && w.b === foe) || (w.a === foe && w.b === code)));
          TL.relations[[code, foe].sort().join("|")] = 70;
          s.stability += 4; TL.stats[foe].stability += 4;
        }
        break;
      }
    }

    // headline
    const leader = TL.leaders[code];
    const foeMeta = payload.foe ? this.meta[payload.foe] : null;
    const ctx = {
      c: meta.name, cur: meta.currency.name, l: leader,
      sg: SCAPEGOAT[leader.traits[0]] || "fate", v: payload,
      foe: foeMeta ? foeMeta.name : "its neighbor",
      nl: newLeaderName || leader.name,
      city: meta.cities[id % meta.cities.length],
      child: payload.child || "the new republic",
      region: REGIONS[id % REGIONS.length]
    };
    const templates = T[kind] || [(x) => kind + " in " + x.c + "."];
    const headline = templates[id % templates.length](ctx);

    const ev = {
      id: id, tick: t, kind: kind, severity: SEVERITY[kind] != null ? SEVERITY[kind] : 1,
      country: code, parentId: parentId, depth: depth,
      intervention: kind.indexOf("INTERVENE_") === 0 || !!forceIv,
      headline: headline, payload: payload, ledger: ledger
    };
    TL.events.push(ev);
    TL.frameEvents = TL.frameEvents || [];
    TL.frameEvents.push(ev);
    this.eventIndex[id] = { ev: ev, tl: TL.id };
    if (parentId != null) {
      if (!TL.children[parentId]) TL.children[parentId] = [];
      TL.children[parentId].push(id);
    }
    return ev;
  };

  // ---- scrubbing ----
  FakeEngine.prototype._worldAt = function (tick) {
    const A = this.A;
    tick = clamp(Math.round(tick), 1, A.tick);
    if (!this.scrubbed) this.wasRunning = this.running;
    this.scrubbed = true;
    this._setRunning(false);
    this._emit(this._snapshotMsg(tick));
  };
  FakeEngine.prototype._leaveScrub = function (resume) {
    this.scrubbed = false;
    this._emit(this._snapshotMsg(this.A.tick));
    if (resume) this._setRunning(true); else this._status();
  };
  FakeEngine.prototype._snapshotMsg = function (tick) {
    const A = this.A;
    const hist = A.history[tick] || A.history[A.tick];
    const spark = {};
    for (const code in hist.stats) {
      const st = [], inf = [], fx = [];
      for (let k = Math.max(1, tick - 89); k <= tick; k++) {
        const h = A.history[k];
        if (!h || !h.stats[code]) continue;
        st.push(h.stats[code].stability);
        inf.push(h.stats[code].inflation);
        fx.push(h.stats[code].fx);
      }
      spark[code] = { stability: st, inflation: inf, fx: fx };
    }
    return {
      type: "snapshot", tick: tick, live: A.tick, scrubbed: this.scrubbed,
      stats: this._roundStats(hist.stats, hist.wars),
      wars: hist.wars.map((w) => [w.a, w.b]), spark: spark,
      recentEvents: A.events.filter((e) => e.tick <= tick).slice(-30),
      leaders: hist.leaders
    };
  };

  // ---- checkpoint restart (v1.3): rewind PRIME, forget the future ----
  FakeEngine.prototype._restart = function (tick) {
    if (this.forks.length) {
      this._emit({ type: "toast", text: "Genesis waits for no drafts — dissolve your forks first.", tone: "warn" });
      return;
    }
    const A = this.A;
    const t = clamp(Math.round(tick), 1, A.tick);
    const hist = A.history[t];
    if (!hist) { this._emit({ type: "toast", text: "No history at t" + t, tone: "warn" }); return; }
    A.tick = t;
    A.history.length = t + 1;
    A.stats = JSON.parse(JSON.stringify(hist.stats));
    A.leaders = JSON.parse(JSON.stringify(hist.leaders));
    A.wars = hist.wars.map((w) => ({ a: w.a, b: w.b, since: w.since }));
    A.codes = A.codes.filter((c) => A.stats[c]); // countries born later un-happen
    A.events = A.events.filter((e) => e.tick <= t);
    const alive = {};
    A.events.forEach((e) => { alive[e.id] = true; });
    A.queue = A.queue.filter((q) => q.parentId == null || alive[q.parentId]);
    A.children = {};
    A.events.forEach((e) => {
      if (e.parentId == null) return;
      if (!A.children[e.parentId]) A.children[e.parentId] = [];
      A.children[e.parentId].push(e.id);
    });
    A.frameEvents = [];
    // a rewound world does not replay itself — new dice
    A.rng = mulberry32((this.seed ^ Math.imul(t + 1, 2654435761)) >>> 0);
    this.scrubbed = false;
    this._emit(this._snapshotMsg(A.tick));
    this._setRunning(true);
  };

  // ---- country dossier ----
  FakeEngine.prototype._countryDetail = function (code, tl) {
    const TL = (tl && tl !== "A" && this._fork(tl)) ? this._fork(tl) : this.A;
    const meta = this.meta[code];
    const s = TL.stats[code];
    if (!meta || !s) { this._emit({ type: "toast", text: "No such country.", tone: "warn" }); return; }
    const series = { stability: [], inflation: [], gdp: [], fx: [], treasury: [] };
    const ticks = [];
    const step = Math.max(1, Math.floor(TL.tick / 240));
    for (let k = 1; k <= TL.tick; k += step) {
      const h = TL.history[k];
      if (!h || !h.stats[code]) continue;
      ticks.push(k);
      series.stability.push(h.stats[code].stability);
      series.inflation.push(h.stats[code].inflation);
      series.gdp.push(h.stats[code].gdp);
      series.fx.push(h.stats[code].fx);
      series.treasury.push(h.stats[code].treasury);
    }
    const relations = [];
    Object.keys(TL.relations).sort().forEach((k) => {
      const pair = k.split("|");
      if (pair[0] === code || pair[1] === code) {
        const other = pair[0] === code ? pair[1] : pair[0];
        if (TL.stats[other]) relations.push({ code: other, value: Math.round(TL.relations[k]) });
      }
    });
    const cityShare = [0.34, 0.22, 0.16, 0.12];
    const cities = meta.cities.map((name, i) => ({
      name: name, pop: Math.round(s.pop * (cityShare[i] || 0.08) * 10) / 10, capital: i === 0
    }));
    this._emit({
      type: "countryDetail", code: code, tl: TL.id, meta: meta,
      stats: this._roundStats((function (o) { const d = {}; d[code] = o; return d; })(s), TL.wars)[code],
      leader: TL.leaders[code],
      ticks: ticks, series: series, relations: relations, cities: cities,
      recentEvents: TL.events.filter((e) => e.country === code).slice(-15),
      bornAt: meta.parent ? (ticks.length ? ticks[0] : null) : 0
    });
  };

  // ---- trace ----
  FakeEngine.prototype._trace = function (eventId) {
    const entry = this.eventIndex[eventId];
    if (!entry) { this._emit({ type: "toast", text: "Event not found.", tone: "warn" }); return; }
    let root = entry.ev;
    while (root.parentId != null && this.eventIndex[root.parentId]) {
      root = this.eventIndex[root.parentId].ev;
    }
    const nodes = [];
    const self = this;
    (function walk(ev, d) {
      if (nodes.length >= 60) return;
      nodes.push({
        id: ev.id, tick: ev.tick, kind: ev.kind, severity: ev.severity,
        country: ev.country, headline: ev.headline, intervention: ev.intervention, d: d
      });
      let kids = (self.A.children[ev.id] || []).slice();
      self.forks.forEach((f) => { kids = kids.concat(f.children[ev.id] || []); });
      kids.forEach(function (cid) {
        const c = self.eventIndex[cid];
        if (c) walk(c.ev, d + 1);
      });
    })(root, 0);
    this._emit({ type: "trace", selectedId: eventId, nodes: nodes });
  };

  // ---- fork ----
  FakeEngine.prototype._intervene = function (kind, country, atTick, target2) {
    if (this.forks.length >= 3) { this._emit({ type: "toast", text: "Three concurrent forks is plenty, even for a god.", tone: "warn" }); return; }
    const A = this.A;
    const t = clamp(Math.round(atTick != null ? atTick : A.tick), 1, A.tick);
    const hist = A.history[t];
    if (!hist) { this._emit({ type: "toast", text: "No history at t" + t, tone: "warn" }); return; }

    let forkId = null;
    ["B", "C", "D", "E"].some((cand) => {
      if (!this._fork(cand)) { forkId = cand; return true; }
      return false;
    });
    const B = this._newTimeline(forkId, mulberry32((this.seed ^ Math.imul(t + this.forks.length * 7919, 0x9E3779B9)) >>> 0));
    B.tick = t;
    B.forkTick = t;
    B.codes = Object.keys(hist.stats);
    B.stats = JSON.parse(JSON.stringify(hist.stats));
    B.wars = hist.wars.map((w) => ({ a: w.a, b: w.b, since: w.since }));
    B.leaders = JSON.parse(JSON.stringify(hist.leaders));
    B.armed = {};
    B.codes.forEach((c) => { B.armed[c] = { infl: true, stab: true, famine: true, debt: true }; });
    B.relations = Object.assign({}, A.relations);
    B.history = A.history.slice(0, t + 1);
    B.events = A.events.filter((e) => e.tick <= t).slice(); // shared pre-fork history
    B.frameEvents = [];

    const iv = INTERVENTIONS.find((x) => x.kind === kind);
    const cMeta = this.meta[country];
    let label = (iv ? iv.label : kind) + " → " + (cMeta ? cMeta.name : country);
    if (target2 && this.meta[target2]) label += " & " + this.meta[target2].name;
    B.label = label;
    this.forks.push(B);
    this.focusId = B.id;

    if (kind === "INTERVENE_CHAOS") {
      const chosen = CHAOS_POOL[Math.floor(B.rng() * CHAOS_POOL.length)];
      this._fire(B, chosen, country, null, 0, {}, true);
    } else {
      this._fire(B, kind, country, null, 0, target2 ? { foe: target2 } : {});
    }

    this.scrubbed = false;
    this._emit({
      type: "forkStarted", id: B.id, tick: t, label: label,
      sharedRecent: A.events.filter((e) => e.tick <= t).slice(-20)
    });
    this._status();
    this._emitFrame();
    this._setRunning(true);
  };

  FakeEngine.prototype._dropFork = function (id) {
    const f = this._fork(id);
    if (!f) return;
    this.forks = this.forks.filter((x) => x.id !== id);
    if (this.focusId === id) this.focusId = "A";
    this._emit({ type: "forkDropped", id: id });
    this._status();
    if (this.focusId === "A") this._emit(this._snapshotMsg(this.A.tick));
  };

  FakeEngine.prototype._adoptFork = function (id) {
    const f = this._fork(id);
    if (!f) return;
    f.id = "A";
    f.events.forEach((e) => { if (this.eventIndex[e.id]) this.eventIndex[e.id].tl = "A"; });
    this.A = f;
    // sibling forks branched from a prime that no longer exists — they dissolve
    this.forks = [];
    this.focusId = "A";
    this._emit({ type: "forkAdopted", id: id });
    this._status();
    this._emit(this._snapshotMsg(this.A.tick));
  };

  FakeEngine.prototype._focusTimeline = function (id) {
    if (id !== "A" && !this._fork(id)) return;
    this.focusId = id;
    this._status();
    if (id === "A") {
      this._emit(this._snapshotMsg(this.A.tick));
    } else {
      const F = this._fork(id);
      const hist = this.A.history[F.tick] || this.A.history[this.A.history.length - 1];
      this._emit({
        type: "timelineFocus", id: id, tick: F.tick, forkTick: F.forkTick, label: F.label,
        statsA: this._roundStats(hist.stats, hist.wars),
        statsB: this._round(F, F.stats),
        warsA: hist.wars.map((w) => [w.a, w.b]),
        warsB: F.wars.map((w) => [w.a, w.b]),
        leadersA: hist.leaders, leadersB: F.leaders,
        recentA: this.A.events.filter((e) => e.tick <= F.tick).slice(-30),
        recentB: F.events.slice(-30)
      });
    }
  };

  // ---- god edits: direct reality manipulation on the focused timeline ----
  FakeEngine.prototype._godTL = function () {
    return this.focusId === "A" ? this.A : (this._fork(this.focusId) || this.A);
  };
  FakeEngine.prototype._godEdit = function (code, field, value) {
    const TL = this._godTL();
    const s = TL.stats[code];
    if (!s) return;
    const v = Number(value);
    switch (field) {
      case "stability": s.stability = clamp(v, 1, 99); break;
      case "inflation": s.inflation = clamp(v, -1, 40); break;
      case "gdp": s.gdp = clamp(v, 5, 600); break;
      case "pop": s.pop = clamp(v, 0.3, 200); break;
      case "treasury": s.treasury = clamp(v, -5000, 20000); break;
      case "grainDays": s.grainDays = clamp(v, 0, 120); break;
      case "fx": s.fx = clamp(v, 0.05, 10); break;
    }
  };
  FakeEngine.prototype._godRelation = function (a, b, delta) {
    const TL = this._godTL();
    if (!TL.stats[a] || !TL.stats[b]) return;
    const k = [a, b].sort().join("|");
    TL.relations[k] = clamp((TL.relations[k] || 0) + delta, -100, 100);
    if (TL.relations[k] > -30) {
      TL.wars = TL.wars.filter((w) => !((w.a === a && w.b === b) || (w.a === b && w.b === a)));
    }
  };
  FakeEngine.prototype._godPeace = function (code, foe) {
    const TL = this._godTL();
    const targets = TL.wars.filter((w) =>
      (w.a === code || w.b === code) && (!foe || w.a === foe || w.b === foe));
    if (!targets.length) return;
    targets.forEach((w) => {
      const other = w.a === code ? w.b : w.a;
      this._fire(TL, "PEACE", code, null, 0, { foe: other }, true);
    });
    this._emitFrame();
  };

  window.MeddlerEngine = {
    create: function (seed) { return new FakeEngine(seed || 1337); }
  };
})();
