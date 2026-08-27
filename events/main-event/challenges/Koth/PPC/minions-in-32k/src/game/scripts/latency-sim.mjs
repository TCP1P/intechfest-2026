// Latency-aware CTF scoring simulation: a field of competitive bots with varied
// ping/jitter, scored by the VALIDATED scheme (A: rating-decay + 10-game
// calibration + per-team concurrency ramp), reusing the production Elo math.
//
// The game is lag-compensated (server rewinds hitscan), so ping penalizes
// performance only PARTIALLY: effective match performance = true skill - a
// dampened latency penalty + noise. This harness produces exhaustive statistics
// on how skill, latency, and activity shape the final leaderboard.
//
// Run: node scripts/latency-sim.mjs

// ---------- production formulas (verbatim from crates/backend/src/elo.rs) ----------
const INITIAL_RATING = 1500, K = 32;
const ANCHOR_LO = 1000, ANCHOR_HI = 3000, SCORE_MIN = 1, SCORE_MAX = 1000;
const CALIB_GAMES = 10, DECAY_FLOOR = 1000, DECAY_GRACE_MIN = 60, DECAY_PER_MIN = 2.4, CALIB_FLOOR = 1400;
const expected = (a, b) => 1 / (1 + 10 ** ((b - a) / 400));
const publicScore = r => {
  const norm = Math.min(1, Math.max(0, (r - ANCHOR_LO) / (ANCHOR_HI - ANCHOR_LO)));
  return Math.round(SCORE_MIN + norm * (SCORE_MAX - SCORE_MIN));
};
const publishedScore = (r, games) => (games < CALIB_GAMES ? 1 : publicScore(r));
function eloUpdate(entries, k = K) {
  return entries.map(e => {
    let sum = 0, n = 0;
    for (const o of entries) { if (o === e) continue; const a = e.rank < o.rank ? 1 : e.rank > o.rank ? 0 : 0.5; sum += a - expected(e.rating, o.rating); n++; }
    return e.rating + (n ? (k * sum) / n : 0);
  });
}
const clampCalib = (r, games) => (games < CALIB_GAMES ? Math.max(r, CALIB_FLOOR) : r);
function applyIdleDecay(rating, lastPlayed, lastDecay, now) {
  if (lastPlayed === 0 || rating <= DECAY_FLOOR) return [rating, lastDecay];
  const decayStart = lastPlayed + DECAY_GRACE_MIN * 60;
  if (now <= decayStart) return [rating, lastDecay];
  const from = Math.max(lastDecay, decayStart);
  if (now <= from) return [rating, lastDecay];
  const loss = ((now - from) / 60) * DECAY_PER_MIN;
  return [Math.max(rating - loss, DECAY_FLOOR), now];
}

