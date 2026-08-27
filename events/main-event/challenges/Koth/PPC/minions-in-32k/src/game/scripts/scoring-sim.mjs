// CTF scoring-scheme simulation harness.
//
// Reuses the PRODUCTION rating math verbatim (crates/backend/src/elo.rs +
// matchmaker.rs): pairwise Elo over match ranks, K=32, expected() logistic,
// public_score anchored 1000->1 / 2000->500, rating-biased lobby sampling.
//
// Models a realistic CTF: fixed duration, diurnal activity, engagement tiers
// (whales..casuals), staggered arrivals/departures, a per-team concurrent-
// instance cap that RAMPS over the event, and four candidate scoring schemes
// (current / A linear-decay / B glicko-confidence / C activity-multiplier),
// each with a calibration (placement) period.
//
// Outputs CTF-relevant metrics so we can pick what's ideal. Deterministic
// (seeded). Run: node scripts/scoring-sim.mjs [--json]

// ---------- production formulas (verbatim) ----------
const INITIAL_RATING = 1500, K = 32;
const ANCHOR_LO = 1000, ANCHOR_HI = 3000, SCORE_MIN = 1, SCORE_MAX = 1000;
const expected = (a, b) => 1 / (1 + 10 ** ((b - a) / 400));
const publicScore = r => {
  const norm = Math.min(1, Math.max(0, (r - ANCHOR_LO) / (ANCHOR_HI - ANCHOR_LO)));
  return Math.round(SCORE_MIN + norm * (SCORE_MAX - SCORE_MIN));
};
// pairwise Elo update over ranks (lower rank = better; equal = draw)
function eloUpdate(entries, k = K) {
  // entries: [{rating, rank}]; returns new ratings (in order)
  return entries.map(e => {
    let sum = 0, n = 0;
    for (const o of entries) {
      if (o === e) continue;
      const actual = e.rank < o.rank ? 1 : e.rank > o.rank ? 0 : 0.5;
      sum += actual - expected(e.rating, o.rating);
      n++;
    }
    return e.rating + (n ? (k * sum) / n : 0);
  });
}

// ---------- seeded rng ----------
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const gauss = rng => {
  let u = 0, v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
};

// ---------- CTF / team model ----------
const HOUR = 60; // minutes
const DT = 5;    // sim step minutes
const SKILL_TO_ELO = 220; // latent skill stddev in "true elo" points

// engagement tiers: [fraction of teams, base online-probability while awake]
const TIERS = [
  { name: 'whale',  frac: 0.10, act: 0.92 },
  { name: 'active', frac: 0.25, act: 0.60 },
  { name: 'casual', frac: 0.40, act: 0.28 },
  { name: 'sparse', frac: 0.25, act: 0.10 },
];

function makeTeams(rng, T) {
  const teams = [];
  for (let i = 0; i < T; i++) {
    // pick tier
    let r = rng(), tier = TIERS[TIERS.length - 1];
    let acc = 0;
    for (const t of TIERS) { acc += t.frac; if (r <= acc) { tier = t; break; } }
    const skill = gauss(rng); // latent ~N(0,1)
    teams.push({
      id: i,
      tier: tier.name,
      act: tier.act,
      trueElo: INITIAL_RATING + skill * SKILL_TO_ELO,
      // staggered arrival (most arrive early; some late) and departure (some quit)
      arrive: Math.floor(Math.max(0, gauss(rng) * 3 + 1)) * HOUR, // ~0-8h
      depart: rng() < 0.2 ? (12 + rng() * 24) * HOUR : Infinity,  // 20% quit 12-36h in
      // per-team diurnal phase jitter
      phase: rng() * 2 - 1,
    });
  }
  return teams;
}

// awake/online probability at minute `t` for team
function onlineProb(team, t) {
  if (t < team.arrive || t > team.depart) return 0;
  const hourOfDay = ((t / HOUR) % 24);
  // diurnal: low 02:00-09:00, high otherwise; smooth
  const diurnal = 0.55 + 0.45 * Math.cos(((hourOfDay - 15) / 24) * 2 * Math.PI + team.phase);
  return team.act * Math.max(0.05, diurnal);
}

// concurrent-instance cap per team at minute t, given a ramp config
function concurrentCap(t, ramp) {
  if (!ramp.enabled) return 1;
  const steps = Math.floor(t / (ramp.everyHours * HOUR));
  return Math.min(ramp.max, ramp.start + steps);
}

