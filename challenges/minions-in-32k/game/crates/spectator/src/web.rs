use crate::ArenaName;
use anyhow::{Context, Result};
use protocol::{Fp, GameEvent, PlayerId, PlayerState, ScenarioKind, Snapshot};
use replay::{Reader, ReplayEvent};
use serde::Serialize;
use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Semaphore;

/// Cap on concurrent live connections; protects against slowloris fan-out.
const MAX_CONNECTIONS: usize = 64;
/// Cap on concurrent `/api/replay` streams (each opens + walks a replay file).
const MAX_REPLAY_STREAMS: usize = 4;
/// Whole-request read budget. Beyond this we give up rather than buffer forever.
const MAX_HEADER_BYTES: usize = 16 * 1024;
/// Time allowed for a client to send its (small) request line + headers.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
/// Hard cap on frames materialized into a single `/api/replay` response.
/// Bounds payload size; the viewer tolerates a truncated frame list.
const MAX_FRAMES: usize = 36_000;

struct ReplayEntry {
    /// URL id: bucket-relative path without the `.mrpl` extension.
    id: String,
    path: PathBuf,
    mtime: std::time::SystemTime,
}

#[derive(Serialize)]
struct ReplayListItem {
    id: String,
}

struct AppState {
    /// Bucket directory served (export only writes finalized replays, so every
    /// file under it is viewable). In single-file mode this is the file's
    /// parent and `single` names the one replay.
    root: PathBuf,
    single: Option<PathBuf>,
    arena: ArenaName,
    conns: Arc<Semaphore>,
    replay_streams: Semaphore,
}

impl AppState {
    /// Available replays, newest first. Re-scanned per request so matches that
    /// finish while the viewer is up appear without a restart.
    async fn list(&self) -> Vec<ReplayEntry> {
        if let Some(path) = &self.single {
            let id = path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("replay")
                .to_string();
            return vec![ReplayEntry {
                id,
                path: path.clone(),
                mtime: file_mtime(path).await,
            }];
        }
        let mut out = scan_bucket(&self.root).await;
        out.sort_by(|a, b| b.mtime.cmp(&a.mtime));
        out
    }

    /// Resolve a request's `?id=` to a replay path (default: newest). Only ever
    /// returns a pre-scanned entry, so `..` path traversal is impossible.
    async fn resolve(&self, id: Option<&str>) -> Option<PathBuf> {
        if let Some(path) = &self.single {
            return Some(path.clone());
        }
        let entries = self.list().await;
        match id {
            Some(id) => entries.into_iter().find(|e| e.id == id).map(|e| e.path),
            None => entries.into_iter().next().map(|e| e.path),
        }
    }
}

pub async fn serve(path: PathBuf, bind: SocketAddr, arena: ArenaName) -> Result<()> {
    // A directory is a replay bucket (only finalized replays ever land there).
    // A file is single-replay back-compat: refuse it if it is unfinalized (a
    // live, still-being-written match has no terminal MatchEnd event, and
    // spectating it would leak live positions).
    let single = if path.is_dir() {
        None
    } else {
        if !replay_finalized(&path).await? {
            anyhow::bail!("replay not finalized; live spectating is disabled");
        }
        Some(path.clone())
    };
    let root = if path.is_dir() {
        path.clone()
    } else {
        path.parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from("."))
    };
    let state = Arc::new(AppState {
        root,
        single,
        arena,
        conns: Arc::new(Semaphore::new(MAX_CONNECTIONS)),
        replay_streams: Semaphore::new(MAX_REPLAY_STREAMS),
    });
    let listener = TcpListener::bind(bind).await?;
    println!("spectator: http://{bind}/");
    loop {
        let (stream, _) = listener.accept().await?;
        let state = state.clone();
        // Drop the connection immediately if we are saturated.
        let Ok(permit) = state.conns.clone().try_acquire_owned() else {
            drop(stream);
            continue;
        };
        tokio::spawn(async move {
            let _permit = permit;
            if let Err(e) = handle_conn(stream, state).await {
                tracing::debug!(error = %e, "spectator http connection");
            }
        });
    }
}

async fn read_request_path(stream: &mut TcpStream) -> Result<String> {
    let mut buf = Vec::with_capacity(1024);
    let mut chunk = [0u8; 4096];
    loop {
        if buf.len() >= MAX_HEADER_BYTES {
            anyhow::bail!("request header too large");
        }
        let n = stream.read(&mut chunk).await?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
        if buf.windows(4).any(|w| w == b"\r\n\r\n") {
            break;
        }
    }
    let request = std::str::from_utf8(&buf).unwrap_or_default();
    Ok(request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("/")
        .to_string())
}

