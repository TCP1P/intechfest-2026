// Elo + leaderboard simulation under a PARETO team-skill field.
//
// The existing scoring-sim.mjs models latent team skill as Gaussian. Real CTF
// fields are heavy-tailed: a large mass of weak/casual teams and a thin tail of
// very strong ones (the "Pareto" shape). This harness re-runs the PRODUCTION
// rating stack against a Pareto skill field and reports how the leaderboard
// behaves — recovery of the true ordering, top-end saturation against the
// anchored 1->1000 cap, and score concentration (Gini).
//
// All rating math is ported VERBATIM from crates/backend/src/elo.rs (and the
// pair-history collusion throttle from state.rs): pairwise Elo over match
// ranks, throttled K, calibration floor + pinned score, anchored public score,
// stateful idle decay. Deterministic (seeded).
//
// Run:  node scripts/elo-pareto-sim.mjs [--json] [--alpha=2] [--teams=200]

// ----------------------------------------------------------------------------
// production constants (verbatim from elo.rs)
// ----------------------------------------------------------------------------
const INITIAL_RATING = 1500, K = 32;
const ANCHOR_LO = 1000, ANCHOR_HI = 3000, SCORE_MIN = 1, SCORE_MAX = 1000;
const CALIB_GAMES = 10, CALIB_FLOOR = 1400;
const DECAY_FLOOR = 1000, DECAY_GRACE_MIN = 60, DECAY_PER_MIN = 2.4;
const PAIR_WINDOW_MIN = 60;        // PAIR_WINDOW_SECS / 60
const PAIR_K_FREE_MEETINGS = 3, PAIR_NET_CAP = 96;

const expected = (a, b) => 1 / (1 + 10 ** ((b - a) / 400));

// pairwise Elo with a per-pair K (mirror of elo.rs::update_with)
function eloUpdateWith(entries, pairK) {
  // entries: [{id, rating, rank}]; returns new ratings in order
  return entries.map(e => {
    let sum = 0, n = 0;
    for (const o of entries) {
      if (o === e) continue;
      const actual = e.rank < o.rank ? 1 : e.rank > o.rank ? 0 : 0.5;
      const k = pairK(e.id, o.id);
      sum += k * (actual - expected(e.rating, o.rating));
      n++;
    }
    return e.rating + (n ? sum / n : 0);
  });
}

const publicScore = r => {
  const norm = Math.min(1, Math.max(0, (r - ANCHOR_LO) / (ANCHOR_HI - ANCHOR_LO)));
  return Math.round(SCORE_MIN + norm * (SCORE_MAX - SCORE_MIN));
};
const publishedScore = (r, games) => (games < CALIB_GAMES ? SCORE_MIN : publicScore(r));
// anti-sandbag: calibrating teams may rise freely but not fall below the floor
const clampCalibration = (r, gamesAfter) => (gamesAfter < CALIB_GAMES ? Math.max(CALIB_FLOOR, r) : r);

// stateful idle decay (clock in MINUTES; elo.rs uses seconds but the rates are
// per-minute / grace-in-minutes, so a minute clock is a faithful 1:1 port).
function applyIdleDecay(rating, lastPlayed, lastDecay, now) {
  if (lastPlayed < 0 || rating <= DECAY_FLOOR) return [rating, lastDecay];
  const decayStart = lastPlayed + DECAY_GRACE_MIN;
  if (now <= decayStart) return [rating, lastDecay];
  const from = Math.max(lastDecay, decayStart);
  if (now <= from) return [rating, lastDecay];
  const loss = (now - from) * DECAY_PER_MIN;
  return [Math.max(DECAY_FLOOR, rating - loss), now];
}

// repeat-opponent K throttle (mirror of elo.rs::throttled_k)
function throttledK(meetings, netTransfer) {
  if (netTransfer >= PAIR_NET_CAP) return 0;
  if (meetings <= PAIR_K_FREE_MEETINGS) return K;
  return K / 2 ** (meetings - PAIR_K_FREE_MEETINGS);
}

// ----------------------------------------------------------------------------
// seeded rng + distributions
// ----------------------------------------------------------------------------
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
// Pareto(xm=1, alpha) via inverse-CDF: X = 1 / U^(1/alpha), X in [1, inf).
// Density peaks at xm=1 (the many ordinary teams); heavy upper tail (few elite).
const paretoSample = (rng, alpha) => 1 / Math.pow(Math.max(1e-12, rng()), 1 / alpha);