// ---------- scoring schemes ----------
// Each scheme manages per-team {rating, rd, games, lastPlayed} and exposes
// score(team, now). Calibration: while games<CALIB, published score = 1.
function makeScheme(kind, p) {
  const CALIB = p.calib;
  const init = () => ({ rating: INITIAL_RATING, rd: p.rdInit ?? 350, games: 0, lastPlayed: -1 });
  return {
    kind, CALIB, init,
    onMatch(s, newRating, now) {
      s.games++;
      // optional higher K during calibration handled by caller via k arg
      s.rating = newRating;
      s.lastPlayed = now;
      if (kind === 'B') s.rd = Math.max(p.rdMin, s.rd * p.rdShrink);
    },
    // called every step to apply idle decay
    decayStep(s, now) {
      if (s.lastPlayed < 0) return;
      const idle = now - s.lastPlayed;
      if (idle <= p.graceMin) return;
      if (kind === 'A') {
        // Only decay teams ABOVE the floor; a sub-floor rating (from losses)
        // must NOT be pulled up to the floor by idling (that would be a
        // score-without-playing cheese — caught by the sim).
        if (s.rating > p.floor) s.rating = Math.max(p.floor, s.rating - p.decayPerStep);
      } else if (kind === 'B') {
        s.rd = Math.min(p.rdMax, s.rd + p.rdGrowthPerStep);
      }
      // C is computed lazily in score()
    },
    score(s, now) {
      if (s.games < CALIB) return 1;
      if (kind === 'current') return publicScore(s.rating);
      if (kind === 'A') return publicScore(s.rating);
      if (kind === 'B') return publicScore(s.rating - p.kRd * s.rd);
      if (kind === 'C') {
        const idle = s.lastPlayed < 0 ? 0 : Math.max(0, now - s.lastPlayed - p.graceMin);
        const act = Math.max(p.floorMul, 1 - idle / p.windowMin);
        return Math.max(1, Math.round(publicScore(s.rating) * act));
      }
    },
  };
}

// ---------- simulation ----------
function simulate(cfg) {
  const rng = mulberry32(cfg.seed);
  const teams = makeTeams(rng, cfg.T);
  const scheme = makeScheme(cfg.scheme, cfg.p);
  const S = teams.map(() => scheme.init());

  const DURATION = cfg.hours * HOUR;
  const MATCH_LEN = 15; // minutes a match occupies a team
  const running = new Array(cfg.T).fill(0); // concurrent instances per team
  const pending = []; // {finishAt, members:[teamIdx], ranks:[...]}

  // cheese metric: a team's published score must NEVER rise unless it finished
  // a match this step. Track prev score + prev games; an increase with no
  // games delta is a "score without playing" cheese.
  let cheeseGain = 0;
  // seed prevScore at the genuine t=0 score so a fresh team's starting score
  // isn't miscounted as an idle "rise".
  const prevScore = teams.map((_, i) => scheme.score(S[i], 0));
  const prevGames = teams.map(() => 0);
  // freeScore: what a team that NEVER plays ends up showing (register-and-idle).
  const freeScore = scheme.score(scheme.init(), DURATION);

  // camp experiment: at t=cfg.campAt, pick current top active team and freeze it
  let campTeam = -1, campRankAtFreeze = -1, campScoreAtFreeze = -1;

  for (let t = 0; t <= DURATION; t += DT) {
    // 1) resolve finished matches
    for (let i = pending.length - 1; i >= 0; i--) {
      if (pending[i].finishAt <= t) {
        const m = pending[i];
        const entries = m.members.map((idx, j) => ({ idx, rating: S[idx].rating, rank: m.ranks[j] }));
        const k = entries.some(e => S[e.idx].games < scheme.CALIB) ? cfg.p.calibK ?? K : K;
        const updated = eloUpdate(entries, k);
        entries.forEach((e, j) => {
          scheme.onMatch(S[e.idx], updated[j], t);
          running[e.idx]--;
        });
        pending.splice(i, 1);
      }
    }

    // 2) idle decay for everyone (the camp team STOPS PLAYING but still decays)
    for (let i = 0; i < cfg.T; i++) {
      scheme.decayStep(S[i], t);
    }

    // 3) form queue: online teams under their concurrent cap. A busy team may
    // re-queue as long as it has spare concurrent slots (running < cap) — this
    // is the ramping-concurrency feature.
    const cap = concurrentCap(t, cfg.ramp);
    const queue = [];
    for (let i = 0; i < cfg.T; i++) {
      if (i === campTeam) continue; // camper stopped playing
      if (running[i] >= cap) continue;
      if (rng() < onlineProb(teams[i], t) * cfg.queuePropensity) {
        queue.push({ idx: i, rating: S[i].rating });
      }
    }

    // 4) lobby formation (rating-biased sampling, mirrors matchmaker.rs)
    const lobbies = formLobbies(rng, queue);
    for (const lobby of lobbies) {
      // outcome: performance = trueElo + noise; rank by perf desc
      const perf = lobby.map(idx => ({ idx, p: teams[idx].trueElo + gauss(rng) * cfg.perfNoise }));
      perf.sort((a, b) => b.p - a.p);
      const ranks = {};
      perf.forEach((e, r) => (ranks[e.idx] = r + 1));
      const members = lobby, rankArr = lobby.map(idx => ranks[idx]);
      const finishAt = t + MATCH_LEN;
      for (const idx of lobby) running[idx]++;
      pending.push({ finishAt, members, ranks: rankArr });
    }

    // 5) camp experiment trigger
    if (cfg.campAt != null && campTeam < 0 && t >= cfg.campAt) {
      // pick the active team currently ranked #1 by published score with >=CALIB games
      const ranked = teams.map((_, i) => i)
        .filter(i => S[i].games >= scheme.CALIB)
        .sort((a, b) => scheme.score(S[b], t) - scheme.score(S[a], t));
      if (ranked.length) {
        campTeam = ranked[0];
        campScoreAtFreeze = scheme.score(S[campTeam], t);
        campRankAtFreeze = 1;
      }
    }

    // 6) cheese metric: a team's published score may only RISE on a step where
    // it finished a match (games increased). Any rise without a games delta is
    // a "score without playing" cheese.
    for (let i = 0; i < cfg.T; i++) {
      const sc = scheme.score(S[i], t);
      if (S[i].games === prevGames[i] && sc > prevScore[i]) {
        cheeseGain = Math.max(cheeseGain, sc - prevScore[i]);
      }
      prevScore[i] = sc;
      prevGames[i] = S[i].games;
    }
  }

  // ----- final metrics -----
  const active = teams.map((_, i) => i).filter(i => S[i].games > 0);
  // final published score + rank
  const finalScore = teams.map((_, i) => scheme.score(S[i], DURATION));
  const ranking = [...active].sort((a, b) => finalScore[b] - finalScore[a]);

  // SkillCorr: spearman(final published rank, latent skill) over graduated teams
  const grad = active.filter(i => S[i].games >= scheme.CALIB);
  const skillCorr = spearman(grad.map(i => finalScore[i]), grad.map(i => teams[i].trueElo));

  // PlayIncentive: among similar-skill pairs, does more games => higher score?
  // corr(games, score) residualized on trueElo (partial corr proxy)
  const playInc = partialCorr(
    grad.map(i => S[i].games), grad.map(i => finalScore[i]), grad.map(i => teams[i].trueElo));

  // CampPenalty: ranks the camp team dropped after freezing (want > 0)
  let campDrop = null, campScoreLoss = null;
  if (campTeam >= 0) {
    const finalRankOfCamp = ranking.indexOf(campTeam) + 1;
    campDrop = finalRankOfCamp - campRankAtFreeze;
    campScoreLoss = campScoreAtFreeze - finalScore[campTeam];
  }

  // LateJoiner fairness: avg final rank percentile of skilled (top-quartile trueElo)
  // teams that arrived late (>6h), among all graduated.
  const lateSkilled = grad.filter(i => teams[i].arrive > 6 * HOUR &&
    teams[i].trueElo > quantile(teams.map(t => t.trueElo), 0.75));
  const lateRankPct = lateSkilled.length
    ? avg(lateSkilled.map(i => (ranking.indexOf(i) + 1) / ranking.length))
    : null;

  const gamesArr = grad.map(i => S[i].games);

  return {
    cheeseGain,
    freeScore,
    skillCorr,
    playInc,
    campDrop, campScoreLoss,
    lateRankPct,
    graduated: grad.length,
    medianGames: median(gamesArr),
    maxGames: Math.max(...gamesArr, 0),
    avgScore: avg(grad.map(i => finalScore[i])),
  };
}

