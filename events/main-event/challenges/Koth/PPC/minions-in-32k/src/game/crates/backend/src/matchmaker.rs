//! Lobby formation: rating-biased random sampling (plan doc).
//!
//! Trigger (checked every tick): ≥2 teams waiting longer than `min_wait`,
//! OR the queue has reached `burst_size` — then form lobbies of up to 8.

use rand::prelude::*;

pub const MAX_LOBBY: usize = 8;
const NEARBY_BAND: f64 = 200.0;
const TEMPERATURE: f64 = 400.0;

/// A queued entry as seen by lobby formation. `team` is the unique queue id;
/// `owner` is the stable verified team/user id. A single owner may have several
/// queued entries for concurrent instances, but only one may appear per lobby.
pub struct Candidate {
    /// Unique queue entry id.
    pub team: String,
    /// Stable rCTF team/user id. Multiple queue entries may share this, but a
    /// lobby may not contain the same owner twice.
    pub owner: String,
    pub rating: f64,
    pub waited_secs: u64,
}

/// Candidates in; lobbies out. Leftover/unpairable singles stay queued.
/// `max_new_lobbies` caps how many lobbies one tick will form (instance/active
/// ceiling enforcement); 0 forms none.
pub fn form_lobbies(
    rng: &mut impl Rng,
    mut pool: Vec<Candidate>,
    min_wait_secs: u64,
    burst_size: usize,
    max_new_lobbies: usize,
) -> Vec<Vec<String>> {
    let overdue = pool
        .iter()
        .filter(|c| c.waited_secs >= min_wait_secs)
        .count();
    if overdue < 2 && pool.len() < burst_size {
        return Vec::new();
    }

    let mut lobbies = Vec::new();
    while pool.len() >= 2 && lobbies.len() < max_new_lobbies {
        let seed_idx = rng.gen_range(0..pool.len());
        let seed = pool.swap_remove(seed_idx);
        let seed_rating = seed.rating;
        let mut owners = vec![seed.owner];
        let mut lobby = vec![seed.team];
        while lobby.len() < MAX_LOBBY && !pool.is_empty() {
            // exp(-|Δrating|/T), boosted inside the nearby band
            let weights: Vec<f64> = pool
                .iter()
                .map(|c| {
                    if owners.contains(&c.owner) {
                        return 0.0;
                    }
                    let d = (c.rating - seed_rating).abs();
                    let w = (-d / TEMPERATURE).exp();
                    if d <= NEARBY_BAND {
                        w * 2.0
                    } else {
                        w
                    }
                })
                .collect();
            let total: f64 = weights.iter().sum();
            if total <= 0.0 {
                break;
            }
            let mut pick = rng.gen::<f64>() * total;
            let mut chosen = 0;
            for (k, w) in weights.iter().enumerate() {
                pick -= w;
                if pick <= 0.0 {
                    chosen = k;
                    break;
                }
            }
            let c = pool.swap_remove(chosen);
            owners.push(c.owner);
            lobby.push(c.team);
        }
        if lobby.len() >= 2 {
            lobbies.push(lobby);
        }
        // A lone seed left with an empty pool is dropped from this tick and
        // stays queued (it was swap_remove'd from the local pool only).
    }
    lobbies
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::rngs::StdRng;
    use rand::SeedableRng;

    fn cand(team: &str, rating: f64, waited: u64) -> Candidate {
        Candidate {
            team: team.into(),
            owner: team.into(),
            rating,
            waited_secs: waited,
        }
    }

    fn cand_for(queue_id: &str, owner: &str, rating: f64, waited: u64) -> Candidate {
        Candidate {
            team: queue_id.into(),
            owner: owner.into(),
            rating,
            waited_secs: waited,
        }
    }

    fn pool(n: usize, waited: u64) -> Vec<Candidate> {
        (0..n)
            .map(|i| cand(&format!("t{i}"), 1500.0 + i as f64 * 10.0, waited))
            .collect()
    }

    #[test]
    fn no_trigger_below_thresholds() {
        let mut rng = StdRng::seed_from_u64(7);
        assert!(form_lobbies(&mut rng, pool(5, 3), 30, 20, 64).is_empty());
        assert!(form_lobbies(&mut rng, vec![cand("solo", 1500.0, 999)], 30, 20, 64).is_empty());
    }

    #[test]
    fn overdue_pair_forms_a_lobby() {
        let mut rng = StdRng::seed_from_u64(7);
        let lobbies = form_lobbies(&mut rng, pool(3, 31), 30, 20, 64);
        assert_eq!(lobbies.len(), 1);
        assert_eq!(lobbies[0].len(), 3);
    }

    #[test]
    fn burst_splits_into_capped_lobbies_and_assigns_everyone() {
        let mut rng = StdRng::seed_from_u64(7);
        let lobbies = form_lobbies(&mut rng, pool(20, 0), 30, 20, 64);
        let total: usize = lobbies.iter().map(Vec::len).sum();
        assert_eq!(total, 20);
        assert!(lobbies.iter().all(|l| l.len() >= 2 && l.len() <= MAX_LOBBY));
        assert!(lobbies.len() >= 3); // 20 players can't fit in 2 lobbies of 8
    }

    #[test]
    fn max_new_lobbies_caps_formation() {
        let mut rng = StdRng::seed_from_u64(7);
        let lobbies = form_lobbies(&mut rng, pool(20, 0), 30, 20, 1);
        assert_eq!(lobbies.len(), 1);
    }

    #[test]
    fn duplicate_owner_entries_do_not_share_a_lobby() {
        let mut rng = StdRng::seed_from_u64(7);
        let lobbies = form_lobbies(
            &mut rng,
            vec![
                cand_for("a#0", "a", 1500.0, 31),
                cand_for("a#1", "a", 1500.0, 31),
                cand_for("b#0", "b", 1500.0, 31),
                cand_for("c#0", "c", 1500.0, 31),
            ],
            30,
            20,
            64,
        );
        assert!(lobbies.iter().all(|lobby| {
            let a_count = lobby.iter().filter(|team| team.starts_with("a#")).count();
            a_count <= 1
        }));
    }

    #[test]
    fn rating_bias_groups_neighbours_more_often_than_not() {
        // two tight clusters far apart; over many runs the seed's cluster
        // should dominate its lobby
        let mut rng = StdRng::seed_from_u64(42);
        let mut same_cluster = 0;
        let mut total = 0;
        for _ in 0..200 {
            let mut p: Vec<Candidate> = (0..4)
                .map(|i| cand(&format!("low{i}"), 1200.0, 40))
                .chain((0..4).map(|i| cand(&format!("high{i}"), 1800.0, 40)))
                .collect();
            p.shuffle(&mut rng);
            let lobbies = form_lobbies(&mut rng, p, 30, 20, 64);
            let lobby = &lobbies[0][..4.min(lobbies[0].len())];
            let lows = lobby.iter().filter(|t| t.starts_with("low")).count();
            same_cluster += lows.max(lobby.len() - lows);
            total += lobby.len();
        }
        assert!(same_cluster as f64 / total as f64 > 0.6);
    }
}
