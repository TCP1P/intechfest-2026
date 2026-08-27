//! Web spectator backend + replay CLI.
//!
//! Phase 2 ships `verify` with deterministic replay: it re-simulates the
//! recorded inputs and diffs each tick against the stored snapshot. Phase 7
//! adds a small offline replay viewer for VOD scrubbing.

mod web;

use anyhow::Result;
use clap::{Parser, Subcommand};
use protocol::{GameEvent, PlayerId};
use replay::{Reader, ReplayEvent};
use std::collections::{BTreeMap, BTreeSet};
use std::net::SocketAddr;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(version, about)]
struct Args {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand, Debug)]
enum Cmd {
    /// Print a replay summary and re-simulate it against the recorded snapshots.
    Verify {
        path: PathBuf,
        /// Don't diff against recorded snapshots; just count events.
        #[arg(long)]
        no_sim: bool,
        /// Arena the replay was recorded against.
        #[arg(long, default_value = "gru-lab-v3")]
        arena: ArenaName,
        /// Stop after N divergent ticks.
        #[arg(long, default_value_t = 10)]
        max_divergences: u64,
    },
    /// Serve the web replay viewer. `path` is a replay bucket directory
    /// (serves every finalized replay under it, with a picker) or a single
    /// finalized `.mrpl` file. Live matches are never viewable.
    Serve {
        path: PathBuf,
        /// HTTP bind address.
        #[arg(long, default_value = "127.0.0.1:8080")]
        bind: SocketAddr,
        /// Arena the replay was recorded against.
        #[arg(long, default_value = "gru-lab-v3")]
        arena: ArenaName,
    },
    /// Print kill/death leaderboard from replay snapshot events.
    Leaderboard { path: PathBuf },
}

#[derive(Clone, Copy, Debug)]
pub enum ArenaName {
    Minimal,
    GruLab,
    GruLabV3,
}

impl std::str::FromStr for ArenaName {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "minimal" => Ok(Self::Minimal),
            "gru-lab" | "grulab" | "gru_lab" => Ok(Self::GruLab),
            "gru-lab-v3" | "gru_lab_v3" | "v3" => Ok(Self::GruLabV3),
            other => Err(format!("unknown arena '{other}'")),
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    match args.cmd {
        Cmd::Verify {
            path,
            no_sim,
            arena,
            max_divergences,
        } => verify(path, no_sim, arena, max_divergences).await,
        Cmd::Serve { path, bind, arena } => web::serve(path, bind, arena).await,
        Cmd::Leaderboard { path } => leaderboard(path).await,
    }
}

async fn verify(path: PathBuf, no_sim: bool, arena: ArenaName, max_divergences: u64) -> Result<()> {
    let mut r = Reader::open(&path).await?;
    println!("path:            {}", path.display());
    println!("format_version:  {}", r.header.format_version);
    println!("protocol:        {}", r.header.protocol_version);
    println!("tick_rate_hz:    {}", r.header.tick_rate_hz);
    println!("seed:            0x{:016x}", r.header.seed);
    println!("start_unix_ms:   {}", r.header.start_unix_ms);

    let mut world = if no_sim {
        None
    } else {
        let a = match arena {
            ArenaName::Minimal => sim::Arena::phase2_minimal(),
            ArenaName::GruLab => sim::Arena::gru_lab(),
            ArenaName::GruLabV3 => sim::Arena::gru_lab_v3(),
        };
        Some(sim::World::with_seed(
            a,
            r.header.tick_rate_hz,
            r.header.seed,
        ))
    };

    let mut joins = 0u64;
    let mut leaves = 0u64;
    let mut inputs = 0u64;
    let mut flags = 0u64;
    let mut snapshots = 0u64;
    let mut compared = 0u64;
    let mut divergences = 0u64;
    let mut last_tick = 0u64;

    while let Some(ev) = r.read_event().await? {
        match ev {
            ReplayEvent::PlayerJoin { id, name, .. } => {
                joins += 1;
                if let Some(w) = world.as_mut() {
                    let assigned = w.alloc(&name).ok();
                    if assigned != Some(id) {
                        eprintln!(
                            "join divergence: replay id={id} name={name} sim_assigned={:?}",
                            assigned
                        );
                    }
                }
            }
            ReplayEvent::PlayerLeave { id, .. } => {
                leaves += 1;
                if let Some(w) = world.as_mut() {
                    w.remove(id);
                }
            }
            ReplayEvent::Input { id, input } => {
                inputs += 1;
                last_tick = last_tick.max(input.tick);
                if let Some(w) = world.as_mut() {
                    w.apply_input(id, input);
                }
            }
            ReplayEvent::FlagFlip { key, value, .. } => {
                flags += 1;
                if let Some(w) = world.as_mut() {
                    if let Some(value) = parse_bool(&value) {
                        w.set_feature_flag(&key, value);
                    }
                }
            }
            ReplayEvent::PlayerDelay { id, ticks, .. } => {
                if let Some(w) = world.as_mut() {
                    w.set_player_delay(id, ticks);
                }
            }
            ReplayEvent::ScoringWindowStarted { .. } => {
                if let Some(w) = world.as_mut() {
                    w.discard_pending_confirmations();
                }
            }
            ReplayEvent::MatchEnd { .. } => {}
            ReplayEvent::Snapshot(expected) => {
                snapshots += 1;
                last_tick = last_tick.max(expected.tick);
                if let Some(w) = world.as_mut() {
                    let actual = w.advance();
                    compared += 1;
                    if actual != expected {
                        divergences += 1;
                        if divergences <= max_divergences {
                            eprintln!(
                                "tick {}: divergence\n  expected: {:?}\n  actual:   {:?}",
                                expected.tick, expected, actual
                            );
                        }
                    }
                }
            }
        }
    }
    println!(
        "events: joins={joins} leaves={leaves} inputs={inputs} flag_flips={flags} snapshots={snapshots}"
    );
    println!("last_tick:       {last_tick}");
    if world.is_some() {
        println!("compared:        {compared}");
        println!(
            "divergences:     {divergences} ({})",
            if divergences == 0 {
                "deterministic"
            } else {
                "non-deterministic"
            }
        );
    }
    Ok(())
}