// rating-biased lobby formation (mirror of matchmaker.rs form_lobbies)
const MAX_LOBBY = 8, NEARBY_BAND = 200, TEMPERATURE = 400;
function formLobbies(rng, queue) {
  const pool = queue.slice();
  if (pool.length < 2) return [];
  const lobbies = [];
  while (pool.length >= 2) {
    const seedIdx = Math.floor(rng() * pool.length);
    const seed = pool.splice(seedIdx, 1)[0];
    const lobby = [seed.idx];
    while (lobby.length < MAX_LOBBY && pool.length) {
      const weights = pool.map(c => {
        const d = Math.abs(c.rating - seed.rating);
        const w = Math.exp(-d / TEMPERATURE);
        return d <= NEARBY_BAND ? w * 2 : w;
      });
      const total = weights.reduce((a, b) => a + b, 0);
      let pick = rng() * total, idx = 0;
      for (let i = 0; i < weights.length; i++) { pick -= weights[i]; if (pick <= 0) { idx = i; break; } }
      lobby.push(pool.splice(idx, 1)[0].idx);
    }
    if (lobby.length >= 2) lobbies.push(lobby);
  }
  return lobbies;
}

// ---------- stats helpers ----------
const avg = a => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
const median = a => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
function quantile(a, q) { const s = [...a].sort((x, y) => x - y); return s[Math.floor(q * (s.length - 1))]; }
function rankvec(a) { // average ranks (1-based)
  const idx = a.map((v, i) => [v, i]).sort((x, y) => x[0] - y[0]);
  const r = new Array(a.length);
  for (let i = 0; i < idx.length;) { let j = i; while (j < idx.length && idx[j][0] === idx[i][0]) j++; const ar = (i + j + 1) / 2; for (let k = i; k < j; k++) r[idx[k][1]] = ar; i = j; }
  return r;
}
function pearson(x, y) {
  const n = x.length; if (n < 2) return 0;
  const mx = avg(x), my = avg(y);
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) { const dx = x[i] - mx, dy = y[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; }
  return sxx && syy ? sxy / Math.sqrt(sxx * syy) : 0;
}
const spearman = (x, y) => pearson(rankvec(x), rankvec(y));
function partialCorr(a, b, c) { // corr(a,b | c): residualize both on c, then pearson
  const res = (v) => { const slope = pearson(c, v) * (std(v) / (std(c) || 1)); const mc = avg(c), mv = avg(v); return v.map((vi, i) => vi - (mv + slope * (c[i] - mc))); };
  return pearson(res(a), res(b));
}
const std = a => Math.sqrt(avg(a.map(v => (v - avg(a)) ** 2)));

