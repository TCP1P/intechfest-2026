//! Per-player input/fire audit of a replay: how many inputs arrived, how
//! many FIRE press edges the client actually sent, and how many shots the
//! sim granted. Splits "client pressed less" from "server ate presses".
//!
//!   cargo run -p replay --example fire_audit -- replays/fleet/m0/run-*.mrpl

use protocol::{Buttons, GameEvent};
use replay::{Reader, ReplayEvent};
use std::collections::HashMap;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args().nth(1).expect("usage: fire_audit <replay>");
    let mut r = Reader::open(&path).await?;

    let mut names: HashMap<u8, String> = HashMap::new();
    let mut inputs: HashMap<u8, u64> = HashMap::new();
    let mut edges: HashMap<u8, u64> = HashMap::new();
    let mut fires: HashMap<u8, u64> = HashMap::new();
    let mut held: HashMap<u8, bool> = HashMap::new();
    // inputs since the last snapshot: only the LAST reaches the sim
    let mut pending: HashMap<u8, Vec<bool>> = HashMap::new();
    let mut overwritten_press: HashMap<u8, u64> = HashMap::new();
    let mut double_ticks: HashMap<u8, u64> = HashMap::new();

    while let Some(ev) = r.read_event().await? {
        match ev {
            ReplayEvent::PlayerJoin { id, name, .. } => {
                names.insert(id, name);
            }
            ReplayEvent::Input { id, input } => {
                *inputs.entry(id).or_default() += 1;
                let f = input.buttons.contains(Buttons::FIRE);
                let was = held.insert(id, f).unwrap_or(false);
                if f && !was {
                    *edges.entry(id).or_default() += 1;
                }
                pending.entry(id).or_default().push(f && !was);
            }
            ReplayEvent::Snapshot(snap) => {
                for (id, v) in pending.iter_mut() {
                    if v.len() > 1 {
                        *double_ticks.entry(*id).or_default() += 1;
                        // every queued edge except the final input is lost
                        for lost in &v[..v.len() - 1] {
                            if *lost {
                                *overwritten_press.entry(*id).or_default() += 1;
                            }
                        }
                    }
                    v.clear();
                }
                // one press = one pull; the shotgun emits a Fire event per
                // pellet, so count distinct (snapshot, shooter) pairs
                let mut pulled: Vec<u8> = Vec::new();
                for ev in &snap.events {
                    if let GameEvent::Fire { shooter, .. } = ev {
                        if !pulled.contains(shooter) {
                            pulled.push(*shooter);
                            *fires.entry(*shooter).or_default() += 1;
                        }
                    }
                }
            }
            _ => {}
        }
    }

    let mut ids: Vec<u8> = names.keys().copied().collect();
    ids.sort();
    println!(
        "{:10} {:>8} {:>8} {:>8} {:>9} {:>10} {:>9}",
        "name", "inputs", "edges", "fires", "granted%", "dbl_ticks", "lostpress"
    );
    for id in ids {
        let e = edges.get(&id).copied().unwrap_or(0);
        let f = fires.get(&id).copied().unwrap_or(0);
        println!(
            "{:10} {:>8} {:>8} {:>8} {:>8.1}% {:>10} {:>9}",
            names[&id],
            inputs.get(&id).copied().unwrap_or(0),
            e,
            f,
            if e > 0 {
                f as f64 / e as f64 * 100.0
            } else {
                0.0
            },
            double_ticks.get(&id).copied().unwrap_or(0),
            overwritten_press.get(&id).copied().unwrap_or(0),
        );
    }
    Ok(())
}
