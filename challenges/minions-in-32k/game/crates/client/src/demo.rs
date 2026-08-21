//! Offline demo mode: run a `sim::World` in-process, fill the rest of the
//! lobby with simple AI bots, and feed the same `NetIn` / `NetOut` channels
//! the remote QUIC path uses so the renderer doesn't know the difference.
//!
//! Default mode for the distributed client — and for the wasm page when no
//! server is in the URL. The sim is driven by a Bevy system with a fixed
//! 64 Hz accumulator (not a thread: wasm has none, and a system behaves
//! identically for an offline sim).

use crate::channels::{NetIn, NetLifecycle, NetLifecycleEvent, NetOut, NetStarter};
use crate::ClientArgs;
use bevy::prelude::*;
use common::FpExt;
use crossbeam_channel::{bounded, Receiver, Sender};
use glam::Vec3;
use protocol::{Buttons, ClientMessage, Fp, Input, PlayerId, ServerMessage, Tick};
use sim::{Arena, World};
use std::sync::{Arc, Mutex};

pub struct DemoPlugin {
    pub args: ClientArgs,
}

impl Plugin for DemoPlugin {
    fn build(&self, app: &mut App) {
        let (in_tx, in_rx) = bounded::<ServerMessage>(256);
        let (out_tx, out_rx) = bounded::<ClientMessage>(64);
        let (lifecycle_tx, lifecycle_rx) = bounded::<NetLifecycleEvent>(16);
        app.insert_resource(NetIn { rx: in_rx });
        app.insert_resource(NetOut { tx: out_tx });
        app.insert_resource(NetLifecycle { rx: lifecycle_rx });

        let start = Arc::new(Mutex::new(None::<u8>));
        let signal = start.clone();
        app.insert_resource(NetStarter::new(move |variant| {
            *signal.lock().unwrap() = Some(variant);
            let _ = lifecycle_tx.try_send(NetLifecycleEvent::Started);
            Ok(())
        }));
        app.insert_resource(DemoBridge {
            start,
            in_tx,
            out_rx: Mutex::new(out_rx),
            name: self.args.name.clone(),
        });
        app.add_systems(Update, demo_drive);
    }
}

const TICK_HZ: u16 = 64;
const NUM_BOTS: usize = 5;
const DUMMY_POS: Vec3 = Vec3::new(9.0, 0.0, 18.8);

#[derive(Resource)]
struct DemoBridge {
    start: Arc<Mutex<Option<u8>>>,
    in_tx: Sender<ServerMessage>,
    out_rx: Mutex<Receiver<ClientMessage>>,
    name: String,
}

#[derive(Resource)]
struct DemoSim {
    world: World,
    bots: Vec<Bot>,
    dummy_id: Option<PlayerId>,
    my_id: PlayerId,
    variant: u8,
    kills: [u16; protocol::MAX_PLAYERS],
    confirmed_kills: [u16; protocol::MAX_PLAYERS],
    deaths: [u16; protocol::MAX_PLAYERS],
    acc: f32,
}

fn make_world() -> World {
    // The arena ships embedded in the binary. Dev-only override: set
    // MINIONS_SCENES_DIR to a scenes dir (e.g. assets/scenes) to live-reload
    // editor output without a rebuild. Never implicit — a CWD-relative
    // default used to mix repo scenes with dist visuals.
    #[cfg(not(target_arch = "wasm32"))]
    if let Ok(dir) = std::env::var("MINIONS_SCENES_DIR") {
        match Arena::load_dir(&dir) {
            Ok(a) => {
                tracing::info!(dir, "arena live-loaded (dev override)");
                return World::new(a, TICK_HZ);
            }
            Err(e) => {
                tracing::warn!(dir, ?e, "scenes override unreadable; using embedded");
            }
        }
    }
    World::new(Arena::gru_lab_v3(), TICK_HZ)
}