// ---------- run sweeps ----------
function runMean(cfg, seeds = 8) {
  const runs = [];
  for (let s = 0; s < seeds; s++) runs.push(simulate({ ...cfg, seed: 1000 + s * 7 }));
  const keys = Object.keys(runs[0]);
  const out = {};
  for (const k of keys) {
    const vals = runs.map(r => r[k]).filter(v => v != null && !Number.isNaN(v));
    out[k] = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }
  return out;
}

const BASE = {
  T: 200, hours: 48,
  perfNoise: 170,       // match-outcome noise (skill is not destiny)
  queuePropensity: 0.5, // chance an online free team queues this step
  campAt: 24 * HOUR,    // freeze the leader at 24h and watch
  ramp: { enabled: false, start: 1, max: 1, everyHours: 999 },
};

// scheme param presets (sensible starting points; swept below)
const P = {
  current: { calib: 0 },
  A: { calib: 10, calibK: 48, graceMin: 60, decayPerStep: 4, floor: 1200 },
  B: { calib: 10, calibK: 48, graceMin: 60, rdInit: 350, rdMin: 60, rdMax: 350, rdShrink: 0.6, rdGrowthPerStep: 1.2, kRd: 1.4 },
  C: { calib: 10, calibK: 48, graceMin: 60, windowMin: 360, floorMul: 0.4 },
};

function fmt(x, d = 2) { return x == null ? '  n/a' : (typeof x === 'number' ? x.toFixed(d) : String(x)); }

const asJson = process.argv.includes('--json');
const results = {};

console.log('=== SCHEME COMPARISON (200 teams, 48h CTF, no concurrency ramp, 8 seeds) ===');
console.log('scheme   cheese freeScore skillCorr playInc campDrop campLoss lateRankPct medGames maxGames');
for (const scheme of ['current', 'A', 'B', 'C']) {
  const r = runMean({ ...BASE, scheme, p: P[scheme] });
  results[scheme] = r;
  console.log(
    `${scheme.padEnd(8)} ${fmt(r.cheeseGain, 1).padStart(6)} ${fmt(r.freeScore, 0).padStart(9)} ${fmt(r.skillCorr).padStart(8)} ` +
    `${fmt(r.playInc).padStart(7)} ${fmt(r.campDrop, 1).padStart(8)} ${fmt(r.campScoreLoss, 0).padStart(8)} ` +
    `${fmt(r.lateRankPct).padStart(11)} ${fmt(r.medianGames, 0).padStart(8)} ${fmt(r.maxGames, 0).padStart(8)}`);
}

console.log('\n=== CONCURRENT-INSTANCE RAMP effect (scheme C, vary ramp) ===');
console.log('ramp                         medGames maxGames skillCorr playInc avgScore');
const ramps = [
  { label: 'off (1 always)', enabled: false, start: 1, max: 1, everyHours: 999 },
  { label: 'flat 3', enabled: true, start: 3, max: 3, everyHours: 999 },
  { label: 'ramp 1->4 (+1/12h)', enabled: true, start: 1, max: 4, everyHours: 12 },
  { label: 'ramp 1->6 (+1/8h)', enabled: true, start: 1, max: 6, everyHours: 8 },
  { label: 'ramp 2->8 (+1/6h)', enabled: true, start: 2, max: 8, everyHours: 6 },
];
for (const ramp of ramps) {
  const r = runMean({ ...BASE, scheme: 'C', p: P.C, ramp });
  console.log(`${ramp.label.padEnd(28)} ${fmt(r.medianGames, 0).padStart(8)} ${fmt(r.maxGames, 0).padStart(8)} ${fmt(r.skillCorr).padStart(9)} ${fmt(r.playInc).padStart(7)} ${fmt(r.avgScore, 0).padStart(8)}`);
}

