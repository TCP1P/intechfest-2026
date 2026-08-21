//! Authoritative game-loop driver.
//!
//! Holds a `sim::World` and drives it from inbound commands. Snapshots are
//! broadcast to clients and written into the replay file. Physics, weapons,
//! and scoring all live in the `sim` crate — this module is I/O only.

use crate::features::FeatureStore;
use crate::instance::{self, InstanceConfig};
use anyhow::Result;
use protocol::{GameEvent, Input, PlayerId, ServerMessage, Tick};
use replay::{Header, ReplayEvent, Writer as ReplayWriter};
use sim::{Arena, World};
use std::path::PathBuf;
use tokio::sync::{broadcast, mpsc, oneshot};

pub enum GameCmd {
    Join {
        name: String,
        key: Option<String>,
        variant: u8,
        reply: oneshot::Sender<Result<(PlayerId, u16), String>>,
    },
    /// Per-connection RTT sample from the net layer's watchdog.
    Rtt {
        player_id: PlayerId,
        rtt_ms: u16,
    },
    Leave {
        player_id: PlayerId,
    },
    Input {
        player_id: PlayerId,
        input: Input,
    },
    AutomatedCombat {
        player_id: PlayerId,
    },
}

pub struct GameConfig {
    pub tick_hz: u16,
    pub seed: u64,
    pub max_players: usize,
    pub replay_path: PathBuf,
    pub arena_choice: ArenaChoice,
    pub feature_flags_path: PathBuf,
    pub instance: Option<InstanceConfig>,
}

