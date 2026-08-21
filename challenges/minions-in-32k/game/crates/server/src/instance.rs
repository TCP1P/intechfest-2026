//! Matchmade-instance mode: per-player access keys, a fixed match clock,
//! and authoritative result reports to the matchmaking backend.
//!
//! Activated by the `PLAYER_TOKENS` env var (JSON:
//! `[{"uuid": "...", "key": "...", "name": "..."}, ...]` — names come from
//! rCTF, players don't pick them in matchmade games) or
//! `PLAYER_TOKENS_FILE` pointing at the same JSON. Secrets ride in env,
//! not argv — argv is world-readable on a shared host. Without tokens the
//! server runs as the open dev server it always was.

use serde::Serialize;
use std::path::Path;
use std::time::Duration;

#[derive(Clone, Debug, serde::Deserialize)]
pub struct InstancePlayer {
    pub uuid: String,
    pub key: String,
    /// Display name as registered in rCTF — clients don't pick names in
    /// matchmade games.
    pub name: String,
}

#[derive(Clone, Debug)]
pub struct InstanceConfig {
    pub players: Vec<InstancePlayer>,
    pub match_id: String,
    pub grace: Duration,
    pub match_len: Duration,
    pub report_url: Option<String>,
    pub replay_upload_url: Option<String>,
    pub report_token: Option<String>,
    /// When present, emit fixed-duration scoring legs followed by one
    /// cumulative reconciliation report. The normal matchmaking backend keeps
    /// its historical one-shot report unless this deployment-only switch is
    /// explicitly set.
    pub scoring_leg: Option<Duration>,
}

impl InstanceConfig {
    pub fn from_env(grace_secs: u64, match_secs: u64) -> anyhow::Result<Option<Self>> {
        let raw = match std::env::var("PLAYER_TOKENS") {
            Ok(v) => v,
            Err(_) => match std::env::var("PLAYER_TOKENS_FILE") {
                Ok(path) => std::fs::read_to_string(path)?,
                Err(_) => return Ok(None),
            },
        };
        // JSON: [{"uuid": "...", "key": "...", "name": "..."}, ...]
        let players: Vec<InstancePlayer> =
            serde_json::from_str(&raw).map_err(|e| anyhow::anyhow!("PLAYER_TOKENS: {e}"))?;
        if players.is_empty() {
            anyhow::bail!("PLAYER_TOKENS set but empty");
        }
        if players.len() > protocol::MAX_PLAYERS {
            anyhow::bail!(
                "{} tokens but MAX_PLAYERS is {}",
                players.len(),
                protocol::MAX_PLAYERS
            );
        }
        let scoring_leg = match std::env::var("MINIONS_SCORING_LEG_SECS") {
            Ok(raw) => {
                let seconds = raw
                    .parse::<u64>()
                    .map_err(|_| anyhow::anyhow!("MINIONS_SCORING_LEG_SECS must be an integer"))?;
                if seconds == 0 || match_secs % seconds != 0 {
                    anyhow::bail!("MINIONS_SCORING_LEG_SECS must evenly divide the match duration");
                }
                Some(Duration::from_secs(seconds))
            }
            Err(_) => None,
        };
        Ok(Some(Self {
            players,
            match_id: std::env::var("MATCH_ID").unwrap_or_else(|_| "unknown".into()),
            grace: Duration::from_secs(grace_secs),
            match_len: Duration::from_secs(match_secs),
            report_url: std::env::var("REPORT_URL").ok(),
            replay_upload_url: std::env::var("REPLAY_UPLOAD_URL").ok(),
            report_token: std::env::var("REPORT_TOKEN").ok(),
            scoring_leg,
        }))
    }

    pub fn scoring_leg_count(&self) -> Option<u32> {
        self.scoring_leg
            .map(|leg| (self.match_len.as_secs() / leg.as_secs()) as u32)
    }
}