fn parse_bool(value: &str) -> Option<bool> {
    match value {
        "true" | "1" | "yes" | "on" => Some(true),
        "false" | "0" | "no" | "off" => Some(false),
        _ => None,
    }
}

async fn leaderboard(path: PathBuf) -> Result<()> {
    let mut r = Reader::open(&path).await?;
    let tick_hz = r.header.tick_rate_hz.max(1) as u64;
    let mut names = BTreeMap::<PlayerId, String>::new();
    let mut seen = BTreeSet::<PlayerId>::new();
    let mut kills = BTreeMap::<PlayerId, u32>::new();
    let mut deaths = BTreeMap::<PlayerId, u32>::new();
    let mut shots = BTreeMap::<PlayerId, u32>::new();
    let mut hits = BTreeMap::<PlayerId, u32>::new();
    let mut dmg_dealt = BTreeMap::<PlayerId, u32>::new();
    let mut dmg_taken = BTreeMap::<PlayerId, u32>::new();
    let mut powerups = BTreeMap::<PlayerId, u32>::new();
    let mut last_tick = 0u64;

    while let Some(ev) = r.read_event().await? {
        match ev {
            ReplayEvent::PlayerJoin { id, name, .. } => {
                names.insert(id, name);
                seen.insert(id);
            }
            ReplayEvent::PlayerLeave { id, .. } => {
                seen.insert(id);
            }
            ReplayEvent::Input { input, .. } => {
                last_tick = last_tick.max(input.tick);
            }
            ReplayEvent::FlagFlip { tick, .. } => {
                last_tick = last_tick.max(tick);
            }
            ReplayEvent::PlayerDelay { tick, .. } => {
                last_tick = last_tick.max(tick);
            }
            ReplayEvent::ScoringWindowStarted { tick } => {
                last_tick = last_tick.max(tick);
            }
            ReplayEvent::MatchEnd { tick } => {
                last_tick = last_tick.max(tick);
            }
            ReplayEvent::Snapshot(snapshot) => {
                last_tick = last_tick.max(snapshot.tick);
                for player in &snapshot.players {
                    seen.insert(player.id);
                }
                for event in snapshot.events {
                    if let Some((attacker, victim)) = event.frag_death() {
                        if let Some(attacker) = attacker {
                            seen.insert(attacker);
                            *kills.entry(attacker).or_default() += 1;
                        }
                        seen.insert(victim);
                        *deaths.entry(victim).or_default() += 1;
                    }
                    match event {
                        GameEvent::Fire { shooter, hit, .. } => {
                            *shots.entry(shooter).or_default() += 1;
                            if hit.is_some() {
                                *hits.entry(shooter).or_default() += 1;
                            }
                        }
                        GameEvent::Damage {
                            attacker,
                            victim,
                            amount,
                        } => {
                            *dmg_dealt.entry(attacker).or_default() += amount as u32;
                            *dmg_taken.entry(victim).or_default() += amount as u32;
                        }
                        GameEvent::ScenarioDamage { id, amount, .. } => {
                            *dmg_taken.entry(id).or_default() += amount as u32;
                        }
                        GameEvent::PowerupPickup { id, .. } => {
                            *powerups.entry(id).or_default() += 1;
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    let mut rows = seen
        .into_iter()
        .map(|id| {
            let k = kills.get(&id).copied().unwrap_or_default();
            let d = deaths.get(&id).copied().unwrap_or_default();
            let name = names
                .get(&id)
                .cloned()
                .unwrap_or_else(|| protocol::fallback_name(id));
            (id, name, k, d)
        })
        .collect::<Vec<_>>();
    rows.sort_by(|a, b| {
        b.2.cmp(&a.2)
            .then_with(|| a.3.cmp(&b.3))
            .then_with(|| a.0.cmp(&b.0))
    });

    let secs = last_tick / tick_hz;
    let total_kills: u32 = rows.iter().map(|r| r.2).sum();
    println!("path:      {}", path.display());
    println!(
        "ticks:     {last_tick} ({}:{:02} at {tick_hz} Hz)",
        secs / 60,
        secs % 60
    );
    println!("players:   {}", rows.len());
    println!("kills:     {total_kills}");
    println!();
    println!(
        "{:<4} {:<12} {:>5} {:>6} {:>5} {:>6} {:>6} {:>6} {:>5} {:>3}",
        "rank", "name", "kills", "deaths", "kd", "dmg+", "dmg-", "shots", "acc%", "pw"
    );
    for (rank, (id, name, k, d)) in rows.iter().enumerate() {
        let sh = shots.get(id).copied().unwrap_or_default();
        let hi = hits.get(id).copied().unwrap_or_default();
        let acc = if sh > 0 { hi * 100 / sh } else { 0 };
        println!(
            "{:<4} {:<12} {:>5} {:>6} {:>5.2} {:>6} {:>6} {:>6} {:>5} {:>3}",
            rank + 1,
            name,
            k,
            d,
            *k as f32 / (*d).max(1) as f32,
            dmg_dealt.get(id).copied().unwrap_or_default(),
            dmg_taken.get(id).copied().unwrap_or_default(),
            sh,
            acc,
            powerups.get(id).copied().unwrap_or_default(),
        );
    }
    Ok(())
}
