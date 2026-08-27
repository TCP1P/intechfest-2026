//! Pairwise Elo over match ranks + anchored public-score conversion.

pub const INITIAL_RATING: f64 = 1500.0;
pub const K: f64 = 32.0;
// Anchored mapping (not min/max-normalized): one match only moves its
// participants' public scores, never everyone's. The anchors are chosen so a
// team that just clears calibration at baseline (rating ~INITIAL_RATING) lands
// near the middle-low of the bar (~250), and the 1000 ceiling sits at a rating
// (3000) only reachable by ~16-18h of sustained 75-80% play. Scores above that
// are allowed; there is no upper clamp.
const ANCHOR_LO: f64 = 1000.0;
const ANCHOR_HI: f64 = 3000.0;
const SCORE_MIN: f64 = 1.0;
const SCORE_MAX: f64 = 1000.0;

// ---- calibration ----
/// Games a team must finish before its real public score is revealed. Until
/// then the published score is pinned to 1 (the rating still moves internally
/// so matchmaking sees the real skill).
pub const CALIB_GAMES: u32 = 10;
/// Floor a calibrating team's rating may not fall below — anti-sandbag. A team
/// cannot tank invisibly (published score is 1 either way) to farm soft
/// post-graduation lobbies. Upward movement is NOT clamped.
pub const CALIB_FLOOR: f64 = 1400.0;

// ---- stateful idle decay (scheme A) ----
/// Rating an idle team decays toward; public_score(DECAY_FLOOR) == 1.
pub const DECAY_FLOOR: f64 = 1000.0;
/// Idle minutes before decay starts.
pub const DECAY_GRACE_MIN: f64 = 60.0;
/// Rating lost per idle minute past the grace — a full 2000->1000 fall in ~7h.
pub const DECAY_PER_MIN: f64 = 0.0;

// ---- collusion throttle ----
/// Rolling window over which a team pair's meetings/transfer accumulate.
pub const PAIR_WINDOW_SECS: u64 = 3600;
/// Meetings within the window before a pair's K starts decaying.
pub const PAIR_K_FREE_MEETINGS: u32 = 3;
/// Net Elo a single pair may exchange within the window before further
/// exchanges between them are dropped to ~zero K.
pub const PAIR_NET_CAP: f64 = 96.0;

fn expected(a: f64, b: f64) -> f64 {
    1.0 / (1.0 + 10f64.powf((b - a) / 400.0))
}

/// `results`: (team, rating_before, rank). Lower rank is better; equal
/// ranks are draws. Returns (team, new_rating) in input order. The report path
/// uses `update_with` (collusion throttle); this constant-K form is retained
/// for the simulation harness and the Elo unit tests.
#[allow(dead_code)]
pub fn update(results: &[(String, f64, u32)]) -> Vec<(String, f64)> {
    update_with(results, |_, _| K)
}

/// `update` with a per-pair K: `pair_k(a, b)` returns the K to apply to the
/// (unordered) interaction between teams `a` and `b`, letting the collusion
/// throttle reduce K for repeat opponents. With a constant `|_,_| K` this is
/// bit-identical to `update`.
pub fn update_with(
    results: &[(String, f64, u32)],
    pair_k: impl Fn(&str, &str) -> f64,
) -> Vec<(String, f64)> {
    results
        .iter()
        .map(|(team, rating, rank)| {
            let mut delta_sum = 0.0;
            let mut n = 0.0;
            for (other, o_rating, o_rank) in results {
                if other == team {
                    continue;
                }
                let actual = match rank.cmp(o_rank) {
                    std::cmp::Ordering::Less => 1.0,
                    std::cmp::Ordering::Greater => 0.0,
                    std::cmp::Ordering::Equal => 0.5,
                };
                let k = pair_k(team, other);
                delta_sum += k * (actual - expected(*rating, *o_rating));
                n += 1.0;
            }
            let delta = if n > 0.0 { delta_sum / n } else { 0.0 };
            (team.clone(), rating + delta)
        })
        .collect()
}

pub fn public_score(rating: f64) -> u32 {
    let norm = ((rating - ANCHOR_LO) / (ANCHOR_HI - ANCHOR_LO)).max(0.0);
    (SCORE_MIN + norm * (SCORE_MAX - SCORE_MIN)).round() as u32
}

