//! QUIC endpoint and per-connection plumbing for one authenticated stream.

use crate::game::GameCmd;
use crate::tls;
use anyhow::{Context, Result};
use common::FpExt;
use glam::Vec3;
use protocol::{
    Buttons, ClientMessage, Input, PlayerId, PlayerState, ServerMessage, Snapshot, MAX_MSG_BYTES,
    MAX_NAME_LEN,
};
use quinn::{Endpoint, RecvStream, SendStream, ServerConfig, VarInt};
use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::io::AsyncWriteExt;
use tokio::sync::broadcast;
use tokio::sync::mpsc::UnboundedSender;
use tokio::sync::oneshot;

const MAX_BIDI_STREAMS_PER_CONN: u32 = 1;
const MAX_UNI_STREAMS_PER_CONN: u32 = 0;
const STREAM_RECV_WINDOW_BYTES: u32 = (MAX_MSG_BYTES as u32) * 2;
const CONN_RECV_WINDOW_BYTES: u32 = (MAX_MSG_BYTES as u32) * 4;
const SEND_WINDOW_BYTES: u64 = (MAX_MSG_BYTES as u64) * 8;
const MAX_IDLE_TIMEOUT: Duration = Duration::from_secs(10);
const KEEP_ALIVE_INTERVAL: Duration = Duration::from_secs(3);
const INITIAL_RTT: Duration = Duration::from_millis(100);
const MAX_RTT: Duration = Duration::from_millis(900);
const RTT_CHECK_INTERVAL: Duration = Duration::from_secs(5);
const MAX_CONSECUTIVE_HIGH_RTT: u8 = 3;
const INPUT_RATE_WINDOW: Duration = Duration::from_secs(1);
const MAX_INPUTS_PER_WINDOW: u16 = 96;
/// Roughly two seconds of post-stall input catch-up at 64 Hz.
const INPUT_BURST_CAP: u16 = 224;
const PROBE_LIFETIME_TICKS: u64 = 5;
const PROBE_INTERVAL_TICKS: u64 = 32;
const PROBE_RADIUS: f32 = 0.28;
const PROBE_SUSPICION_KICK: i32 = 100;
const REAL_TRACK_RADIUS: f32 = 0.045;
const REAL_FIRE_RADIUS: f32 = 0.055;
const REAL_SUSPICION_KICK: i32 = 140;
/// Absolute precision is not meaningful in a point-blank serum fight.
const REAL_MIN_RANGE: f32 = 12.0;
const EYE_HEIGHT: f32 = 1.35;
/// Prevent one-tick visibility strobing at cover edges.
const SEEN_GRACE_TICKS: u64 = 12;
const TARGET_HEIGHT: f32 = 1.0;
const RAIL_RANGE: f32 = 80.0;
/// Independent bound for authenticated-pending QUIC connections.
const MAX_PREAUTH_CONNS: usize = 64;
/// Stops silent sockets from retaining a pre-authentication permit.
const FIRST_MSG_TIMEOUT: Duration = Duration::from_secs(3);
/// One second of future-tick slack, shared with the simulation guard.
pub fn max_input_future_ticks(tick_hz: u16) -> u64 {
    tick_hz as u64
}

pub fn make_endpoint(bind: SocketAddr) -> Result<Endpoint> {
    let (cert_chain, key) = tls::from_env_or_self_signed()?;
    let mut server_crypto = rustls::ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(cert_chain, key)
        .context("rustls server config")?;
    server_crypto.alpn_protocols = vec![protocol::ALPN.to_vec()];

    let quic_crypto = Arc::new(
        quinn::crypto::rustls::QuicServerConfig::try_from(server_crypto).context("quinn tls")?,
    );
    let mut config = ServerConfig::with_crypto(quic_crypto);
    let mut transport = quinn::TransportConfig::default();
    transport.max_concurrent_bidi_streams(VarInt::from_u32(MAX_BIDI_STREAMS_PER_CONN));
    transport.max_concurrent_uni_streams(VarInt::from_u32(MAX_UNI_STREAMS_PER_CONN));
    transport.stream_receive_window(VarInt::from_u32(STREAM_RECV_WINDOW_BYTES));
    transport.receive_window(VarInt::from_u32(CONN_RECV_WINDOW_BYTES));
    transport.send_window(SEND_WINDOW_BYTES);
    transport.max_idle_timeout(Some(MAX_IDLE_TIMEOUT.try_into()?));
    transport.keep_alive_interval(Some(KEEP_ALIVE_INTERVAL));
    transport.initial_rtt(INITIAL_RTT);
    config.transport_config(Arc::new(transport));

    Ok(Endpoint::server(config, bind)?)
}

