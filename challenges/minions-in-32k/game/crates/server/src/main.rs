mod automation;
mod features;
mod game;
mod instance;
mod net;
mod tls;
mod wt;

use anyhow::Result;
use clap::Parser;
use std::net::SocketAddr;
use std::path::PathBuf;

/// Minions-in-32K authoritative game server.
#[derive(Parser, Debug)]
#[command(version, about)]
struct Args {
    /// UDP address to bind the QUIC endpoint on.
    #[arg(long, default_value = "0.0.0.0:4433")]
    bind: SocketAddr,

    /// Gameplay tick rate in Hz. Still an open question in PLAN.md.
    #[arg(long, default_value_t = common::DEFAULT_TICK_HZ)]
    tick_hz: u16,

    /// Deterministic RNG seed. Also written into every replay header.
    #[arg(long, default_value_t = 0xC001_C0DEu64)]
    seed: u64,

    /// Directory for replay files (created if missing).
    #[arg(long, default_value = "replays")]
    replay_dir: PathBuf,

    /// Max concurrent players. Hard capped to `protocol::MAX_PLAYERS`.
    #[arg(long, default_value_t = protocol::MAX_PLAYERS as u8)]
    max_players: u8,

    /// Which arena to load.
    #[arg(long, default_value = "gru-lab-v3")]
    arena: game::ArenaChoice,

    /// TOML file for live feature flags. Missing file means defaults.
    #[arg(long, default_value = "feature_flags.toml")]
    feature_flags: PathBuf,

    /// Match length once the grace period ends (instance mode only —
    /// activated by the PLAYER_TOKENS / PLAYER_TOKENS_FILE env vars).
    #[arg(long, default_value_t = 300)]
    match_secs: u64,

    /// Seconds to wait for players to connect before the match clock starts.
    #[arg(long, default_value_t = 30)]
    grace_secs: u64,

    /// WebTransport bridge bind for browser clients (sessions are bridged
    /// onto the QUIC endpoint over loopback).
    #[arg(long, default_value = "0.0.0.0:4434")]
    wt_bind: std::net::SocketAddr,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let args = Args::parse();
    let max_players = (args.max_players as usize).min(protocol::MAX_PLAYERS);
    let instance = instance::InstanceConfig::from_env(args.grace_secs, args.match_secs)?;
    if let Some(cfg) = &instance {
        tracing::info!(
            match_id = %cfg.match_id,
            players = cfg.players.len(),
            grace_s = cfg.grace.as_secs(),
            match_s = cfg.match_len.as_secs(),
            report = cfg.report_url.is_some(),
            "instance mode"
        );
    }

    tokio::fs::create_dir_all(&args.replay_dir).await?;

    rustls::crypto::ring::default_provider()
        .install_default()
        .ok();

    let (cmd_tx, cmd_rx) = tokio::sync::mpsc::unbounded_channel::<game::GameCmd>();
    let (broadcast_tx, _) = tokio::sync::broadcast::channel::<protocol::ServerMessage>(256);

    // Filename carries match id + a random nonce so concurrent instances (or
    // a restart within the same second) can't collide; create_new in the
    // writer then refuses to clobber an existing file.
    let match_tag = instance
        .as_ref()
        .map(|c| c.match_id.clone())
        .unwrap_or_else(|| "local".into());
    let nonce: u64 = {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        std::time::SystemTime::now().hash(&mut h);
        std::process::id().hash(&mut h);
        h.finish()
    };
    let replay_path = args.replay_dir.join(format!(
        "run-{}-{}-{:016x}.mrpl",
        game::now_unix(),
        sanitize_tag(&match_tag),
        nonce,
    ));

    let game_handle = tokio::spawn(game::run(
        cmd_rx,
        broadcast_tx.clone(),
        game::GameConfig {
            tick_hz: args.tick_hz,
            seed: args.seed,
            max_players,
            replay_path,
            arena_choice: args.arena,
            feature_flags_path: args.feature_flags,
            instance,
        },
    ));

    let endpoint = net::make_endpoint(args.bind)?;
    if let Ok(path) = std::env::var("MINIONS_READY_FILE") {
        tokio::fs::write(path, b"ready\n").await?;
    }
    let quic_target: SocketAddr = format!("127.0.0.1:{}", args.bind.port()).parse()?;
    tokio::spawn(async move {
        if let Err(e) = wt::run_bridge(args.wt_bind, quic_target).await {
            tracing::error!(error = %e, "webtransport bridge failed");
        }
    });
    tracing::info!(bind = %args.bind, tick_hz = args.tick_hz, "server ready");

    tokio::select! {
        r = net::accept_loop(endpoint, cmd_tx, broadcast_tx, std::sync::Arc::new(args.arena.build()), args.seed) => r?,
        // In instance mode the game loop ends itself at the match deadline
        // (report sent, replay flushed) and the whole process exits.
        r = game_handle => { r??; }
    }
    Ok(())
}

/// Keep replay filenames to a safe charset (match ids come from rCTF).
fn sanitize_tag(s: &str) -> String {
    let cleaned: String = s
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .take(48)
        .collect();
    if cleaned.is_empty() {
        "x".into()
    } else {
        cleaned
    }
}