// ----------------------------------------------------------------------------
// CTF / team model
// ----------------------------------------------------------------------------
const HOUR = 60;            // minutes
const DT = 1;               // sim step (minutes)
const MATCH_LEN = 6;        // minutes a match occupies a team (~grace+match=330s)

// skill -> elo mapping. X=1 (most common) sits near BASE; the Pareto tail rises.
// Only rating DIFFERENCES drive outcomes, so the absolute offset is cosmetic;
// SKILL_SCALE sets how far the elite tail separates from the pack.
const SKILL_BASE = 1350, SKILL_SCALE = 250, ELO_MIN = 900, ELO_MAX = 3000;

// engagement tiers (independent of skill): [fraction, base online-prob awake]
const TIERS = [
  { name: 'whale',  frac: 0.10, act: 0.92 },
  { name: 'active', frac: 0.25, act: 0.60 },
  { name: 'casual', frac: 0.40, act: 0.28 },
  { name: 'sparse', frac: 0.25, act: 0.10 },
];

function skillToElo(x, centerX) {
  // center the BULK at ~INITIAL_RATING (Elo is zero-sum; only differences drive
  // outcomes, so this is cosmetic), then clamp the unbounded tail to a sane
  // band. Beyond ~2100 expected() is already ~1.0, so the high clamp is
  // behaviorally inert — it only stops the histogram from running off.
  const e = INITIAL_RATING + SKILL_SCALE * (x - centerX);
  return Math.min(ELO_MAX, Math.max(ELO_MIN, e));
}

function makeTeams(rng, T, dist, alpha) {
  const raw = [];
  for (let i = 0; i < T; i++) raw.push(dist === 'pareto' ? paretoSample(rng, alpha) : gauss(rng));
  // MEDIAN centering, not mean: for heavy tails (alpha<=1.5, incl. the infinite-
  // mean regime) a single huge outlier would drag a mean-center up and collapse
  // the whole field onto the floor. The median pins the bulk regardless.
  const centerX = quantile(raw, 0.5);
  const teams = [];
  for (let i = 0; i < T; i++) {
    let r = rng(), tier = TIERS[TIERS.length - 1], acc = 0;
    for (const t of TIERS) { acc += t.frac; if (r <= acc) { tier = t; break; } }
    const trueElo = dist === 'pareto'
      ? skillToElo(raw[i], centerX)
      : Math.min(ELO_MAX, Math.max(ELO_MIN, INITIAL_RATING + raw[i] * 220));
    teams.push({
      id: i, tier: tier.name, act: tier.act, trueElo,
      arrive: Math.floor(Math.max(0, gauss(rng) * 3 + 1)) * HOUR,        // ~0-8h
      depart: rng() < 0.2 ? (12 + rng() * 24) * HOUR : Infinity,         // 20% quit
      phase: rng() * 2 - 1,
    });
  }
  return teams;
}

function onlineProb(team, t) {
  if (t < team.arrive || t > team.depart) return 0;
  const hourOfDay = (t / HOUR) % 24;
  const diurnal = 0.55 + 0.45 * Math.cos(((hourOfDay - 15) / 24) * 2 * Math.PI + team.phase);
  return team.act * Math.max(0.05, diurnal);
}

// rating-biased lobby formation (mirror of matchmaker.rs::form_lobbies)
const MAX_LOBBY = 8, NEARBY_BAND = 200, TEMPERATURE = 400;
function formLobbies(rng, queue) {
  const pool = queue.slice();
  if (pool.length < 2) return [];
  const lobbies = [];
  while (pool.length >= 2) {
    const seed = pool.splice(Math.floor(rng() * pool.length), 1)[0];
    const lobby = [seed];
    while (lobby.length < MAX_LOBBY && pool.length) {
      const weights = pool.map(c => {
        const d = Math.abs(c.rating - seed.rating);
        const w = Math.exp(-d / TEMPERATURE);
        return d <= NEARBY_BAND ? w * 2 : w;
      });
      const total = weights.reduce((a, b) => a + b, 0);
      let pick = rng() * total, idx = 0;
      for (let i = 0; i < weights.length; i++) { pick -= weights[i]; if (pick <= 0) { idx = i; break; } }
      lobby.push(pool.splice(idx, 1)[0]);
    }
    if (lobby.length >= 2) lobbies.push(lobby);
  }
  return lobbies;
}