/// Active connection per player slot — first-connect-wins: once a slot/key is
/// bound to a live connection, a second connection presenting the same key is
/// rejected and the incumbent is left untouched.
type ConnRegistry = Arc<Mutex<HashMap<PlayerId, quinn::Connection>>>;
type AntiCheatRegistry = Arc<Mutex<HashMap<PlayerId, Arc<Mutex<AntiCheatState>>>>>;

pub async fn accept_loop(
    endpoint: Endpoint,
    cmd_tx: UnboundedSender<GameCmd>,
    broadcast: broadcast::Sender<ServerMessage>,
    arena: Arc<sim::Arena>,
    seed: u64,
) -> Result<()> {
    let registry: ConnRegistry = Arc::new(Mutex::new(HashMap::new()));
    let anti_cheat_registry: AntiCheatRegistry = Arc::new(Mutex::new(HashMap::new()));
    // Bound in-flight unauthenticated handshakes.
    let preauth = Arc::new(tokio::sync::Semaphore::new(MAX_PREAUTH_CONNS));
    while let Some(incoming) = endpoint.accept().await {
        let cmd_tx = cmd_tx.clone();
        let broadcast = broadcast.clone();
        let registry = registry.clone();
        let anti_cheat_registry = anti_cheat_registry.clone();
        let arena = arena.clone();
        let Ok(permit) = preauth.clone().try_acquire_owned() else {
            tracing::warn!("preauth connection cap hit; refusing");
            incoming.refuse();
            continue;
        };
        tokio::spawn(async move {
            if let Err(e) = handle_incoming(
                incoming,
                cmd_tx,
                broadcast,
                registry,
                anti_cheat_registry,
                arena,
                seed,
                permit,
            )
            .await
            {
                tracing::warn!(error = %e, "connection dropped");
            }
        });
    }
    Ok(())
}

async fn handle_incoming(
    incoming: quinn::Incoming,
    cmd_tx: UnboundedSender<GameCmd>,
    broadcast: broadcast::Sender<ServerMessage>,
    registry: ConnRegistry,
    anti_cheat_registry: AntiCheatRegistry,
    arena: Arc<sim::Arena>,
    seed: u64,
    preauth: tokio::sync::OwnedSemaphorePermit,
) -> Result<()> {
    let conn = incoming.await.context("handshake")?;
    let peer = conn.remote_address();
    tracing::info!(%peer, "connection accepted");

    let (mut send, mut recv) = conn.accept_bi().await.context("accept_bi")?;

    // Bound how long an accepted-but-silent socket can hold its preauth permit.
    let first =
        match tokio::time::timeout(FIRST_MSG_TIMEOUT, read_msg::<ClientMessage>(&mut recv)).await {
            Ok(r) => r?,
            Err(_) => anyhow::bail!("first message timed out"),
        };
    let (name, key, variant) = match first {
        ClientMessage::Join {
            protocol_version,
            name,
            key,
            variant,
        } => {
            if protocol_version != protocol::PROTOCOL_VERSION {
                send_reject(
                    &mut send,
                    format!(
                        "protocol version mismatch (server={}, client={})",
                        protocol::PROTOCOL_VERSION,
                        protocol_version
                    ),
                )
                .await;
                return Ok(());
            }
            if name.is_empty() || name.len() > MAX_NAME_LEN {
                send_reject(&mut send, "invalid name".into()).await;
                return Ok(());
            }
            (name, key, variant)
        }
        _ => {
            send_reject(&mut send, "expected Join as first message".into()).await;
            return Ok(());
        }
    };

    let (reply_tx, reply_rx) = oneshot::channel();
    cmd_tx
        .send(GameCmd::Join {
            name: name.clone(),
            key,
            variant,
            reply: reply_tx,
        })
        .context("game loop gone")?;
    let (player_id, tick_rate_hz) = match reply_rx.await.context("join reply")? {
        Ok(v) => v,
        Err(reason) => {
            send_reject(&mut send, reason).await;
            return Ok(());
        }
    };
    // First-connect-wins: if a live connection already holds this slot, reject
    // the newcomer and leave the incumbent (the legit first bot/player) intact.
    let slot_taken = {
        let mut reg = registry.lock().unwrap();
        match reg.get(&player_id) {
            Some(existing) if existing.close_reason().is_none() => true,
            _ => {
                // Vacant or the incumbent already closed — claim the slot.
                reg.insert(player_id, conn.clone());
                false
            }
        }
    };
    if slot_taken {
        tracing::warn!(id = player_id, %peer, "slot already live; rejecting duplicate");
        send_reject(&mut send, "slot already connected".into()).await;
        return Ok(());
    }
    write_msg(
        &mut send,
        &ServerMessage::Welcome {
            your_id: player_id,
            tick_rate_hz,
        },
    )
    .await?;
    tracing::info!(id = player_id, %name, "player joined");
    // Authenticated: release the preauth permit so the slow-loris budget frees.
    drop(preauth);
    let rtt_watch = tokio::spawn(rtt_watchdog(conn.clone(), peer, player_id, cmd_tx.clone()));
    // Keep evidence across reconnects so cycling a socket cannot reset it.
    let anti_cheat = anti_cheat_for_player(&anti_cheat_registry, player_id, name, arena, seed);

    // Split: reader forwards inputs to game loop; writer forwards broadcast to client.
    let rx = broadcast.subscribe();
    let writer = tokio::spawn(writer_task(conn.clone(), send, rx, anti_cheat.clone()));
    let reader_res = reader_task(recv, player_id, cmd_tx.clone(), anti_cheat).await;
    // Deregister only this connection, never a later reconnect.
    {
        let mut reg = registry.lock().unwrap();
        if reg
            .get(&player_id)
            .is_some_and(|c| c.stable_id() == conn.stable_id())
        {
            reg.remove(&player_id);
        }
    }
    // Always notify the game loop, so it drops (or marks) our state.
    let _ = cmd_tx.send(GameCmd::Leave { player_id });
    writer.abort();
    rtt_watch.abort();
    reader_res
}