pub async fn run(
    mut cmd_rx: mpsc::UnboundedReceiver<GameCmd>,
    broadcast: broadcast::Sender<ServerMessage>,
    config: GameConfig,
) -> Result<()> {
    let tick_hz = config.tick_hz.max(1);
    let arena = config.arena_choice.build();
    let mut world = World::with_seed(arena, tick_hz, config.seed);

    let mut writer = ReplayWriter::create(
        &config.replay_path,
        Header {
            format_version: replay::FORMAT_VERSION,
            protocol_version: protocol::PROTOCOL_VERSION,
            tick_rate_hz: tick_hz,
            seed: config.seed,
            start_unix_ms: now_millis(),
        },
    )
    .await?;
    tracing::info!(path = ?config.replay_path, arena = ?config.arena_choice, "replay writer open");

    let period = std::time::Duration::from_nanos(1_000_000_000 / tick_hz as u64);
    let mut interval = tokio::time::interval(period);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Burst);
    let mut feature_interval = tokio::time::interval(std::time::Duration::from_secs(1));
    feature_interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut feature_store = FeatureStore::new(config.feature_flags_path);

    let max_players = config.max_players.min(protocol::MAX_PLAYERS);

    // Perf accounting: per-tick handler time (sim + replay write +
    // broadcast), snapshot wire size, and ticks that blow the 1/tick_hz
    // budget. A `perf` line every 10 s, a summary at shutdown.
    let mut perf_window: Vec<u32> = Vec::with_capacity(tick_hz as usize * 10);
    let mut perf_sim_us: u64 = 0;
    let mut perf_bytes: u64 = 0;
    let mut perf_overruns: u32 = 0;
    let (mut life_ticks, mut life_us, mut life_max_us) = (0u64, 0u64, 0u64);
    let (mut life_overruns, mut life_bytes) = (0u64, 0u64);
    let report_every: u64 = tick_hz as u64 * 10;

    // Instance-mode state: key -> slot bindings, per-slot match stats, and
    // the hard deadline (grace + match length) that ends the process.
    let mut key_slots: std::collections::HashMap<String, PlayerId> =
        std::collections::HashMap::new();
    let mut slot_uuid: [Option<String>; protocol::MAX_PLAYERS] = Default::default();
    let mut slot_name: [Option<String>; protocol::MAX_PLAYERS] = Default::default();
    let mut slot_variant = [0u8; protocol::MAX_PLAYERS];
    let mut slot_rtt = [0u16; protocol::MAX_PLAYERS];
    let mut counters = [instance::PlayerCounters::default(); protocol::MAX_PLAYERS];
    let mut connected = [false; protocol::MAX_PLAYERS];
    let mut last_input_tick = [0u64; protocol::MAX_PLAYERS];
    let mut last_takeover_tick = [0u64; protocol::MAX_PLAYERS];
    let clock_anchor = tokio::time::Instant::now();
    let clock_unix_ms = now_millis();
    let match_start = config.instance.as_ref().map(|cfg| clock_anchor + cfg.grace);
    let match_start_unix_ms = config
        .instance
        .as_ref()
        .map(|cfg| clock_unix_ms.saturating_add(cfg.grace.as_millis() as u64));
    let deadline = config
        .instance
        .as_ref()
        .map(|cfg| clock_anchor + cfg.grace + cfg.match_len);
    let mut scoring_started = false;
    let mut next_leg = 1u32;
    let mut leg_window: Option<instance::LegWindow> = None;
    let (mut report_tx, mut report_task) = if let Some(cfg) = config
        .instance
        .as_ref()
        .filter(|cfg| cfg.scoring_leg.is_some())
    {
        let (tx, rx) = mpsc::unbounded_channel();
        let cfg = cfg.clone();
        (
            Some(tx),
            Some(tokio::spawn(instance::report_worker(cfg, rx))),
        )
    } else {
        (None, None)
    };

    loop {
        tokio::select! {
            _ = async { tokio::time::sleep_until(deadline.unwrap()).await }, if deadline.is_some() => {
                let cfg = config.instance.as_ref().unwrap();
                tracing::info!(match_id = %cfg.match_id, "match over — reporting and exiting");
                if let (Some(leg), Some(leg_count), Some(started), Some(window), Some(tx)) = (
                    cfg.scoring_leg,
                    cfg.scoring_leg_count(),
                    match_start_unix_ms,
                    leg_window.as_mut(),
                    report_tx.as_ref(),
                ) {
                    while next_leg <= leg_count {
                        let report = build_leg_report(
                            cfg,
                            next_leg,
                            leg_count,
                            started,
                            leg,
                            window,
                            &key_slots,
                            counters,
                        );
                        if tx.send(report).is_err() {
                            tracing::error!(sequence = next_leg, "match report worker stopped early");
                        }
                        next_leg += 1;
                    }
                }
                let standings: Vec<protocol::RosterEntry> = world
                    .players
                    .iter()
                    .enumerate()
                    .filter_map(|(i, slot)| {
                        let p = slot.as_ref()?;
                        Some(protocol::RosterEntry {
                            id: i as PlayerId,
                            name: slot_name[i].clone().unwrap_or_else(|| p.name.clone()),
                            variant: slot_variant[i],
                            rtt_ms: slot_rtt[i],
                            kills: counters[i].kills.min(u16::MAX as u32) as u16,
                            confirmed_kills: counters[i]
                                .confirmed_kills
                                .min(u16::MAX as u32)
                                as u16,
                            deaths: counters[i].deaths.min(u16::MAX as u32) as u16,
                            connected: connected[i],
                        })
                    })
                    .collect();
                let _ = broadcast.send(ServerMessage::GameOver { standings });
                // give connection writers a moment to flush the packet
                tokio::time::sleep(std::time::Duration::from_millis(800)).await;
                let players = cumulative_players(cfg, &key_slots, counters, &connected);
                let report = if let Some(leg_count) = cfg.scoring_leg_count() {
                    let started = match_start_unix_ms.expect("instance match start exists");
                    let ended = started.saturating_add(cfg.match_len.as_millis() as u64);
                    instance::MatchReport {
                        match_id: cfg.match_id.clone(),
                        kind: Some(instance::ReportKind::Final),
                        sequence: Some(leg_count + 1),
                        leg_count: Some(leg_count),
                        started_unix_ms: started,
                        ended_unix_ms: ended,
                        duration_ms: ended.saturating_sub(started),
                        players,
                    }
                } else {
                    let ended_unix_ms = now_millis();
                    instance::MatchReport {
                        match_id: cfg.match_id.clone(),
                        kind: None,
                        sequence: None,
                        leg_count: None,
                        started_unix_ms: clock_unix_ms,
                        ended_unix_ms,
                        duration_ms: ended_unix_ms.saturating_sub(clock_unix_ms),
                        players,
                    }
                };
                writer
                    .write_event(&ReplayEvent::MatchEnd { tick: world.tick })
                    .await
                    .ok();
                writer.flush().await.ok();
                instance::upload_replay(cfg, &config.replay_path).await;
                if let Some(tx) = report_tx.take() {
                    if tx.send(report).is_err() {
                        tracing::error!("match report worker stopped before final reconciliation");
                    }
                    drop(tx);
                    if let Some(task) = report_task.take() {
                        match task.await {
                            Ok(true) => {}
                            Ok(false) => tracing::error!("one or more match reports were not accepted"),
                            Err(error) => tracing::error!(%error, "match report worker failed"),
                        }
                    }
                } else {
                    let _ = instance::submit_report(cfg, &report).await;
                }
                break;
            }
            _ = shutdown_signal() => {
                tracing::info!("shutdown signal — flushing replay");
                break;
            }
            _ = interval.tick() => {
                let t0 = std::time::Instant::now();
                let now = tokio::time::Instant::now();
                if !scoring_started
                    && config
                        .instance
                        .as_ref()
                        .is_some_and(|cfg| cfg.scoring_leg.is_some())
                    && match_start.is_some_and(|start| now >= start)
                {
                    let discarded = world.discard_pending_confirmations();
                    writer
                        .write_event(&ReplayEvent::ScoringWindowStarted { tick: world.tick })
                        .await
                        .ok();
                    instance::reset_scoring_counters(&mut counters);
                    for (index, is_connected) in connected.iter().copied().enumerate() {
                        if is_connected {
                            last_input_tick[index] = world.tick;
                        }
                    }
                    leg_window = Some(instance::LegWindow::new(counters));
                    scoring_started = true;
                    tracing::info!(discarded_confirmations = discarded, "official scoring window started");
                }
                if scoring_started {
                    if let (Some(cfg), Some(start), Some(started), Some(leg), Some(leg_count), Some(window), Some(tx)) = (
                        config.instance.as_ref(),
                        match_start,
                        match_start_unix_ms,
                        config.instance.as_ref().and_then(|cfg| cfg.scoring_leg),
                        config.instance.as_ref().and_then(InstanceConfig::scoring_leg_count),
                        leg_window.as_mut(),
                        report_tx.as_ref(),
                    ) {
                        while next_leg <= leg_count
                            && now >= start + leg.saturating_mul(next_leg)
                        {
                            let report = build_leg_report(
                                cfg,
                                next_leg,
                                leg_count,
                                started,
                                leg,
                                window,
                                &key_slots,
                                counters,
                            );
                            if tx.send(report).is_err() {
                                tracing::error!(sequence = next_leg, "match report worker stopped early");
                            }
                            next_leg += 1;
                        }
                    }
                }
                let snap = world.advance();
                let sim_us = t0.elapsed().as_micros() as u64;
                let tick_idx: Tick = snap.tick;
                for ev in &snap.events {
                    if let Some((frag, death)) = ev.frag_death() {
                        if let Some(a) = frag {
                            counters[a as usize].kills += 1;
                        }
                        counters[death as usize].deaths += 1;
                    }
                    if let GameEvent::KillConfirmed { killer, .. } = *ev {
                        counters[killer as usize].confirmed_kills += 1;
                    }
                }
                if scoring_started {
                    let active_limit = u64::from(tick_hz) * 2;
                    let present = std::array::from_fn(|index| {
                        connected[index]
                            && world.tick.saturating_sub(last_input_tick[index]) <= active_limit
                    });
                    if let Some(window) = leg_window.as_mut() {
                        window.record_tick(&present);
                    }
                }
                let wire = protocol::encode(&ServerMessage::Snapshot(snap.clone()))
                    .map(|b| b.len() as u64)
                    .unwrap_or(0);
                if let Err(e) = writer.write_event(&ReplayEvent::Snapshot(snap.clone())).await {
                    tracing::error!(error = %e, "replay snapshot write");
                }
                if tick_idx.is_multiple_of(32) {
                    if let Err(e) = writer.flush().await {
                        tracing::warn!(error = %e, "replay flush");
                    }
                }
                // Events ride the reliable stream; the snapshot itself goes
                // out as an unreliable datagram (see writer_task) — a lost
                // datagram must never cost a kill or a hit.
                let mut snap = snap;
                let events = std::mem::take(&mut snap.events);
                if !events.is_empty() {
                    let _ = broadcast.send(ServerMessage::Events {
                        tick: snap.tick,
                        events,
                    });
                }
                let _ = broadcast.send(ServerMessage::Snapshot(snap));

                if tick_idx.is_multiple_of(tick_hz as u64) {
                    let roster: Vec<protocol::RosterEntry> = world
                        .players
                        .iter()
                        .enumerate()
                        .filter_map(|(i, slot)| {
                            let p = slot.as_ref()?;
                            Some(protocol::RosterEntry {
                                id: i as PlayerId,
                                name: slot_name[i].clone().unwrap_or_else(|| p.name.clone()),
                                variant: slot_variant[i],
                                rtt_ms: slot_rtt[i],
                                kills: counters[i].kills.min(u16::MAX as u32) as u16,
                                confirmed_kills: counters[i]
                                    .confirmed_kills
                                    .min(u16::MAX as u32)
                                    as u16,
                                deaths: counters[i].deaths.min(u16::MAX as u32) as u16,
                                connected: connected[i],
                            })
                        })
                        .collect();
                    let _ = broadcast.send(ServerMessage::Roster(roster));
                }
                let tick_us = t0.elapsed().as_micros() as u64;
                perf_window.push(tick_us.min(u32::MAX as u64) as u32);
                perf_sim_us += sim_us;
                perf_bytes += wire;
                if tick_us > period.as_micros() as u64 {
                    perf_overruns += 1;
                }
                life_ticks += 1;
                life_us += tick_us;
                life_max_us = life_max_us.max(tick_us);
                life_bytes += wire;
                if tick_idx.is_multiple_of(report_every) && !perf_window.is_empty() {
                    perf_window.sort_unstable();
                    let n = perf_window.len();
                    let pct = |p: f64| perf_window[((n as f64 * p) as usize).min(n - 1)];
                    let players = world.players.iter().filter(|p| p.is_some()).count();
                    tracing::info!(
                        tick_p50_us = pct(0.50),
                        tick_p99_us = pct(0.99),
                        tick_max_us = perf_window[n - 1],
                        sim_avg_us = perf_sim_us / n as u64,
                        snapshot_kib_s = perf_bytes / 1024 / 10,
                        budget_overruns = perf_overruns,
                        players,
                        "perf"
                    );
                    life_overruns += perf_overruns as u64;
                    perf_window.clear();
                    perf_sim_us = 0;
                    perf_bytes = 0;
                    perf_overruns = 0;
                }
            }
            _ = feature_interval.tick() => {
                for (key, value) in feature_store.poll().await {
                    if world.set_feature_flag(&key, value) {
                        let value_string = value.to_string();
                        writer.write_event(&ReplayEvent::FlagFlip {
                            tick: world.tick,
                            key: key.clone(),
                            value: value_string.clone(),
                        }).await.ok();
                        tracing::info!(%key, value = %value_string, "feature flag flip");
                    } else {
                        tracing::warn!(%key, "unknown feature flag");
                    }
                }
            }
            maybe_cmd = cmd_rx.recv() => {
                let Some(cmd) = maybe_cmd else { break; };
                match cmd {
                    GameCmd::Rtt { player_id, rtt_ms } => {
                        if let Some(s) = slot_rtt.get_mut(player_id as usize) {
                            *s = rtt_ms;
                        }
                        // Lag compensation: rewind hitscan by the player's
                        // RTT (+1 tick of client interpolation). Recorded in
                        // the replay — it's sim state.
                        let ticks = ((rtt_ms as u64 * config.tick_hz as u64) / 1000 + 1)
                            .min(sim::world::LAG_COMP_MAX_TICKS as u64)
                            as u8;
                        if world.player_delay(player_id) != ticks {
                            world.set_player_delay(player_id, ticks);
                            writer.write_event(&ReplayEvent::PlayerDelay {
                                tick: world.tick,
                                id: player_id,
                                ticks,
                            }).await.ok();
                        }
                    }
                    GameCmd::Join { name, key, variant, reply } => {
                        let variant = variant % 4;
                        if let Some(cfg) = &config.instance {
                            let Some(key) = key else {
                                let _ = reply.send(Err("access key required".into()));
                                continue;
                            };
                            let Some(entry) = cfg.players.iter().find(|p| p.key == key) else {
                                let _ = reply.send(Err("invalid access key".into()));
                                continue;
                            };
                            // Display name comes from rCTF, not the client.
                            let name = entry.name.clone();
                            if let Some(&id) = key_slots.get(&key) {
                                // First-connect-wins: the slot stays bound to
                                // whoever holds it. A second key holder may
                                // only RECONNECT once the incumbent has
                                // dropped (connected == false); a live slot
                                // refuses the duplicate outright.
                                let i = id as usize;
                                if connected[i] {
                                    let _ = reply.send(Err("slot already connected".into()));
                                    continue;
                                }
                                if world.tick.saturating_sub(last_takeover_tick[i])
                                    < 2 * tick_hz as u64
                                {
                                    let _ = reply.send(Err("reconnecting too fast".into()));
                                    continue;
                                }
                                last_takeover_tick[i] = world.tick;
                                counters[i].takeovers += 1;
                                connected[i] = true;
                                last_input_tick[i] = world.tick;
                                slot_variant[i] = variant;
                                tracing::info!(id, %name, takeovers = counters[i].takeovers, "session reconnect");
                                let _ = reply.send(Ok((id, tick_hz)));
                                continue;
                            }
                            match world.alloc(&name) {
                                Ok(id) => {
                                    key_slots.insert(key, id);
                                    let i = id as usize;
                                    slot_uuid[i] = Some(entry.uuid.clone());
                                    slot_name[i] = Some(name.clone());
                                    slot_variant[i] = variant;
                                    connected[i] = true;
                                    last_input_tick[i] = world.tick;
                                    writer.write_event(&ReplayEvent::PlayerJoin {
                                        tick: world.tick, id, name: name.clone(),
                                    }).await.ok();
                                    tracing::info!(id, %name, uuid = %entry.uuid, "player joined (instance)");
                                    let _ = reply.send(Ok((id, tick_hz)));
                                }
                                Err(_) => { let _ = reply.send(Err("server full".into())); }
                            }
                            continue;
                        }
                        if world.players.iter().filter(|p| p.is_some()).count() >= max_players {
                            let _ = reply.send(Err("server full".into()));
                            continue;
                        }
                        match world.alloc(&name) {
                            Ok(id) => {
                                let i = id as usize;
                                slot_name[i] = Some(name.clone());
                                slot_variant[i] = variant;
                                connected[i] = true;
                                counters[i] = instance::PlayerCounters::default();
                                last_input_tick[i] = world.tick;
                                writer.write_event(&ReplayEvent::PlayerJoin {
                                    tick: world.tick, id, name: name.clone(),
                                }).await.ok();
                                tracing::info!(id, %name, variant, "player joined");
                                let _ = reply.send(Ok((id, tick_hz)));
                            }
                            Err(_) => { let _ = reply.send(Err("server full".into())); }
                        }
                    }
                    GameCmd::Leave { player_id } => {
                        if config.instance.is_some() {
                            // Slot, score, and sim state persist for the
                            // whole match — the key can reconnect into it.
                            let i = player_id as usize;
                            if connected[i] {
                                connected[i] = false;
                                tracing::info!(id = player_id, "player disconnected (slot kept)");
                            }
                            continue;
                        }
                        if world.remove(player_id) {
                            writer.write_event(&ReplayEvent::PlayerLeave {
                                tick: world.tick, id: player_id,
                            }).await.ok();
                            tracing::info!(id = player_id, "player left");
                        }
                    }
                    GameCmd::Input { player_id, input } => {
                        // Don't let a malicious client claim a future tick.
                        // Shared window definition with the net-layer anti-cheat.
                        if input.tick <= world.tick.saturating_add(crate::net::max_input_future_ticks(tick_hz)) {
                            if connected.get(player_id as usize).copied().unwrap_or(false) {
                                last_input_tick[player_id as usize] = world.tick;
                            }
                            world.apply_input(player_id, input);
                            writer.write_event(&ReplayEvent::Input { id: player_id, input }).await.ok();
                        }
                    }
                    GameCmd::AutomatedCombat { player_id } => {
                        if let Some(player) = counters.get_mut(player_id as usize) {
                            if !player.automated_combat {
                                player.automated_combat = true;
                                tracing::warn!(id = player_id, "player scoring quarantined");
                            }
                        }
                    }
                }
            }
        }
    }

    writer.flush().await.ok();
    life_overruns += perf_overruns as u64;
    tracing::info!(
        ticks = life_ticks,
        tick_avg_us = if life_ticks > 0 {
            life_us / life_ticks
        } else {
            0
        },
        tick_max_us = life_max_us,
        budget_overruns = life_overruns,
        budget_us = period.as_micros() as u64,
        snapshot_mib_total = life_bytes / (1024 * 1024),
        "perf summary"
    );
    Ok(())
}

