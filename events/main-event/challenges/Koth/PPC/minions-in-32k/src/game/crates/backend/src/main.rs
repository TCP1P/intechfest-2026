//! Matchmaking backend: queue intake (from rCTF), lobby formation, instance
//! provisioning, one-shot match-report ingestion, pairwise Elo, and score
//! publication (dry-run until the rCTF API ships).

mod elo;
mod gcs;
mod instancer;
mod matchmaker;
mod state;

use anyhow::{Context, Result};
use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use clap::Parser;
use instancer::{
    build_connection, challenge_instance_name, GameInstancer, InstanceRequest, K8sInstancer,
    LocalProcess,
};
use rand::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::json;
use state::{lock, MatchPlayer, MatchState, PublishAudit, Store};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Parser, Debug, Clone)]
#[command(version, about)]
struct Args {
    #[arg(long, default_value = "127.0.0.1:8080")]
    bind: std::net::SocketAddr,
    /// Base URL players/instances reach us at (report URL, wasm links).
    #[arg(long, default_value = "http://127.0.0.1:8080")]
    public_base: String,
    /// Host players connect to game instances on.
    #[arg(long, default_value = "127.0.0.1")]
    game_host: String,
    /// Game server binary for the local-process instancer.
    #[arg(long, default_value = "target/release/server")]
    server_bin: PathBuf,
    #[arg(long, default_value = "gru-lab-v3")]
    arena: String,
    #[arg(long, default_value_t = 4500)]
    base_port: u16,
    /// Instancer backend: `local` spawns server processes on this host;
    /// `k8s` creates ChallengeInstance objects in a cluster (needs the
    /// K8S_INSTANCER_* env vars). Pod config, challenge id, and udprouter
    /// address are forwarded per-request by rCTF.
    #[arg(long, default_value = "local")]
    instancer: String,
    /// Queue wait that makes a team "overdue" (lobby forms at 2+ overdue).
    #[arg(long, default_value_t = 30)]
    min_wait_secs: u64,
    /// Queue size that triggers immediate lobby formation.
    #[arg(long, default_value_t = 20)]
    burst_size: usize,
    #[arg(long, default_value_t = 30)]
    grace_secs: u64,
    #[arg(long, default_value_t = 300)]
    match_secs: u64,
    /// Matchmaker scheduler period.
    #[arg(long, default_value_t = 5)]
    tick_secs: u64,
    /// Directory with the wasm-bindgen client bundle (see scripts/build-wasm.sh).
    #[arg(long, default_value = "dist/wasm-pkg")]
    wasm_dir: PathBuf,
    /// rCTF API base URL for dynamic-score publication (e.g.
    /// `http://127.0.0.1:3000`). Unset → publication stays a dry-run log.
    #[arg(long)]
    rctf_url: Option<String>,
    /// Shared secret for the challenge's dynamic-scoring webhook. Must match
    /// the rCTF challenge's `scoring.source.secret`. Required for live publish.
    #[arg(long, env = "RCTF_SECRET")]
    rctf_secret: Option<String>,
    /// Bearer secret guarding the queue endpoints. UNSET → queue stays open
    /// (local dev); the body's `team_uuid` is trusted and scores publish as
    /// before. SET → join/leave/status require `Authorization: Bearer <secret>`
    /// and only authenticated enrollments are published to rCTF.
    #[arg(long, env = "QUEUE_SECRET")]
    queue_secret: Option<String>,
    /// Comma-separated allowlist of challenge ids permitted to publish scores.
    /// UNSET → any challenge id publishes. SET → non-listed ids are refused.
    #[arg(long, value_delimiter = ',')]
    challenge_allowlist: Option<Vec<String>>,
    /// Optional JSON file for rating/counter durability. UNSET → in-memory only
    /// (local dev unaffected). SET → loaded on startup, rewritten on each
    /// rating change.
    #[arg(long)]
    state_file: Option<PathBuf>,
    /// Directory replays are exported to when a match finishes (a finished
    /// instance's replay is copied to `<bucket>/<challenge>/<match>.mrpl`).
    /// Spectators serve exclusively from this bucket, so a live match is never
    /// viewable. UNSET → no export (replay stays in its per-match temp dir).
    #[arg(long)]
    replay_bucket: Option<PathBuf>,
    /// GCS bucket for API-owned state and finalized replay objects. Uses ADC,
    /// so k8s should grant this pod a narrow GSA through Workload Identity.
    #[arg(long, env = "GCS_BUCKET")]
    gcs_bucket: Option<String>,
    /// Object key for the backend state JSON inside --gcs-bucket.
    #[arg(long, default_value = "state/backend.json")]
    gcs_state_object: String,
    /// Object prefix for finalized replays inside --gcs-bucket.
    #[arg(long, default_value = "replays")]
    gcs_replay_prefix: String,
    /// Hard ceiling on concurrently live matches (instance + active). Lobby
    /// formation refuses above this; the local-port pool also bounds it.
    #[arg(long, default_value_t = 64)]
    max_live_matches: usize,
    /// Hard ceiling on queue size; joins past this are refused.
    #[arg(long, default_value_t = 4096)]
    max_queue: usize,
    /// Per-identity join rate limit: minimum seconds between accepted joins
    /// from the same authenticated identity (or team_uuid in open mode).
    #[arg(long, default_value_t = 2)]
    join_min_interval_secs: u64,
    /// Event start (unix seconds) for the per-team concurrency ramp. UNSET →
    /// process start time. Only consulted when --event-hours > 0.
    #[arg(long)]
    event_start_unix: Option<u64>,
    /// Event duration in hours; the per-team concurrency cap ramps 1 → --max-
    /// concurrent-per-team across it. <= 0 (the default 0) keeps cap pinned at 1
    /// so local dev / e2e behaviour is unchanged unless opted in.
    #[arg(long, default_value_t = 0)]
    event_hours: i64,
    /// Ceiling of the per-team concurrency ramp (live commitments a team may
    /// hold at event end). Floor of the ramp is always 1.
    #[arg(long, default_value_t = 4)]
    max_concurrent_per_team: u32,
}

/// Unix seconds now (saturating; clock-skew safe).
fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn now_millis() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

/// Safety margin before the configured event end: we refuse to start any match
/// that wouldn't finish at least this many seconds before `endDate`, so a match
/// served right at the wire still wraps up (report + GC) inside the window.
const EVENT_END_BUFFER_SECS: u64 = 20;

/// Event window (unix seconds), parsed from the forwarded rCTF instancer config
/// (`startDate` / `endDate`). Either bound may be absent → that side is
/// unconstrained, so an unconfigured challenge behaves exactly as before.
#[derive(Clone, Copy, Default, Debug, PartialEq)]
struct EventWindow {
    start: Option<u64>,
    end: Option<u64>,
}

impl EventWindow {
    /// Pull `startDate` / `endDate` out of a forwarded provider-config blob.
    fn from_config(config: Option<&serde_json::Value>) -> EventWindow {
        let Some(config) = config else {
            return EventWindow::default();
        };
        EventWindow {
            start: config.get("startDate").and_then(parse_event_time),
            end: config.get("endDate").and_then(parse_event_time),
        }
    }

    /// True when no bound is set (nothing to enforce).
    fn is_unset(&self) -> bool {
        self.start.is_none() && self.end.is_none()
    }

    /// Whether a `match_total`-second match begun at `now` may be served: it
    /// must start at/after `startDate` and finish at least
    /// `EVENT_END_BUFFER_SECS` before `endDate`. Absent bounds don't constrain.
    fn can_serve(&self, now: u64, match_total: u64) -> bool {
        if let Some(start) = self.start {
            if now < start {
                return false;
            }
        }
        if let Some(end) = self.end {
            // Projected finish (now + full match length) plus the buffer must
            // not run past the configured end.
            let finish = now
                .saturating_add(match_total)
                .saturating_add(EVENT_END_BUFFER_SECS);
            if finish > end {
                return false;
            }
        }
        true
    }
}