async fn handle_conn(mut stream: TcpStream, state: Arc<AppState>) -> Result<()> {
    let path = match tokio::time::timeout(REQUEST_TIMEOUT, read_request_path(&mut stream)).await {
        Ok(res) => res?,
        Err(_) => anyhow::bail!("request read timed out"),
    };
    let (route, query) = path.split_once('?').unwrap_or((path.as_str(), ""));
    match route {
        "/" | "/index.html" => {
            write_response(&mut stream, "text/html; charset=utf-8", INDEX_HTML).await
        }
        "/api/replays" => {
            let items: Vec<ReplayListItem> = state
                .list()
                .await
                .into_iter()
                .map(|e| ReplayListItem { id: e.id })
                .collect();
            let body = serde_json::to_string(&items).context("serialize replay list")?;
            write_response(&mut stream, "application/json", &body).await
        }
        "/api/replay" => {
            let Ok(_permit) = state.replay_streams.try_acquire() else {
                return write_too_many(&mut stream).await;
            };
            let id = query_param(query, "id");
            match state.resolve(id.as_deref()).await {
                Some(replay_path) => stream_replay(&mut stream, &state, &replay_path).await,
                None => write_not_found(&mut stream).await,
            }
        }
        p if p.starts_with("/assets/") => write_asset(&mut stream, route).await,
        p if p.starts_with("/vendor/") => write_vendor(&mut stream, route).await,
        _ => write_not_found(&mut stream).await,
    }
}

/// First `?key=value` match in a raw query string, percent-decoded.
fn query_param(query: &str, key: &str) -> Option<String> {
    query.split('&').find_map(|kv| {
        let (k, v) = kv.split_once('=')?;
        (k == key).then(|| percent_decode(v))
    })
}

fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 3 <= bytes.len() => match u8::from_str_radix(&s[i + 1..i + 3], 16) {
                Ok(b) => {
                    out.push(b);
                    i += 3;
                }
                Err(_) => {
                    out.push(b'%');
                    i += 1;
                }
            },
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// All `*.mrpl` files under `root` (recursive), as URL-id'd replay entries.
async fn scan_bucket(root: &Path) -> Vec<ReplayEntry> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(mut rd) = tokio::fs::read_dir(&dir).await else {
            continue;
        };
        while let Ok(Some(entry)) = rd.next_entry().await {
            let path = entry.path();
            let Ok(ft) = entry.file_type().await else {
                continue;
            };
            if ft.is_dir() {
                stack.push(path);
            } else if path.extension().and_then(|e| e.to_str()) == Some("mrpl") {
                let mtime = file_mtime(&path).await;
                let rel = path.strip_prefix(root).unwrap_or(&path).with_extension("");
                let id = rel.to_string_lossy().replace('\\', "/");
                out.push(ReplayEntry { id, path, mtime });
            }
        }
    }
    out
}

async fn file_mtime(path: &Path) -> std::time::SystemTime {
    match tokio::fs::metadata(path).await.and_then(|m| m.modified()) {
        Ok(t) => t,
        Err(_) => std::time::UNIX_EPOCH,
    }
}

/// True if the replay contains a terminal `MatchEnd` (used for single-file mode
/// only; bucket files are finalized by construction).
async fn replay_finalized(path: &Path) -> Result<bool> {
    let mut reader = Reader::open(path).await.context("open replay")?;
    while let Some(event) = reader.read_event().await.context("scan replay")? {
        if matches!(event, ReplayEvent::MatchEnd { .. }) {
            return Ok(true);
        }
    }
    Ok(false)
}

