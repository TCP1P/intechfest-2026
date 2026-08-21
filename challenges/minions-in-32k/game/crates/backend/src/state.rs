//! In-memory state, v1. Every mutation goes through one mutex — match
//! volume is tiny (a CTF weekend), simplicity wins. Postgres is the
//! plan-doc upgrade path once durability matters.

use crate::elo;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};
use std::time::Instant;

/// Per-team rating record. `rating` is the live matchmaking rating; the public
/// score is derived from it (calibration-pinned while `games_played < 10`).
/// Timestamps are unix seconds so they survive persistence across restarts.
#[derive(Clone, Serialize, Deserialize)]
pub struct TeamRecord {
    pub rating: f64,
    pub games_played: u32,
    /// Unix seconds of the last finished match; drives the idle-decay grace.
    pub last_played_unix: u64,
    /// Unix seconds the stateful decay sweep last advanced this team's rating.
    pub last_decay_unix: u64,
}

impl Default for TeamRecord {
    fn default() -> Self {
        TeamRecord {
            rating: elo::INITIAL_RATING,
            games_played: 0,
            last_played_unix: 0,
            last_decay_unix: 0,
        }
    }
}

/// Lock helper that recovers from a poisoned mutex rather than panicking: a
/// handler that paniced mid-mutation shouldn't take the whole backend down.
pub fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

#[derive(Clone)]
pub struct QueueEntry {
    pub team_uuid: String,
    pub queued_at: Instant,
    /// rCTF display name, passed through to instances and rosters.
    pub name: String,
    /// Forwarded k8s pod spec (`{ "pods": [...], "udprouterAddr": ... }`); the
    /// lobby seed's copy is used at spawn. `None` on the local-process dev path.
    pub config: Option<serde_json::Value>,
    /// Forwarded challengeIntegrationId; used for rCTF score posts.
    pub challenge_id: Option<String>,
    /// Set when the queue secret was enforced and verified for this enrollment
    /// (always true in open dev mode). Only authenticated teams publish scores.
    pub authenticated: bool,
}

#[derive(Clone, Serialize)]
pub struct MatchPlayer {
    pub team_uuid: String,
    #[serde(skip)]
    #[allow(dead_code)] // audit trail; connection strings embed it
    pub key: String,
    pub connection: Vec<String>,
    /// Carried from the queue entry: only authenticated teams publish scores.
    #[serde(skip)]
    pub authenticated: bool,
}

#[derive(Clone)]
pub struct MatchState {
    pub match_id: String,
    pub report_token: String,
    pub report_used: bool,
    pub players: Vec<MatchPlayer>,
    #[allow(dead_code)] // ops/debug
    pub started_at: Instant,
    pub deadline: Instant,
    /// 32-hex udp-router demux key for this server.
    #[allow(dead_code)] // ops/debug; baked into connection strings
    pub instance_id: String,
    /// DNS-safe ChallengeInstance object name; used to GC the
    /// pod on finish/void. Empty on the local-process path.
    pub crd_name: String,
    /// rCTF challenge id this match belongs to; used to address the
    /// dynamic-score publish endpoint (`/api/v2/challs/{id}/scores`).
    pub challenge_id: String,
}

#[derive(Default)]
pub struct Store {
    pub queue: HashMap<String, QueueEntry>,
    pub ratings: HashMap<String, TeamRecord>,
    /// Cumulative net Elo exchanged per unordered team pair within the current
    /// rolling window — the collusion throttle's memory. Key is the sorted pair.
    pub pair_history: HashMap<(String, String), PairHistory>,
    /// match_id -> state; also serves connection info to matched teams.
    pub matches: HashMap<String, MatchState>,
    /// Teams dequeued and committed to a forming lobby whose instance hasn't
    /// been created yet, mapped to their original queue entry. Closes the
    /// takeover race: queue_join/leave and the next form_lobbies tick must see
    /// these as already spoken-for. On create failure the entry is restored to
    /// `queue`; on success the team moves into `matches`.
    pub reserved: HashMap<String, QueueEntry>,
    /// identity -> last accepted join time, for per-identity join rate limiting.
    pub last_join: HashMap<String, Instant>,
    /// match_id -> publish audit record; also the idempotency guard so a retried
    /// report doesn't double-publish.
    pub published: HashMap<String, PublishAudit>,
    pub finished: u64,
    pub voided: u64,
    /// Optional JSON durability file (best-effort). `None` → in-memory only.
    pub state_file: Option<PathBuf>,
}

/// Rolling-window record of how often a team pair has met and how much Elo has
/// flowed between them — feeds the collusion throttle (repeat-opponent K decay
/// + a per-window net-transfer cap).
#[derive(Clone)]
pub struct PairHistory {
    /// Matches this pair has shared inside the current window.
    pub meetings: u32,
    /// Net rating moved between the pair this window (absolute, both directions
    /// summed); bounds how much a colluding pair can pump each other.
    pub net_transfer: f64,
    /// Instant the window opened; reset when it elapses.
    pub window_start: Instant,
}