/// Parse an event timestamp from forwarded config into unix seconds. Accepts an
/// RFC3339 string (`"2026-07-01T00:00:00Z"`) or a number. A number large enough
/// to be epoch-milliseconds is scaled down: a ms/s mix-up would otherwise push
/// the end date centuries into the future and silently never close the window.
fn parse_event_time(v: &serde_json::Value) -> Option<u64> {
    match v {
        serde_json::Value::String(s) => {
            let s = s.trim();
            if s.is_empty() {
                return None;
            }
            use time::format_description::well_known::Rfc3339;
            time::OffsetDateTime::parse(s, &Rfc3339)
                .ok()
                .map(|t| t.unix_timestamp().max(0) as u64)
        }
        serde_json::Value::Number(n) => {
            let n = n.as_f64()?;
            if !n.is_finite() || n <= 0.0 {
                return None;
            }
            // >= 1e11 (~year 5138 in seconds) is almost certainly milliseconds.
            let secs = if n >= 1e11 { n / 1000.0 } else { n };
            Some(secs as u64)
        }
        _ => None,
    }
}

impl Args {
    /// Per-team concurrency cap at unix time `now`. Ramps linearly from 1 to
    /// `max_concurrent_per_team` over the forwarded rCTF window, falling back to
    /// `event_hours` for local/manual runs. With neither configured, cap is 1.
    fn cap_at(&self, window: EventWindow, fallback_start: u64, now: u64) -> usize {
        let max = self.max_concurrent_per_team.max(1);
        if max == 1 {
            return 1;
        }
        let from_window = window
            .start
            .zip(window.end)
            .and_then(|(start, end)| end.checked_sub(start).map(|dur| (start, dur)));
        let Some((event_start, total_secs)) = from_window.or_else(|| {
            (self.event_hours > 0).then(|| (fallback_start, self.event_hours as u64 * 3600))
        }) else {
            return 1;
        };
        let elapsed = now.saturating_sub(event_start) as f64;
        let total = total_secs.max(1) as f64;
        // one extra slot per (total / max) elapsed, clamped to [1, max].
        let step = total / max as f64;
        let cap = 1 + (elapsed / step).floor() as u32;
        cap.clamp(1, max) as usize
    }
}

#[derive(Clone)]
struct App {
    store: Arc<Mutex<Store>>,
    args: Arc<Args>,
    instancer: Arc<GameInstancer>,
    http: reqwest::Client,
    gcs: Option<gcs::GcsStore>,
    /// Resolved event start (unix secs): --event-start-unix or process start.
    event_start: u64,
}

impl App {
    /// Current per-team concurrency cap.
    fn cap_now(&self, window: EventWindow) -> usize {
        self.args.cap_at(window, self.event_start, now_unix())
    }
}

fn hex_token(rng: &mut impl Rng, bytes: usize) -> String {
    (0..bytes)
        .map(|_| format!("{:02x}", rng.gen::<u8>()))
        .collect()
}

fn bearer<'a>(headers: &'a HeaderMap) -> &'a str {
    headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("")
}

const REPLAY_UPLOAD_LIMIT_BYTES: usize = 128 * 1024 * 1024;

/// Constant-time-ish equality: HMACs both sides under a random key so length
/// and content don't leak via early-exit, then compares the tags.
fn secret_eq(a: &[u8], b: &[u8]) -> bool {
    use std::sync::OnceLock;
    static KEY: OnceLock<ring::hmac::Key> = OnceLock::new();
    let key = KEY.get_or_init(|| {
        let mut seed = [0u8; 32];
        StdRng::from_entropy().fill_bytes(&mut seed);
        ring::hmac::Key::new(ring::hmac::HMAC_SHA256, &seed)
    });
    let ta = ring::hmac::sign(key, a);
    let tb = ring::hmac::sign(key, b);
    ta.as_ref() == tb.as_ref()
}

/// Bearer check. Returns `true` when `secret` is unset (open dev mode) or the
/// presented token matches.
fn queue_authed(secret: &Option<String>, headers: &HeaderMap) -> bool {
    let Some(secret) = secret else { return true };
    secret_eq(bearer(headers).as_bytes(), secret.as_bytes())
}