// ----------------------------------------------------------------------------
// simulation: PRODUCTION rating stack over a configurable skill field
// ----------------------------------------------------------------------------
function simulate(cfg) {
  const rng = mulberry32(cfg.seed);
  const teams = makeTeams(rng, cfg.T, cfg.dist, cfg.alpha);
  const S = teams.map(() => ({ rating: INITIAL_RATING, games: 0, lastPlayed: -1, lastDecay: -1 }));
  const DURATION = cfg.hours * HOUR;
  const running = new Array(cfg.T).fill(0);
  const pending = [];
  const cap = cfg.cap ?? 1;

  // collusion-throttle pair history: key "a:b" (sorted) -> {meet, net, start}
  const pairHist = new Map();
  const pkey = (a, b) => (a < b ? a + ':' + b : b + ':' + a);
  const pairStats = (a, b, now) => {
    const h = pairHist.get(pkey(a, b));
    if (h && now - h.start < PAIR_WINDOW_MIN) return [h.meet, h.net];
    return [0, 0];
  };
  const recordPair = (a, b, transfer, now) => {
    const key = pkey(a, b);
    let h = pairHist.get(key);
    if (!h || now - h.start >= PAIR_WINDOW_MIN) { h = { meet: 0, net: 0, start: now }; pairHist.set(key, h); }
    h.meet += 1; h.net += Math.abs(transfer);
  };

  for (let t = 0; t <= DURATION; t += DT) {
    // 1) resolve finished matches with the full production update
    for (let i = pending.length - 1; i >= 0; i--) {
      if (pending[i].finishAt > t) continue;
      const m = pending[i];
      const entries = m.members.map((idx, j) => ({ id: idx, rating: S[idx].rating, rank: m.ranks[j] }));
      // snapshot per-pair K from the rolling window (mirror of report path)
      const updated = eloUpdateWith(entries, (a, b) => {
        const [meet, net] = pairStats(a, b, t);
        return throttledK(meet, net);
      });
      // commit pair transfers into the window
      for (let a = 0; a < entries.length; a++) {
        for (let b = a + 1; b < entries.length; b++) {
          const da = updated[a] - entries[a].rating, db = updated[b] - entries[b].rating;
          recordPair(entries[a].id, entries[b].id, (da - db) / 2, t);
        }
      }
      entries.forEach((e, j) => {
        const s = S[e.id];
        s.games += 1;
        s.rating = clampCalibration(updated[j], s.games);
        s.lastPlayed = t; s.lastDecay = t;
        running[e.id] -= 1;
      });
      pending.splice(i, 1);
    }

    // 2) stateful idle decay for everyone
    for (let i = 0; i < cfg.T; i++) {
      const s = S[i];
      const [r, ld] = applyIdleDecay(s.rating, s.lastPlayed, s.lastDecay, t);
      s.rating = r; s.lastDecay = ld;
    }

    // 3) queue online teams under their concurrency cap
    const queue = [];
    for (let i = 0; i < cfg.T; i++) {
      if (running[i] >= cap) continue;
      if (rng() < onlineProb(teams[i], t) * cfg.queuePropensity) queue.push({ idx: i, rating: S[i].rating });
    }

    // 4) form lobbies, play them out (perf = trueElo + noise; rank by perf)
    for (const lobby of formLobbies(rng, queue)) {
      const perf = lobby.map(c => ({ idx: c.idx, p: teams[c.idx].trueElo + gauss(rng) * cfg.perfNoise }));
      perf.sort((a, b) => b.p - a.p);
      const ranks = {}; perf.forEach((e, r) => (ranks[e.idx] = r + 1));
      for (const c of lobby) running[c.idx] += 1;
      pending.push({ finishAt: t + MATCH_LEN, members: lobby.map(c => c.idx), ranks: lobby.map(c => ranks[c.idx]) });
    }
  }

  // ----- leaderboard (mirror of backend leaderboard(): rank by hidden rating,
  //       display the calibration-aware published score) -----
  const finalScore = teams.map((_, i) => publishedScore(S[i].rating, S[i].games));
  const active = teams.map((_, i) => i).filter(i => S[i].games > 0);
  const grad = active.filter(i => S[i].games >= CALIB_GAMES);
  const ranking = [...active].sort((a, b) => S[b].rating - S[a].rating); // by rating, like prod

  return { teams, S, finalScore, active, grad, ranking };
}