fn demo_drive(
    time: Res<Time>,
    mut commands: Commands,
    bridge: Res<DemoBridge>,
    sim: Option<ResMut<DemoSim>>,
) {
    let Some(mut sim) = sim else {
        // not deployed yet: wait for the selection screen's variant pick
        let Some(variant) = bridge.start.lock().unwrap().take() else {
            return;
        };
        let mut world = make_world();
        let my_id = match world.alloc(&bridge.name) {
            Ok(id) => id,
            Err(_) => {
                tracing::error!("demo: no slot for the local player");
                return;
            }
        };
        let mut bots: Vec<Bot> = Vec::new();
        for i in 0..NUM_BOTS {
            let name = format!("bot_{i}");
            match world.alloc(&name) {
                Ok(id) => bots.push(Bot::new(id, i as u64)),
                Err(_) => break,
            }
        }
        let dummy_id = match world.alloc("target_dummy") {
            Ok(id) => {
                pin_dummy(&mut world, id, 0);
                Some(id)
            }
            Err(_) => None,
        };
        tracing::info!(me = my_id, bots = bots.len(), dummy = ?dummy_id, "demo world ready");
        let _ = bridge.in_tx.send(ServerMessage::Welcome {
            your_id: my_id,
            tick_rate_hz: TICK_HZ,
        });
        commands.insert_resource(DemoSim {
            world,
            bots,
            dummy_id,
            my_id,
            variant,
            kills: [0; protocol::MAX_PLAYERS],
            confirmed_kills: [0; protocol::MAX_PLAYERS],
            deaths: [0; protocol::MAX_PLAYERS],
            acc: 0.0,
        });
        return;
    };

    // fixed 64 Hz steps; clamp the debt so a hitch can't spiral
    let sim = &mut *sim;
    sim.acc = (sim.acc + time.delta_seconds()).min(0.25);
    let dt = 1.0 / TICK_HZ as f32;
    while sim.acc >= dt {
        sim.acc -= dt;
        {
            let rx = bridge.out_rx.lock().unwrap();
            for msg in rx.try_iter() {
                match msg {
                    ClientMessage::Input(input) => sim.world.apply_input(sim.my_id, input),
                    ClientMessage::Leave | ClientMessage::Join { .. } => {}
                }
            }
        }

        let target_pos = sim.world.players[sim.my_id as usize]
            .as_ref()
            .filter(|p| p.alive)
            .map(|p| p.pos);
        let current_tick = sim.world.tick;
        for bot in sim.bots.iter_mut() {
            let input = bot.think(&sim.world, target_pos, current_tick);
            sim.world.apply_input(bot.id, input);
        }
        if let Some(id) = sim.dummy_id {
            pin_dummy(&mut sim.world, id, current_tick);
        }

        let snap = sim.world.advance();
        for ev in &snap.events {
            if let Some((attacker, victim)) = ev.frag_death() {
                if let Some(a) = attacker {
                    sim.kills[a as usize] += 1;
                }
                sim.deaths[victim as usize] += 1;
            }
            if let protocol::GameEvent::KillConfirmed { killer, .. } = *ev {
                sim.confirmed_kills[killer as usize] += 1;
            }
        }
        if snap.tick.is_multiple_of(TICK_HZ as u64) {
            let roster: Vec<protocol::RosterEntry> = sim
                .world
                .players
                .iter()
                .enumerate()
                .filter_map(|(i, slot)| {
                    let p = slot.as_ref()?;
                    Some(protocol::RosterEntry {
                        id: i as PlayerId,
                        name: p.name.clone(),
                        variant: if i as PlayerId == sim.my_id {
                            sim.variant
                        } else {
                            (i % 4) as u8
                        },
                        rtt_ms: 0,
                        kills: sim.kills[i],
                        confirmed_kills: sim.confirmed_kills[i],
                        deaths: sim.deaths[i],
                        connected: true,
                    })
                })
                .collect();
            let _ = bridge.in_tx.send(ServerMessage::Roster(roster));
        }
        let _ = bridge.in_tx.send(ServerMessage::Snapshot(snap));
    }
}

fn pin_dummy(world: &mut World, id: PlayerId, tick: Tick) {
    let Some(Some(dummy)) = world.players.get_mut(id as usize) else {
        return;
    };
    dummy.pos = DUMMY_POS;
    dummy.vel = Vec3::ZERO;
    dummy.yaw = 0.0;
    dummy.pitch = 0.0;
    dummy.health = 100;
    dummy.alive = true;
    dummy.grounded = true;
    dummy.crouching = false;
    dummy.respawn_at = Tick::MAX;
    dummy.fire_ready_at = Tick::MAX;
    dummy.quad_until = 0;
    dummy.haste_until = 0;
    dummy.pending = Input {
        tick,
        buttons: Buttons::EMPTY,
        yaw: Fp::from_f32(0.0),
        pitch: Fp::from_f32(0.0),
    };
    dummy.prev_buttons = Buttons::EMPTY;
}

/// Very simple AI: seek the local player, strafe a bit, fire when in range.
struct Bot {
    id: PlayerId,
    strafe_until: Tick,
    strafe_dir: i8, // -1, 0, +1
    salt: u64,      // differs per bot so they don't move in lockstep
}

impl Bot {
    fn new(id: PlayerId, salt: u64) -> Self {
        Self {
            id,
            strafe_until: 0,
            strafe_dir: 0,
            salt,
        }
    }

    fn think(&mut self, world: &World, target: Option<Vec3>, tick: Tick) -> Input {
        let Some(me) = world.players[self.id as usize].as_ref() else {
            return Input::default();
        };
        if !me.alive {
            return Input::default();
        }

        let mut buttons = Buttons::EMPTY;
        let mut yaw_rad: f32 = me.yaw;

        if let Some(target) = target {
            let delta = target - me.pos;
            let flat = Vec3::new(delta.x, 0.0, delta.z);
            let dist = flat.length();

            // Forward is -Z at yaw=0: yaw = atan2(-dx, -dz).
            if dist > 0.01 {
                yaw_rad = (-flat.x).atan2(-flat.z);
            }

            if dist > 4.0 {
                buttons.insert(Buttons::FORWARD);
            }
            // Rail reaches 80 m; engage from half that to keep pressure up
            // across the bigger v2 arena.
            if dist < 50.0 {
                buttons.insert(Buttons::FIRE);
            }
        } else {
            // No target (we're dead or not joined yet): wander forward lazily.
            if tick % 128 < 64 {
                buttons.insert(Buttons::FORWARD);
            }
        }

        // Simple strafe that flips every ~0.75 s, offset per bot.
        if tick >= self.strafe_until {
            let pick = ((tick.wrapping_add(self.salt * 7)) % 3) as i64 - 1;
            self.strafe_dir = pick as i8;
            self.strafe_until = tick + 48;
        }
        match self.strafe_dir {
            d if d > 0 => buttons.insert(Buttons::RIGHT),
            d if d < 0 => buttons.insert(Buttons::LEFT),
            _ => {}
        }

        Input {
            tick,
            buttons,
            yaw: Fp::from_f32(yaw_rad),
            pitch: Fp::from_f32(0.0),
        }
    }
}