fn anti_cheat_for_player(
    registry: &AntiCheatRegistry,
    player_id: PlayerId,
    name: String,
    arena: Arc<sim::Arena>,
    seed: u64,
) -> Arc<Mutex<AntiCheatState>> {
    registry
        .lock()
        .unwrap()
        .entry(player_id)
        .or_insert_with(|| {
            let rng = common::DetRng::from_seed(
                seed ^ (player_id as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15),
            );
            Arc::new(Mutex::new(AntiCheatState::new(player_id, name, arena, rng)))
        })
        .clone()
}

async fn rtt_watchdog(
    conn: quinn::Connection,
    peer: SocketAddr,
    player_id: PlayerId,
    cmd_tx: UnboundedSender<GameCmd>,
) {
    let mut consecutive_high = 0u8;
    let mut beat = 0u32;
    loop {
        tokio::time::sleep(RTT_CHECK_INTERVAL).await;
        let rtt = conn.rtt();
        let _ = cmd_tx.send(GameCmd::Rtt {
            player_id,
            rtt_ms: rtt.as_millis().min(u16::MAX as u128) as u16,
        });
        beat += 1;
        if beat % 2 == 0 {
            let stats = conn.stats();
            tracing::info!(
                %peer,
                rtt_ms = rtt.as_millis(),
                cwnd = stats.path.cwnd,
                lost_packets = stats.path.lost_packets,
                "conn perf"
            );
        }
        if rtt > MAX_RTT {
            consecutive_high = consecutive_high.saturating_add(1);
            tracing::warn!(
                %peer,
                rtt_ms = rtt.as_millis(),
                consecutive_high,
                "connection RTT above limit"
            );
            if consecutive_high >= MAX_CONSECUTIVE_HIGH_RTT {
                conn.close(VarInt::from_u32(0), b"rtt too high");
                return;
            }
        } else {
            consecutive_high = 0;
        }
    }
}