impl Default for PairHistory {
    fn default() -> Self {
        PairHistory {
            meetings: 0,
            net_transfer: 0.0,
            window_start: Instant::now(),
        }
    }
}

/// Backend-side audit of a score publish — never part of the signed rCTF body.
#[derive(Clone, Serialize)]
pub struct PublishAudit {
    pub challenge_id: String,
    pub user_ids: Vec<String>,
    pub points: Vec<u32>,
    /// HTTP status, or 0 for a dry-run / transport failure.
    pub status: u16,
}

/// Subset of `Store` persisted to `--state-file`: ratings plus enough match
/// bookkeeping to survive a restart. Transient queue/reservation state and
/// `Instant`-based timers are intentionally not persisted.
#[derive(Serialize, Deserialize, Default)]
struct Persisted {
    ratings: HashMap<String, TeamRecord>,
    finished: u64,
    voided: u64,
}

impl Store {
    /// Live commitments held by a team right now: pending reservation (counts
    /// once) plus every active match containing it. The concurrency gate caps
    /// this against cap(t).
    pub fn live_count(&self, team: &str) -> usize {
        let reserved = self
            .reserved
            .values()
            .filter(|e| e.team_uuid == team)
            .count();
        let active = self
            .matches
            .values()
            .filter(|m| m.players.iter().any(|p| p.team_uuid == team))
            .count();
        reserved + active
    }

    pub fn queued_count(&self, team: &str) -> usize {
        self.queue.values().filter(|e| e.team_uuid == team).count()
    }

    /// Per-identity join throttle. Returns true (and records now) when the
    /// caller is allowed; false when it joined within `min_interval`.
    pub fn allow_join(&mut self, identity: &str, min_interval: std::time::Duration) -> bool {
        let now = Instant::now();
        if let Some(prev) = self.last_join.get(identity) {
            if now.duration_since(*prev) < min_interval {
                return false;
            }
        }
        // Opportunistic GC so the map can't grow without bound.
        if self.last_join.len() > 16_384 {
            self.last_join
                .retain(|_, t| now.duration_since(*t) < std::time::Duration::from_secs(3600));
        }
        self.last_join.insert(identity.to_string(), now);
        true
    }

    pub fn rating(&self, team: &str) -> f64 {
        self.ratings
            .get(team)
            .map(|r| r.rating)
            .unwrap_or(elo::INITIAL_RATING)
    }

    pub fn games_played(&self, team: &str) -> u32 {
        self.ratings.get(team).map(|r| r.games_played).unwrap_or(0)
    }

    /// Mutable record, created at the default (INITIAL_RATING) on first touch.
    pub fn record_mut(&mut self, team: &str) -> &mut TeamRecord {
        self.ratings.entry(team.to_string()).or_default()
    }

    /// Calibration/decay-aware published score: pinned to 1 while the team is
    /// still in calibration (`games_played < CALIB_GAMES`), otherwise the
    /// anchored public score of its current (possibly decayed) rating.
    pub fn published_score(&self, team: &str) -> u32 {
        elo::published_score(self.rating(team), self.games_played(team))
    }

    /// Sorted key for an unordered team pair.
    pub fn pair_key(a: &str, b: &str) -> (String, String) {
        if a <= b {
            (a.to_string(), b.to_string())
        } else {
            (b.to_string(), a.to_string())
        }
    }

    /// Meetings + net transfer this pair has accumulated in the current window,
    /// rolling the window over if it has elapsed. Pure read used to derive the
    /// throttled K before applying Elo.
    pub fn pair_stats(&self, a: &str, b: &str) -> (u32, f64) {
        let key = Self::pair_key(a, b);
        match self.pair_history.get(&key) {
            Some(h)
                if h.window_start.elapsed()
                    < std::time::Duration::from_secs(elo::PAIR_WINDOW_SECS) =>
            {
                (h.meetings, h.net_transfer)
            }
            _ => (0, 0.0),
        }
    }

    /// Record that `a` and `b` met and exchanged `transfer` (absolute) rating,
    /// rolling the window if stale.
    pub fn record_pair(&mut self, a: &str, b: &str, transfer: f64) {
        let key = Self::pair_key(a, b);
        let now = Instant::now();
        let h = self.pair_history.entry(key).or_insert_with(|| PairHistory {
            window_start: now,
            ..PairHistory::default()
        });
        if h.window_start.elapsed() >= std::time::Duration::from_secs(elo::PAIR_WINDOW_SECS) {
            *h = PairHistory {
                window_start: now,
                ..PairHistory::default()
            };
        }
        h.meetings += 1;
        h.net_transfer += transfer.abs();
        // Opportunistic GC of stale windows so the map can't grow unbounded.
        if self.pair_history.len() > 65_536 {
            self.pair_history.retain(|_, v| {
                v.window_start.elapsed() < std::time::Duration::from_secs(elo::PAIR_WINDOW_SECS)
            });
        }
    }