console.log('\n=== CALIBRATION length sweep (scheme B, ramp 1->4) ===');
console.log('calib  graduated skillCorr playInc');
for (const calib of [0, 5, 10, 15, 20]) {
  const r = runMean({ ...BASE, scheme: 'B', p: { ...P.B, calib }, ramp: ramps[2] });
  console.log(`${String(calib).padStart(5)} ${fmt(r.graduated, 0).padStart(9)} ${fmt(r.skillCorr).padStart(9)} ${fmt(r.playInc).padStart(7)}`);
}

console.log('\n=== DECAY strength sweep (scheme A vs C, ramp 1->4) ===');
console.log('config                    cheese campDrop campLoss playInc skillCorr');
for (const dps of [2, 4, 8]) {
  const r = runMean({ ...BASE, scheme: 'A', p: { ...P.A, decayPerStep: dps }, ramp: ramps[2] });
  console.log(`A decay=${dps}/step`.padEnd(25) + ` ${fmt(r.cheeseGain, 1).padStart(6)} ${fmt(r.campDrop, 1).padStart(8)} ${fmt(r.campScoreLoss, 0).padStart(8)} ${fmt(r.playInc).padStart(7)} ${fmt(r.skillCorr).padStart(9)}`);
}
for (const win of [240, 360, 600]) {
  const r = runMean({ ...BASE, scheme: 'C', p: { ...P.C, windowMin: win }, ramp: ramps[2] });
  console.log(`C window=${win}m`.padEnd(25) + ` ${fmt(r.cheeseGain, 1).padStart(6)} ${fmt(r.campDrop, 1).padStart(8)} ${fmt(r.campScoreLoss, 0).padStart(8)} ${fmt(r.playInc).padStart(7)} ${fmt(r.skillCorr).padStart(9)}`);
}

if (asJson) {
  console.log('\n__JSON__' + JSON.stringify(results));
}
console.log('\nLegend: cheese=max published gain over an idle gap (MUST be ~0); skillCorr=spearman(score,trueSkill) for graduated;');
console.log('playInc=partial corr(games,score | skill) — want >0 (playing more helps at equal skill);');
console.log('campDrop=ranks the frozen 24h-leader fell (want >0); campLoss=score points the camper lost (want >0);');
console.log('lateRankPct=avg final-rank percentile of skilled late-joiners (lower=better, can still climb).');

// ---- robustness: team-count sensitivity (scheme B, ramp 1->4) ----
console.log('\n=== TEAM-COUNT sensitivity (scheme B, ramp 1->4) ===');
console.log('teams  cheese skillCorr playInc campDrop graduated');
for (const T of [50, 200, 500]) {
  const r = runMean({ ...BASE, T, scheme: 'B', p: P.B, ramp: ramps[2] }, 6);
  console.log(`${String(T).padStart(5)} ${fmt(r.cheeseGain,1).padStart(6)} ${fmt(r.skillCorr).padStart(9)} ${fmt(r.playInc).padStart(7)} ${fmt(r.campDrop,1).padStart(8)} ${fmt(r.graduated,0).padStart(9)}`);
}

// ============================================================================
// CORRECTIONS from adversarial verification:
//  (1) LOSS-TAP experiment: the decisive anti-cheese test the rise-without-
//      games metric was blind to. A camper at the top either fully idles
//      ("camp") or throws ONE game every tapMin minutes ("tap") to trigger any
//      activity/RD reset, then idles. We measure RETAINED published score. Rule
//      3 ("no score without playing") demands the tapper's score still craters.
//  (2) GLOBAL-CEILING ramp: the real, safe concurrency feature — a global
//      live-match ceiling that grows over the event, strict one-match-per-team
//      (preserves the queue-once anti-self-play invariant).
// ============================================================================

function lossTapExperiment(kind, p, mode, tapMin, durMin) {
  const scheme = makeScheme(kind, p);
  const s = scheme.init();
  s.rating = 1900; s.games = Math.max(p.calib, 12); s.lastPlayed = 0; // graduated leader
  if (kind === 'B') s.rd = p.rdMin; // freshly-confident (just been active)
  const startScore = scheme.score(s, 0);
  const opp = [1500, 1500, 1500, 1500, 1500, 1500, 1500];
  for (let t = DT; t <= durMin; t += DT) {
    scheme.decayStep(s, t);
    if (mode === 'tap' && t % tapMin === 0) {
      // throw a game: tapper ranks LAST (8th) among 8
      const entries = [{ rating: s.rating, rank: 8 }, ...opp.map(r => ({ rating: r, rank: 1 }))];
      const updated = eloUpdate(entries);
      scheme.onMatch(s, updated[0], t);
    }
  }
  return { startScore, endScore: scheme.score(s, durMin) };
}