async fn reader_task(
    mut recv: RecvStream,
    player_id: PlayerId,
    cmd_tx: UnboundedSender<GameCmd>,
    anti_cheat: Arc<Mutex<AntiCheatState>>,
) -> Result<()> {
    // Token bucket permits retransmit bursts; the sim applies one input per tick.
    let mut budget = MAX_INPUTS_PER_WINDOW as f64;
    let mut budget_at = Instant::now();
    loop {
        let msg = match read_msg::<ClientMessage>(&mut recv).await {
            Ok(m) => m,
            Err(e) if is_clean_eof(&e) => return Ok(()),
            Err(e) => return Err(e),
        };
        match msg {
            ClientMessage::Input(input) => {
                let (reason, automated_combat) = {
                    let mut state = anti_cheat
                        .lock()
                        .map_err(|_| anyhow::anyhow!("anti-cheat state poisoned"))?;
                    let reason = state.observe_input(input);
                    (reason, state.take_automated_combat())
                };
                if automated_combat {
                    let _ = cmd_tx.send(GameCmd::AutomatedCombat { player_id });
                }
                if let Some(reason) = reason {
                    anyhow::bail!("anti-cheat kick: {reason}");
                }
                let now = Instant::now();
                budget = (budget
                    + now.duration_since(budget_at).as_secs_f64() * MAX_INPUTS_PER_WINDOW as f64
                        / INPUT_RATE_WINDOW.as_secs_f64())
                .min(INPUT_BURST_CAP as f64);
                budget_at = now;
                if budget < 1.0 {
                    anyhow::bail!("input rate exceeded");
                }
                budget -= 1.0;
                if cmd_tx.send(GameCmd::Input { player_id, input }).is_err() {
                    return Ok(());
                }
            }
            ClientMessage::Leave => return Ok(()),
            ClientMessage::Join { .. } => anyhow::bail!("duplicate Join"),
        }
    }
}

async fn writer_task(
    conn: quinn::Connection,
    mut send: SendStream,
    mut rx: broadcast::Receiver<ServerMessage>,
    anti_cheat: Arc<Mutex<AntiCheatState>>,
) {
    loop {
        match rx.recv().await {
            Ok(mut msg) => {
                if let ServerMessage::Snapshot(snapshot) = &mut msg {
                    {
                        let Ok(mut state) = anti_cheat.lock() else {
                            tracing::warn!("anti-cheat state poisoned; closing writer");
                            return;
                        };
                        state.personalize_snapshot(snapshot);
                    }
                    // Unreliable, unordered: a dropped snapshot is obsolete
                    // by the next tick anyway, and skipping the stream means
                    // loss can't head-of-line-block the world state.
                    if let Ok(bytes) = protocol::encode(&msg) {
                        let fits = conn
                            .max_datagram_size()
                            .is_some_and(|max| bytes.len() <= max);
                        if fits {
                            match conn.send_datagram(bytes.into()) {
                                Ok(()) => continue,
                                Err(quinn::SendDatagramError::ConnectionLost(_)) => return,
                                // unsupported/too-large: fall through to stream
                                Err(_) => {}
                            }
                        }
                    }
                }
                if let Err(e) = write_msg(&mut send, &msg).await {
                    tracing::debug!(error = %e, "writer exit");
                    return;
                }
            }
            Err(broadcast::error::RecvError::Lagged(n)) => {
                tracing::warn!(n, "broadcast lag; client too slow");
            }
            Err(broadcast::error::RecvError::Closed) => return,
        }
    }
}

async fn read_msg<T: for<'de> serde::Deserialize<'de>>(recv: &mut RecvStream) -> Result<T> {
    let mut len_buf = [0u8; 4];
    recv.read_exact(&mut len_buf).await?;
    let len = u32::from_le_bytes(len_buf) as usize;
    if len > MAX_MSG_BYTES {
        anyhow::bail!("oversized message ({len} bytes)");
    }
    let mut buf = vec![0u8; len];
    recv.read_exact(&mut buf).await?;
    Ok(protocol::decode(&buf)?)
}

async fn write_msg<T: serde::Serialize>(send: &mut SendStream, msg: &T) -> Result<()> {
    let bytes = protocol::encode(msg)?;
    send.write_all(&(bytes.len() as u32).to_le_bytes()).await?;
    send.write_all(&bytes).await?;
    Ok(())
}

async fn send_reject(send: &mut SendStream, reason: String) {
    write_msg(send, &ServerMessage::Reject { reason })
        .await
        .ok();
    send.flush().await.ok();
    send.finish().ok();
    tokio::time::sleep(Duration::from_millis(25)).await;
}

fn is_clean_eof(err: &anyhow::Error) -> bool {
    err.downcast_ref::<quinn::ReadExactError>()
        .map(|e| matches!(e, quinn::ReadExactError::FinishedEarly(0)))
        .unwrap_or(false)
}