#[derive(Clone, Serialize, Debug, PartialEq, Eq)]
pub struct ReportPlayer {
    pub team_uuid: String,
    pub name: String,
    pub rank: u32,
    pub kills: u32,
    pub confirmed_kills: u32,
    pub deaths: u32,
    pub connected: bool,
    pub takeovers: u32,
    pub integrity_status: IntegrityStatus,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum IntegrityStatus {
    #[default]
    Clean,
    AutomatedCombat,
}

#[derive(Clone, Copy, Serialize, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ReportKind {
    Leg,
    Final,
}

#[derive(Clone, Serialize, Debug)]
pub struct MatchReport {
    pub match_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kind: Option<ReportKind>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sequence: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub leg_count: Option<u32>,
    pub started_unix_ms: u64,
    pub ended_unix_ms: u64,
    pub duration_ms: u64,
    pub players: Vec<ReportPlayer>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PlayerCounters {
    pub kills: u32,
    pub confirmed_kills: u32,
    pub deaths: u32,
    pub takeovers: u32,
    pub automated_combat: bool,
}

/// Start a fresh official scoring window without clearing integrity evidence
/// collected during staging. Integrity is match-lifetime state; the numeric
/// counters are scoring-window state.
pub fn reset_scoring_counters(counters: &mut [PlayerCounters; protocol::MAX_PLAYERS]) {
    for player in counters {
        let automated_combat = player.automated_combat;
        *player = PlayerCounters {
            automated_combat,
            ..PlayerCounters::default()
        };
    }
}

/// Per-leg counter baselines and server-observed presence. Presence is only an
/// admission gate; it never adds points. A player must be connected and still
/// sending valid input for at least 90% of the leg.
#[derive(Clone, Debug)]
pub struct LegWindow {
    baseline: [PlayerCounters; protocol::MAX_PLAYERS],
    present_ticks: [u64; protocol::MAX_PLAYERS],
    total_ticks: u64,
}

impl LegWindow {
    pub fn new(baseline: [PlayerCounters; protocol::MAX_PLAYERS]) -> Self {
        Self {
            baseline,
            present_ticks: [0; protocol::MAX_PLAYERS],
            total_ticks: 0,
        }
    }

    pub fn record_tick(&mut self, present: &[bool; protocol::MAX_PLAYERS]) {
        self.total_ticks = self.total_ticks.saturating_add(1);
        for (ticks, is_present) in self.present_ticks.iter_mut().zip(present) {
            if *is_present {
                *ticks = ticks.saturating_add(1);
            }
        }
    }