/// Opaque, stable team id for public surfaces (leaderboard) — never leak the
/// raw team_uuid.
fn opaque_team_id(team_uuid: &str) -> String {
    let digest = ring::digest::digest(&ring::digest::SHA256, team_uuid.as_bytes());
    digest.as_ref()[..6]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[tokio::main]
async fn main() -> Result<()> {
    install_rustls_crypto_provider();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();
    let args = Arc::new(Args::parse());
    let instancer = match args.instancer.as_str() {
        "local" => GameInstancer::Local(LocalProcess::new(
            args.server_bin.clone(),
            args.game_host.clone(),
            args.base_port,
            args.replay_bucket.clone(),
        )),
        "k8s" => GameInstancer::K8s(K8sInstancer::from_env()?),
        other => anyhow::bail!("unknown --instancer {other:?} (expected `local` or `k8s`)"),
    };
    if args.rctf_url.is_some() && args.rctf_secret.is_none() {
        anyhow::bail!("--rctf-url set but --rctf-secret (or RCTF_SECRET) is missing");
    }
    let gcs = args
        .gcs_bucket
        .as_ref()
        .map(|bucket| gcs::GcsStore::new(bucket.clone()))
        .transpose()
        .context("initialize GCS storage")?;
    let store = if let Some(gcs) = &gcs {
        match gcs.get(&args.gcs_state_object).await.with_context(|| {
            format!(
                "load gs://{}/{}",
                gcs.bucket(),
                args.gcs_state_object.trim_matches('/')
            )
        })? {
            Some(bytes) => Store::load_bytes(
                &bytes,
                args.state_file.clone(),
                &format!(
                    "gs://{}/{}",
                    gcs.bucket(),
                    args.gcs_state_object.trim_matches('/')
                ),
            ),
            None => {
                tracing::info!(
                    bucket = %gcs.bucket(),
                    object = %args.gcs_state_object,
                    "GCS state object not found; starting fresh"
                );
                Store::load(args.state_file.clone())
            }
        }
    } else {
        Store::load(args.state_file.clone())
    };
    let app = App {
        store: Arc::new(Mutex::new(store)),
        instancer: Arc::new(instancer),
        event_start: args.event_start_unix.unwrap_or_else(now_unix),
        args: args.clone(),
        http: reqwest::Client::new(),
        gcs,
    };

    tokio::spawn(matchmaker_loop(app.clone()));

    let router = Router::new()
        .route("/api/queue/join", post(queue_join))
        .route("/api/queue/leave", post(queue_leave))
        .route("/api/queue/status", get(queue_status))
        .route("/api/replay/upload", post(replay_upload))
        .route("/api/report", post(report))
        .route("/api/leaderboard", get(leaderboard))
        .route("/wasm", get(wasm_page))
        .nest_service(
            "/wasm/pkg",
            tower_http::services::ServeDir::new(&args.wasm_dir),
        )
        .layer(DefaultBodyLimit::max(REPLAY_UPLOAD_LIMIT_BYTES))
        .with_state(app);

    let listener = tokio::net::TcpListener::bind(args.bind).await?;
    tracing::info!(bind = %args.bind, "backend ready");
    axum::serve(listener, router).await?;
    Ok(())
}

async fn matchmaker_loop(app: App) {
    let mut interval = tokio::time::interval(Duration::from_secs(app.args.tick_secs));
    let mut rng = StdRng::from_entropy();
    loop {
        interval.tick().await;
        let dead_crds = sweep_dead_matches(&app);
        if !dead_crds.is_empty() {
            persist_state(&app).await;
        }
        for crd_name in dead_crds {
            gc_instance(&app, &crd_name);
        }
        // Stateful idle-decay sweep: pull idle teams' ratings toward the floor
        // and re-publish only the (authenticated) teams whose published score
        // actually changed.
        let (republish, decayed) = decay_sweep(&app);
        if decayed {
            persist_state(&app).await;
        }
        for (challenge_id, scores) in republish {
            let app2 = app.clone();
            tokio::spawn(async move { republish_scores(&app2, &challenge_id, scores).await });
        }
        // Form lobbies AND reserve their teams inside one locked section, so a
        // concurrent queue_join/leave or the next tick can't grab a team that's
        // already committed to a forming match (takeover race). `reserved`
        // teams are dequeued here and only become a real match on create success.
        let lobbies = {
            let mut store = lock(&app.store);
            // Headroom against the live-match ceiling: in-flight reservations
            // each become a match, so count them as live too.
            let live = store.matches.len() + store.reserved.len();
            let headroom = app.args.max_live_matches.saturating_sub(live);
            // Event window (forwarded rCTF instancer config): never form a lobby
            // for a match that couldn't finish before endDate - buffer, or before
            // startDate. The window is a per-challenge property, so take it from
            // the first queued entry carrying one (mirrors start_match's
            // first-present-wins config pick). No configured window → always open.
            let match_total = app.args.grace_secs + app.args.match_secs;
            let serving_open = store
                .queue
                .values()
                .map(|e| EventWindow::from_config(e.config.as_ref()))
                .find(|w| !w.is_unset())
                .is_none_or(|w| w.can_serve(now_unix(), match_total));
            if headroom == 0 || !serving_open {
                Vec::new()
            } else {
                let pool: Vec<matchmaker::Candidate> = store
                    .queue
                    .iter()
                    .map(|(queue_id, e)| matchmaker::Candidate {
                        team: queue_id.clone(),
                        owner: e.team_uuid.clone(),
                        rating: store.rating(&e.team_uuid),
                        waited_secs: e.queued_at.elapsed().as_secs(),
                    })
                    .collect();
                let lobbies = matchmaker::form_lobbies(
                    &mut rng,
                    pool,
                    app.args.min_wait_secs,
                    app.args.burst_size,
                    headroom,
                );
                // Dequeue + reserve atomically under this same lock: moving the
                // QueueEntry into `reserved` closes the takeover race and lets us
                // restore it verbatim if create fails.
                for lobby in &lobbies {
                    for queue_id in lobby {
                        if let Some(entry) = store.queue.remove(queue_id) {
                            store.reserved.insert(queue_id.clone(), entry);
                        }
                    }
                }
                lobbies
            }
        };
        for lobby in lobbies {
            if let Err(e) = start_match(&app, &mut rng, lobby.clone()).await {
                tracing::error!(error = %e, "match start failed — teams re-queued");
                // Create failed: restore each reservation to the queue verbatim
                // so the teams are eligible again next tick.
                let mut store = lock(&app.store);
                for queue_id in lobby {
                    if let Some(entry) = store.reserved.remove(&queue_id) {
                        store.queue.entry(queue_id).or_insert(entry);
                    }
                }
            }
        }
    }
}

/// Stateful idle-decay sweep. Decays every idle team's stored rating toward the
/// floor (respecting grace + the no-pull-up invariant), and returns the score
/// re-publications grouped by challenge id plus whether persisted state changed.
/// Only teams whose PUBLISHED score actually changed and that were previously
/// published (i.e. authenticated) are re-published. Unchanged teams are skipped.
fn decay_sweep(app: &App) -> (Vec<(String, Vec<(String, u32)>)>, bool) {
    let now = now_unix();
    let mut store = lock(&app.store);
    // Teams that have ever published are exactly the authenticated, allow-listed
    // ones; remember each one's challenge id so the re-publish addresses the
    // right webhook with the UNCHANGED body/HMAC contract.
    let mut publishable: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    for audit in store.published.values() {
        for uid in &audit.user_ids {
            publishable
                .entry(uid.clone())
                .or_insert_with(|| audit.challenge_id.clone());
        }
    }

    let mut changed: std::collections::HashMap<String, Vec<(String, u32)>> =
        std::collections::HashMap::new();
    let mut any = false;
    let teams: Vec<String> = store.ratings.keys().cloned().collect();
    for team in teams {
        let rec = match store.ratings.get(&team) {
            Some(r) => r.clone(),
            None => continue,
        };
        // An in-match or about-to-play team isn't idle; skip the active set.
        if store.live_count(&team) > 0 {
            continue;
        }
        let (new_rating, new_last_decay) =
            elo::apply_idle_decay(rec.rating, rec.last_played_unix, rec.last_decay_unix, now);
        if (new_rating - rec.rating).abs() < 1e-9 {
            // No rating change (still in grace, or already at/below floor). Keep
            // the decay clock current so we never re-charge already-elapsed time.
            if new_last_decay != rec.last_decay_unix {
                store.record_mut(&team).last_decay_unix = new_last_decay;
                any = true;
            }
            continue;
        }
        let before_score = elo::published_score(rec.rating, rec.games_played);
        let after_score = elo::published_score(new_rating, rec.games_played);
        let r = store.record_mut(&team);
        r.rating = new_rating;
        r.last_decay_unix = new_last_decay;
        any = true;
        if after_score != before_score {
            if let Some(challenge_id) = publishable.get(&team) {
                changed
                    .entry(challenge_id.clone())
                    .or_default()
                    .push((team.clone(), after_score));
            }
        }
    }
    (changed.into_iter().collect(), any)
}

/// Fire-and-forget GC of an instance's backing pod (no-op on the empty
/// local-process CRD name).
fn gc_instance(app: &App, crd_name: &str) {
    if !crd_name.is_empty() {
        let inst = app.instancer.clone();
        let crd_name = crd_name.to_string();
        tokio::spawn(async move { inst.destroy(&crd_name).await });
    }
}

/// A match that never reported by deadline + slack is voided: no Elo. Returns
/// the CRD names to GC (empty strings on the local-process path).
fn sweep_dead_matches(app: &App) -> Vec<String> {
    let mut store = lock(&app.store);
    let now = Instant::now();
    let dead: Vec<String> = store
        .matches
        .values()
        .filter(|m| now > m.deadline + Duration::from_secs(60))
        .map(|m| m.match_id.clone())
        .collect();
    let mut crd_names = Vec::with_capacity(dead.len());
    for id in dead {
        if let Some(crd_name) = store.void_match(&id) {
            crd_names.push(crd_name);
            tracing::warn!(match_id = %id, "match voided (no report by deadline)");
        }
    }
    crd_names
}

async fn persist_state(app: &App) {
    if app.gcs.is_none() {
        let store = lock(&app.store);
        store.persist();
        return;
    }

    let json = {
        let store = lock(&app.store);
        store.persist();
        match store.persisted_json() {
            Ok(json) => json,
            Err(e) => {
                tracing::warn!(error = %e, "state serialize failed");
                return;
            }
        }
    };

    let Some(gcs) = &app.gcs else { return };
    if let Err(e) = gcs.put(&app.args.gcs_state_object, json).await {
        tracing::warn!(
            error = %e,
            bucket = %gcs.bucket(),
            object = %app.args.gcs_state_object,
            "GCS state persist failed"
        );
    }
}

async fn start_match(app: &App, rng: &mut StdRng, lobby: Vec<String>) -> Result<()> {
    let match_id = format!("m-{}", hex_token(rng, 6));
    let report_token = hex_token(rng, 24);
    // 16 random bytes = 32 hex chars; the udp-router demux key (matches the
    // client's parse_instance_id). One per server.
    let instance_id = hex_token(rng, 16);
    // Fresh per-match deterministic seed; passed to the server as --seed and
    // recorded into the replay header so the match reproduces bit-exactly.
    let seed: u64 = rng.gen();
    // Lobby queue entries were moved into `reserved` by the loop; read them
    // from there.
    let (players, authed, config, challenge_id) = {
        let store = lock(&app.store);
        let players: Vec<(String, String, String)> = lobby
            .iter()
            .filter_map(|qid| {
                let e = store.reserved.get(qid)?;
                Some((e.team_uuid.clone(), e.name.clone(), hex_token(rng, 16)))
            })
            .collect();
        let authed: std::collections::HashMap<String, bool> = lobby
            .iter()
            .filter_map(|qid| {
                let e = store.reserved.get(qid)?;
                Some((e.team_uuid.clone(), e.authenticated))
            })
            .collect();
        // Teams in a lobby carry the same per-challenge config/id (rCTF forwards
        // them); first present wins.
        let config = lobby
            .iter()
            .find_map(|qid| store.reserved.get(qid).and_then(|e| e.config.clone()));
        let challenge_id = lobby
            .iter()
            .find_map(|qid| store.reserved.get(qid).and_then(|e| e.challenge_id.clone()))
            .unwrap_or_else(|| "minions".into());
        (players, authed, config, challenge_id)
    };
    let public_base = app.args.public_base.trim_end_matches('/');
    let req = InstanceRequest {
        match_id: match_id.clone(),
        players: players.clone(),
        report_url: format!("{public_base}/api/report"),
        replay_upload_url: app
            .gcs
            .as_ref()
            .map(|_| format!("{public_base}/api/replay/upload")),
        report_token: report_token.clone(),
        grace_secs: app.args.grace_secs,
        match_secs: app.args.match_secs,
        arena: app.args.arena.clone(),
        instance_id: instance_id.clone(),
        challenge_id: challenge_id.clone(),
        seed,
        config,
    };
    // Teams are already reserved (dequeued under the form_lobbies lock); a
    // failed create restores them to the queue in the caller.
    let ep = app.instancer.create(&req).await?;
    // Only routed (k8s) instances have a ChallengeInstance object to GC.
    let crd_name = if ep.routed {
        challenge_instance_name(&challenge_id, &match_id)
    } else {
        String::new()
    };

    let match_players: Vec<MatchPlayer> = players
        .iter()
        .map(|(team, _name, key)| MatchPlayer {
            team_uuid: team.clone(),
            key: key.clone(),
            connection: build_connection(&ep, &instance_id, key, &app.args.public_base),
            authenticated: authed.get(team).copied().unwrap_or(false),
        })
        .collect();

    // rCTF connection-info publication — API not live yet, so log the
    // exact payload we'll POST.
    for p in &match_players {
        tracing::info!(
            target: "rctf_dryrun",
            payload = %json!({"team": p.team_uuid, "connection": p.connection}),
            "publish connection info"
        );
    }

    let mut store = lock(&app.store);
    // Reservation fulfilled: convert the reserved teams into a live match.
    for team in &lobby {
        store.reserved.remove(team);
    }
    let total = app.args.grace_secs + app.args.match_secs;
    store.matches.insert(
        match_id.clone(),
        MatchState {
            match_id: match_id.clone(),
            report_token,
            report_used: false,
            players: match_players,
            started_at: Instant::now(),
            deadline: Instant::now() + Duration::from_secs(total),
            instance_id,
            crd_name,
            challenge_id: challenge_id.clone(),
        },
    );
    tracing::info!(match_id = %match_id, teams = lobby.len(), port = ep.port, "match started");
    Ok(())
}

#[derive(Deserialize)]
struct TeamReq {
    team_uuid: String,
}

/// createInstance payload from rCTF. `timeoutMilliseconds` is currently ignored
/// (expiry derives from grace + match length).
#[derive(Deserialize)]
struct JoinReq {
    team_uuid: String,
    #[serde(default)]
    name: String,
    /// Provider config (`{ "pods": [...], "udprouterAddr": "host:port" }`) for
    /// this team's lobby.
    #[serde(default)]
    config: Option<serde_json::Value>,
    /// challengeIntegrationId; names the ChallengeInstance object.
    #[serde(default, alias = "challengeIntegrationId")]
    challenge_integration_id: Option<String>,
}

async fn queue_join(
    State(app): State<App>,
    headers: HeaderMap,
    Json(req): Json<JoinReq>,
) -> (StatusCode, Json<serde_json::Value>) {
    let authed = queue_authed(&app.args.queue_secret, &headers);
    if !authed {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid queue secret"})),
        );
    }
    let window = EventWindow::from_config(req.config.as_ref());
    let match_total = app.args.grace_secs + app.args.match_secs;
    let cap = app.cap_now(window);
    let mut store = lock(&app.store);
    let queued = store.queued_count(&req.team_uuid);
    let live = store.live_count(&req.team_uuid);
    // Event window (from the forwarded rCTF instancer config): once we're past
    // the point a fresh match could finish before endDate (minus the buffer), or
    // still before startDate, refuse NEW enrollments. A team already queued or
    // holding a live commitment is left alone, so its in-flight match still
    // surfaces its connection through the createInstance → status path.
    let committed = queued + live > 0;
    if !committed && !window.can_serve(now_unix(), match_total) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "event is not accepting new instances"})),
        );
    }
    // Concurrency gate: one rCTF createInstance call fills all missing pending
    // commitments up to cap(t), because rCTF only exposes one join action.
    if queued + live >= cap {
        return (
            StatusCode::CONFLICT,
            Json(json!({"error": "concurrency cap reached"})),
        );
    }
    // Per-identity join throttle (anti-flood). Only count genuinely new joins.
    if queued == 0
        && live == 0
        && !store.allow_join(
            &req.team_uuid,
            Duration::from_secs(app.args.join_min_interval_secs),
        )
    {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(json!({"error": "join rate limit"})),
        );
    }
    let want = cap - queued - live;
    let room = app.args.max_queue.saturating_sub(store.queue.len());
    if room == 0 {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error": "queue full"})),
        );
    }
    let to_insert = want.min(room);
    let display = if req.name.is_empty() {
        req.team_uuid.clone()
    } else {
        req.name.clone()
    };
    let base = store.queue.len() + store.reserved.len() + store.matches.len();
    for idx in 0..to_insert {
        let mut n = base + idx;
        let queue_id = loop {
            let candidate = format!("{}#{n}", req.team_uuid);
            if !store.queue.contains_key(&candidate) && !store.reserved.contains_key(&candidate) {
                break candidate;
            }
            n += 1;
        };
        store.queue.insert(
            queue_id,
            state::QueueEntry {
                team_uuid: req.team_uuid.clone(),
                queued_at: Instant::now(),
                name: display.clone(),
                config: req.config.clone(),
                challenge_id: req.challenge_integration_id.clone(),
                authenticated: authed,
            },
        );
    }
    tracing::info!(team = %req.team_uuid, added = to_insert, queued = store.queue.len(), "queue join");
    (StatusCode::OK, Json(json!({"status": "queued"})))
}