struct AntiCheatState {
    player_id: PlayerId,
    name: String,
    arena: Arc<sim::Arena>,
    /// Server-seeded RNG for probe spoof offsets (not client-reproducible).
    rng: common::DetRng,
    /// Authoritative tick from the latest personalized server snapshot.
    server_tick: u64,
    suspicion: i32,
    real_suspicion: i32,
    last_buttons: Buttons,
    last_yaw: Option<f32>,
    last_pitch: Option<f32>,
    last_self_pos: Option<Vec3>,
    last_real_players: Vec<PlayerState>,
    aimed_real_target: Option<PlayerId>,
    real_target_since: u64,
    precise_real_ticks: u16,
    automation: crate::automation::Detector,
    automated_combat_pending: bool,
    next_probe_tick: u64,
    probe: Option<Probe>,
    /// Last tick we had line-of-sight to each enemy id (grace window).
    last_seen: HashMap<PlayerId, u64>,
    /// Enemy deaths we personally witnessed (had LOS to the victim at the
    /// kill tick) — only these recipients keep the death's last position.
    witnessed_deaths: HashSet<PlayerId>,
}

struct Probe {
    target_id: PlayerId,
    born_tick: u64,
    expires_tick: u64,
    real_pos: Vec3,
    spoof_pos: Vec3,
    aimed_ticks: u8,
}

impl AntiCheatState {
    fn new(player_id: PlayerId, name: String, arena: Arc<sim::Arena>, rng: common::DetRng) -> Self {
        Self {
            player_id,
            name,
            arena,
            rng,
            server_tick: 0,
            suspicion: 0,
            real_suspicion: 0,
            last_buttons: Buttons::EMPTY,
            last_yaw: None,
            last_pitch: None,
            last_self_pos: None,
            last_real_players: Vec::new(),
            aimed_real_target: None,
            real_target_since: 0,
            precise_real_ticks: 0,
            automation: crate::automation::Detector::default(),
            automated_combat_pending: false,
            next_probe_tick: PROBE_INTERVAL_TICKS,
            probe: None,
            last_seen: HashMap::new(),
            witnessed_deaths: HashSet::new(),
        }
    }

    fn personalize_snapshot(&mut self, snapshot: &mut Snapshot) {
        let Some(me) = snapshot
            .players
            .iter()
            .find(|p| p.id == self.player_id)
            .copied()
        else {
            return;
        };
        self.server_tick = snapshot.tick;
        self.last_self_pos = Some(player_pos(&me));
        self.last_real_players = snapshot
            .players
            .iter()
            .copied()
            .filter(|p| p.id < protocol::MAX_PLAYERS as u8)
            .collect();
        self.decay(snapshot.tick);
        self.expire_probe(snapshot.tick);

        if self.probe.is_none() && snapshot.tick >= self.next_probe_tick && me.respawn_in == 0 {
            if let Some(target) = self.choose_probe_target(snapshot, &me) {
                let pos = concealed_probe_position(&self.arena, &me, target, &mut self.rng);
                self.probe = Some(Probe {
                    target_id: target.id,
                    born_tick: snapshot.tick,
                    expires_tick: snapshot.tick + PROBE_LIFETIME_TICKS,
                    real_pos: player_pos(target),
                    spoof_pos: pos,
                    aimed_ticks: 0,
                });
                self.next_probe_tick = snapshot.tick + PROBE_INTERVAL_TICKS;
            }
        }

        if let Some(target_id) = self.probe.as_ref().map(|p| p.target_id) {
            let target = snapshot
                .players
                .iter()
                .find(|p| p.id == target_id && p.respawn_in == 0)
                .copied();
            // Cancel a stale or newly visible probe before an honest client renders it.
            match target {
                Some(target) if self.occluded(&me, &target) => {
                    let spoof = concealed_probe_position(&self.arena, &me, &target, &mut self.rng);
                    if let Some(probe) = self.probe.as_mut() {
                        probe.real_pos = player_pos(&target);
                        probe.spoof_pos = spoof;
                    }
                    if let Some(outgoing) = snapshot.players.iter_mut().find(|p| p.id == target_id)
                    {
                        outgoing.pos = protocol_vec3(spoof);
                    }
                }
                _ => self.probe = None,
            }
        }

        // Preserve a dead player's last position only for witnesses.
        let tick = snapshot.tick;
        for ev in &snapshot.events {
            if let protocol::GameEvent::Kill { victim, .. } = ev {
                if *victim == self.player_id {
                    continue;
                }
                if let Some(v) = snapshot.players.iter().find(|p| p.id == *victim) {
                    if !self.occluded(&me, v) {
                        self.witnessed_deaths.insert(*victim);
                    }
                }
            }
        }

        // Keep the spoofed probe target in this recipient's view.
        let probe_target = self.probe.as_ref().map(|p| p.target_id);

        let mut view: Vec<PlayerState> = Vec::with_capacity(snapshot.players.len());
        for p in snapshot.players.iter().copied() {
            if p.id == self.player_id {
                view.push(p);
                continue;
            }

            let is_probe = probe_target == Some(p.id);
            let dead = p.respawn_in != 0;
            let visible = is_probe || !self.occluded(&me, &p);
            if visible {
                self.last_seen.insert(p.id, tick);
            }
            let in_grace = self
                .last_seen
                .get(&p.id)
                .is_some_and(|&t| tick.saturating_sub(t) <= SEEN_GRACE_TICKS);

            if dead {
                // Only recipients who witnessed the kill keep the body's
                // position; everyone else loses the dead player entirely.
                if self.witnessed_deaths.contains(&p.id) {
                    let mut d = p;
                    neutralize_combat(&mut d);
                    view.push(d);
                }
                continue;
            }
            self.witnessed_deaths.remove(&p.id);

            if !visible && !in_grace {
                continue;
            }

            let mut out = p;
            neutralize_combat(&mut out);
            // A spoofed probe target must not leak its true facing either.
            if is_probe {
                out.yaw = protocol::Fp::from_f32(0.0);
                out.pitch = protocol::Fp::from_f32(0.0);
            }
            view.push(out);
        }
        snapshot.players = view;
    }