    /// Every live match containing `team`, with that team's player record in
    /// each, sorted by match_id for a stable order. A team may hold up to
    /// cap(t) concurrent commitments, so the status path surfaces all of them —
    /// each is a separately-connectable instance.
    pub fn active_matches_for(&self, team: &str) -> Vec<(&MatchState, &MatchPlayer)> {
        let mut out: Vec<(&MatchState, &MatchPlayer)> = self
            .matches
            .values()
            .filter_map(|m| {
                m.players
                    .iter()
                    .find(|p| p.team_uuid == team)
                    .map(|p| (m, p))
            })
            .collect();
        out.sort_by(|a, b| a.0.match_id.cmp(&b.0.match_id));
        out
    }

    /// Non-mutating lookup used for all report validation; does NOT consume the
    /// token. Pair with `consume_report_token` on the success path.
    pub fn lookup_match_by_token(&self, bearer: &str) -> Option<String> {
        self.matches
            .values()
            .find(|m| !m.report_token.is_empty() && m.report_token == bearer && !m.report_used)
            .map(|m| m.match_id.clone())
    }

    /// Mark a match's token consumed. Returns false if it was already used or
    /// the match is gone (so the caller can bail).
    pub fn consume_report_token(&mut self, match_id: &str) -> bool {
        match self.matches.get_mut(match_id) {
            Some(m) if !m.report_used => {
                m.report_used = true;
                true
            }
            _ => false,
        }
    }

    /// Remove a match, count it voided, and return its CRD name (for pod GC).
    /// `None` if the match is already gone. Caller persists.
    pub fn void_match(&mut self, match_id: &str) -> Option<String> {
        let crd_name = self.matches.remove(match_id)?.crd_name;
        self.voided += 1;
        Some(crd_name)
    }

    /// Best-effort load of persisted ratings/counters from raw JSON bytes.
    pub fn load_bytes(bytes: &[u8], state_file: Option<PathBuf>, source: &str) -> Self {
        let mut store = Store {
            state_file,
            ..Store::default()
        };
        match serde_json::from_slice::<Persisted>(bytes) {
            Ok(p) => {
                store.ratings = p.ratings;
                store.finished = p.finished;
                store.voided = p.voided;
                tracing::info!(%source, teams = store.ratings.len(), "state loaded");
            }
            Err(e) => tracing::warn!(%source, error = %e, "state unreadable; starting fresh"),
        }
        store
    }

    /// Best-effort load of persisted ratings/counters from `state_file`.
    pub fn load(state_file: Option<PathBuf>) -> Self {
        let mut store = Store {
            state_file: state_file.clone(),
            ..Store::default()
        };
        if let Some(path) = &state_file {
            match std::fs::read(path) {
                Ok(bytes) => match serde_json::from_slice::<Persisted>(&bytes) {
                    Ok(p) => {
                        store.ratings = p.ratings;
                        store.finished = p.finished;
                        store.voided = p.voided;
                        tracing::info!(path = %path.display(), teams = store.ratings.len(), "state loaded");
                    }
                    Err(e) => {
                        tracing::warn!(path = %path.display(), error = %e, "state file unreadable; starting fresh")
                    }
                },
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => {
                    tracing::warn!(path = %path.display(), error = %e, "state file read failed; starting fresh")
                }
            }
        }
        store
    }

    /// Best-effort durable write (atomic via temp + rename). No-op without a
    /// `state_file`, so local dev is unaffected.
    pub fn persist(&self) {
        let Some(path) = &self.state_file else { return };
        let json = match self.persisted_json() {
            Ok(j) => j,
            Err(e) => {
                tracing::warn!(error = %e, "state serialize failed");
                return;
            }
        };
        let tmp = path.with_extension("tmp");
        if let Err(e) = std::fs::write(&tmp, &json).and_then(|_| std::fs::rename(&tmp, path)) {
            tracing::warn!(path = %path.display(), error = %e, "state persist failed");
        }
    }

    pub fn persisted_json(&self) -> serde_json::Result<Vec<u8>> {
        let snapshot = Persisted {
            ratings: self.ratings.clone(),
            finished: self.finished,
            voided: self.voided,
        };
        serde_json::to_vec_pretty(&snapshot)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn report_token_is_single_use() {
        let mut s = Store::default();
        s.matches.insert(
            "m1".into(),
            MatchState {
                match_id: "m1".into(),
                report_token: "tok".into(),
                report_used: false,
                players: vec![],
                started_at: Instant::now(),
                deadline: Instant::now() + Duration::from_secs(1),
                instance_id: "id".into(),
                crd_name: String::new(),
                challenge_id: "minions".into(),
            },
        );
        assert_eq!(s.lookup_match_by_token("tok").as_deref(), Some("m1"));
        // Lookup is non-mutating: still resolvable until explicitly consumed.
        assert_eq!(s.lookup_match_by_token("tok").as_deref(), Some("m1"));
        assert!(s.consume_report_token("m1"));
        assert_eq!(s.lookup_match_by_token("tok"), None);
        assert!(!s.consume_report_token("m1"));
        assert_eq!(s.lookup_match_by_token("other"), None);
    }
}