// ----------------------------------------------------------------------------
// stats helpers
// ----------------------------------------------------------------------------
const avg = a => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
const sortNum = a => [...a].sort((x, y) => x - y);
const quantile = (a, q) => { const s = sortNum(a); return s.length ? s[Math.min(s.length - 1, Math.floor(q * (s.length - 1)))] : 0; };
function rankvec(a) {
  const idx = a.map((v, i) => [v, i]).sort((x, y) => x[0] - y[0]);
  const r = new Array(a.length);
  for (let i = 0; i < idx.length;) { let j = i; while (j < idx.length && idx[j][0] === idx[i][0]) j++; const ar = (i + j + 1) / 2; for (let k = i; k < j; k++) r[idx[k][1]] = ar; i = j; }
  return r;
}
function pearson(x, y) {
  const n = x.length; if (n < 2) return 0;
  const mx = avg(x), my = avg(y); let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) { const dx = x[i] - mx, dy = y[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; }
  return sxx && syy ? sxy / Math.sqrt(sxx * syy) : 0;
}
const spearman = (x, y) => pearson(rankvec(x), rankvec(y));
function gini(a) {
  const s = sortNum(a); const n = s.length; if (!n) return 0;
  let cum = 0; for (let i = 0; i < n; i++) cum += (i + 1) * s[i];
  const tot = s.reduce((x, y) => x + y, 0);
  return tot ? (2 * cum) / (n * tot) - (n + 1) / n : 0;
}
function histogram(vals, lo, hi, bins, width = 40) {
  const counts = new Array(bins).fill(0);
  for (const v of vals) { const b = Math.min(bins - 1, Math.max(0, Math.floor(((v - lo) / (hi - lo)) * bins))); counts[b]++; }
  const max = Math.max(1, ...counts);
  const lines = [];
  for (let b = 0; b < bins; b++) {
    const a0 = lo + (b / bins) * (hi - lo), a1 = lo + ((b + 1) / bins) * (hi - lo);
    const bar = '#'.repeat(Math.round((counts[b] / max) * width));
    lines.push(`${a0.toFixed(0).padStart(5)}-${a1.toFixed(0).padStart(5)} | ${bar} ${counts[b]}`);
  }
  return lines.join('\n');
}

// ----------------------------------------------------------------------------
// reporting
// ----------------------------------------------------------------------------
const argv = process.argv.slice(2);
const asJson = argv.includes('--json');
const argNum = (name, def) => { const a = argv.find(x => x.startsWith(`--${name}=`)); return a ? Number(a.split('=')[1]) : def; };

const BASE = { T: argNum('teams', 200), hours: 48, perfNoise: 170, queuePropensity: 0.5, cap: 1 };
const SEEDS = 8;

// aggregate metrics across seeds for one config
function metrics(cfg) {
  const acc = { skillCorrRating: [], skillCorrDisplay: [], topHit: [], sat500: [], rate2000: [], giniScore: [], graduated: [], medGames: [], maxRating: [], distinctTop: [] };
  for (let s = 0; s < SEEDS; s++) {
    const { teams, S, finalScore, grad, ranking } = simulate({ ...cfg, seed: 1000 + s * 7 });
    if (!grad.length) continue;
    acc.skillCorrRating.push(spearman(grad.map(i => S[i].rating), grad.map(i => teams[i].trueElo)));
    acc.skillCorrDisplay.push(spearman(grad.map(i => finalScore[i]), grad.map(i => teams[i].trueElo)));
    // of the true top-10 by skill, how many land in the leaderboard top-10
    const trueTop = teams.map((_, i) => i).sort((a, b) => teams[b].trueElo - teams[a].trueElo).slice(0, 10);
    const boardTop = ranking.slice(0, 10);
    acc.topHit.push(trueTop.filter(i => boardTop.includes(i)).length / 10);
    acc.sat500.push(grad.filter(i => finalScore[i] >= SCORE_MAX).length);
    acc.rate2000.push(grad.filter(i => S[i].rating >= ANCHOR_HI).length);
    acc.giniScore.push(gini(grad.map(i => finalScore[i])));
    acc.graduated.push(grad.length);
    acc.medGames.push(quantile(grad.map(i => S[i].games), 0.5));
    acc.maxRating.push(Math.max(...grad.map(i => S[i].rating)));
    acc.distinctTop.push(new Set(ranking.slice(0, 10).map(i => finalScore[i])).size); // distinct scores in top 10
  }
  const m = {}; for (const k of Object.keys(acc)) m[k] = avg(acc[k]);
  return m;
}

console.log(`=== ELO + LEADERBOARD under a PARETO skill field (${BASE.T} teams, ${BASE.hours}h, ${SEEDS} seeds, prod rating stack) ===\n`);