    fn observe_input(&mut self, input: Input) -> Option<String> {
        // Anti-cheat keys off the SERVER tick captured at receive time, never
        // the client's self-reported `input.tick` (which a cheat can stamp to
        // dodge probe-expiry / tracking-cadence / target-age windows). The
        // client tick is clamped/validated for sim application in game.rs via
        // the shared `max_input_future_ticks`; here it is ignored entirely.
        let now = self.server_tick;
        let fire_edge =
            input.buttons.contains(Buttons::FIRE) && !self.last_buttons.contains(Buttons::FIRE);
        self.last_buttons = input.buttons;
        let yaw = input.yaw.to_f32();
        let pitch = input.pitch.to_f32();
        let yaw_delta = self
            .last_yaw
            .map(|last| angle_delta(last, yaw).abs())
            .unwrap_or(0.0);
        let pitch_delta = self
            .last_pitch
            .map(|last| (pitch - last).abs())
            .unwrap_or(0.0);
        self.last_yaw = Some(yaw);
        self.last_pitch = Some(pitch);

        let origin = self
            .last_self_pos
            .map(|p| p + Vec3::new(0.0, EYE_HEIGHT, 0.0))?;

        if let Some(evidence) =
            self.automation
                .observe(now, self.player_id, input, origin, &self.last_real_players)
        {
            self.automated_combat_pending = true;
            tracing::warn!(
                id = self.player_id,
                name = %self.name,
                ?evidence,
                "automated combat classified"
            );
        }

        if let Some(reason) = self.observe_real_target(RealTargetObservation {
            now,
            yaw,
            pitch,
            yaw_delta,
            pitch_delta,
            fire_edge,
            origin,
        }) {
            return Some(reason);
        }

        let probe = self.probe.as_ref()?;
        if now > probe.expires_tick.saturating_add(4) {
            return None;
        }
        let target_id = probe.target_id;
        let probe_pos = probe.spoof_pos;

        let probe_distance = aim_distance_to_point(
            origin,
            yaw,
            pitch,
            probe_pos + Vec3::new(0.0, TARGET_HEIGHT, 0.0),
        )?;

        if probe_distance <= PROBE_RADIUS
            && self.nearest_real_aim_distance(origin, yaw, pitch) > probe_distance + 0.15
        {
            let quick_reaction = now <= probe.born_tick.saturating_add(2);
            let hard_snap = yaw_delta.max(pitch_delta) >= 0.20;
            let probe = self.probe.as_mut()?;
            probe.aimed_ticks = probe.aimed_ticks.saturating_add(1);
            self.suspicion += 1;
            if fire_edge && (quick_reaction || hard_snap) {
                self.suspicion += 70;
                tracing::warn!(
                    id = self.player_id,
                    name = %self.name,
                    target = target_id,
                    suspicion = self.suspicion,
                    quick_reaction,
                    hard_snap,
                    "anti-cheat probe fire"
                );
            }
        }

        if self.suspicion >= PROBE_SUSPICION_KICK {
            Some(format!(
                "{} fired/tracked spoofed target {} (suspicion={})",
                self.name, target_id, self.suspicion
            ))
        } else {
            None
        }
    }

    fn take_automated_combat(&mut self) -> bool {
        std::mem::take(&mut self.automated_combat_pending)
    }