async fn queue_leave(
    State(app): State<App>,
    headers: HeaderMap,
    Json(req): Json<TeamReq>,
) -> (StatusCode, Json<serde_json::Value>) {
    if !queue_authed(&app.args.queue_secret, &headers) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid queue secret"})),
        );
    }
    let mut store = lock(&app.store);
    // A reserved/active commitment can't be abandoned (it would strand a
    // forming/running instance). With concurrency a team can be both queued and
    // committed; leaving only removes the pending queue entry.
    if store.live_count(&req.team_uuid) > 0 && store.queued_count(&req.team_uuid) == 0 {
        return (
            StatusCode::CONFLICT,
            Json(json!({"error": "committed to a match; cannot leave"})),
        );
    }
    store.queue.retain(|_, e| e.team_uuid != req.team_uuid);
    (StatusCode::OK, Json(json!({"status": "left"})))
}

#[derive(Deserialize)]
struct StatusQuery {
    team_uuid: String,
}

async fn queue_status(
    State(app): State<App>,
    headers: HeaderMap,
    Query(q): Query<StatusQuery>,
) -> (StatusCode, Json<serde_json::Value>) {
    if !queue_authed(&app.args.queue_secret, &headers) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid queue secret"})),
        );
    }
    let store = lock(&app.store);
    (
        StatusCode::OK,
        Json(queue_status_payload(&store, &q.team_uuid)),
    )
}