    pub fn finish(
        &mut self,
        cfg: &InstanceConfig,
        key_slots: &std::collections::HashMap<String, protocol::PlayerId>,
        current: [PlayerCounters; protocol::MAX_PLAYERS],
    ) -> Vec<ReportPlayer> {
        let players = cfg
            .players
            .iter()
            .map(|player| {
                let index = key_slots.get(&player.key).map(|slot| *slot as usize);
                let counters = index.map(|i| current[i]).unwrap_or_default();
                let baseline = index.map(|i| self.baseline[i]).unwrap_or_default();
                let connected = index.is_some_and(|i| {
                    self.total_ticks > 0
                        && u128::from(self.present_ticks[i]) * 10
                            >= u128::from(self.total_ticks) * 9
                });
                ReportPlayer {
                    team_uuid: player.uuid.clone(),
                    name: player.name.clone(),
                    rank: 0,
                    kills: counters.kills.saturating_sub(baseline.kills),
                    confirmed_kills: counters
                        .confirmed_kills
                        .saturating_sub(baseline.confirmed_kills),
                    deaths: counters.deaths.saturating_sub(baseline.deaths),
                    connected,
                    takeovers: counters.takeovers.saturating_sub(baseline.takeovers),
                    integrity_status: if counters.automated_combat {
                        IntegrityStatus::AutomatedCombat
                    } else {
                        IntegrityStatus::Clean
                    },
                }
            })
            .collect();
        self.baseline = current;
        self.present_ticks.fill(0);
        self.total_ticks = 0;
        rank_players(players)
    }
}

/// Dense competition ranking: confirmed kills, total kills, then deaths.
/// Exact score-line ties share a rank.
pub fn rank_players(mut players: Vec<ReportPlayer>) -> Vec<ReportPlayer> {
    players.sort_by(|a, b| {
        // Disconnected/AFK players sort strictly below every connected
        // player so they can't farm Elo by camping a slot.
        b.connected
            .cmp(&a.connected)
            .then_with(|| b.confirmed_kills.cmp(&a.confirmed_kills))
            .then_with(|| b.kills.cmp(&a.kills))
            .then_with(|| a.deaths.cmp(&b.deaths))
            .then_with(|| a.team_uuid.cmp(&b.team_uuid))
    });
    let mut rank = 0u32;
    let mut prev: Option<(bool, u32, u32, u32)> = None;
    for p in players.iter_mut() {
        let key = (p.connected, p.confirmed_kills, p.kills, p.deaths);
        if prev != Some(key) {
            rank += 1;
            prev = Some(key);
        }
        p.rank = rank;
    }
    players
}

/// Ordered report POST with retries. Competitors never see the bearer token
/// (env-only, server-only). Deployment-specific leg reporting uses a reusable
/// internal credential; the default matchmaking path still sends one report.
pub async fn submit_report(cfg: &InstanceConfig, report: &MatchReport) -> bool {
    let Some(url) = cfg.report_url.as_deref() else {
        tracing::warn!("no REPORT_URL — printing report to log");
        tracing::info!(report = %serde_json::to_string(report).unwrap_or_default(), "match report");
        return true;
    };
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, "report client build");
            return false;
        }
    };
    for attempt in 1..=3u32 {
        let mut req = client.post(url).json(report);
        if let Some(token) = cfg.report_token.as_deref() {
            req = req.bearer_auth(token);
        }
        match req.send().await {
            Ok(resp) if resp.status().is_success() => {
                tracing::info!(attempt, kind = ?report.kind, sequence = ?report.sequence, "match report accepted");
                return true;
            }
            Ok(resp) => {
                tracing::warn!(attempt, status = %resp.status(), "match report rejected");
            }
            Err(e) => {
                tracing::warn!(attempt, error = %e, "match report send failed");
            }
        }
        tokio::time::sleep(Duration::from_secs(2 * attempt as u64)).await;
    }
    tracing::error!("match report failed after 3 attempts");
    false
}

pub async fn report_worker(
    cfg: InstanceConfig,
    mut reports: tokio::sync::mpsc::UnboundedReceiver<MatchReport>,
) -> bool {
    let mut history = Vec::with_capacity(cfg.scoring_leg_count().unwrap_or(0) as usize + 1);
    let mut accepted = true;
    while let Some(report) = reports.recv().await {
        // Replay the complete ordered prefix whenever a new leg is ready. The
        // bridge treats identical reports as idempotent, so this also rebuilds
        // its in-memory sequence after a restart without delaying the game loop
        // or requiring durable credentials/report state on the bridge host.
        let batch = report_replay_batch(&mut history, report);
        accepted = true;
        for report in &batch {
            if !submit_report(&cfg, report).await {
                // A later report cannot be admitted before this prefix. Stop
                // this pass so an outage costs one bounded retry window, then
                // replay the prefix again when the next leg arrives.
                accepted = false;
                break;
            }
        }
    }
    accepted
}

fn report_replay_batch(history: &mut Vec<MatchReport>, report: MatchReport) -> Vec<MatchReport> {
    history.push(report);
    history.clone()
}