    fn observe_real_target(&mut self, obs: RealTargetObservation) -> Option<String> {
        let Some(aim) = self.best_real_target_aim(obs.origin, obs.yaw, obs.pitch) else {
            self.aimed_real_target = None;
            self.precise_real_ticks = 0;
            return None;
        };

        if self.aimed_real_target != Some(aim.id) {
            self.aimed_real_target = Some(aim.id);
            self.real_target_since = obs.now;
            self.precise_real_ticks = 0;
        }

        // The radii are absolute metres, so precision only means anything
        // at range: 5 cm off-centre at 3 m is any human pressing fire in a
        // melee scrum (serum claw!), at 30 m it's an aimbot.
        if aim.range < REAL_MIN_RANGE {
            self.precise_real_ticks = 0;
            return None;
        }

        if aim.distance <= REAL_TRACK_RADIUS {
            self.precise_real_ticks = self.precise_real_ticks.saturating_add(1);
            if self.precise_real_ticks >= 16 && obs.now.is_multiple_of(8) {
                self.real_suspicion += 4;
            }
        } else {
            self.precise_real_ticks = 0;
        }

        if obs.fire_edge && aim.distance <= REAL_FIRE_RADIUS {
            let target_age = obs.now.saturating_sub(self.real_target_since);
            let fast_lock = target_age <= 6;
            let very_precise = aim.distance <= REAL_TRACK_RADIUS;
            let clean_snap = obs.yaw_delta.max(obs.pitch_delta) >= 0.14;
            let mut add = 40;
            if fast_lock {
                add += 10;
            }
            if very_precise {
                add += 10;
            }
            if clean_snap {
                add += 8;
            }
            self.real_suspicion += add;
            tracing::warn!(
                id = self.player_id,
                name = %self.name,
                target = aim.id,
                distance = aim.distance,
                target_age,
                suspicion = self.real_suspicion,
                "anti-cheat real-target fire"
            );
        }

        if self.real_suspicion >= REAL_SUSPICION_KICK {
            Some(format!(
                "{} showed inhuman real-target aim on {} (suspicion={})",
                self.name, aim.id, self.real_suspicion
            ))
        } else {
            None
        }
    }

    fn best_real_target_aim(&self, origin: Vec3, yaw: f32, pitch: f32) -> Option<RealAim> {
        self.last_real_players
            .iter()
            .filter(|p| p.id != self.player_id && p.respawn_in == 0)
            .filter_map(|p| {
                let center = player_pos(p) + Vec3::new(0.0, TARGET_HEIGHT, 0.0);
                let distance = aim_distance_to_point(origin, yaw, pitch, center)?;
                Some(RealAim {
                    id: p.id,
                    distance,
                    range: (center - origin).length(),
                })
            })
            .min_by(|a, b| a.distance.total_cmp(&b.distance))
    }

    fn nearest_real_aim_distance(&self, origin: Vec3, yaw: f32, pitch: f32) -> f32 {
        self.last_real_players
            .iter()
            .filter(|p| p.id != self.player_id && p.respawn_in == 0)
            .filter_map(|p| {
                aim_distance_to_point(
                    origin,
                    yaw,
                    pitch,
                    player_pos(p) + Vec3::new(0.0, TARGET_HEIGHT, 0.0),
                )
            })
            .fold(f32::INFINITY, f32::min)
    }

    /// True when walls block the line from my eye to EVERY part of the
    /// target hull. Probes may only spoof fully occluded targets: a
    /// wallhack tracks them anyway (that's the tell), while an honest
    /// client never renders the swap. Centre-only testing still flashed
    /// enemies peeking over half-height cover.
    fn occluded(&self, me: &PlayerState, target: &PlayerState) -> bool {
        let eye = player_pos(me) + Vec3::new(0.0, EYE_HEIGHT, 0.0);
        !sim::weapon::player_hull_visible(
            &self.arena,
            &[],
            eye,
            player_pos(target),
            target.crouching,
        )
    }

    fn choose_probe_target<'a>(
        &self,
        snapshot: &'a Snapshot,
        me: &PlayerState,
    ) -> Option<&'a PlayerState> {
        let me_pos = player_pos(me);
        snapshot
            .players
            .iter()
            .filter(|p| p.id != self.player_id && p.id < protocol::MAX_PLAYERS as u8)
            .filter(|p| p.respawn_in == 0)
            .filter(|p| self.occluded(me, p))
            .min_by(|a, b| {
                let da = (player_pos(a) - me_pos).length_squared();
                let db = (player_pos(b) - me_pos).length_squared();
                da.total_cmp(&db)
            })
    }

    fn expire_probe(&mut self, tick: u64) {
        if self
            .probe
            .as_ref()
            .is_some_and(|probe| tick > probe.expires_tick)
        {
            self.probe = None;
        }
    }

    fn decay(&mut self, tick: u64) {
        if tick.is_multiple_of(common::DEFAULT_TICK_HZ as u64) {
            self.suspicion = (self.suspicion - 2).max(0);
            self.real_suspicion = (self.real_suspicion - 8).max(0);
        }
    }
}