fn cumulative_players(
    cfg: &InstanceConfig,
    key_slots: &std::collections::HashMap<String, PlayerId>,
    counters: [instance::PlayerCounters; protocol::MAX_PLAYERS],
    connected: &[bool; protocol::MAX_PLAYERS],
) -> Vec<instance::ReportPlayer> {
    let players = cfg
        .players
        .iter()
        .map(|player| {
            let index = key_slots.get(&player.key).map(|slot| *slot as usize);
            let current = index.map(|i| counters[i]).unwrap_or_default();
            instance::ReportPlayer {
                team_uuid: player.uuid.clone(),
                name: player.name.clone(),
                rank: 0,
                kills: current.kills,
                confirmed_kills: current.confirmed_kills,
                deaths: current.deaths,
                connected: index.is_some_and(|i| connected[i]),
                takeovers: current.takeovers,
                integrity_status: if current.automated_combat {
                    instance::IntegrityStatus::AutomatedCombat
                } else {
                    instance::IntegrityStatus::Clean
                },
            }
        })
        .collect();
    instance::rank_players(players)
}

fn build_leg_report(
    cfg: &InstanceConfig,
    sequence: u32,
    leg_count: u32,
    match_started_unix_ms: u64,
    leg_duration: std::time::Duration,
    window: &mut instance::LegWindow,
    key_slots: &std::collections::HashMap<String, PlayerId>,
    counters: [instance::PlayerCounters; protocol::MAX_PLAYERS],
) -> instance::MatchReport {
    let leg_ms = leg_duration.as_millis() as u64;
    let started_unix_ms = match_started_unix_ms
        .saturating_add(leg_ms.saturating_mul(u64::from(sequence.saturating_sub(1))));
    let ended_unix_ms = started_unix_ms.saturating_add(leg_ms);
    instance::MatchReport {
        match_id: cfg.match_id.clone(),
        kind: Some(instance::ReportKind::Leg),
        sequence: Some(sequence),
        leg_count: Some(leg_count),
        started_unix_ms,
        ended_unix_ms,
        duration_ms: leg_ms,
        players: window.finish(cfg, key_slots, counters),
    }
}