// 1) representative single run for the histograms + top table (alpha=2)
const repAlpha = argNum('alpha', 2);
const rep = simulate({ ...BASE, dist: 'pareto', alpha: repAlpha, seed: 1000 });
console.log(`TRUE-SKILL field (Pareto alpha=${repAlpha}) — trueElo distribution:`);
console.log(histogram(rep.teams.map(t => t.trueElo), ELO_MIN, ELO_MAX, 16));
const teP = rep.teams.map(t => t.trueElo);
console.log(`  p50=${quantile(teP,0.5).toFixed(0)}  p90=${quantile(teP,0.9).toFixed(0)}  p99=${quantile(teP,0.99).toFixed(0)}  max=${Math.max(...teP).toFixed(0)}\n`);

console.log(`FINAL published-score distribution (graduated teams, 1..1000 anchored):`);
console.log(histogram(rep.grad.map(i => rep.finalScore[i]), 0, 1000, 16));
console.log();

console.log('LEADERBOARD top-15 (ranked by hidden rating, as the backend does):');
console.log('  #   team   trueElo  rating  score  games  tier');
rep.ranking.slice(0, 15).forEach((i, r) => {
  const t = rep.teams[i], s = rep.S[i];
  console.log(`  ${String(r + 1).padStart(2)}  t-${String(i).padStart(3,'0')}  ${t.trueElo.toFixed(0).padStart(7)}  ${s.rating.toFixed(0).padStart(6)}  ${String(rep.finalScore[i]).padStart(5)}  ${String(s.games).padStart(5)}  ${t.tier}`);
});
console.log();

// 2) alpha sweep + gaussian baseline (mean over seeds)
console.log('=== SKILL-DISTRIBUTION SWEEP (mean over seeds) ===');
console.log('field                 corr(rating) corr(score)  trueTop10hit  sat@1k  r>=3k  distinctTop10  giniScore  medGames graduated');
const rows = [
  ['gaussian (baseline)', { dist: 'gaussian' }],
  ['pareto a=3.0 (light tail)', { dist: 'pareto', alpha: 3.0 }],
  ['pareto a=2.0 (moderate)',   { dist: 'pareto', alpha: 2.0 }],
  ['pareto a=1.5 (heavy)',      { dist: 'pareto', alpha: 1.5 }],
  ['pareto a=1.16 (80/20)',     { dist: 'pareto', alpha: 1.16 }],
];
const out = {};
for (const [label, c] of rows) {
  const m = metrics({ ...BASE, ...c });
  out[label] = m;
  console.log(
    `${label.padEnd(22)} ${m.skillCorrRating.toFixed(3).padStart(11)} ${m.skillCorrDisplay.toFixed(3).padStart(11)} ` +
    `${(m.topHit*100).toFixed(0).padStart(11)}% ${m.sat500.toFixed(1).padStart(8)} ${m.rate2000.toFixed(1).padStart(8)} ` +
    `${m.distinctTop.toFixed(1).padStart(13)} ${m.giniScore.toFixed(3).padStart(10)} ${m.medGames.toFixed(0).padStart(9)} ${m.graduated.toFixed(0).padStart(9)}`);
}

// 3) does a per-team concurrency cap (ramp end-state) change top-end recovery?
console.log('\n=== CONCURRENCY CAP effect on recovery (pareto a=1.5) ===');
console.log('cap   corr(rating) trueTop10hit  sat@1k  medGames');
for (const cap of [1, 2, 4]) {
  const m = metrics({ ...BASE, dist: 'pareto', alpha: 1.5, cap });
  console.log(`${String(cap).padStart(3)}  ${m.skillCorrRating.toFixed(3).padStart(11)} ${(m.topHit*100).toFixed(0).padStart(11)}% ${m.sat500.toFixed(1).padStart(8)} ${m.medGames.toFixed(0).padStart(9)}`);
}

console.log('\nLegend:');
console.log('  corr(rating)  = spearman(hidden rating, true skill) over graduated — does the engine recover the field order?');
console.log('  corr(score)   = spearman(displayed 1..1000 score, true skill) — what players actually see (cap compresses it).');
console.log('  trueTop10hit  = % of the true top-10 skill teams that land in the leaderboard top-10.');
console.log('  sat@1k        = graduated teams pinned at the 1000 max (rating>=3000); distinctTop10 = distinct scores among top 10.');
console.log('  giniScore     = concentration of displayed scores (0=flat, 1=one team has all).');

if (asJson) console.log('\n__JSON__' + JSON.stringify(out));