struct RealAim {
    id: PlayerId,
    distance: f32,
    range: f32,
}

struct RealTargetObservation {
    /// Server world tick captured at receive time (not `input.tick`).
    now: u64,
    yaw: f32,
    pitch: f32,
    yaw_delta: f32,
    pitch_delta: f32,
    fire_edge: bool,
    origin: Vec3,
}

fn player_pos(p: &PlayerState) -> Vec3 {
    Vec3::new(p.pos.x.to_f32(), p.pos.y.to_f32(), p.pos.z.to_f32())
}

/// Blank the over-disclosed combat fields on an enemy view. The renderer
/// only needs presence, pose, and the visible weapon model; exact
/// health/armor/ammo and powerup timers are bot fuel, so zero them.
fn neutralize_combat(p: &mut PlayerState) {
    p.health = 0;
    p.armor = 0;
    p.ammo = 0;
    p.quad_ticks = 0;
    p.haste_ticks = 0;
    p.serum_ticks = 0;
}

fn protocol_vec3(v: Vec3) -> protocol::Vec3 {
    protocol::Vec3 {
        x: protocol::Fp::from_f32(v.x),
        y: protocol::Fp::from_f32(v.y),
        z: protocol::Fp::from_f32(v.z),
    }
}

/// Place the spoofed probe target behind the real one. The lateral offset is
/// drawn from the server-seeded RNG (recorded in the replay header, so this
/// reproduces on playback) rather than the client tick — a wallhack tracks
/// wherever we put it, but the client cannot predict where that is.
fn probe_pos_behind_target(
    me: &PlayerState,
    target: &PlayerState,
    rng: &mut common::DetRng,
) -> Vec3 {
    let me_pos = player_pos(me);
    let target_pos = player_pos(target);
    let away = (target_pos - me_pos)
        .try_normalize()
        .unwrap_or(Vec3::new(0.0, 0.0, 1.0));
    let side = Vec3::new(-away.z, 0.0, away.x);
    let side_sign = if rng.u64() & 1 == 0 { 1.0 } else { -1.0 };
    // 0.95..=1.40 m of lateral offset, quantized from the RNG.
    let side_dist = 0.95 + (rng.range_i32(0, 451) as f32) / 1000.0;
    target_pos + away * 0.75 + side * (side_sign * side_dist)
}

/// Keep randomized wallhack probes behind actual cover. The target's real
/// hull is already known to be occluded when this is called; if every sampled
/// offset peeks around an edge, its real position is the safe fallback.
fn concealed_probe_position(
    arena: &sim::Arena,
    me: &PlayerState,
    target: &PlayerState,
    rng: &mut common::DetRng,
) -> Vec3 {
    let eye = player_pos(me) + Vec3::new(0.0, EYE_HEIGHT, 0.0);
    for _ in 0..8 {
        let candidate = probe_pos_behind_target(me, target, rng);
        if !sim::weapon::player_hull_visible(arena, &[], eye, candidate, target.crouching) {
            return candidate;
        }
    }
    player_pos(target)
}

fn aim_distance_to_point(origin: Vec3, yaw: f32, pitch: f32, point: Vec3) -> Option<f32> {
    let dir = look_dir(yaw, pitch);
    let to_probe = point - origin;
    let t = to_probe.dot(dir);
    if !(0.0..=RAIL_RANGE).contains(&t) {
        return None;
    }
    let closest = origin + dir * t;
    Some(closest.distance(point))
}

fn look_dir(yaw: f32, pitch: f32) -> Vec3 {
    let (sy, cy) = yaw.sin_cos();
    let (sp, cp) = pitch.sin_cos();
    Vec3::new(-sy * cp, sp, -cy * cp).normalize()
}

fn angle_delta(a: f32, b: f32) -> f32 {
    let tau = std::f32::consts::TAU;
    ((b - a + std::f32::consts::PI).rem_euclid(tau)) - std::f32::consts::PI
}

#[cfg(test)]
#[path = "net_tests.rs"]
mod tests;