fn queue_status_payload(store: &Store, team_uuid: &str) -> serde_json::Value {
    // A team may hold several concurrent instances (per-team concurrency ramp).
    // Surface EVERY one: flatten each match's connection lines into a single
    // array so the rCTF provider renders one connectable endpoint per instance
    // (× transport variant). With cap=1 this is exactly the old single-match
    // behaviour. `ends_in_secs` is the longest-lived; `match_id` the soonest to
    // end (what a single-instance client would treat as "current").
    let live = store.active_matches_for(team_uuid);
    if !live.is_empty() {
        let connection: Vec<String> = live
            .iter()
            .flat_map(|(_, p)| p.connection.iter().cloned())
            .collect();
        let now = Instant::now();
        let ends_in_secs = live
            .iter()
            .map(|(m, _)| m.deadline.saturating_duration_since(now).as_secs())
            .max()
            .unwrap_or(0);
        let match_id = live
            .iter()
            .min_by_key(|(m, _)| m.deadline)
            .map(|(m, _)| m.match_id.clone())
            .unwrap_or_default();
        return json!({
            "state": "matched",
            "match_id": match_id,
            "connection": connection,
            "ends_in_secs": ends_in_secs,
        });
    }
    if let Some(e) = store
        .queue
        .values()
        .chain(store.reserved.values())
        .filter(|e| e.team_uuid == team_uuid)
        .min_by_key(|e| e.queued_at)
    {
        return json!({
            "state": "queued",
            "waited_secs": e.queued_at.elapsed().as_secs(),
            "queue_size": store.queue.len(),
        });
    }
    json!({"state": "idle"})
}

// Mirror of the game server's report shape.
#[derive(Deserialize, Debug)]
struct MatchReport {
    match_id: String,
    duration_ms: u64,
    players: Vec<ReportPlayer>,
}

#[derive(Deserialize, Debug)]
struct ReportPlayer {
    team_uuid: String,
    rank: u32,
    kills: u32,
    confirmed_kills: u32,
    deaths: u32,
    connected: bool,
    takeovers: u32,
}

#[derive(Deserialize)]
struct ReplayUploadQuery {
    match_id: String,
}

async fn replay_upload(
    State(app): State<App>,
    headers: HeaderMap,
    Query(query): Query<ReplayUploadQuery>,
    body: Bytes,
) -> (StatusCode, Json<serde_json::Value>) {
    if body.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "empty replay"})),
        );
    }

    let token = bearer(&headers);
    let challenge_id = {
        let store = lock(&app.store);
        let Some(match_id) = store.lookup_match_by_token(token) else {
            tracing::warn!("replay upload with unknown or already-used token");
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "invalid or used token"})),
            );
        };
        if match_id != query.match_id {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "match id mismatch"})),
            );
        }
        store.matches[&match_id].challenge_id.clone()
    };

    let Some(gcs) = app.gcs.clone() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error": "replay storage not configured"})),
        );
    };
    let object = replay_object(&app.args.gcs_replay_prefix, &challenge_id, &query.match_id);
    let bytes = body.len();
    match gcs.put(&object, body.to_vec()).await {
        Ok(()) => {
            tracing::info!(
                match_id = %query.match_id,
                challenge = %challenge_id,
                bytes,
                object = %object,
                "replay stored"
            );
            (
                StatusCode::OK,
                Json(json!({"status": "stored", "object": object})),
            )
        }
        Err(e) => {
            tracing::warn!(error = %e, match_id = %query.match_id, "replay upload persist failed");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "replay persist failed"})),
            )
        }
    }
}

fn replay_object(prefix: &str, challenge_id: &str, match_id: &str) -> String {
    let replay = format!(
        "{}/{}.mrpl",
        object_segment(challenge_id),
        object_segment(match_id)
    );
    let prefix = prefix.trim_matches('/');
    if prefix.is_empty() {
        replay
    } else {
        format!("{prefix}/{replay}")
    }
}

fn object_segment(raw: &str) -> String {
    let segment: String = raw
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') {
                c
            } else {
                '-'
            }
        })
        .collect();
    if segment.is_empty() {
        "unknown".to_string()
    } else {
        segment
    }
}

enum ReportOutcome {
    Voided {
        match_id: String,
        crd_name: String,
        connected: usize,
    },
    Accepted {
        match_id: String,
        crd_name: String,
        challenge_id: String,
        scores: Vec<(String, u32)>,
    },
}

async fn report(
    State(app): State<App>,
    headers: HeaderMap,
    Json(report): Json<MatchReport>,
) -> (StatusCode, Json<serde_json::Value>) {
    let outcome = match ingest_report(&app, &headers, &report) {
        Ok(outcome) => outcome,
        Err(response) => return response,
    };

    persist_state(&app).await;
    match outcome {
        ReportOutcome::Voided {
            match_id,
            crd_name,
            connected,
        } => {
            gc_instance(&app, &crd_name);
            tracing::warn!(match_id = %match_id, connected, "match voided (too few connected)");
            (StatusCode::OK, Json(json!({"status": "voided"})))
        }
        ReportOutcome::Accepted {
            match_id,
            crd_name,
            challenge_id,
            scores,
        } => {
            gc_instance(&app, &crd_name);
            {
                let app = app.clone();
                let match_id = match_id.clone();
                tokio::spawn(async move {
                    publish_scores(&app, &match_id, &challenge_id, scores).await
                });
            }
            tracing::info!(match_id = %match_id, "match finished");
            (StatusCode::OK, Json(json!({"status": "accepted"})))
        }
    }
}