async fn write_response(stream: &mut TcpStream, content_type: &str, body: &str) -> Result<()> {
    let header = format!(
        "HTTP/1.1 200 OK\r\ncontent-type: {content_type}\r\ncontent-length: {}\r\ncache-control: no-store\r\nconnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(header.as_bytes()).await?;
    stream.write_all(body.as_bytes()).await?;
    Ok(())
}

async fn write_binary_response(
    stream: &mut TcpStream,
    content_type: &str,
    body: &[u8],
) -> Result<()> {
    let header = format!(
        "HTTP/1.1 200 OK\r\ncontent-type: {content_type}\r\ncontent-length: {}\r\ncache-control: no-store\r\nconnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(header.as_bytes()).await?;
    stream.write_all(body).await?;
    Ok(())
}

async fn write_too_many(stream: &mut TcpStream) -> Result<()> {
    let body = "too many requests";
    let header = format!(
        "HTTP/1.1 429 Too Many Requests\r\ncontent-type: text/plain\r\nretry-after: 1\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(header.as_bytes()).await?;
    stream.write_all(body.as_bytes()).await?;
    Ok(())
}

async fn write_not_found(stream: &mut TcpStream) -> Result<()> {
    let body = "not found";
    let header = format!(
        "HTTP/1.1 404 Not Found\r\ncontent-type: text/plain\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(header.as_bytes()).await?;
    stream.write_all(body.as_bytes()).await?;
    Ok(())
}

async fn write_asset(stream: &mut TcpStream, request_path: &str) -> Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../assets");
    write_static_file(stream, request_path, "/assets/", &root).await
}

async fn write_vendor(stream: &mut TcpStream, request_path: &str) -> Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/vendor");
    write_static_file(stream, request_path, "/vendor/", &root).await
}

async fn write_static_file(
    stream: &mut TcpStream,
    request_path: &str,
    prefix: &str,
    root: &Path,
) -> Result<()> {
    let path = request_path.split('?').next().unwrap_or(request_path);
    let Some(relative) = path.strip_prefix(prefix) else {
        return write_not_found(stream).await;
    };
    if relative.is_empty()
        || relative
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
        || relative.contains('\\')
    {
        return write_not_found(stream).await;
    }

    let file_path = root.join(relative);
    let Ok(bytes) = tokio::fs::read(&file_path).await else {
        return write_not_found(stream).await;
    };
    write_binary_response(stream, mime_for(relative), &bytes).await
}

fn mime_for(path: &str) -> &'static str {
    match Path::new(path).extension().and_then(|e| e.to_str()) {
        Some("glb") => "model/gltf-binary",
        Some("gltf") => "model/gltf+json",
        Some("js") => "text/javascript; charset=utf-8",
        Some("json") => "application/json",
        Some("png") => "image/png",
        Some("jpg") | Some("jpeg") => "image/jpeg",
        _ => "application/octet-stream",
    }
}

/// Stream the replay as JSON without ever holding the whole file (or whole
/// serialized payload) in memory. We emit the meta prefix, then one frame's
/// JSON at a time straight onto the socket via HTTP chunked encoding, reusing a
/// single scratch buffer. Resident memory is bounded to ~one frame; the total
/// number of frames emitted is capped to bound payload size.
async fn stream_replay(stream: &mut TcpStream, state: &AppState, replay_path: &Path) -> Result<()> {
    let mut reader = Reader::open(replay_path).await?;
    let arena = match state.arena {
        ArenaName::Minimal => sim::Arena::phase2_minimal(),
        ArenaName::GruLab => sim::Arena::gru_lab(),
        ArenaName::GruLabV3 => sim::Arena::gru_lab_v3(),
    };
    let meta = ReplayMeta {
        source: replay_path.display().to_string(),
        protocol_version: reader.header.protocol_version,
        tick_rate_hz: reader.header.tick_rate_hz,
        start_unix_ms: reader.header.start_unix_ms,
        arena: arena_json(state.arena, &arena),
    };

    let header = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncache-control: no-store\r\ntransfer-encoding: chunked\r\nconnection: close\r\n\r\n";
    stream.write_all(header.as_bytes()).await?;

    // Open object: meta fields (without the surrounding braces) then `,"frames":[`.
    let meta_json = serde_json::to_string(&meta).context("serialize replay meta")?;
    let prefix = format!(
        "{},\"frames\":[",
        meta_json
            .strip_suffix('}')
            .and_then(|s| s.strip_prefix('{'))
            .unwrap_or("")
    );
    write_chunk(stream, prefix.as_bytes()).await?;

    let mut names = BTreeMap::<PlayerId, String>::new();
    let mut scratch = String::new();
    let mut count = 0usize;
    while count < MAX_FRAMES {
        let Some(event) = reader.read_event().await? else {
            break;
        };
        match event {
            ReplayEvent::PlayerJoin { id, name, .. } => {
                names.insert(id, name);
            }
            ReplayEvent::PlayerLeave { id, .. } => {
                names.remove(&id);
            }
            ReplayEvent::Snapshot(snapshot) => {
                let frame = frame_json(snapshot, &names);
                scratch.clear();
                if count > 0 {
                    scratch.push(',');
                }
                scratch.push_str(&serde_json::to_string(&frame).context("serialize frame")?);
                write_chunk(stream, scratch.as_bytes()).await?;
                count += 1;
            }
            ReplayEvent::Input { .. }
            | ReplayEvent::FlagFlip { .. }
            | ReplayEvent::PlayerDelay { .. }
            | ReplayEvent::ScoringWindowStarted { .. }
            | ReplayEvent::MatchEnd { .. } => {}
        }
    }

    write_chunk(stream, b"]}").await?;
    // Terminating zero-length chunk.
    stream.write_all(b"0\r\n\r\n").await?;
    Ok(())
}

async fn write_chunk(stream: &mut TcpStream, body: &[u8]) -> Result<()> {
    if body.is_empty() {
        return Ok(());
    }
    let size = format!("{:x}\r\n", body.len());
    stream.write_all(size.as_bytes()).await?;
    stream.write_all(body).await?;
    stream.write_all(b"\r\n").await?;
    Ok(())
}

#[derive(Serialize)]
struct ReplayMeta {
    source: String,
    protocol_version: u16,
    tick_rate_hz: u16,
    start_unix_ms: u64,
    arena: ArenaJson,
}

#[derive(Serialize)]
struct ArenaJson {
    name: &'static str,
    floor_y: f32,
    ceiling_y: f32,
    walls: Vec<BoxJson>,
    zones: Vec<ZoneJson>,
    powerups: Vec<PowerupJson>,
}

#[derive(Serialize)]
struct BoxJson {
    min_x: f32,
    min_z: f32,
    max_x: f32,
    max_z: f32,
}

#[derive(Serialize)]
struct ZoneJson {
    name: &'static str,
    bounds: BoxJson,
}

#[derive(Serialize)]
struct PowerupJson {
    kind: &'static str,
    x: f32,
    z: f32,
    radius: f32,
}

#[derive(Serialize)]
struct FrameJson {
    tick: u64,
    players: Vec<PlayerJson>,
    confirmations: Vec<ConfirmationJson>,
    events: Vec<EventJson>,
}

#[derive(Serialize)]
struct ConfirmationJson {
    id: u16,
    killer: PlayerId,
    victim: PlayerId,
    x: f32,
    y: f32,
    z: f32,
    expires_in: u16,
}

#[derive(Serialize)]
struct PlayerJson {
    id: PlayerId,
    name: String,
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
    pitch: f32,
    health: u8,
    respawn_in: u16,
    crouching: bool,
}

#[derive(Serialize)]
struct EventJson {
    kind: &'static str,
    text: String,
    a: Option<PlayerId>,
    b: Option<PlayerId>,
}

fn arena_json(name: ArenaName, arena: &sim::Arena) -> ArenaJson {
    ArenaJson {
        name: match name {
            ArenaName::Minimal => "minimal",
            ArenaName::GruLab => "gru-lab",
            ArenaName::GruLabV3 => "gru-lab-v3",
        },
        floor_y: arena.floor_y,
        ceiling_y: arena.ceiling_y,
        walls: arena
            .walls
            .iter()
            .map(|w| BoxJson {
                min_x: w.min.x,
                min_z: w.min.z,
                max_x: w.max.x,
                max_z: w.max.z,
            })
            .collect(),
        zones: arena
            .zones
            .iter()
            .map(|z| ZoneJson {
                name: z.name,
                bounds: BoxJson {
                    min_x: z.bounds.min.x,
                    min_z: z.bounds.min.z,
                    max_x: z.bounds.max.x,
                    max_z: z.bounds.max.z,
                },
            })
            .collect(),
        powerups: arena
            .powerups
            .iter()
            .map(|p| PowerupJson {
                kind: p.kind.slug(),
                x: p.center.x,
                z: p.center.z,
                radius: p.radius,
            })
            .collect(),
    }
}

fn frame_json(snapshot: Snapshot, names: &BTreeMap<PlayerId, String>) -> FrameJson {
    let players = snapshot
        .players
        .iter()
        .map(|p| player_json(p, names))
        .collect();
    let events = snapshot
        .events
        .iter()
        .map(|e| event_json(e, names))
        .collect();
    let confirmations = snapshot
        .confirmations
        .iter()
        .map(|confirmation| ConfirmationJson {
            id: confirmation.id,
            killer: confirmation.killer,
            victim: confirmation.victim,
            x: fp(confirmation.pos.x),
            y: fp(confirmation.pos.y),
            z: fp(confirmation.pos.z),
            expires_in: confirmation.expires_in,
        })
        .collect();
    FrameJson {
        tick: snapshot.tick,
        players,
        confirmations,
        events,
    }
}

fn player_json(p: &PlayerState, names: &BTreeMap<PlayerId, String>) -> PlayerJson {
    PlayerJson {
        id: p.id,
        name: names
            .get(&p.id)
            .cloned()
            .unwrap_or_else(|| protocol::fallback_name(p.id)),
        x: fp(p.pos.x),
        y: fp(p.pos.y),
        z: fp(p.pos.z),
        yaw: fp(p.yaw),
        pitch: fp(p.pitch),
        health: p.health,
        respawn_in: p.respawn_in,
        crouching: p.crouching,
    }
}

fn event_json(event: &GameEvent, names: &BTreeMap<PlayerId, String>) -> EventJson {
    match *event {
        GameEvent::Fire { shooter, hit, .. } => EventJson {
            kind: "fire",
            text: match hit {
                Some(victim) => format!("{} hit {}", label(shooter, names), label(victim, names)),
                None => format!("{} fired", label(shooter, names)),
            },
            a: Some(shooter),
            b: hit,
        },
        GameEvent::Explosion { .. } => EventJson {
            kind: "explosion",
            text: "boom".into(),
            a: None,
            b: None,
        },
        GameEvent::Flame { shooter, .. } => EventJson {
            kind: "flame",
            text: format!("{} is flaming", label(shooter, names)),
            a: Some(shooter),
            b: None,
        },
        GameEvent::Damage {
            attacker,
            victim,
            amount,
        } => EventJson {
            kind: "damage",
            text: format!(
                "{} dealt {} to {}",
                label(attacker, names),
                amount,
                label(victim, names)
            ),
            a: Some(attacker),
            b: Some(victim),
        },
        GameEvent::Kill { attacker, victim } => EventJson {
            kind: "kill",
            text: format!(
                "{} eliminated {}",
                label(attacker, names),
                label(victim, names)
            ),
            a: Some(attacker),
            b: Some(victim),
        },
        GameEvent::Spawn { id, .. } => EventJson {
            kind: "spawn",
            text: format!("{} spawned", label(id, names)),
            a: Some(id),
            b: None,
        },
        GameEvent::PowerupPickup { id, kind } => EventJson {
            kind: "powerup",
            text: format!("{} picked up {}", label(id, names), kind.slug()),
            a: Some(id),
            b: None,
        },
        GameEvent::ScenarioState { kind, active } => EventJson {
            kind: "scenario",
            text: format!(
                "{} {}",
                scenario_name(kind),
                if active { "started" } else { "ended" }
            ),
            a: None,
            b: None,
        },
        GameEvent::ScenarioDamage { id, kind, amount } => EventJson {
            kind: "scenario_damage",
            text: format!(
                "{} took {} from {}",
                label(id, names),
                amount,
                scenario_name(kind)
            ),
            a: Some(id),
            b: None,
        },
        GameEvent::ScenarioKill { id, kind } => EventJson {
            kind: "scenario_kill",
            text: format!("{} died to {}", label(id, names), scenario_name(kind)),
            a: Some(id),
            b: None,
        },
        GameEvent::ConfirmationDropped { killer, victim, .. } => EventJson {
            kind: "confirmation_dropped",
            text: format!(
                "{} dropped {}'s banana",
                label(killer, names),
                label(victim, names)
            ),
            a: Some(killer),
            b: Some(victim),
        },
        GameEvent::KillConfirmed { killer, victim, .. } => EventJson {
            kind: "kill_confirmed",
            text: format!(
                "{} confirmed a kill on {}",
                label(killer, names),
                label(victim, names)
            ),
            a: Some(killer),
            b: Some(victim),
        },
        GameEvent::KillDenied { killer, victim, .. } => EventJson {
            kind: "kill_denied",
            text: format!(
                "{} denied {}'s kill",
                label(victim, names),
                label(killer, names)
            ),
            a: Some(victim),
            b: Some(killer),
        },
        GameEvent::ConfirmationExpired { killer, victim, .. } => EventJson {
            kind: "confirmation_expired",
            text: format!(
                "{}'s banana for {} expired",
                label(killer, names),
                label(victim, names)
            ),
            a: Some(killer),
            b: Some(victim),
        },
    }
}

fn label(id: PlayerId, names: &BTreeMap<PlayerId, String>) -> String {
    names
        .get(&id)
        .cloned()
        .unwrap_or_else(|| protocol::fallback_name(id))
}

fn fp(v: Fp) -> f32 {
    v.0 as f32 / 65_536.0
}

fn scenario_name(kind: ScenarioKind) -> &'static str {
    match kind {
        ScenarioKind::KitchenLava => "kitchen lava",
    }
}

const INDEX_HTML: &str = include_str!("index.html");