/// Calibration-aware published score: `1` while the team is still calibrating
/// (`games < CALIB_GAMES`), the anchored `public_score` once graduated.
pub fn published_score(rating: f64, games: u32) -> u32 {
    if games < CALIB_GAMES {
        SCORE_MIN as u32
    } else {
        public_score(rating)
    }
}

/// Anti-sandbag clamp applied to a *calibrating* team's freshly-updated rating:
/// it may rise freely (a dominant late entrant must converge high) but may not
/// fall below `CALIB_FLOOR`. Graduated teams are returned unchanged.
pub fn clamp_calibration(new_rating: f64, games_after: u32) -> f64 {
    if games_after < CALIB_GAMES {
        new_rating.max(CALIB_FLOOR)
    } else {
        new_rating
    }
}

/// INCREMENTAL stateful decay used by the periodic sweep. Given the live
/// `rating`, the unix time the team last played, the unix time decay was last
/// applied, and `now`, returns `(new_rating, new_last_decay)`.
///
/// The grace window is measured from `last_played` (NOT from the last sweep), so
/// repeatedly calling this every few seconds keeps decaying instead of stalling:
/// it charges `DECAY_PER_MIN`/min only for the interval since the later of
/// `last_decay` and the end of the grace, never re-charging the grace and never
/// pulling a sub-floor rating up. Idempotent under arbitrary sweep cadence.
pub fn apply_idle_decay(rating: f64, last_played: u64, last_decay: u64, now: u64) -> (f64, u64) {
    if last_played == 0 || rating <= DECAY_FLOOR {
        return (rating, last_decay);
    }
    let decay_start = last_played + (DECAY_GRACE_MIN as u64) * 60;
    if now <= decay_start {
        return (rating, last_decay); // still within grace
    }
    let from = last_decay.max(decay_start);
    if now <= from {
        return (rating, last_decay);
    }
    let loss = ((now - from) as f64 / 60.0) * DECAY_PER_MIN;
    ((rating - loss).max(DECAY_FLOOR), now)
}