fn ingest_report(
    app: &App,
    headers: &HeaderMap,
    report: &MatchReport,
) -> std::result::Result<ReportOutcome, (StatusCode, Json<serde_json::Value>)> {
    let token = bearer(headers);
    let mut store = lock(&app.store);
    // Non-mutating lookup for all validation; the token is only consumed on the
    // success path so a rejected report can be corrected and retried.
    let Some(match_id) = store.lookup_match_by_token(token) else {
        tracing::warn!("report with unknown or already-used token");
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid or used token"})),
        ));
    };
    if match_id != report.match_id {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "match id mismatch"})),
        ));
    }
    let expected: std::collections::HashSet<String> = store.matches[&match_id]
        .players
        .iter()
        .map(|p| p.team_uuid.clone())
        .collect();
    // Exact set equality: collect reported ids into a set (rejecting any
    // duplicate team_uuid) and require it to equal the expected roster.
    let reported: std::collections::HashSet<String> =
        report.players.iter().map(|p| p.team_uuid.clone()).collect();
    if reported.len() != report.players.len() || reported != expected {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "player set mismatch"})),
        ));
    }

    let mut ranked = report
        .players
        .iter()
        .filter(|p| p.confirmed_kills > 0 || p.kills > 0 || p.deaths > 0)
        .collect::<Vec<_>>();
    ranked.sort_by(|a, b| {
        b.confirmed_kills
            .cmp(&a.confirmed_kills)
            .then_with(|| b.kills.cmp(&a.kills))
            .then_with(|| a.deaths.cmp(&b.deaths))
            .then_with(|| a.team_uuid.cmp(&b.team_uuid))
    });
    let score_differentials = ranked
        .iter()
        .map(|p| (p.confirmed_kills, p.kills, p.deaths))
        .collect::<std::collections::HashSet<_>>()
        .len();
    if ranked.len() < 2 || score_differentials < 2 {
        // Consume the token (a valid one-shot report arrived) and void: no Elo,
        // no publish, no games_played credit. Mirrors the dead-match void path.
        store.consume_report_token(&match_id);
        let crd_name = store.void_match(&match_id).unwrap_or_default();
        return Ok(ReportOutcome::Voided {
            match_id,
            crd_name,
            connected: report.players.iter().filter(|p| p.connected).count(),
        });
    }
    let mut ranks = std::collections::HashMap::with_capacity(ranked.len());
    let mut rank = 0u32;
    let mut prev = None;
    for p in &ranked {
        let key = (p.confirmed_kills, p.kills, p.deaths);
        if prev != Some(key) {
            rank += 1;
            prev = Some(key);
        }
        ranks.insert(p.team_uuid.clone(), rank);
    }

    // All validation passed — consume the token now (right before ingest).
    if !store.consume_report_token(&match_id) {
        // Lost a race to a concurrent report; treat as already used.
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid or used token"})),
        ));
    }

    // Only enrollments that were authenticated at queue time may be published
    // to rCTF — blocks userId injection via a spoofed report body. Elo still
    // updates internally for all teams.
    let authed_teams: std::collections::HashSet<String> = store.matches[&match_id]
        .players
        .iter()
        .filter(|p| p.authenticated)
        .map(|p| p.team_uuid.clone())
        .collect();
    let inputs: Vec<(String, f64, u32)> = ranked
        .iter()
        .map(|p| {
            let rank = ranks[&p.team_uuid];
            (p.team_uuid.clone(), store.rating(&p.team_uuid), rank)
        })
        .collect();

    // Collusion throttle: snapshot each unordered pair's window stats now, then
    // derive a per-pair K before mutating store.
    let mut pair_k: std::collections::HashMap<(String, String), f64> =
        std::collections::HashMap::new();
    for (i, (a, _, _)) in inputs.iter().enumerate() {
        for (b, _, _) in inputs.iter().skip(i + 1) {
            let (meet, net) = store.pair_stats(a, b);
            let k = elo::throttled_k(meet, net);
            pair_k.insert(Store::pair_key(a, b), k);
        }
    }
    let updated = elo::update_with(&inputs, |a, b| {
        pair_k
            .get(&Store::pair_key(a, b))
            .copied()
            .unwrap_or(elo::K)
    });

    let now = now_unix();
    // Commit the per-pair transfer into the rolling window (drives future K).
    for (i, (a, ra, _)) in inputs.iter().enumerate() {
        for (b, rb, _) in inputs.iter().skip(i + 1) {
            let na = updated
                .iter()
                .find(|(t, _)| t == a)
                .map(|(_, r)| *r)
                .unwrap_or(*ra);
            let nb = updated
                .iter()
                .find(|(t, _)| t == b)
                .map(|(_, r)| *r)
                .unwrap_or(*rb);
            store.record_pair(a, b, ((na - ra) - (nb - rb)).abs() / 2.0);
        }
    }

    let mut scores: Vec<(String, u32)> = Vec::with_capacity(updated.len());
    for (team, new_rating) in &updated {
        let old = store.rating(team);
        let rec = store.record_mut(team);
        rec.games_played = rec.games_played.saturating_add(1);
        // Anti-sandbag: a calibrating team's rating may not fall below the
        // calibration floor.
        rec.rating = elo::clamp_calibration(*new_rating, rec.games_played);
        rec.last_played_unix = now;
        rec.last_decay_unix = now;
        let games = rec.games_played;
        let stored = rec.rating;
        let score = elo::published_score(stored, games);
        if authed_teams.contains(team) {
            scores.push((team.clone(), score));
        }
        tracing::info!(
            %team,
            old = format!("{old:.1}"),
            new = format!("{stored:.1}"),
            games,
            score,
            "rating update"
        );
    }
    for p in &report.players {
        tracing::info!(
            team = %p.team_uuid, rank = p.rank, confirmed_kills = p.confirmed_kills,
            kills = p.kills, deaths = p.deaths,
            connected = p.connected, takeovers = p.takeovers,
            duration_ms = report.duration_ms, "match result row"
        );
    }
    let (crd_name, challenge_id) = store
        .matches
        .get(&match_id)
        .map(|m| (m.crd_name.clone(), m.challenge_id.clone()))
        .unwrap_or_default();
    store.matches.remove(&match_id);
    store.finished += 1;
    Ok(ReportOutcome::Accepted {
        match_id,
        crd_name,
        challenge_id,
        scores,
    })
}

/// Publish a match's public scores to rCTF's dynamic-scoring webhook. The
/// request is HMAC-signed exactly as `dynamic-challenge-auth` expects:
/// `X-RCTF-Signature: sha256=hex(HMAC_SHA256(secret, "{ts_ms}.{body}"))`,
/// `X-RCTF-Timestamp: {ts_ms}`. A missing `--rctf-url` keeps it a dry-run log.
async fn publish_scores(app: &App, match_id: &str, challenge_id: &str, scores: Vec<(String, u32)>) {
    // Allowlist binding happens HERE (backend-side), not in the signature: a
    // non-allowlisted challenge id never publishes. Signed bytes are untouched.
    if !challenge_allowed(app, challenge_id) {
        return;
    }
    if scores.is_empty() {
        tracing::info!(challenge = %challenge_id, "no authenticated teams to publish");
        return;
    }
    // Idempotency: a match publishes at most once. A retried/duplicated report
    // for the same match_id is a no-op. Backend-side state only — not signed.
    {
        let mut store = lock(&app.store);
        if store.published.contains_key(match_id) {
            tracing::warn!(%match_id, challenge = %challenge_id, "publish skipped: already published (idempotent)");
            return;
        }
        // Reserve the key now so concurrent retries can't both pass the guard.
        store.published.insert(
            match_id.to_string(),
            PublishAudit {
                challenge_id: challenge_id.to_string(),
                user_ids: scores.iter().map(|(t, _)| t.clone()).collect(),
                points: scores.iter().map(|(_, p)| *p).collect(),
                status: 0,
            },
        );
    }
    let status = submit_scores(app, match_id, challenge_id, &scores).await;
    if let Some(code) = status {
        let mut store = lock(&app.store);
        if let Some(rec) = store.published.get_mut(match_id) {
            rec.status = code;
        }
    }
}

/// Decay re-publication: same allowlist + signing + body as `publish_scores`,
/// but NOT idempotency-guarded (re-publishing a decayed score is the point) and
/// without polluting the per-match `published` audit/idempotency map.
async fn republish_scores(app: &App, challenge_id: &str, scores: Vec<(String, u32)>) {
    if !challenge_allowed(app, challenge_id) || scores.is_empty() {
        return;
    }
    submit_scores(app, "decay-sweep", challenge_id, &scores).await;
}

/// True when `challenge_id` may publish (allowlist unset or contains it).
fn challenge_allowed(app: &App, challenge_id: &str) -> bool {
    match &app.args.challenge_allowlist {
        Some(allow) if !allow.iter().any(|c| c == challenge_id) => {
            tracing::warn!(challenge = %challenge_id, "publish refused: challenge not in allowlist");
            false
        }
        _ => true,
    }
}