console.log('\n=== LOSS-TAP anti-cheese test (1900-rated leader at hour 24; 24h of idle vs throw-1-game/78min) ===');
console.log('scheme   start  camp_end  tap_end   tap_retained%  VERDICT');
for (const sc of ['A', 'B', 'C']) {
  const camp = lossTapExperiment(sc, P[sc], 'camp', 78, 24 * HOUR);
  const tap = lossTapExperiment(sc, P[sc], 'tap', 78, 24 * HOUR);
  const ret = (100 * tap.endScore / tap.startScore).toFixed(0);
  const verdict = tap.endScore <= camp.endScore + 5 ? 'SAFE (throwing does not help)'
    : `CHEESE: throwing retains ${ret}% of the top score`;
  console.log(`${sc.padEnd(8)} ${String(camp.startScore).padStart(5)} ${String(camp.endScore).padStart(9)} ${String(tap.endScore).padStart(8)} ${String(ret + '%').padStart(13)}   ${verdict}`);
}

// global-ceiling ramp: cap TOTAL concurrent matches at ceiling(t); team cap=1.
function simulateGlobal(cfg) {
  const rng = mulberry32(cfg.seed);
  const teams = makeTeams(rng, cfg.T);
  const scheme = makeScheme(cfg.scheme, cfg.p);
  const S = teams.map(() => scheme.init());
  const DURATION = cfg.hours * HOUR, MATCH_LEN = 15;
  const running = new Array(cfg.T).fill(0);
  const pending = [];
  const ceilingAt = t => {
    const q = Math.floor(t / (cfg.hours * HOUR / cfg.gc.steps));
    return Math.round(cfg.gc.base + (cfg.gc.peak - cfg.gc.base) * Math.min(1, q / (cfg.gc.steps - 1)));
  };
  let totalGames = 0, totalWait = 0, waitSamples = 0;
  for (let t = 0; t <= DURATION; t += DT) {
    for (let i = pending.length - 1; i >= 0; i--) if (pending[i].finishAt <= t) {
      const m = pending[i];
      const entries = m.members.map((idx, j) => ({ idx, rating: S[idx].rating, rank: m.ranks[j] }));
      const k = entries.some(e => S[e.idx].games < scheme.CALIB) ? (cfg.p.calibK ?? K) : K;
      const up = eloUpdate(entries, k);
      entries.forEach((e, j) => { scheme.onMatch(S[e.idx], up[j], t); running[e.idx]--; totalGames++; });
      pending.splice(i, 1);
    }
    for (let i = 0; i < cfg.T; i++) scheme.decayStep(S[i], t);
    const queue = [];
    for (let i = 0; i < cfg.T; i++) {
      if (running[i] >= 1) continue; // strict one-match-per-team
      if (rng() < onlineProb(teams[i], t) * cfg.queuePropensity) queue.push({ idx: i, rating: S[i].rating });
    }
    waitSamples += queue.length;
    const headroom = Math.max(0, ceilingAt(t) - pending.length);
    const lobbies = formLobbies(rng, queue).slice(0, headroom);
    totalWait += queue.length - lobbies.reduce((a, l) => a + l.length, 0);
    for (const lobby of lobbies) {
      const perf = lobby.map(idx => ({ idx, p: teams[idx].trueElo + gauss(rng) * cfg.perfNoise }));
      perf.sort((a, b) => b.p - a.p);
      const ranks = {}; perf.forEach((e, r) => ranks[e.idx] = r + 1);
      const finishAt = t + MATCH_LEN;
      for (const idx of lobby) running[idx]++;
      pending.push({ finishAt, members: lobby, ranks: lobby.map(idx => ranks[idx]) });
    }
  }
  return { gamesPerTeam: totalGames / cfg.T, unmatchedRate: waitSamples ? totalWait / waitSamples : 0 };
}

console.log('\n=== GLOBAL-CEILING ramp (scheme A, strict 1 match/team; 200 teams) ===');
console.log('ceiling schedule                 gamesPerTeam unmatchedRate');
const gcs = [
  { label: 'flat 6 (tight)',          base: 6,  peak: 6,  steps: 4 },
  { label: 'flat 25 (loose)',         base: 25, peak: 25, steps: 4 },
  { label: 'ramp 6->25 (capacity)',   base: 6,  peak: 25, steps: 4 },
  { label: 'ramp 10->40 (capacity)',  base: 10, peak: 40, steps: 4 },
];
// test at a LARGE field (500) and high activity so match slots can actually bind
for (const gc of gcs) {
  const runs = [];
  for (let s = 0; s < 6; s++) runs.push(simulateGlobal({ ...BASE, T: 500, queuePropensity: 0.8, scheme: 'A', p: P.A, gc, seed: 2000 + s * 7 }));
  const gpt = avg(runs.map(r => r.gamesPerTeam)), un = avg(runs.map(r => r.unmatchedRate));
  console.log(`${gc.label.padEnd(32)} ${gpt.toFixed(1).padStart(12)} ${(un * 100).toFixed(0) + '%'}`.padEnd(60));
}