/// Seconds since the Unix epoch. Net/report path only — never feeds sim.
pub fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Milliseconds since the Unix epoch. Net/report path only — never feeds sim.
pub fn now_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

/// SIGINT or SIGTERM. Without this, `kill`/`pkill` skipped the replay
/// tail flush and the perf summary.
async fn shutdown_signal() {
    let ctrl_c = tokio::signal::ctrl_c();
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("install SIGTERM handler");
        tokio::select! {
            _ = ctrl_c => {}
            _ = term.recv() => {}
        }
    }
    #[cfg(not(unix))]
    ctrl_c.await.ok();
}

#[derive(Clone, Copy, Debug)]
pub enum ArenaChoice {
    MinimalBox,
    GruLab,
    GruLabV3,
}

impl ArenaChoice {
    pub fn build(self) -> Arena {
        match self {
            ArenaChoice::MinimalBox => Arena::phase2_minimal(),
            ArenaChoice::GruLab => Arena::gru_lab(),
            ArenaChoice::GruLabV3 => Arena::gru_lab_v3(),
        }
    }
}

impl std::str::FromStr for ArenaChoice {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "minimal" => Ok(Self::MinimalBox),
            "gru-lab" | "gru_lab" | "grulab" => Ok(Self::GruLab),
            "gru-lab-v3" | "gru_lab_v3" | "v3" => Ok(Self::GruLabV3),
            other => Err(format!(
                "unknown arena '{other}' (expected: minimal, gru-lab, gru-lab-v3)"
            )),
        }
    }
}