/// Best-effort finalized replay upload. It uses the same bearer token as the
/// report, so callers must upload before `/api/report` consumes the token.
pub async fn upload_replay(cfg: &InstanceConfig, replay_path: &Path) {
    let Some(url) = cfg.replay_upload_url.as_deref() else {
        return;
    };
    let Some(token) = cfg.report_token.as_deref() else {
        tracing::warn!("REPLAY_UPLOAD_URL set but REPORT_TOKEN missing");
        return;
    };
    let bytes = match tokio::fs::read(replay_path).await {
        Ok(bytes) => bytes::Bytes::from(bytes),
        Err(e) => {
            tracing::warn!(path = %replay_path.display(), error = %e, "replay read failed");
            return;
        }
    };
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, "replay upload client build");
            return;
        }
    };
    for attempt in 1..=3u32 {
        match client
            .post(url)
            .query(&[("match_id", cfg.match_id.as_str())])
            .bearer_auth(token)
            .header("content-type", "application/octet-stream")
            .body(bytes.clone())
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                tracing::info!(attempt, bytes = bytes.len(), "replay upload accepted");
                return;
            }
            Ok(resp) => {
                tracing::warn!(attempt, status = %resp.status(), "replay upload rejected");
            }
            Err(e) => {
                tracing::warn!(attempt, error = %e, "replay upload send failed");
            }
        }
        tokio::time::sleep(Duration::from_secs(2 * attempt as u64)).await;
    }
    tracing::error!("replay upload failed after 3 attempts");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn p(uuid: &str, confirmed_kills: u32, kills: u32, deaths: u32) -> ReportPlayer {
        ReportPlayer {
            team_uuid: uuid.into(),
            name: uuid.into(),
            rank: 0,
            kills,
            confirmed_kills,
            deaths,
            connected: true,
            takeovers: 0,
            integrity_status: IntegrityStatus::Clean,
        }
    }

    #[test]
    fn ranking_is_dense_with_draws() {
        let ranked = rank_players(vec![
            p("c", 2, 5, 9),
            p("a", 4, 10, 2),
            p("b", 4, 10, 2),
            p("d", 2, 5, 3),
        ]);
        let got: Vec<(&str, u32)> = ranked
            .iter()
            .map(|r| (r.team_uuid.as_str(), r.rank))
            .collect();
        // a/b draw at rank 1, d beats c on deaths
        assert_eq!(got, vec![("a", 1), ("b", 1), ("d", 2), ("c", 3)]);
    }

    #[test]
    fn confirmed_kills_rank_ahead_of_unconfirmed_frags() {
        let ranked = rank_players(vec![p("frag-farmer", 2, 12, 1), p("confirmer", 3, 4, 5)]);
        assert_eq!(ranked[0].team_uuid, "confirmer");
        assert_eq!(ranked[0].rank, 1);
        assert_eq!(ranked[1].rank, 2);
    }

    fn config() -> InstanceConfig {
        InstanceConfig {
            players: vec![
                InstancePlayer {
                    uuid: "team-a".into(),
                    key: "key-a".into(),
                    name: "alpha".into(),
                },
                InstancePlayer {
                    uuid: "team-b".into(),
                    key: "key-b".into(),
                    name: "beta".into(),
                },
            ],
            match_id: "match".into(),
            grace: Duration::from_secs(30),
            match_len: Duration::from_secs(300),
            report_url: None,
            replay_upload_url: None,
            report_token: None,
            scoring_leg: Some(Duration::from_secs(60)),
        }
    }

    #[test]
    fn scoring_leg_count_is_fixed_by_match_and_leg_lengths() {
        assert_eq!(config().scoring_leg_count(), Some(5));
    }

    #[test]
    fn scoring_window_reset_preserves_staging_integrity_evidence() {
        let mut counters = [PlayerCounters::default(); protocol::MAX_PLAYERS];
        counters[2] = PlayerCounters {
            kills: 9,
            confirmed_kills: 7,
            deaths: 3,
            takeovers: 1,
            automated_combat: true,
        };

        reset_scoring_counters(&mut counters);

        assert_eq!(
            counters[2],
            PlayerCounters {
                automated_combat: true,
                ..PlayerCounters::default()
            }
        );
        assert_eq!(counters[1], PlayerCounters::default());
    }

    #[test]
    fn leg_window_uses_deltas_and_ninety_percent_presence_gate() {
        let cfg = config();
        let slots = HashMap::from([("key-a".to_string(), 0), ("key-b".to_string(), 1)]);
        let mut baseline = [PlayerCounters::default(); protocol::MAX_PLAYERS];
        baseline[0] = PlayerCounters {
            kills: 20,
            confirmed_kills: 12,
            deaths: 4,
            takeovers: 1,
            ..PlayerCounters::default()
        };
        let mut window = LegWindow::new(baseline);
        for tick in 0..10 {
            let mut present = [false; protocol::MAX_PLAYERS];
            present[0] = tick < 9;
            present[1] = tick < 8;
            window.record_tick(&present);
        }
        let mut current = baseline;
        current[0] = PlayerCounters {
            kills: 27,
            confirmed_kills: 17,
            deaths: 7,
            takeovers: 2,
            ..PlayerCounters::default()
        };
        current[1] = PlayerCounters {
            kills: 9,
            confirmed_kills: 3,
            deaths: 5,
            takeovers: 0,
            ..PlayerCounters::default()
        };
        let players = window.finish(&cfg, &slots, current);
        let alpha = players
            .iter()
            .find(|player| player.name == "alpha")
            .unwrap();
        let beta = players.iter().find(|player| player.name == "beta").unwrap();
        assert_eq!(
            (
                alpha.confirmed_kills,
                alpha.kills,
                alpha.deaths,
                alpha.takeovers
            ),
            (5, 7, 3, 1)
        );
        assert!(alpha.connected);
        assert!(!beta.connected);

        let mut present = [false; protocol::MAX_PLAYERS];
        present[0] = true;
        window.record_tick(&present);
        let next = window.finish(&cfg, &slots, current);
        let alpha = next.iter().find(|player| player.name == "alpha").unwrap();
        assert_eq!(
            (
                alpha.confirmed_kills,
                alpha.kills,
                alpha.deaths,
                alpha.takeovers
            ),
            (0, 0, 0, 0)
        );
    }

    #[test]
    fn segmented_report_fields_are_explicit_but_legacy_report_stays_unchanged() {
        let mut report = MatchReport {
            match_id: "match".into(),
            kind: None,
            sequence: None,
            leg_count: None,
            started_unix_ms: 1,
            ended_unix_ms: 2,
            duration_ms: 1,
            players: Vec::new(),
        };
        let legacy = serde_json::to_value(&report).unwrap();
        assert!(legacy.get("kind").is_none());
        assert!(legacy.get("sequence").is_none());
        assert!(legacy.get("leg_count").is_none());

        report.kind = Some(ReportKind::Leg);
        report.sequence = Some(1);
        report.leg_count = Some(5);
        let segmented = serde_json::to_value(&report).unwrap();
        assert_eq!(segmented["kind"], "leg");
        assert_eq!(segmented["sequence"], 1);
        assert_eq!(segmented["leg_count"], 5);
    }

    #[test]
    fn each_new_report_replays_the_complete_ordered_prefix() {
        let report = |sequence, kind| MatchReport {
            match_id: "match".into(),
            kind: Some(kind),
            sequence: Some(sequence),
            leg_count: Some(5),
            started_unix_ms: u64::from(sequence) * 60_000,
            ended_unix_ms: u64::from(sequence + 1) * 60_000,
            duration_ms: 60_000,
            players: Vec::new(),
        };
        let mut history = Vec::new();

        let first = report_replay_batch(&mut history, report(1, ReportKind::Leg));
        assert_eq!(
            first.iter().map(|item| item.sequence).collect::<Vec<_>>(),
            vec![Some(1)]
        );

        let second = report_replay_batch(&mut history, report(2, ReportKind::Leg));
        assert_eq!(
            second.iter().map(|item| item.sequence).collect::<Vec<_>>(),
            vec![Some(1), Some(2)]
        );

        let final_batch = report_replay_batch(&mut history, report(6, ReportKind::Final));
        assert_eq!(
            final_batch
                .iter()
                .map(|item| item.sequence)
                .collect::<Vec<_>>(),
            vec![Some(1), Some(2), Some(6)]
        );
    }
}