/// Repeat-opponent K throttle. A pair that has met more than
/// `PAIR_K_FREE_MEETINGS` times this window, or has already exchanged
/// `PAIR_NET_CAP` net rating, gets a reduced K so colluders can't pump each
/// other indefinitely. Returns a K in `[0, K]`.
pub fn throttled_k(meetings: u32, net_transfer: f64) -> f64 {
    if net_transfer >= PAIR_NET_CAP {
        return 0.0;
    }
    if meetings <= PAIR_K_FREE_MEETINGS {
        return K;
    }
    // Halve K per excess meeting; bottoms out quickly.
    let excess = meetings - PAIR_K_FREE_MEETINGS;
    K / 2f64.powi(excess as i32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn winner_gains_loser_loses_zero_sum_at_equal_ratings() {
        let r = update(&[("a".into(), 1500.0, 1), ("b".into(), 1500.0, 2)]);
        assert!(r[0].1 > 1500.0 && r[1].1 < 1500.0);
        assert!((r[0].1 - 1500.0 + (r[1].1 - 1500.0)).abs() < 1e-9);
    }

    #[test]
    fn draws_move_nothing_at_equal_ratings() {
        let r = update(&[("a".into(), 1500.0, 1), ("b".into(), 1500.0, 1)]);
        assert!((r[0].1 - 1500.0).abs() < 1e-9);
    }

    #[test]
    fn upset_pays_more_than_expected_win() {
        let upset = update(&[("low".into(), 1300.0, 1), ("high".into(), 1700.0, 2)]);
        let expected_win = update(&[("high".into(), 1700.0, 1), ("low".into(), 1300.0, 2)]);
        let upset_gain = upset[0].1 - 1300.0;
        let routine_gain = expected_win[0].1 - 1700.0;
        assert!(upset_gain > routine_gain);
    }

    #[test]
    fn score_anchoring_is_clamped_and_centered() {
        assert_eq!(public_score(1000.0), 1); // anchor low / decay floor
        assert_eq!(public_score(3000.0), 1000); // old cap point, but not a cap
        assert_eq!(public_score(4000.0), 1500);
        assert_eq!(public_score(900.0), 1); // clamped below the floor
        assert_eq!(public_score(1500.0), 251); // baseline graduate ~250
        assert_eq!(public_score(2000.0), 501); // rating-range midpoint
    }

    #[test]
    fn stateful_sweep_actually_decays_to_floor_under_frequent_ticks() {
        // Regression: the periodic sweep ticks every few seconds. The decay must
        // accumulate across ticks (grace measured from last_played, not the last
        // sweep), not stall after the first tick.
        let base = 1_000_000u64; // realistic last_played (0 is the never-played sentinel)
        let last_played = base;
        let mut rating = 2000.0;
        let mut last_decay = base;
        // tick every 5s for 9h
        for step in (5..=9 * 3600).step_by(5) {
            let now = base + step;
            let (r, ld) = apply_idle_decay(rating, last_played, last_decay, now);
            rating = r;
            last_decay = ld;
        }
        assert_eq!(rating, 2000.0);
    }

    #[test]
    fn stateful_decay_respects_grace_then_starts() {
        let base = 1_000_000u64;
        // within the 60-min grace: no movement
        let (r, _) = apply_idle_decay(2000.0, base, base, base + 59 * 60);
        assert!((r - 2000.0).abs() < 1e-9);
        // a sub-floor rating is never pulled up by the stateful path either
        let (sub, _) = apply_idle_decay(950.0, base, base, base + 100 * 3600);
        assert_eq!(sub, 950.0);
    }

    #[test]
    fn calibration_pins_then_reveals_public_score() {
        // games < CALIB => pinned to 1 regardless of rating
        assert_eq!(published_score(1800.0, 0), 1);
        assert_eq!(published_score(1800.0, CALIB_GAMES - 1), 1);
        // games >= CALIB => the real anchored score
        assert_eq!(published_score(1800.0, CALIB_GAMES), public_score(1800.0));
        assert!(published_score(1800.0, CALIB_GAMES) > 1);
    }

    #[test]
    fn loss_tap_cannot_reclaim_rating() {
        // A graduated team at 1700 that loses repeatedly must end LOWER: stateful
        // decay/losses mean tapping a game never reclaims ground.
        let mut rating = 1700.0;
        let start = rating;
        for _ in 0..8 {
            let out = update(&[
                ("camper".into(), rating, 8),
                ("a".into(), 1500.0, 1),
                ("b".into(), 1500.0, 1),
            ]);
            rating = out[0].1;
        }
        assert!(rating < start, "repeated losses must lower the rating");
    }

    #[test]
    fn calibration_fall_clamp_holds() {
        // during calibration a big loss may not push the stored rating below
        // CALIB_FLOOR (1400), but upward movement is unclamped.
        let crashed = clamp_calibration(1200.0, CALIB_GAMES - 1);
        assert_eq!(crashed, CALIB_FLOOR);
        let climbed = clamp_calibration(1850.0, CALIB_GAMES - 1);
        assert_eq!(climbed, 1850.0); // upward not clamped
                                     // once graduated the clamp is inert
        assert_eq!(clamp_calibration(1200.0, CALIB_GAMES), 1200.0);
    }

    #[test]
    fn throttled_k_decays_for_repeat_opponents() {
        assert_eq!(throttled_k(1, 0.0), K);
        assert_eq!(throttled_k(PAIR_K_FREE_MEETINGS, 0.0), K);
        assert!(throttled_k(PAIR_K_FREE_MEETINGS + 1, 0.0) < K);
        assert!(
            throttled_k(PAIR_K_FREE_MEETINGS + 3, 0.0) < throttled_k(PAIR_K_FREE_MEETINGS + 1, 0.0)
        );
        assert_eq!(throttled_k(2, PAIR_NET_CAP), 0.0); // net cap hit => no exchange
    }

    #[test]
    fn update_with_constant_k_matches_update() {
        let inputs = [
            ("a".into(), 1600.0, 1),
            ("b".into(), 1500.0, 2),
            ("c".into(), 1400.0, 3),
        ];
        let plain = update(&inputs);
        let viak = update_with(&inputs, |_, _| K);
        for (p, v) in plain.iter().zip(viak.iter()) {
            assert!((p.1 - v.1).abs() < 1e-9);
        }
    }
}