/// Sign + POST scores to the rCTF dynamic-scoring webhook with the UNCHANGED
/// HMAC material and body. Returns the HTTP status (None on dry-run/transport
/// failure). `tag` is only used for logging.
async fn submit_scores(
    app: &App,
    tag_id: &str,
    challenge_id: &str,
    scores: &[(String, u32)],
) -> Option<u16> {
    let body = json!({
        "scores": scores
            .iter()
            .map(|(team, score)| json!({"userId": team, "points": *score}))
            .collect::<Vec<_>>(),
    })
    .to_string();

    let (Some(base), Some(secret)) = (&app.args.rctf_url, &app.args.rctf_secret) else {
        tracing::info!(target: "rctf_dryrun", id = %tag_id, challenge = %challenge_id, payload = %body, "submit-dynamic-scores (dry-run)");
        return None;
    };

    let ts = now_millis();
    let key = ring::hmac::Key::new(ring::hmac::HMAC_SHA256, secret.as_bytes());
    let tag = ring::hmac::sign(&key, format!("{ts}.{body}").as_bytes());
    let sig = format!(
        "sha256={}",
        tag.as_ref()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    );

    let url = format!(
        "{}/api/v2/challs/{}/scores",
        base.trim_end_matches('/'),
        challenge_id
    );
    match app
        .http
        .post(&url)
        .header("content-type", "application/json")
        .header("X-RCTF-Timestamp", ts.to_string())
        .header("X-RCTF-Signature", sig)
        .body(body)
        .send()
        .await
    {
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            if status.is_success() {
                tracing::info!(id = %tag_id, challenge = %challenge_id, teams = scores.len(), %status, "scores published to rCTF (audited)");
            } else {
                tracing::error!(id = %tag_id, challenge = %challenge_id, %status, body = %text, "rCTF rejected score publish");
            }
            Some(status.as_u16())
        }
        Err(e) => {
            tracing::error!(id = %tag_id, challenge = %challenge_id, error = %e, "rCTF score publish failed");
            None
        }
    }
}

fn install_rustls_crypto_provider() {
    let _ = rustls::crypto::ring::default_provider().install_default();
}

/// The browser client. The page itself is transport-dumb: the wasm module
/// reads host/port/certhash from the query string and the access key from the
/// URL fragment (`#key=...`), then dials WebTransport with the pinned cert hash.
///
/// `Referrer-Policy: no-referrer` so the key (and any URL params) never leak via
/// the Referer header on outbound requests the page makes.
async fn wasm_page() -> axum::response::Response {
    use axum::response::IntoResponse;
    (
        [
            (axum::http::header::REFERRER_POLICY, "no-referrer"),
            (axum::http::header::CONTENT_TYPE, "text/html; charset=utf-8"),
        ],
        WASM_PAGE_HTML,
    )
        .into_response()
}

const WASM_PAGE_HTML: &str = r#"<!doctype html>
<html><head><meta charset="utf-8"><title>minions-in-16k</title>
<style>
html,body{margin:0;height:100%;background:#0b0e2a;display:grid;place-items:center;font-family:monospace}
canvas{image-rendering:pixelated;outline:none}
#load{color:#f2b441;text-align:center}
#bar{width:240px;height:10px;border:2px solid #f2b441;margin:12px auto}
#fill{height:100%;width:0;background:#f2b441}
#howto{position:fixed;left:18px;bottom:18px;color:#cfd6ff;background:rgba(6,8,28,.92);
  border:2px solid #f2b441;padding:14px 18px;font-size:13px;line-height:1.55;max-width:330px}
#howto h2{margin:0 0 6px;color:#f2b441;font-size:14px}
#howto b{color:#fff}
#howto .dim{color:#7b84b8;font-size:11px;margin-top:8px}
</style>
</head><body>
<div id="load"><div>FETCHING MINIONS</div><div id="bar"><div id="fill"></div></div><div id="pct">0%</div></div>
<div id="howto">
<h2>HOW TO PLAY</h2>
<b>Click a minion</b> to pick it and deploy.<br>
<b>WASD</b> move &nbsp;<b>mouse</b> aim &nbsp;<b>LMB</b> fire<br>
<b>Space</b> jump &nbsp;<b>Shift</b>/<b>Ctrl</b> crouch<br>
<b>Tab</b> scoreboard &nbsp;<b>C</b>/<b>V</b> camera<br>
<b>Esc</b> menu + volume (frees the mouse)<br>
Grab weapons &amp; powerups off the pads — the good
stuff is up high. Stay out of the kitchen when it glows.
<div class="dim">First click: fullscreen + mouse lock + sound.
This panel disappears when you click.<br>
No server in the URL? You're in offline demo mode vs bots.</div>
</div>
<script>
// Browsers suspend AudioContexts created before a user gesture, and the
// engine builds its context at startup. Capture every context and resume
// the suspended ones on the first gesture.
(() => {
  const ctxs = [];
  for (const name of ["AudioContext", "webkitAudioContext"]) {
    const Orig = window[name];
    if (!Orig) continue;
    window[name] = function (...args) {
      const ctx = new Orig(...args);
      ctxs.push(ctx);
      return ctx;
    };
    window[name].prototype = Orig.prototype;
  }
  const resume = () => {
    for (const c of ctxs) if (c.state === "suspended") c.resume();
  };
  for (const t of ["pointerdown", "keydown", "touchstart"])
    document.addEventListener(t, resume, { capture: true });

  // Fullscreen + Keyboard Lock on the first click: holding Ctrl+W
  // (crouch-walk) closes the tab otherwise. Keyboard Lock only works in
  // fullscreen and only on Chromium — elsewhere Shift-crouch is the
  // workaround.
  let fsArmed = false;
  document.addEventListener("pointerdown", async () => {
    const howto = document.getElementById("howto");
    if (howto) howto.remove();
    if (fsArmed || document.fullscreenElement) return;
    fsArmed = true;
    try {
      await document.documentElement.requestFullscreen();
      if (navigator.keyboard && navigator.keyboard.lock) {
        await navigator.keyboard.lock([
          "KeyW", "KeyA", "KeyS", "KeyD", "Tab", "KeyC", "KeyV",
          "Space", "ControlLeft", "ControlRight",
        ]);
      }
    } catch (e) { /* user denied or unsupported — Shift crouches */ }
  }, { capture: true });
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) {
      fsArmed = false;
      if (navigator.keyboard && navigator.keyboard.unlock)
        navigator.keyboard.unlock();
    }
  });
})();
</script>
<script type="module">
import init from "/wasm/pkg/client.js";
const fill = document.getElementById("fill"), pct = document.getElementById("pct");
async function fetchWasm() {
  const resp = await fetch("/wasm/pkg/client_bg.wasm");
  const total = +resp.headers.get("Content-Length") || 0;
  const reader = resp.body.getReader();
  let got = 0; const chunks = [];
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    chunks.push(value); got += value.length;
    if (total) { const p = Math.round(got/total*100); fill.style.width = p + "%"; pct.textContent = p + "%"; }
  }
  const buf = new Uint8Array(got); let o = 0;
  for (const c of chunks) { buf.set(c, o); o += c.length; }
  return buf.buffer;
}
fetchWasm()
  .then(bytes => init({ module_or_path: bytes }))
  .then(() => document.getElementById("load").remove())
  .catch(e => { document.getElementById("load").innerHTML = "<pre>" + e + "</pre>"; });
</script>
</body></html>"#;

#[derive(Serialize)]
struct LeaderboardRow {
    /// Opaque short hash of the team_uuid — never expose the raw id publicly.
    id: String,
    public_score: u32,
    /// Hidden sort key; not serialized.
    #[serde(skip)]
    rating: f64,
}