// ============================================================================
// PER-TEAM CONCURRENCY safety (user's design): a team may hold cap(t) concurrent
// enrollments but is NEVER in the same lobby twice (structural in form_lobbies).
// Decisive question: can opening many instances and NOT playing them gain score?
// Under scheme A every match applies pairwise Elo; an un-played instance = AFK =
// ranks last = a LOSS = rating down. So instance-spam is self-punishing.
// ============================================================================
function concurrencySpam(kind, p, mode, cap, cycles) {
  const scheme = makeScheme(kind, p);
  const s = scheme.init();
  s.rating = 1700; s.games = Math.max(p.calib, 12); s.lastPlayed = 0;
  if (kind === 'B') s.rd = p.rdMin;
  const start = scheme.score(s, 0);
  let t = 0;
  for (let c = 0; c < cycles; c++) {
    t += 20;
    // open `cap` concurrent matches vs avg (1500) lobbies; resolve each
    for (let k = 0; k < cap; k++) {
      const opp = [1500, 1500, 1500, 1500, 1500, 1500, 1500];
      // mode 'afk' => our team ranks LAST (didn't play); 'play' => competes (rank by 1700 vs 1500 + noise)
      const myRank = mode === 'afk' ? 8 : (1700 > 1500 ? 1 : 8); // genuinely better => wins
      const entries = [{ rating: s.rating, rank: myRank }, ...opp.map((r, i) => ({ rating: r, rank: myRank === 1 ? i + 2 : i + 1 }))];
      const up = eloUpdate(entries);
      scheme.onMatch(s, up[0], t);
    }
  }
  return { start, end: scheme.score(s, t), games: s.games - Math.max(p.calib, 12) };
}

console.log('\n=== PER-TEAM CONCURRENCY: instance-spam-AND-AFK vs genuinely-play (scheme A, cap=4, 12 cycles) ===');
console.log('behavior                       start  end   games  result');
for (const [label, mode] of [['open 4 instances, AFK all', 'afk'], ['open 4 instances, actually play', 'play']]) {
  const r = concurrencySpam('A', P.A, mode, 4, 12);
  const res = mode === 'afk'
    ? (r.end < r.start ? `SAFE: score FELL ${r.start}->${r.end} (each AFK instance is a loss)` : 'CHEESE')
    : `legit: genuine wins raised ${r.start}->${r.end}`;
  console.log(`${label.padEnd(30)} ${String(r.start).padStart(5)} ${String(r.end).padStart(5)} ${String(r.games).padStart(6)}  ${res}`);
}

// ============================================================================
// LATE DOMINANT BOT: can a genuinely top-skill bot built in the last ~2h climb
// fast? Tension: calibration hides score for 10 games. KEY: a HIGH calibration-K
// converges the hidden rating to true skill within those 10 games, so graduation
// reveals a high score immediately (not 1500). Concurrency (cap=4 at event end)
// lets it play those games fast. We inject a 2100-true-elo bot arriving 2h before
// the end and measure its final leaderboard rank + score.
// ============================================================================
function simulateLateBot(cfg) {
  const rng = mulberry32(cfg.seed);
  const teams = makeTeams(rng, cfg.T);
  // inject team 0 as the late dominant bot
  const arriveH = cfg.hours - cfg.botHoursBeforeEnd;
  teams[0] = { id: 0, tier: 'bot', act: 0.97, trueElo: cfg.botElo, arrive: arriveH * HOUR, depart: Infinity, phase: 0 };
  const scheme = makeScheme(cfg.scheme, cfg.p);
  const S = teams.map(() => scheme.init());
  const DURATION = cfg.hours * HOUR, MATCH_LEN = cfg.matchLen ?? 15;
  const running = new Array(cfg.T).fill(0);
  const pending = [];
  let botGames = 0, botGradAt = -1, botQueuedSteps = 0, botMatchedSteps = 0;
  for (let t = 0; t <= DURATION; t += DT) {
    for (let i = pending.length - 1; i >= 0; i--) if (pending[i].finishAt <= t) {
      const m = pending[i];
      const entries = m.members.map((idx, j) => ({ idx, rating: S[idx].rating, rank: m.ranks[j] }));
      const k = entries.some(e => S[e.idx].games < scheme.CALIB) ? (cfg.p.calibK ?? K) : K;
      const up = eloUpdate(entries, k);
      entries.forEach((e, j) => {
        const wasCalib = S[e.idx].games < scheme.CALIB;
        scheme.onMatch(S[e.idx], up[j], t); running[e.idx]--;
        if (e.idx === 0) { botGames++; if (wasCalib && S[0].games >= scheme.CALIB && botGradAt < 0) botGradAt = t; }
      });
      pending.splice(i, 1);
    }
    for (let i = 0; i < cfg.T; i++) scheme.decayStep(S[i], t);
    const cap = concurrentCap(t, cfg.ramp);
    const queue = [];
    for (let i = 0; i < cfg.T; i++) {
      if (running[i] >= cap) continue;
      // the late bot is maximally aggressive: always online, always fills its cap
      const aggressive = (i === 0 && t >= teams[0].arrive);
      if (aggressive || rng() < onlineProb(teams[i], t) * cfg.queuePropensity) queue.push({ idx: i, rating: S[i].rating });
    }
    if (queue.some(c => c.idx === 0)) botQueuedSteps++;
    const lobbiesFormed = formLobbies(rng, queue);
    if (lobbiesFormed.some(l => l.includes(0))) botMatchedSteps++;
    for (const lobby of lobbiesFormed) {
      const perf = lobby.map(idx => ({ idx, p: teams[idx].trueElo + gauss(rng) * cfg.perfNoise }));
      perf.sort((a, b) => b.p - a.p);
      const ranks = {}; perf.forEach((e, r) => ranks[e.idx] = r + 1);
      for (const idx of lobby) running[idx]++;
      pending.push({ finishAt: t + MATCH_LEN, members: lobby, ranks: lobby.map(idx => ranks[idx]) });
    }
  }
  const finalScore = teams.map((_, i) => scheme.score(S[i], DURATION));
  const active = teams.map((_, i) => i).filter(i => S[i].games > 0);
  const ranking = [...active].sort((a, b) => finalScore[b] - finalScore[a]);
  const botRank = ranking.indexOf(0) + 1;
  return { botGames, botGradAtMin: botGradAt < 0 ? null : botGradAt - arriveH * HOUR, botScore: finalScore[0], botRank, field: ranking.length, botRating: S[0].rating, matchRate: botQueuedSteps ? botMatchedSteps / botQueuedSteps : 0 };
}