// ---------- rng + stats ----------
function mulberry32(seed) { let a = seed >>> 0; return () => { a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const gauss = rng => { let u = 0, v = 0; while (!u) u = rng(); while (!v) v = rng(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
const avg = a => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
const std = a => { const m = avg(a); return Math.sqrt(avg(a.map(v => (v - m) ** 2))); };
const median = a => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const quantile = (a, q) => { const s = [...a].sort((x, y) => x - y); return s[Math.max(0, Math.min(s.length - 1, Math.floor(q * (s.length - 1))))]; };
function rankvec(a) { const idx = a.map((v, i) => [v, i]).sort((x, y) => x[0] - y[0]); const r = new Array(a.length); for (let i = 0; i < idx.length;) { let j = i; while (j < idx.length && idx[j][0] === idx[i][0]) j++; const ar = (i + j + 1) / 2; for (let k = i; k < j; k++) r[idx[k][1]] = ar; i = j; } return r; }
function pearson(x, y) { const n = x.length; if (n < 2) return 0; const mx = avg(x), my = avg(y); let sxy = 0, sxx = 0, syy = 0; for (let i = 0; i < n; i++) { const dx = x[i] - mx, dy = y[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; } return sxx && syy ? sxy / Math.sqrt(sxx * syy) : 0; }
const spearman = (x, y) => pearson(rankvec(x), rankvec(y));
function olsSlope(x, y) { const mx = avg(x), my = avg(y); let sxy = 0, sxx = 0; for (let i = 0; i < x.length; i++) { sxy += (x[i] - mx) * (y[i] - my); sxx += (x[i] - mx) ** 2; } return sxx ? sxy / sxx : 0; }
// partial: regress y on c, regress x on c, correlate residuals
function residual(v, c) { const s = olsSlope(c, v), mc = avg(c), mv = avg(v); return v.map((vi, i) => vi - (mv + s * (c[i] - mc))); }
const partialCorr = (a, b, c) => pearson(residual(a, c), residual(b, c));

// ---------- latency model ----------
// Lag-compensated: ping penalty is dampened. effective = true - penalty + noise.
// penalty grows past a ~40ms baseline; jitter hurts extra (unpredictable).
const PING_BASE = 40;
function latPenalty(ping, jitter, LAT_K, JIT_K) {
  return LAT_K * Math.max(0, ping - PING_BASE) + JIT_K * jitter;
}

// ---------- team field: a competitive bot tournament ----------
// ping tiers (ms): datacenter/fiber, regional broadband, cross-region, poor.
const PING_TIERS = [
  { name: 'fiber',  frac: 0.35, lo: 8,   hi: 35,  jit: 3 },
  { name: 'region', frac: 0.40, lo: 35,  hi: 90,  jit: 8 },
  { name: 'cross',  frac: 0.20, lo: 90,  hi: 160, jit: 20 },
  { name: 'poor',   frac: 0.05, lo: 160, hi: 290, jit: 45 },
];
function makeField(rng, T, skillMean, skillSig) {
  const teams = [];
  for (let i = 0; i < T; i++) {
    let r = rng(), tier = PING_TIERS[PING_TIERS.length - 1], acc = 0;
    for (const t of PING_TIERS) { acc += t.frac; if (r <= acc) { tier = t; break; } }
    const ping = tier.lo + rng() * (tier.hi - tier.lo);
    teams.push({
      id: i, pingTier: tier.name,
      trueElo: skillMean + gauss(rng) * skillSig,
      ping, jitter: tier.jit * (0.5 + rng()),
      act: 0.7 + rng() * 0.28, // competitive bots: high, varied activity
      arrive: Math.floor(Math.max(0, gauss(rng) * 2)) * 60,
      depart: rng() < 0.1 ? (24 + rng() * 20) * 60 : Infinity,
      phase: rng() * 2 - 1,
    });
  }
  return teams;
}
const onlineProb = (team, t) => {
  if (t < team.arrive || t > team.depart) return 0;
  const h = (t / 60) % 24;
  const diurnal = 0.6 + 0.4 * Math.cos(((h - 15) / 24) * 2 * Math.PI + team.phase);
  return team.act * Math.max(0.1, diurnal);
};
const HOUR = 60, DT = 5;
const concurrentCap = (t, ramp) => ramp.enabled ? Math.min(ramp.max, ramp.start + Math.floor(t / (ramp.everyHours * HOUR))) : 1;

const MAX_LOBBY = 8, NEARBY_BAND = 200, TEMPERATURE = 400;
function formLobbies(rng, queue) {
  const pool = queue.slice(); if (pool.length < 2) return [];
  const lobbies = [];
  while (pool.length >= 2) {
    const seed = pool.splice(Math.floor(rng() * pool.length), 1)[0];
    const lobby = [seed.idx];
    while (lobby.length < MAX_LOBBY && pool.length) {
      const w = pool.map(c => { const d = Math.abs(c.rating - seed.rating); const x = Math.exp(-d / TEMPERATURE); return d <= NEARBY_BAND ? x * 2 : x; });
      const tot = w.reduce((a, b) => a + b, 0); let pick = rng() * tot, idx = 0;
      for (let i = 0; i < w.length; i++) { pick -= w[i]; if (pick <= 0) { idx = i; break; } }
      lobby.push(pool.splice(idx, 1)[0].idx);
    }
    if (lobby.length >= 2) lobbies.push(lobby);
  }
  return lobbies;
}

// scheme A (rating-decay) + calibration, stateful
function makeRec() { return { rating: INITIAL_RATING, games: 0, lastPlayed: 0, lastDecay: 0 }; }

function simulate(cfg) {
  const rng = mulberry32(cfg.seed);
  const teams = makeField(rng, cfg.T, cfg.skillMean, cfg.skillSig);
  // reference probes: identical skill, different ping (and a low-ping mediocre)
  if (cfg.probes) {
    teams[0] = { ...teams[0], trueElo: 2000, ping: 18, jitter: 3, act: 0.95, arrive: 0, depart: Infinity, pingTier: 'probe', label: 'strong/low-ping' };
    teams[1] = { ...teams[1], trueElo: 2000, ping: 210, jitter: 45, act: 0.95, arrive: 0, depart: Infinity, pingTier: 'probe', label: 'strong/high-ping' };
    teams[2] = { ...teams[2], trueElo: 1760, ping: 18, jitter: 3, act: 0.95, arrive: 0, depart: Infinity, pingTier: 'probe', label: 'mediocre/low-ping' };
  }
  const S = teams.map(makeRec);
  const DURATION = cfg.hours * HOUR, MATCH_LEN = 15;
  const running = new Array(cfg.T).fill(0), pending = [];
  const scoreHist = teams.map(() => []); // sparse score snapshots for churn
  const baseUnix = 1_000_000;
  for (let t = 0; t <= DURATION; t += DT) {
    const now = baseUnix + t * 60;
    // resolve
    for (let i = pending.length - 1; i >= 0; i--) if (pending[i].finishAt <= t) {
      const m = pending[i];
      const entries = m.members.map((idx, j) => ({ idx, rating: S[idx].rating, rank: m.ranks[j] }));
      const k = entries.some(e => S[e.idx].games < CALIB_GAMES) ? K : K;
      const up = eloUpdate(entries, k);
      entries.forEach((e, j) => { const r = S[e.idx]; r.games++; r.rating = clampCalib(up[j], r.games); r.lastPlayed = now; r.lastDecay = now; running[e.idx]--; });
      pending.splice(i, 1);
    }
    // decay sweep
    for (let i = 0; i < cfg.T; i++) { if (running[i] > 0) continue; const r = S[i]; const [nr, nd] = applyIdleDecay(r.rating, r.lastPlayed, r.lastDecay, now); r.rating = nr; r.lastDecay = nd; }
    // queue + form
    const cap = concurrentCap(t, cfg.ramp), queue = [];
    for (let i = 0; i < cfg.T; i++) { if (running[i] >= cap) continue; if (rng() < onlineProb(teams[i], t) * cfg.queueProp) queue.push({ idx: i, rating: S[i].rating }); }
    for (const lobby of formLobbies(rng, queue)) {
      // PERFORMANCE = true skill - lag penalty + per-match noise (+ jitter noise)
      const perf = lobby.map(idx => ({ idx, p: teams[idx].trueElo - latPenalty(teams[idx].ping, teams[idx].jitter, cfg.LAT_K, cfg.JIT_K) + gauss(rng) * cfg.perfNoise + gauss(rng) * teams[idx].jitter * cfg.JIT_NOISE }));
      perf.sort((a, b) => b.p - a.p);
      const ranks = {}; perf.forEach((e, r) => ranks[e.idx] = r + 1);
      for (const idx of lobby) running[idx]++;
      pending.push({ finishAt: t + MATCH_LEN, members: lobby, ranks: lobby.map(idx => ranks[idx]) });
    }
    // churn snapshot every 2h in the last 12h
    if (t > DURATION - 12 * HOUR && t % (2 * HOUR) === 0) for (let i = 0; i < cfg.T; i++) scoreHist[i].push(publishedScore(S[i].rating, S[i].games));
  }
  const score = teams.map((_, i) => publishedScore(S[i].rating, S[i].games));
  const active = teams.map((_, i) => i).filter(i => S[i].games > 0);
  const grad = active.filter(i => S[i].games >= CALIB_GAMES);
  const ranking = [...active].sort((a, b) => score[b] - score[a]);
  return { teams, S, score, active, grad, ranking, scoreHist };
}

// ---------- run + report ----------
const BASE = { T: 200, hours: 48, perfNoise: 150, queueProp: 0.7, skillMean: 1880, skillSig: 130, LAT_K: 1.1, JIT_K: 1.6, JIT_NOISE: 1.2, ramp: { enabled: true, start: 1, max: 4, everyHours: 12 }, probes: true };
const SEEDS = 12;
const fmt = (x, d = 1) => x == null ? ' n/a' : x.toFixed(d);

console.log('=== COMPETITIVE BOT FIELD: 200 teams, skill~N(1880,130), lag-compensated latency, 48h, scheme A+calib+ramp, ' + SEEDS + ' seeds ===\n');

// aggregate across seeds
const runs = []; for (let s = 0; s < SEEDS; s++) runs.push(simulate({ ...BASE, seed: 7000 + s * 13 }));

// 1) field composition
const allPings = runs.flatMap(r => r.teams.map(t => t.ping));
console.log('FIELD PING (ms):  p10=' + fmt(quantile(allPings, .1), 0) + '  p50=' + fmt(median(allPings), 0) + '  p90=' + fmt(quantile(allPings, .9), 0) + '  max=' + fmt(Math.max(...allPings), 0));
for (const tier of PING_TIERS) { const frac = avg(runs.map(r => r.teams.filter(t => t.pingTier === tier.name).length / r.teams.length)); console.log('  ' + tier.name.padEnd(8) + ('~' + tier.lo + '-' + tier.hi + 'ms').padEnd(12) + (100 * frac).toFixed(0) + '% of field'); }

// 2) final score distribution (graduated only)
const gradScores = runs.flatMap(r => r.grad.map(i => r.score[i]));
console.log('\nFINAL SCORE (graduated teams): min=' + fmt(Math.min(...gradScores), 0) + ' p25=' + fmt(quantile(gradScores, .25), 0) + ' median=' + fmt(median(gradScores), 0) + ' p75=' + fmt(quantile(gradScores, .75), 0) + ' p95=' + fmt(quantile(gradScores, .95), 0) + ' max=' + fmt(Math.max(...gradScores), 0));
// histogram
const bins = [1, 250, 400, 550, 700, 850, 1000]; const hist = bins.slice(0, -1).map((lo, i) => gradScores.filter(s => s >= lo && s < bins[i + 1]).length);
const maxh = Math.max(...hist); console.log('  histogram:');
bins.slice(0, -1).forEach((lo, i) => console.log('   ' + (lo + '-' + bins[i + 1]).padEnd(9) + '|' + '#'.repeat(Math.round(40 * hist[i] / maxh)) + ' ' + (100 * hist[i] / gradScores.length).toFixed(0) + '%'));

// 3) correlations: how much do skill / latency drive the final score
const cs = [], cl = []; // pooled per-team
for (const r of runs) for (const i of r.grad) cs.push([r.teams[i].trueElo, r.teams[i].ping, r.teams[i].jitter, r.score[i], r.S[i].games]);
const sk = cs.map(x => x[0]), pg = cs.map(x => x[1]), jt = cs.map(x => x[2]), sc = cs.map(x => x[3]), gm = cs.map(x => x[4]);
console.log('\nWHAT DRIVES THE SCORE (graduated, pooled n=' + cs.length + '):');
console.log('  score ~ true skill   : r=' + fmt(pearson(sk, sc), 2) + '   (partial, ping held)=' + fmt(partialCorr(sk, sc, pg), 2));
console.log('  score ~ ping         : r=' + fmt(pearson(pg, sc), 2) + '   (partial, skill held)=' + fmt(partialCorr(pg, sc, sk), 2));
console.log('  score ~ jitter       : r=' + fmt(pearson(jt, sc), 2));
console.log('  score ~ games played : r=' + fmt(pearson(gm, sc), 2));
// effective skill = true skill minus the lag penalty (what the player actually performs at)
const effEloAll = [];
for (const r of runs) for (const i of r.grad) effEloAll.push(r.teams[i].trueElo - latPenalty(r.teams[i].ping, r.teams[i].jitter, BASE.LAT_K, BASE.JIT_K));
console.log('  --- does the board track RAW skill or EFFECTIVE (lag-adjusted) performance? ---');
console.log('  spearman(score, RAW true skill)        = ' + fmt(spearman(sk, sc), 2));
console.log('  spearman(score, EFFECTIVE skill-lag)   = ' + fmt(spearman(effEloAll, sc), 2) + '   (higher => board faithfully measures in-game performance, ping included)');

// 4) latency tax: among matched-skill teams (1850-1950 trueElo), score vs ping
const band = cs.filter(x => x[0] >= 1850 && x[0] <= 1950);
const bScore = band.map(x => x[3]), bPing = band.map(x => x[1]);
const taxPer100 = olsSlope(bPing, bScore) * 100;
console.log('\nLATENCY TAX (matched skill 1850-1950, n=' + band.length + '): ' + fmt(taxPer100, 1) + ' score points per +100ms ping');
console.log('  ping bucket   avg score  avg rank%  (same skill band)');
const pbuckets = [[0, 40], [40, 90], [90, 160], [160, 300]];
for (const [lo, hi] of pbuckets) {
  const grp = []; for (const r of runs) for (const i of r.grad) { const tEl = r.teams[i].trueElo, p = r.teams[i].ping; if (tEl >= 1850 && tEl <= 1950 && p >= lo && p < hi) grp.push([r.score[i], (r.ranking.indexOf(i) + 1) / r.ranking.length]); }
  if (grp.length) console.log('  ' + (lo + '-' + hi + 'ms').padEnd(13) + fmt(avg(grp.map(g => g[0])), 0).padStart(8) + '   ' + fmt(100 * avg(grp.map(g => g[1])), 0).padStart(7) + '%   (n=' + grp.length + ')');
}

// 5) top-10 ping profile
const top10ping = runs.flatMap(r => r.ranking.slice(0, 10).map(i => r.teams[i].ping));
const botHalfPing = runs.flatMap(r => r.ranking.slice(Math.floor(r.ranking.length / 2)).map(i => r.teams[i].ping));
console.log('\nTOP-10 avg ping = ' + fmt(avg(top10ping), 0) + 'ms   vs bottom-half avg ping = ' + fmt(avg(botHalfPing), 0) + 'ms');
const top10frac = avg(runs.map(r => r.ranking.slice(0, 10).filter(i => r.teams[i].ping < 40).length / 10));
console.log('  fraction of top-10 that are low-ping (<40ms): ' + (100 * top10frac).toFixed(0) + '%');

// 6) fairness probes (identical-skill different-ping, + mediocre-low-ping)
console.log('\nFAIRNESS PROBES (mean over seeds, field of 200):');
for (const [idx, label] of [[0, 'strong(2000) / low-ping 18ms '], [1, 'strong(2000) / high-ping 210ms'], [2, 'mediocre(1760) / low-ping 18ms']]) {
  const rk = avg(runs.map(r => r.ranking.indexOf(idx) + 1)), scP = avg(runs.map(r => r.score[idx])), fld = avg(runs.map(r => r.ranking.length));
  console.log('  ' + label.padEnd(32) + ' -> rank ' + fmt(rk, 0).padStart(3) + '/' + fmt(fld, 0) + '  score ' + fmt(scP, 0).padStart(3) + '  (true skill ' + (idx === 2 ? 1760 : 2000) + ')');
}
const strongHighBeatsMediocreLow = avg(runs.map(r => (r.ranking.indexOf(1) < r.ranking.indexOf(2)) ? 1 : 0));
console.log('  => P(laggy-strong outranks low-ping-mediocre) = ' + (100 * strongHighBeatsMediocreLow).toFixed(0) + '%   (does raw skill still win through 210ms?)');

// 7) rank churn in last 12h (stability)
const churn = []; for (const r of runs) for (const i of r.grad) { const h = r.scoreHist[i].filter(v => v != null); if (h.length >= 2) churn.push(std(h)); }
console.log('\nLEADERBOARD STABILITY (last 12h): mean per-team score stddev = ' + fmt(avg(churn), 1) + ' pts (lower = steadier board)');

// 8) latency-impact sensitivity (LAT_K low/med/high) — bracket the uncertain penalty
console.log('\nLATENCY-IMPACT SENSITIVITY (tax per +100ms, top-10 avg ping):');
for (const [lab, LAT_K, JIT_K] of [['light  (LAT_K=0.6)', 0.6, 0.9], ['medium (LAT_K=1.1)', 1.1, 1.6], ['heavy  (LAT_K=1.8)', 1.8, 2.6]]) {
  const rr = []; for (let s = 0; s < SEEDS; s++) rr.push(simulate({ ...BASE, LAT_K, JIT_K, seed: 7000 + s * 13 }));
  const bnd = []; for (const r of rr) for (const i of r.grad) { const tEl = r.teams[i].trueElo; if (tEl >= 1850 && tEl <= 1950) bnd.push([r.teams[i].ping, r.score[i]]); }
  const tax = olsSlope(bnd.map(b => b[0]), bnd.map(b => b[1])) * 100;
  const t10 = avg(rr.flatMap(r => r.ranking.slice(0, 10).map(i => r.teams[i].ping)));
  const probeGap = avg(rr.map(r => (r.ranking.indexOf(1) + 1) - (r.ranking.indexOf(0) + 1)));
  console.log('  ' + lab + ': tax=' + fmt(tax, 1).padStart(6) + ' pts/100ms   top10 ping=' + fmt(t10, 0) + 'ms   210ms-vs-18ms rank gap=' + fmt(probeGap, 0) + ' places');
}
console.log('\nNotes: lag-compensation dampens ping; "tax" = score points a same-skill team loses per +100ms. Probes isolate ping at fixed skill.');