async fn leaderboard(State(app): State<App>) -> Json<serde_json::Value> {
    let store = lock(&app.store);
    let teams: Vec<String> = store.ratings.keys().cloned().collect();
    let mut rows: Vec<LeaderboardRow> = teams
        .iter()
        .map(|t| LeaderboardRow {
            id: opaque_team_id(t),
            public_score: store.published_score(t),
            rating: store.rating(t),
        })
        .collect();
    rows.sort_by(|a, b| b.rating.partial_cmp(&a.rating).unwrap());
    Json(json!({
        "finished_matches": store.finished,
        "voided_matches": store.voided,
        "rows": rows,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args_from(extra: &[&str]) -> Args {
        let mut argv = vec!["backend"];
        argv.extend_from_slice(extra);
        Args::parse_from(argv)
    }

    #[test]
    fn join_req_accepts_rctf_challenge_integration_ids() {
        let snake: JoinReq = serde_json::from_value(json!({
            "team_uuid": "team-1",
            "challenge_integration_id": "minions-rctf-chall"
        }))
        .unwrap();
        let camel: JoinReq = serde_json::from_value(json!({
            "team_uuid": "team-1",
            "challengeIntegrationId": "minions-rctf-chall"
        }))
        .unwrap();

        assert_eq!(
            snake.challenge_integration_id.as_deref(),
            Some("minions-rctf-chall")
        );
        assert_eq!(
            camel.challenge_integration_id.as_deref(),
            Some("minions-rctf-chall")
        );
    }

    #[test]
    fn queue_status_reports_reserved_team_as_queued() {
        let mut store = Store::default();
        store.reserved.insert(
            "team-1".into(),
            state::QueueEntry {
                team_uuid: "team-1".into(),
                queued_at: Instant::now() - Duration::from_secs(5),
                name: "Team 1".into(),
                config: None,
                challenge_id: Some("game_minions-in-16k".into()),
                authenticated: true,
            },
        );

        let payload = queue_status_payload(&store, "team-1");
        assert_eq!(
            payload.get("state").and_then(|v| v.as_str()),
            Some("queued")
        );
        assert_eq!(payload.get("queue_size").and_then(|v| v.as_u64()), Some(0));
        assert!(
            payload
                .get("waited_secs")
                .and_then(|v| v.as_u64())
                .unwrap_or(0)
                >= 5
        );
    }

    #[test]
    fn queue_status_surfaces_all_concurrent_instances() {
        // A team holding cap>1 concurrent matches must have EVERY instance
        // surfaced in the status connection array (regression: the old
        // active_match_for returned only the first, so extra instances were
        // unreachable by the player/rCTF frontend).
        let mut store = Store::default();
        for (mid, port) in [("m-a", "4500"), ("m-b", "4502")] {
            store.matches.insert(
                mid.into(),
                MatchState {
                    match_id: mid.into(),
                    report_token: format!("tok-{mid}"),
                    report_used: false,
                    players: vec![MatchPlayer {
                        team_uuid: "team-1".into(),
                        key: "k".into(),
                        connection: vec![format!("client --remote 127.0.0.1:{port} --key k")],
                        authenticated: true,
                    }],
                    started_at: Instant::now(),
                    deadline: Instant::now() + Duration::from_secs(30),
                    instance_id: "id".into(),
                    crd_name: String::new(),
                    challenge_id: "minions".into(),
                },
            );
        }
        let payload = queue_status_payload(&store, "team-1");
        assert_eq!(payload["state"], "matched");
        let conns = payload["connection"].as_array().unwrap();
        assert_eq!(conns.len(), 2, "both concurrent instances must surface");
        let joined = conns
            .iter()
            .map(|c| c.as_str().unwrap())
            .collect::<Vec<_>>()
            .join(" ");
        assert!(joined.contains("4500") && joined.contains("4502"));
    }

    #[test]
    fn cap_stays_one_without_event_hours() {
        // HARD CONSTRAINT: local dev / e2e is unchanged unless --event-hours is
        // opted in. Default (event_hours = 0) pins the concurrency cap at 1 for
        // all time, regardless of max_concurrent_per_team.
        let a = args_from(&["--max-concurrent-per-team", "4"]);
        for t in [0u64, 3600, 48 * 3600, 1_000_000] {
            assert_eq!(
                a.cap_at(EventWindow::default(), 0, t),
                1,
                "no window/event_hours must keep cap=1 at t={t}"
            );
        }
        // Negative event_hours is also treated as "off".
        let a = args_from(&["--event-hours=-5", "--max-concurrent-per-team", "4"]);
        assert_eq!(a.cap_at(EventWindow::default(), 0, 48 * 3600), 1);
    }

    #[test]
    fn cap_ramps_from_one_to_max_over_the_event() {
        // 48h event, ramp ceiling 4: cap starts at 1, never exceeds the ceiling,
        // is monotonic non-decreasing, and reaches the ceiling by event end.
        let a = args_from(&["--event-hours", "48", "--max-concurrent-per-team", "4"]);
        let start = 1_000u64;
        assert_eq!(
            a.cap_at(EventWindow::default(), start, start),
            1,
            "cap is 1 at event start"
        );
        let mut prev = 0usize;
        for h in 0..=48 {
            let cap = a.cap_at(EventWindow::default(), start, start + h * 3600);
            assert!((1..=4).contains(&cap), "cap {cap} out of [1,4] at h={h}");
            assert!(cap >= prev, "cap must be non-decreasing (h={h})");
            prev = cap;
        }
        // By the end of the event the cap has reached its ceiling.
        assert_eq!(
            a.cap_at(EventWindow::default(), start, start + 48 * 3600),
            4
        );
        // Past the event end the clamp holds it at the ceiling.
        assert_eq!(
            a.cap_at(EventWindow::default(), start, start + 200 * 3600),
            4
        );
    }

    #[test]
    fn cap_uses_forwarded_event_window_without_event_hours() {
        let a = args_from(&["--max-concurrent-per-team", "4"]);
        let window = EventWindow {
            start: Some(1_000),
            end: Some(1_000 + 48 * 3600),
        };
        assert_eq!(a.cap_at(window, 0, 1_000), 1);
        assert_eq!(a.cap_at(window, 0, 1_000 + 48 * 3600), 4);
    }

    #[test]
    fn cap_max_one_is_inert() {
        // A ramp ceiling of 1 is just the old behaviour even with event_hours set.
        let a = args_from(&["--event-hours", "48", "--max-concurrent-per-team", "1"]);
        assert_eq!(a.cap_at(EventWindow::default(), 0, 48 * 3600), 1);
    }

    #[test]
    fn parse_event_time_accepts_rfc3339_and_numbers() {
        // RFC3339 (Zulu).
        assert_eq!(parse_event_time(&json!("1970-01-01T00:00:10Z")), Some(10));
        // Bare unix seconds.
        assert_eq!(
            parse_event_time(&json!(1_700_000_000u64)),
            Some(1_700_000_000)
        );
        // Epoch milliseconds are scaled down so a ms/s mix-up can't push the end
        // date centuries out (which would never close the window).
        assert_eq!(
            parse_event_time(&json!(1_700_000_000_000u64)),
            Some(1_700_000_000)
        );
        // Junk / empty / wrong type → no bound.
        assert_eq!(parse_event_time(&json!("")), None);
        assert_eq!(parse_event_time(&json!("not-a-date")), None);
        assert_eq!(parse_event_time(&json!(0)), None);
        assert_eq!(parse_event_time(&json!(true)), None);
    }

    #[test]
    fn window_unset_is_always_open() {
        let w = EventWindow::from_config(None);
        assert!(w.is_unset());
        assert!(w.can_serve(0, 320));
        assert!(w.can_serve(u64::MAX, 320));
        // A config without the keys is also unset.
        let w = EventWindow::from_config(Some(&json!({"pods": [], "udprouterAddr": "h:1"})));
        assert!(w.is_unset());
        assert!(w.can_serve(1_000, 320));
    }

    #[test]
    fn window_refuses_matches_that_would_run_past_end_minus_buffer() {
        // 320s match (grace 20 + match 300), end at t=1000.
        let w = EventWindow::from_config(Some(&json!({"endDate": 1000})));
        let total = 320;
        // Finishes at 660 + 20 buffer = 680 <= 1000: served.
        assert!(w.can_serve(340, total));
        // Latest start that still leaves the buffer: now + 320 + 20 == 1000.
        assert!(w.can_serve(660, total));
        // One second later it would finish past end - buffer: refused.
        assert!(!w.can_serve(661, total));
        // Well after end: refused.
        assert!(!w.can_serve(1000, total));
    }

    #[test]
    fn window_refuses_before_start() {
        let w = EventWindow::from_config(Some(&json!({
            "startDate": "1970-01-01T00:00:10Z",
            "endDate": 100_000,
        })));
        assert!(!w.can_serve(9, 320));
        assert!(w.can_serve(10, 320));
        assert!(w.can_serve(500, 320));
    }
}