console.log('\n=== LATE DOMINANT BOT (true-elo 2100, maximally aggressive, arrives 2h before end; scheme A, cap ramps to 4) ===');
console.log('config                              botGames gradMin botScore botRank/field    rating matchRate');
const lateBase = { ...BASE, scheme: 'A', botElo: 2100, botHoursBeforeEnd: 2, ramp: { enabled: true, start: 1, max: 4, everyHours: 12 } };
function lateRun(label, p, matchLen, ramp) {
  const runs = [];
  for (let s = 0; s < 12; s++) runs.push(simulateLateBot({ ...lateBase, p, matchLen, ramp: ramp ?? lateBase.ramp, seed: 3000 + s * 7 }));
  const g = avg(runs.map(r => r.botGames)), gm = avg(runs.map(r => r.botGradAtMin ?? 120));
  const sc = avg(runs.map(r => r.botScore)), rk = avg(runs.map(r => r.botRank)), fld = avg(runs.map(r => r.field));
  const mr = avg(runs.map(r => r.matchRate));
  console.log(`${label.padEnd(35)} ${g.toFixed(1).padStart(8)} ${gm.toFixed(0).padStart(7)} ${sc.toFixed(0).padStart(8)} ${(rk.toFixed(0)+'/'+fld.toFixed(0)).padStart(13)} ${avg(runs.map(r=>r.botRating)).toFixed(0).padStart(9)} ${(mr*100).toFixed(0)+'%'}`);
}
// match length is the big lever: real minions matches are short (grace+match). Test 5min and 2min.
for (const ml of [15, 5, 2]) {
  lateRun(`calib=10 calibK=64, match=${ml}min`, { ...P.A, calib: 10, calibK: 64 }, ml);
}
console.log('  --- shorter calibration / higher calibK (match=5min) ---');
lateRun('calib=5  calibK=64, match=5min', { ...P.A, calib: 5,  calibK: 64 }, 5);
lateRun('calib=5  calibK=96, match=5min', { ...P.A, calib: 5,  calibK: 96 }, 5);
lateRun('calib=3  calibK=96, match=5min', { ...P.A, calib: 3,  calibK: 96 }, 5);
lateRun('calib=10 calibK=96, match=2min', { ...P.A, calib: 10, calibK: 96 }, 2);
console.log('  --- effect of concurrency cap (calib=5 calibK=64, match=5min) ---');
lateRun('cap=1 (no concurrency)', { ...P.A, calib: 5, calibK: 64 }, 5, { enabled:false, start:1, max:1, everyHours:999 });
lateRun('cap=4 (ramp)',           { ...P.A, calib: 5, calibK: 64 }, 5, { enabled:true, start:1, max:4, everyHours:12 });
lateRun('cap=8 (aggressive ramp)',{ ...P.A, calib: 5, calibK: 64 }, 5, { enabled:true, start:1, max:8, everyHours:8 });
console.log('\nmatchRate = fraction of steps the bot queued that it got into a lobby (opponent availability).');

console.log('\n  --- calibK sensitivity for the late bot (calib=10, match=15min, cap=4) ---');
for (const ck of [32, 48, 64, 96]) lateRun(`calibK=${ck}`, { ...P.A, calib: 10, calibK: ck }, 15);
