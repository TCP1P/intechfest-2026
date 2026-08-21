//! Authoritative game simulation: movement, weapon, respawn. Pure, input-driven.

use crate::arena::{Aabb, Arena, PowerupSpawn};
use crate::powerup;
use crate::weapon;
use common::{DetRng, FpExt, Vec3Ext};
use glam::Vec3;
use protocol::{
    Buttons, ConfirmationState, GameEvent, Input, PlayerId, PlayerState, ScenarioKind, Snapshot,
    Tick, MAX_PLAYERS,
};

pub const PLAYER_RADIUS: f32 = 0.4;
pub const PLAYER_HEIGHT: f32 = 1.7;
pub const PLAYER_HEIGHT_CROUCH: f32 = 1.1;
pub const EYE_HEIGHT: f32 = 1.5;
pub const EYE_HEIGHT_CROUCH: f32 = 0.85;

pub const fn player_height(crouching: bool) -> f32 {
    if crouching {
        PLAYER_HEIGHT_CROUCH
    } else {
        PLAYER_HEIGHT
    }
}
pub const fn eye_height(crouching: bool) -> f32 {
    if crouching {
        EYE_HEIGHT_CROUCH
    } else {
        EYE_HEIGHT
    }
}

const MAX_GROUND_SPEED: f32 = 7.0;
const MAX_GROUND_SPEED_CROUCH: f32 = 3.5;
const MAX_AIR_WISH_SPEED: f32 = 1.5; // Quake-style air cap — enables strafe jumping.
const GROUND_ACCEL: f32 = 14.0;
const AIR_ACCEL: f32 = 14.0;
const FRICTION: f32 = 6.0;
const JUMP_VELOCITY: f32 = 5.0;
const GRAVITY: f32 = 20.0;

const STEP_HEIGHT: f32 = 0.65; // tallest step the player auto-climbs without jumping

const FIRE_COOLDOWN_TICKS: u64 = 64; // 1 s at 64 Hz
/// Fire presses arriving this close to cooldown end are buffered and
/// released at ready — without it, fractional-tick latencies eat clicks
/// (found by the latency-ladder fleet: the 100 ms tier lost 1/3 of its
/// shots because its presses landed exactly one tick early).
const FIRE_BUFFER_TICKS: u64 = 8;
const FLAME_PERIOD_TICKS: u64 = 8; // 8 emissions/s while held
pub(crate) const FLAME_FUEL: u16 = 64; // emissions per pickup (~8 s of fire)
const FLAME_RANGE: f32 = 7.0;
const FLAME_COS: f32 = 0.94; // ~20 degree cone
const FLAME_DAMAGE: i16 = 12; // 96 dps held — LG-flavored, short range
pub(crate) const ARMOR_PICKUP: i16 = 50;
pub(crate) const ARMOR_MAX: i16 = 100;
const SHOTGUN_PELLETS: usize = 10;
const SHOTGUN_DAMAGE: i16 = 10; // per pellet, Q3: 10x10 = 100 max
const SHOTGUN_RANGE: f32 = 30.0;
const SHOTGUN_COOLDOWN_TICKS: u64 = 51; // 0.8 s
pub(crate) const SHOTGUN_SHELLS: u16 = 8;
const ROCKET_SPEED: f32 = 22.0;
const ROCKET_COOLDOWN_TICKS: u64 = 58; // 0.9 s
const ROCKET_RADIUS: f32 = 4.0;
const ROCKET_DAMAGE: i16 = 100; // at the center (Q3), linear falloff
const ROCKET_KNOCKBACK: f32 = 14.0;
pub(crate) const ROCKET_AMMO: u16 = 6;
const MAX_ROCKETS: usize = 16;
/// Fixed pellet spread (yaw, pitch) offsets in radians — deterministic.
const SHOTGUN_SPREAD: [(f32, f32); SHOTGUN_PELLETS] = [
    (0.0, 0.0),
    (0.035, 0.0),
    (-0.035, 0.0),
    (0.0, 0.030),
    (0.0, -0.030),
    (0.055, 0.025),
    (-0.055, 0.025),
    (0.055, -0.025),
    (-0.055, -0.025),
    (0.085, 0.0),
];
const RAIL_DAMAGE: i16 = 40;
const RAIL_RANGE: f32 = 80.0;
const RESPAWN_TICKS: u64 = 128; // 2 s
const STARTING_HEALTH: i16 = 100;
const KITCHEN_LAVA_START_TICKS: Tick = 30 * 64;
const KITCHEN_LAVA_DURATION_TICKS: Tick = 30 * 64;
const KITCHEN_LAVA_CYCLE_TICKS: Tick = 120 * 64;
const KITCHEN_LAVA_DAMAGE_PERIOD: Tick = 16;
const KITCHEN_LAVA_DAMAGE: i16 = 8;

/// A frag remains confirmable for this long. Two seconds of respawn time
/// gives the victim a genuine denial route without letting stale objectives
/// clutter the arena.
pub const CONFIRMATION_LIFETIME_SECONDS: Tick = 12;
pub const CONFIRMATION_PICKUP_RADIUS: f32 = 1.25;
pub const MAX_CONFIRMATIONS: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FeatureFlags {
    pub powerups: bool,
    pub kitchen_lava: bool,
}

impl Default for FeatureFlags {
    fn default() -> Self {
        Self {
            powerups: true,
            kitchen_lava: true,
        }
    }
}

#[derive(Debug)]
pub enum JoinError {
    Full,
}

#[derive(Clone, Debug)]
pub struct Player {
    pub name: String,
    pub pos: Vec3,
    pub vel: Vec3,
    pub yaw: f32,
    pub pitch: f32,
    pub health: i16,
    pub grounded: bool,
    pub alive: bool,
    pub crouching: bool,
    pub respawn_at: Tick,
    pub fire_ready_at: Tick,
    pub quad_until: Tick,
    pub haste_until: Tick,
    pub weapon: protocol::WeaponKind,
    pub ammo: u16,
    pub armor: i16,
    pub serum_until: Tick,
    /// Fire press that landed during cooldown's final ticks — released
    /// the moment the weapon is ready (input buffering; clicks arriving
    /// a tick early under latency otherwise vanish).
    pub fire_queued: bool,
    pub pending: Input,
    /// false while `pending` holds an input no tick has consumed yet —
    /// a second input arriving in the same tick window must not drop
    /// that input's FIRE press (last-wins used to eat clicks whenever a
    /// client's send phase grazed the tick boundary).
    pub pending_seen: bool,
    pub prev_buttons: Buttons,
}

impl Player {
    fn fresh(name: &str, pos: Vec3) -> Self {
        let mut p = Self {
            name: name.to_string(),
            pos,
            vel: Vec3::ZERO,
            yaw: 0.0,
            pitch: 0.0,
            health: STARTING_HEALTH,
            grounded: true,
            alive: true,
            crouching: false,
            respawn_at: 0,
            fire_ready_at: 0,
            quad_until: 0,
            haste_until: 0,
            weapon: protocol::WeaponKind::Rail,
            ammo: 0,
            armor: 0,
            serum_until: 0,
            fire_queued: false,
            pending: Input::default(),
            pending_seen: true,
            prev_buttons: Buttons::EMPTY,
        };
        p.respawn_at_pos(pos);
        p
    }

    /// Reset the shared life-state for a (re)spawn at `pos`. Fields that
    /// legitimately differ per call site (respawn_at, pending, etc.) are
    /// left untouched.
    fn respawn_at_pos(&mut self, pos: Vec3) {
        self.pos = pos;
        self.vel = Vec3::ZERO;
        self.health = STARTING_HEALTH;
        self.alive = true;
        self.grounded = true;
        self.crouching = false;
        self.quad_until = 0;
        self.haste_until = 0;
        self.weapon = protocol::WeaponKind::Rail;
        self.ammo = 0;
        self.armor = 0;
        self.serum_until = 0;
        self.fire_queued = false;
    }
}

#[derive(Clone, Debug)]
struct PowerupSlot {
    spawn: PowerupSpawn,
    available_at: Tick,
}

#[derive(Clone, Debug)]
struct Rocket {
    id: u16,
    owner: PlayerId,
    pos: Vec3,
    vel: Vec3,
}

#[derive(Clone, Copy, Debug)]
struct Confirmation {
    id: u16,
    killer: PlayerId,
    victim: PlayerId,
    pos: Vec3,
    expires_at: Tick,
}

/// Longest hitscan rewind (~375 ms at 64 Hz). Past this, high-ping
/// players eat their own latency rather than shooting deeper into the
/// past of everyone else.
pub const LAG_COMP_MAX_TICKS: u8 = 24;

pub struct World {
    pub arena: Arena,
    pub players: [Option<Player>; MAX_PLAYERS],
    rockets: Vec<Rocket>,
    next_rocket_id: u16,
    confirmations: Vec<Confirmation>,
    next_confirmation_id: u16,
    powerups: Vec<PowerupSlot>,
    rng: DetRng,
    flags: FeatureFlags,
    kitchen_lava_active: bool,
    /// Per-player hitscan rewind in ticks, set by the server from the
    /// connection's RTT. Part of simulation state: replays record changes
    /// (ReplayEvent::PlayerDelay) so re-simulation stays bit-exact.
    delays: [u8; MAX_PLAYERS],
    /// Player hulls of recent ticks (back = current tick), for rewound
    /// hit tests.
    history: std::collections::VecDeque<[weapon::Hull; MAX_PLAYERS]>,
    /// Tick of each player's latest (re)spawn — never rewind across it.
    last_spawn: [Tick; MAX_PLAYERS],
    pub tick: Tick,
    pub tick_hz: u16,
}

impl World {
    pub fn new(arena: Arena, tick_hz: u16) -> Self {
        Self::with_seed(arena, tick_hz, 0)
    }

    pub fn with_seed(arena: Arena, tick_hz: u16, seed: u64) -> Self {
        let powerups = arena
            .powerups
            .iter()
            .copied()
            .map(|spawn| PowerupSlot {
                spawn,
                available_at: 0,
            })
            .collect();
        Self {
            arena,
            players: Default::default(),
            rockets: Vec::new(),
            next_rocket_id: 0,
            confirmations: Vec::new(),
            next_confirmation_id: 0,
            powerups,
            rng: DetRng::from_seed(seed),
            flags: FeatureFlags::default(),
            kitchen_lava_active: false,
            delays: [0; MAX_PLAYERS],
            history: std::collections::VecDeque::new(),
            last_spawn: [0; MAX_PLAYERS],
            tick: 0,
            tick_hz: tick_hz.max(1),
        }
    }

    /// Set a player's hitscan rewind. Replays must record every call
    /// (ReplayEvent::PlayerDelay) or re-simulation diverges.
    pub fn set_player_delay(&mut self, id: PlayerId, ticks: u8) {
        if let Some(d) = self.delays.get_mut(id as usize) {
            *d = ticks.min(LAG_COMP_MAX_TICKS);
        }
    }

    pub fn player_delay(&self, id: PlayerId) -> u8 {
        self.delays.get(id as usize).copied().unwrap_or(0)
    }

    /// Discard kill-confirmation objectives created outside an official
    /// scoring window. The server records this boundary in the replay before
    /// calling it, so deterministic verification performs the same mutation.
    pub fn discard_pending_confirmations(&mut self) -> usize {
        let discarded = self.confirmations.len();
        self.confirmations.clear();
        discarded
    }

    /// Victim hulls as the shooter saw them: rewound by the shooter's
    /// delay, except players who (re)spawned after the rewind point or
    /// are dead now — those use the present.
    fn hulls_for(&self, shooter: PlayerId) -> [weapon::Hull; MAX_PLAYERS] {
        let r = self.delays[shooter as usize] as usize;
        let rewind_tick = self.tick.saturating_sub(r as Tick);
        let mut hulls: [weapon::Hull; MAX_PLAYERS] = Default::default();
        for i in 0..MAX_PLAYERS {
            let Some(p) = self.players[i].as_ref() else {
                continue;
            };
            if !p.alive {
                continue;
            }
            let now = Some((p.pos, p.crouching));
            hulls[i] = if r == 0 || self.history.len() <= r || self.last_spawn[i] > rewind_tick {
                now
            } else {
                let idx = self.history.len() - 1 - r;
                self.history[idx][i].or(now)
            };
        }
        hulls
    }

    pub fn alloc(&mut self, name: &str) -> Result<PlayerId, JoinError> {
        let Some(i) = self.players.iter().position(Option::is_none) else {
            return Err(JoinError::Full);
        };
        let id = i as PlayerId;
        let pos = self.pick_spawn(id);
        self.players[i] = Some(Player::fresh(name, pos));
        self.delays[i] = 0;
        self.last_spawn[i] = self.tick;
        Ok(id)
    }

    pub fn remove(&mut self, id: PlayerId) -> bool {
        self.players
            .get_mut(id as usize)
            .and_then(Option::take)
            .is_some()
    }

    pub fn apply_input(&mut self, id: PlayerId, input: Input) {
        if let Some(Some(p)) = self.players.get_mut(id as usize) {
            // Clamp pitch to ±π/2 so we can't look past straight up/down.
            let mut pitch = input.pitch.to_f32();
            pitch = pitch.clamp(-1.5, 1.5);
            let mut yaw = input.yaw.to_f32();
            // Wrap yaw into [-π, π). Matters for bandwidth, not correctness.
            let tau = std::f32::consts::TAU;
            yaw = ((yaw + std::f32::consts::PI).rem_euclid(tau)) - std::f32::consts::PI;
            let mut buttons = input.buttons;
            // superseding an unconsumed input: its FIRE press survives
            if !p.pending_seen && p.pending.buttons.contains(Buttons::FIRE) {
                buttons.insert(Buttons::FIRE);
            }
            p.pending = Input {
                pitch: protocol::Fp::from_f32(pitch),
                yaw: protocol::Fp::from_f32(yaw),
                buttons,
                ..input
            };
            p.pending_seen = false;
        }
    }

    pub fn set_feature_flag(&mut self, key: &str, value: bool) -> bool {
        match key {
            "powerups" => self.flags.powerups = value,
            "kitchen_lava" => {
                self.flags.kitchen_lava = value;
                if !value {
                    self.kitchen_lava_active = false;
                }
            }
            _ => return false,
        }
        true
    }

    fn pick_spawn(&mut self, id: PlayerId) -> Vec3 {
        if self.arena.spawn_points.is_empty() {
            return Vec3::ZERO;
        }

        let start = (self.rng.u64() as usize) % self.arena.spawn_points.len();
        let mut best = self.arena.spawn_points[start];
        let mut best_score = f32::NEG_INFINITY;

        for offset in 0..self.arena.spawn_points.len() {
            let spawn = self.arena.spawn_points[(start + offset) % self.arena.spawn_points.len()];
            let nearest_alive = self
                .players
                .iter()
                .enumerate()
                .filter_map(|(other_id, slot)| {
                    if other_id == id as usize {
                        return None;
                    }
                    let p = slot.as_ref()?;
                    p.alive.then_some(spawn.distance_squared(p.pos))
                })
                .fold(f32::INFINITY, f32::min);

            if nearest_alive > best_score {
                best = spawn;
                best_score = nearest_alive;
            }
        }

        best
    }

    fn player_inside_confirmation(&self, id: PlayerId, position: Vec3) -> bool {
        let Some(player) = self.players.get(id as usize).and_then(Option::as_ref) else {
            return false;
        };
        if !player.alive {
            return false;
        }
        let delta = player.pos - position;
        delta.x * delta.x + delta.z * delta.z
            <= CONFIRMATION_PICKUP_RADIUS * CONFIRMATION_PICKUP_RADIUS
            && delta.y.abs() <= 2.5
    }

    /// Resolve existing bananas before weapons run. A banana dropped during
    /// this tick therefore cannot be collected until the next authoritative
    /// tick, making event ordering deterministic for replays and spectators.
    fn resolve_confirmations(&mut self, events: &mut Vec<GameEvent>) {
        let mut index = 0;
        while index < self.confirmations.len() {
            let confirmation = self.confirmations[index];
            let event = if self.tick >= confirmation.expires_at {
                Some(GameEvent::ConfirmationExpired {
                    confirmation_id: confirmation.id,
                    killer: confirmation.killer,
                    victim: confirmation.victim,
                })
            } else if self.player_inside_confirmation(confirmation.killer, confirmation.pos) {
                Some(GameEvent::KillConfirmed {
                    confirmation_id: confirmation.id,
                    killer: confirmation.killer,
                    victim: confirmation.victim,
                })
            } else if self.player_inside_confirmation(confirmation.victim, confirmation.pos) {
                Some(GameEvent::KillDenied {
                    confirmation_id: confirmation.id,
                    killer: confirmation.killer,
                    victim: confirmation.victim,
                })
            } else {
                None
            };

            if let Some(event) = event {
                self.confirmations.remove(index);
                events.push(event);
            } else {
                index += 1;
            }
        }
    }

    fn allocate_confirmation_id(&mut self) -> u16 {
        loop {
            let id = self.next_confirmation_id;
            self.next_confirmation_id = self.next_confirmation_id.wrapping_add(1);
            if self.confirmations.iter().all(|item| item.id != id) {
                return id;
            }
        }
    }

    fn drop_frag_confirmations(&mut self, events: &mut Vec<GameEvent>) {
        let frags: Vec<(PlayerId, PlayerId)> = events
            .iter()
            .filter_map(|event| match *event {
                GameEvent::Kill { attacker, victim } if attacker != victim => {
                    Some((attacker, victim))
                }
                _ => None,
            })
            .collect();

        for (killer, victim) in frags {
            let Some(pos) = self
                .players
                .get(victim as usize)
                .and_then(Option::as_ref)
                .map(|player| player.pos)
            else {
                continue;
            };
            if self.confirmations.len() == MAX_CONFIRMATIONS {
                let expired = self.confirmations.remove(0);
                events.push(GameEvent::ConfirmationExpired {
                    confirmation_id: expired.id,
                    killer: expired.killer,
                    victim: expired.victim,
                });
            }
            let id = self.allocate_confirmation_id();
            self.confirmations.push(Confirmation {
                id,
                killer,
                victim,
                pos,
                expires_at: self.tick.saturating_add(
                    CONFIRMATION_LIFETIME_SECONDS.saturating_mul(self.tick_hz as Tick),
                ),
            });
            events.push(GameEvent::ConfirmationDropped {
                confirmation_id: id,
                killer,
                victim,
            });
        }
    }

    pub fn advance(&mut self) -> Snapshot {
        let dt = 1.0 / self.tick_hz as f32;
        let mut events: Vec<GameEvent> = Vec::new();
        let lava_active = self.flags.kitchen_lava && kitchen_lava_active(self.tick, self.tick_hz);
        if lava_active != self.kitchen_lava_active {
            self.kitchen_lava_active = lava_active;
            events.push(GameEvent::ScenarioState {
                kind: ScenarioKind::KitchenLava,
                active: lava_active,
            });
        }

        // 1. respawns
        let respawning: Vec<PlayerId> = self
            .players
            .iter()
            .enumerate()
            .filter_map(|(i, slot)| {
                let p = slot.as_ref()?;
                (!p.alive && self.tick >= p.respawn_at).then_some(i as PlayerId)
            })
            .collect();
        for id in respawning {
            let spawn = self.pick_spawn(id);
            self.last_spawn[id as usize] = self.tick;
            if let Some(p) = self.players[id as usize].as_mut() {
                // respawn_at_pos clears crouch too — dying crouched otherwise
                // respawns you at half speed with a smaller hull until you
                // re-toggle.
                p.respawn_at_pos(spawn);
                events.push(GameEvent::Spawn {
                    id,
                    pos: fp_vec3(spawn),
                });
            }
        }

        // 2. movement (elevator AABBs at end-of-tick position collide and
        // carry alongside the static walls)
        let dyn_walls: Vec<Aabb> = self
            .arena
            .elevators
            .iter()
            .map(|e| e.aabb_at(self.tick.wrapping_add(1), self.tick_hz))
            .collect();
        for slot in self.players.iter_mut() {
            let Some(p) = slot else { continue };
            if !p.alive {
                continue;
            }
            integrate_player(p, &self.arena, dt, self.tick, &dyn_walls);
        }

        // 2.25 Kill confirmation. The server owns pickup eligibility,
        // distance, expiry, and the resulting score event.
        self.resolve_confirmations(&mut events);

        // 2.5 mega-health overheal rots 1 HP/s back down to 100
        if self.tick.is_multiple_of(self.tick_hz as u64) {
            for slot in self.players.iter_mut() {
                if let Some(p) = slot {
                    if p.alive && p.health > 100 {
                        p.health -= 1;
                    }
                }
            }
        }

        // 3. powerup pickups
        if self.flags.powerups {
            for i in 0..MAX_PLAYERS {
                let Some(p) = self.players[i].as_mut() else {
                    continue;
                };
                if !p.alive {
                    continue;
                }
                for slot in self.powerups.iter_mut() {
                    if self.tick < slot.available_at {
                        continue;
                    }
                    let d = p.pos - slot.spawn.center;
                    let reach = slot.spawn.radius + PLAYER_RADIUS;
                    if d.x * d.x + d.z * d.z <= reach * reach && d.y.abs() <= 1.2 {
                        powerup::apply(slot.spawn.kind, p, self.tick, self.tick_hz);
                        slot.available_at =
                            self.tick + powerup::respawn_ticks(slot.spawn.kind, self.tick_hz);
                        events.push(GameEvent::PowerupPickup {
                            id: i as PlayerId,
                            kind: slot.spawn.kind,
                        });
                    }
                }
            }
        }

        // 4. scenario hazards
        if lava_active && self.tick.is_multiple_of(KITCHEN_LAVA_DAMAGE_PERIOD) {
            for i in 0..MAX_PLAYERS {
                let Some(p) = self.players[i].as_mut() else {
                    continue;
                };
                if !p.alive || !point_in_zone(&self.arena, "kitchen", p.pos) {
                    continue;
                }
                p.health = (p.health - KITCHEN_LAVA_DAMAGE).max(0);
                events.push(GameEvent::ScenarioDamage {
                    id: i as PlayerId,
                    kind: ScenarioKind::KitchenLava,
                    amount: KITCHEN_LAVA_DAMAGE as u8,
                });
                if p.health <= 0 {
                    p.alive = false;
                    p.respawn_at = self.tick + RESPAWN_TICKS;
                    p.quad_until = 0;
                    p.haste_until = 0;
                    events.push(GameEvent::ScenarioKill {
                        id: i as PlayerId,
                        kind: ScenarioKind::KitchenLava,
                    });
                }
            }
        }

        // Record this tick's hulls for lag-compensated hit tests.
        {
            let mut entry: [weapon::Hull; MAX_PLAYERS] = Default::default();
            for (i, slot) in self.players.iter().enumerate() {
                if let Some(p) = slot {
                    if p.alive {
                        entry[i] = Some((p.pos, p.crouching));
                    }
                }
            }
            self.history.push_back(entry);
            while self.history.len() > LAG_COMP_MAX_TICKS as usize + 1 {
                self.history.pop_front();
            }
        }

        // 5. weapons. Rail: edge-triggered hitscan with a cooldown.
        // Flamethrower: held-fire cone with limited fuel, reverts to rail
        // when dry.
        let mut fires: Vec<(PlayerId, Vec3, Vec3, i16)> = Vec::new();
        let mut flames: Vec<(PlayerId, Vec3, Vec3, i16)> = Vec::new();
        let mut shots: Vec<(PlayerId, Vec3, f32, f32, bool)> = Vec::new();
        let mut launches: Vec<(PlayerId, Vec3, Vec3)> = Vec::new();
        for i in 0..MAX_PLAYERS {
            let Some(p) = self.players[i].as_mut() else {
                continue;
            };
            if !p.alive {
                continue;
            }
            let now_fire = p.pending.buttons.contains(Buttons::FIRE);
            let origin = Vec3::new(p.pos.x, p.pos.y + eye_height(p.crouching), p.pos.z);
            let dir = look_dir(p.yaw, p.pitch);
            let quad = self.tick < p.quad_until;
            match p.weapon {
                protocol::WeaponKind::Flamethrower => {
                    if now_fire && p.ammo > 0 && self.tick.is_multiple_of(FLAME_PERIOD_TICKS) {
                        let damage = if quad { FLAME_DAMAGE * 4 } else { FLAME_DAMAGE };
                        flames.push((i as PlayerId, origin, dir, damage));
                    }
                }
                protocol::WeaponKind::Rail => {
                    if trigger_fire(p, now_fire, self.tick) {
                        let damage = if quad { RAIL_DAMAGE * 4 } else { RAIL_DAMAGE };
                        fires.push((i as PlayerId, origin, dir, damage));
                    }
                }
                protocol::WeaponKind::Shotgun => {
                    if p.ammo > 0 && trigger_fire(p, now_fire, self.tick) {
                        shots.push((i as PlayerId, origin, p.yaw, p.pitch, quad));
                    }
                }
                protocol::WeaponKind::RocketLauncher => {
                    if p.ammo > 0 && trigger_fire(p, now_fire, self.tick) {
                        launches.push((i as PlayerId, origin, dir));
                    }
                }
            }
        }
        // Shotgun: fixed deterministic pellet fan, per-pellet hitscan.
        for (shooter, origin, yaw, pitch, quad) in shots {
            if let Some(p) = self.players[shooter as usize].as_mut() {
                p.fire_ready_at = self.tick + SHOTGUN_COOLDOWN_TICKS;
                spend_ammo(p);
            }
            let mut victim_damage = [0i16; MAX_PLAYERS];
            let hulls = self.hulls_for(shooter);
            for (dy, dp) in SHOTGUN_SPREAD {
                let dir = look_dir(yaw + dy, pitch + dp);
                let hit = weapon::raycast_hulls(
                    &self.arena,
                    &dyn_walls,
                    &hulls,
                    shooter,
                    origin,
                    dir,
                    SHOTGUN_RANGE,
                );
                let endpoint = match hit {
                    Some((_, t, _)) => origin + dir * t,
                    None => {
                        let t = weapon::raycast_walls(
                            &self.arena,
                            &dyn_walls,
                            origin,
                            dir,
                            SHOTGUN_RANGE,
                        );
                        origin + dir * t
                    }
                };
                events.push(GameEvent::Fire {
                    shooter,
                    from: fp_vec3(origin),
                    to: fp_vec3(endpoint),
                    hit: hit.map(|(id, _, _)| id),
                });
                if let Some((victim, _, _)) = hit {
                    let dmg = if quad {
                        SHOTGUN_DAMAGE * 4
                    } else {
                        SHOTGUN_DAMAGE
                    };
                    victim_damage[victim as usize] += dmg;
                }
            }
            for (vi, dmg) in victim_damage.into_iter().enumerate() {
                if dmg == 0 {
                    continue;
                }
                if let Some(victim) = self.players[vi].as_mut() {
                    apply_weapon_hit(victim, shooter, vi as PlayerId, dmg, self.tick, &mut events);
                }
            }
        }
        // Rocket launches.
        for (shooter, origin, dir) in launches {
            if self.rockets.len() >= MAX_ROCKETS {
                continue;
            }
            if let Some(p) = self.players[shooter as usize].as_mut() {
                p.fire_ready_at = self.tick + ROCKET_COOLDOWN_TICKS;
                spend_ammo(p);
            }
            let id = self.next_rocket_id;
            self.next_rocket_id = self.next_rocket_id.wrapping_add(1);
            self.rockets.push(Rocket {
                id,
                owner: shooter,
                pos: origin + dir * 0.7, // clear the shooter's own hull
                vel: dir * ROCKET_SPEED,
            });
        }
        // Rocket flight + detonation: explode on the first wall or player
        // along this tick's segment; splash with linear falloff +
        // knockback (half self-damage — rocket jumps are a feature).
        let quad_owners: Vec<bool> = self
            .players
            .iter()
            .map(|s| s.as_ref().is_some_and(|p| self.tick < p.quad_until))
            .collect();
        let mut explosions: Vec<(PlayerId, Vec3, bool)> = Vec::new();
        let mut keep = Vec::with_capacity(self.rockets.len());
        for mut r in std::mem::take(&mut self.rockets) {
            let step = r.vel * dt;
            let dist = step.length();
            let dir = step / dist.max(1e-6);
            let mut wall_t = weapon::raycast_walls(&self.arena, &dyn_walls, r.pos, dir, dist);
            // the ground is a plane, not a wall AABB — intersect it too
            if dir.y < -1e-6 {
                let t_floor = (r.pos.y - self.arena.floor_y) / -dir.y;
                if t_floor >= 0.0 {
                    wall_t = wall_t.min(t_floor);
                }
            }
            let body_hit = weapon::raycast_players(
                &self.arena,
                &dyn_walls,
                &self.players,
                r.owner,
                r.pos,
                dir,
                wall_t.min(dist),
            );
            if let Some((_, t, _)) = body_hit {
                let at = r.pos + dir * t;
                explosions.push((r.owner, at, quad_owners[r.owner as usize]));
                continue;
            }
            if wall_t < dist {
                let at = r.pos + dir * (wall_t - 0.05).max(0.0);
                explosions.push((r.owner, at, quad_owners[r.owner as usize]));
                continue;
            }
            r.pos += step;
            if r.pos.y < -2.0 || r.pos.y > 40.0 {
                continue; // out of the world
            }
            keep.push(r);
        }
        self.rockets = keep;
        for (owner, at, quad) in explosions {
            events.push(GameEvent::Explosion { at: fp_vec3(at) });
            for vi in 0..MAX_PLAYERS {
                let Some(v) = self.players[vi].as_mut() else {
                    continue;
                };
                if !v.alive {
                    continue;
                }
                let center = v.pos + Vec3::Y * (player_height(v.crouching) * 0.5);
                let d = (center - at).length();
                if d > ROCKET_RADIUS {
                    continue;
                }
                let falloff = 1.0 - d / ROCKET_RADIUS;
                let mut dmg = (ROCKET_DAMAGE as f32 * falloff) as i16;
                if quad {
                    dmg *= 4;
                }
                if vi as PlayerId == owner {
                    dmg /= 2; // rocket jumping costs half
                }
                if dmg > 0 {
                    let push_dir = ((center - at) / d.max(1e-3) + Vec3::Y * 0.8).normalize();
                    v.vel += push_dir * (ROCKET_KNOCKBACK * falloff);
                    v.grounded = false;
                    apply_weapon_hit(v, owner, vi as PlayerId, dmg, self.tick, &mut events);
                }
            }
        }
        for (shooter, origin, dir, damage) in flames {
            if let Some(p) = self.players[shooter as usize].as_mut() {
                spend_ammo(p);
            }
            events.push(GameEvent::Flame {
                shooter,
                from: fp_vec3(origin),
                dir: fp_vec3(dir),
            });
            let mut burns: Vec<(PlayerId, i16)> = Vec::new();
            for (vi, slot) in self.players.iter().enumerate() {
                let Some(v) = slot else { continue };
                if vi as PlayerId == shooter || !v.alive {
                    continue;
                }
                let center = v.pos + Vec3::Y * (player_height(v.crouching) * 0.5);
                let to = center - origin;
                let dist = to.length();
                if dist > FLAME_RANGE || dist < 1e-3 {
                    continue;
                }
                if to.normalize().dot(dir) < FLAME_COS {
                    continue;
                }
                if weapon::raycast_walls(&self.arena, &dyn_walls, origin, to / dist, dist) < dist {
                    continue; // wall blocks the flame
                }
                burns.push((vi as PlayerId, damage));
            }
            for (victim_id, dmg) in burns {
                if let Some(victim) = self.players[victim_id as usize].as_mut() {
                    apply_weapon_hit(victim, shooter, victim_id, dmg, self.tick, &mut events);
                }
            }
        }
        for (shooter, origin, dir, damage) in fires {
            let hulls = self.hulls_for(shooter);
            let hit = weapon::raycast_hulls(
                &self.arena,
                &dyn_walls,
                &hulls,
                shooter,
                origin,
                dir,
                RAIL_RANGE,
            );
            let endpoint = match hit {
                Some((_, t, _)) => origin + dir * t,
                None => {
                    let t_wall =
                        weapon::raycast_walls(&self.arena, &dyn_walls, origin, dir, RAIL_RANGE);
                    origin + dir * t_wall
                }
            };
            events.push(GameEvent::Fire {
                shooter,
                from: fp_vec3(origin),
                to: fp_vec3(endpoint),
                hit: hit.map(|(id, _, _)| id),
            });
            if let Some(p) = self.players[shooter as usize].as_mut() {
                p.fire_ready_at = self.tick + FIRE_COOLDOWN_TICKS;
            }
            if let Some((victim_id, _, _)) = hit {
                if let Some(victim) = self.players[victim_id as usize].as_mut() {
                    apply_weapon_hit(victim, shooter, victim_id, damage, self.tick, &mut events);
                }
            }
        }

        self.drop_frag_confirmations(&mut events);

        // 6. tape over prev_buttons, apply yaw/pitch from input
        for slot in self.players.iter_mut() {
            let Some(p) = slot else { continue };
            p.yaw = p.pending.yaw.to_f32();
            p.pitch = p.pending.pitch.to_f32();
            p.prev_buttons = p.pending.buttons;
            p.pending_seen = true;
        }

        // 7. build snapshot
        let players: Vec<PlayerState> = self
            .players
            .iter()
            .enumerate()
            .filter_map(|(i, s)| {
                s.as_ref().map(|p| PlayerState {
                    id: i as PlayerId,
                    pos: fp_vec3(p.pos),
                    yaw: protocol::Fp::from_f32(p.yaw),
                    pitch: protocol::Fp::from_f32(p.pitch),
                    health: p.health.clamp(0, 255) as u8,
                    respawn_in: if p.alive {
                        0
                    } else {
                        p.respawn_at.saturating_sub(self.tick) as u16
                    },
                    crouching: p.crouching,
                    weapon: p.weapon,
                    ammo: p.ammo,
                    armor: p.armor.clamp(0, 255) as u8,
                    serum_ticks: p.serum_until.saturating_sub(self.tick).min(u16::MAX as u64)
                        as u16,
                    quad_ticks: p.quad_until.saturating_sub(self.tick).min(u16::MAX as u64) as u16,
                    haste_ticks: p.haste_until.saturating_sub(self.tick).min(u16::MAX as u64)
                        as u16,
                })
            })
            .collect();

        let powerups_up: Vec<bool> = self
            .powerups
            .iter()
            .map(|slot| self.tick >= slot.available_at)
            .collect();
        let projectiles: Vec<protocol::ProjectileState> = self
            .rockets
            .iter()
            .map(|r| protocol::ProjectileState {
                id: r.id,
                pos: fp_vec3(r.pos),
                vel: fp_vec3(r.vel),
            })
            .collect();
        let confirmations = self
            .confirmations
            .iter()
            .map(|confirmation| ConfirmationState {
                id: confirmation.id,
                killer: confirmation.killer,
                victim: confirmation.victim,
                pos: fp_vec3(confirmation.pos),
                expires_in: confirmation
                    .expires_at
                    .saturating_sub(self.tick)
                    .min(u16::MAX as Tick) as u16,
            })
            .collect();
        let snap = Snapshot {
            tick: self.tick,
            players,
            projectiles,
            confirmations,
            powerups_up,
            events,
        };

        self.tick = self.tick.wrapping_add(1);
        snap
    }
}

/// Q3-style armor: armor absorbs 2/3 of incoming damage (truncated),
/// health takes the rest; absorption is limited by remaining armor.
/// Returns true if this damage killed the player.
/// Edge-triggered fire with a small input buffer: a press in the last
/// FIRE_BUFFER_TICKS of cooldown queues and releases at ready.
fn trigger_fire(p: &mut Player, now_fire: bool, tick: Tick) -> bool {
    let was_fire = p.prev_buttons.contains(Buttons::FIRE);
    let edge = now_fire && !was_fire;
    let ready = tick >= p.fire_ready_at;
    if edge && !ready && p.fire_ready_at - tick <= FIRE_BUFFER_TICKS {
        p.fire_queued = true;
    }
    if ready && (edge || p.fire_queued) {
        p.fire_queued = false;
        return true;
    }
    false
}

/// Apply `dmg` from `attacker` to `victim`, pushing the Damage event and,
/// if it kills, the death bookkeeping + Kill event. Preserves event order
/// (Damage before Kill).
fn apply_weapon_hit(
    victim: &mut Player,
    attacker: PlayerId,
    victim_id: PlayerId,
    dmg: i16,
    tick: Tick,
    events: &mut Vec<GameEvent>,
) {
    apply_hit(victim, attacker, victim_id, dmg, tick, events);
}

fn apply_hit(
    victim: &mut Player,
    attacker: PlayerId,
    victim_id: PlayerId,
    dmg: i16,
    tick: Tick,
    events: &mut Vec<GameEvent>,
) {
    let died = inflict(victim, dmg, tick);
    events.push(GameEvent::Damage {
        attacker,
        victim: victim_id,
        amount: dmg.min(u8::MAX as i16) as u8,
    });
    if died {
        victim.alive = false;
        victim.respawn_at = tick + RESPAWN_TICKS;
        victim.quad_until = 0;
        victim.haste_until = 0;
        events.push(GameEvent::Kill {
            attacker,
            victim: victim_id,
        });
    }
}

fn spend_ammo(p: &mut Player) {
    p.ammo = p.ammo.saturating_sub(1);
    if p.ammo == 0 {
        p.weapon = protocol::WeaponKind::Rail;
    }
}

fn inflict(p: &mut Player, dmg: i16, tick: Tick) -> bool {
    // PX-41 berserk: the purple monster shrugs off half of everything
    // (weapons only — lava doesn't route through here on purpose)
    let dmg = if tick < p.serum_until {
        (dmg + 1) / 2
    } else {
        dmg
    };
    let absorb = (dmg * 2 / 3).min(p.armor);
    p.armor -= absorb;
    p.health = (p.health - (dmg - absorb)).max(0);
    p.health <= 0 && p.alive
}

fn integrate_player(p: &mut Player, arena: &Arena, dt: f32, tick: Tick, dyn_walls: &[Aabb]) {
    // Elevator carry: a player standing on a platform's top surface tracks
    // it exactly, both directions, so riding never flickers grounded state.
    let tick_hz = (1.0 / dt).round() as u16;
    for (e, next) in arena.elevators.iter().zip(dyn_walls) {
        let prev_top = e.top_y(tick, tick_hz);
        if h_inflated_overlap(p.pos.x, p.pos.z, next, PLAYER_RADIUS)
            && (p.pos.y - prev_top).abs() < 0.08
        {
            p.pos.y = next.max.y;
            if p.vel.y < 0.0 {
                p.vel.y = 0.0;
            }
            p.grounded = true;
        }
    }

    // Yaw-relative wish direction. Forward is -Z when yaw = 0.
    let yaw = p.pending.yaw.to_f32();
    let (sy, cy) = common::trig::sin_cos(yaw);
    let forward = Vec3::new(-sy, 0.0, -cy);
    let right = Vec3::new(cy, 0.0, -sy);

    let b = p.pending.buttons;
    // Crouch — Source-style: crouching mid-air pulls the legs up (the
    // head stays put), buying PLAYER_HEIGHT - PLAYER_HEIGHT_CROUCH of
    // feet clearance, so jump-then-crouch mounts crates a plain jump
    // can't. Standing back up needs headroom (and, airborne, legroom).
    let want_crouch = b.contains(Buttons::CROUCH);
    let tuck = PLAYER_HEIGHT - PLAYER_HEIGHT_CROUCH;
    if want_crouch && !p.crouching {
        p.crouching = true;
        if !p.grounded {
            p.pos.y += tuck;
        }
    } else if !want_crouch && p.crouching {
        if p.grounded {
            if !collides_xz(arena, p.pos, PLAYER_HEIGHT, dyn_walls) {
                p.crouching = false;
            }
        } else {
            let stand = Vec3::new(p.pos.x, p.pos.y - tuck, p.pos.z);
            if !collides_xz(arena, stand, PLAYER_HEIGHT, dyn_walls) {
                p.pos = stand;
                p.crouching = false;
            }
        }
    }
    let height = player_height(p.crouching);
    let mut wish = Vec3::ZERO;
    if b.contains(Buttons::FORWARD) {
        wish += forward;
    }
    if b.contains(Buttons::BACK) {
        wish -= forward;
    }
    if b.contains(Buttons::RIGHT) {
        wish += right;
    }
    if b.contains(Buttons::LEFT) {
        wish -= right;
    }
    let wish_dir = if wish.length_squared() > 1e-6 {
        wish.normalize()
    } else {
        Vec3::ZERO
    };

    // Friction (ground only).
    if p.grounded {
        let h_speed = (p.vel.x * p.vel.x + p.vel.z * p.vel.z).sqrt();
        if h_speed > 0.01 {
            let drop = h_speed * FRICTION * dt;
            let factor = ((h_speed - drop).max(0.0)) / h_speed;
            p.vel.x *= factor;
            p.vel.z *= factor;
        } else {
            p.vel.x = 0.0;
            p.vel.z = 0.0;
        }
    }

    // Acceleration.
    let haste = tick < p.haste_until;
    let serum = tick < p.serum_until;
    let (wish_speed, accel) = if p.grounded {
        let s = if p.crouching {
            MAX_GROUND_SPEED_CROUCH
        } else {
            MAX_GROUND_SPEED
        };
        let mult = if haste {
            1.45
        } else if serum {
            1.3
        } else {
            1.0
        };
        (s * mult, GROUND_ACCEL * mult)
    } else {
        let mult = if haste {
            1.25
        } else if serum {
            1.15
        } else {
            1.0
        };
        (MAX_AIR_WISH_SPEED * mult, AIR_ACCEL * mult)
    };
    let current = p.vel.dot(wish_dir);
    let add = (wish_speed - current).max(0.0);
    let step = (accel * dt * wish_speed).min(add);
    if step > 0.0 {
        p.vel += wish_dir * step;
    }

    // Gravity.
    p.vel.y -= GRAVITY * dt;

    // Jump: edge-triggered on JUMP + grounded.
    if b.contains(Buttons::JUMP) && !p.prev_buttons.contains(Buttons::JUMP) && p.grounded {
        p.vel.y = JUMP_VELOCITY;
        p.grounded = false;
    }

    // Jump pads — detect BEFORE integration so the launch velocity is the
    // one used for this tick's motion. We only launch when the player is
    // actually on the ground (grounded=true). Crouching disables pads so
    // the player can hide on top of one without being catapulted.
    if p.grounded && !p.crouching {
        for pad in &arena.jump_pads {
            let dx = p.pos.x - pad.center.x;
            let dz = p.pos.z - pad.center.z;
            if dx * dx + dz * dz <= pad.radius * pad.radius && (p.pos.y - pad.center.y).abs() < 0.25
            {
                p.vel = pad.velocity;
                p.grounded = false;
                break;
            }
        }
    }

    let delta = p.vel * dt;

    // Horizontal axis-sweep: revert per-axis on collision (gives free sliding).
    // When grounded and blocked by something ≤ STEP_HEIGHT tall, auto-climb so
    // stairs and small ledges don't require jumping.
    let candidate = Vec3::new(p.pos.x + delta.x, p.pos.y, p.pos.z);
    if !collides_xz(arena, candidate, height, dyn_walls) {
        p.pos.x = candidate.x;
    } else if !try_step_up(arena, p, candidate, height, dyn_walls) {
        p.vel.x = 0.0;
    }
    let candidate = Vec3::new(p.pos.x, p.pos.y, p.pos.z + delta.z);
    if !collides_xz(arena, candidate, height, dyn_walls) {
        p.pos.z = candidate.z;
    } else if !try_step_up(arena, p, candidate, height, dyn_walls) {
        p.vel.z = 0.0;
    }

    // Vertical with landing detection on wall tops and ceiling stop on wall bottoms.
    let new_y = p.pos.y + delta.y;
    let prev_y = p.pos.y;
    p.grounded = false;
    if new_y <= arena.floor_y {
        p.pos.y = arena.floor_y;
        p.vel.y = 0.0;
        p.grounded = true;
    } else {
        p.pos.y = new_y;
    }
    if delta.y <= 0.0 {
        for wall in arena.walls.iter().chain(dyn_walls) {
            if !h_inflated_overlap(p.pos.x, p.pos.z, wall, PLAYER_RADIUS) {
                continue;
            }
            if prev_y >= wall.max.y - 1e-3 && new_y <= wall.max.y {
                p.pos.y = wall.max.y;
                p.vel.y = 0.0;
                p.grounded = true;
            }
        }
    } else {
        for wall in arena.walls.iter().chain(dyn_walls) {
            if !h_inflated_overlap(p.pos.x, p.pos.z, wall, PLAYER_RADIUS) {
                continue;
            }
            let head_prev = prev_y + height;
            let head_new = new_y + height;
            if head_prev <= wall.min.y && head_new > wall.min.y {
                p.pos.y = wall.min.y - height;
                p.vel.y = 0.0;
            }
        }
    }
}

fn try_step_up(
    arena: &Arena,
    p: &mut Player,
    target: Vec3,
    height: f32,
    dyn_walls: &[Aabb],
) -> bool {
    if !p.grounded {
        return false;
    }
    let lifted = Vec3::new(target.x, target.y + STEP_HEIGHT, target.z);
    if collides_xz(arena, lifted, height, dyn_walls) {
        return false;
    }
    let base = target.y;
    let ceil = base + STEP_HEIGHT + 1e-3;
    let mut top = base;
    for wall in arena.walls.iter().chain(dyn_walls) {
        if !h_inflated_overlap(target.x, target.z, wall, PLAYER_RADIUS) {
            continue;
        }
        if wall.max.y > top && wall.max.y <= ceil {
            top = wall.max.y;
        }
    }
    p.pos.x = target.x;
    p.pos.z = target.z;
    p.pos.y = top;
    true
}

fn collides_xz(arena: &Arena, pos: Vec3, height: f32, dyn_walls: &[Aabb]) -> bool {
    for wall in arena.walls.iter().chain(dyn_walls) {
        if pos.x + PLAYER_RADIUS < wall.min.x || pos.x - PLAYER_RADIUS > wall.max.x {
            continue;
        }
        if pos.z + PLAYER_RADIUS < wall.min.z || pos.z - PLAYER_RADIUS > wall.max.z {
            continue;
        }
        if pos.y >= wall.max.y || pos.y + height <= wall.min.y {
            continue;
        }
        return true;
    }
    false
}

fn h_inflated_overlap(x: f32, z: f32, wall: &Aabb, r: f32) -> bool {
    x + r >= wall.min.x && x - r <= wall.max.x && z + r >= wall.min.z && z - r <= wall.max.z
}

fn look_dir(yaw: f32, pitch: f32) -> Vec3 {
    let (sy, cy) = common::trig::sin_cos(yaw);
    let (sp, cp) = common::trig::sin_cos(pitch);
    Vec3::new(-sy * cp, sp, -cy * cp).normalize()
}

fn point_in_zone(arena: &Arena, zone_name: &str, pos: Vec3) -> bool {
    arena
        .zones
        .iter()
        .any(|z| z.name == zone_name && z.bounds.contains(pos))
}

fn kitchen_lava_active(tick: Tick, tick_hz: u16) -> bool {
    let scale = tick_hz as Tick;
    let start = KITCHEN_LAVA_START_TICKS / 64 * scale;
    let duration = KITCHEN_LAVA_DURATION_TICKS / 64 * scale;
    let cycle = KITCHEN_LAVA_CYCLE_TICKS / 64 * scale;
    let phase = tick % cycle.max(1);
    phase >= start && phase < start + duration
}

fn fp_vec3(v: Vec3) -> protocol::Vec3 {
    protocol::Vec3::from_f32(v.x, v.y, v.z)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::arena::PowerupSpawn;
    use protocol::PowerupKind;

    fn two_player_world_with_powerup(kind: PowerupKind) -> World {
        let mut arena = Arena::phase2_minimal();
        arena.powerups = vec![PowerupSpawn {
            kind,
            center: Vec3::ZERO,
            radius: 0.75,
        }];
        let mut world = World::new(arena, 64);
        let a = world.alloc("a").unwrap();
        let b = world.alloc("b").unwrap();
        let attacker = world.players[a as usize].as_mut().unwrap();
        attacker.pos = Vec3::ZERO;
        world.players[b as usize].as_mut().unwrap().pos = Vec3::new(0.0, 0.0, -8.0);
        world
    }

    fn drop_test_confirmation(
        world: &mut World,
        killer: PlayerId,
        victim: PlayerId,
        pos: Vec3,
    ) -> u16 {
        let player = world.players[victim as usize].as_mut().unwrap();
        player.pos = pos;
        player.health = 1;
        player.alive = true;
        let mut events = Vec::new();
        apply_weapon_hit(player, killer, victim, 1, world.tick, &mut events);
        world.drop_frag_confirmations(&mut events);
        events
            .iter()
            .find_map(|event| match *event {
                GameEvent::ConfirmationDropped {
                    confirmation_id, ..
                } => Some(confirmation_id),
                _ => None,
            })
            .expect("frag drops a confirmation")
    }

    #[test]
    fn pvp_damage_needs_no_route_or_client_authorization() {
        let mut world = World::new(Arena::phase2_minimal(), 64);
        let shooter = world.alloc("player").unwrap();
        let victim_id = world.alloc("target").unwrap();
        world.players[shooter as usize].as_mut().unwrap().pos = Vec3::ZERO;
        world.players[victim_id as usize].as_mut().unwrap().pos = Vec3::new(0.0, 0.0, -8.0);
        world.apply_input(
            shooter,
            Input {
                tick: world.tick,
                buttons: Buttons::FIRE,
                yaw: protocol::Fp::from_f32(0.0),
                pitch: protocol::Fp::from_f32(0.0),
            },
        );

        let snap = world.advance();

        assert!(snap.events.iter().any(|event| matches!(
            event,
            GameEvent::Fire {
                shooter: 0,
                hit: Some(1),
                ..
            }
        )));
        assert!(snap.events.iter().any(|event| matches!(
            event,
            GameEvent::Damage {
                attacker: 0,
                victim: 1,
                ..
            }
        )));
        assert_eq!(
            world.players[victim_id as usize].as_ref().unwrap().health,
            60
        );
    }

    #[test]
    fn killer_confirms_by_collecting_the_authoritative_drop() {
        let mut world = World::new(Arena::phase2_minimal(), 64);
        let killer = world.alloc("killer").unwrap();
        let victim = world.alloc("victim").unwrap();
        let pos = Vec3::new(3.0, 0.0, 4.0);
        let confirmation_id = drop_test_confirmation(&mut world, killer, victim, pos);
        world.players[killer as usize].as_mut().unwrap().pos = pos;

        let snapshot = world.advance();

        assert!(snapshot.events.iter().any(|event| matches!(
            event,
            GameEvent::KillConfirmed { confirmation_id: id, killer: k, victim: v }
                if *id == confirmation_id && *k == killer && *v == victim
        )));
        assert!(snapshot.confirmations.is_empty());
    }

    #[test]
    fn victim_can_deny_but_a_third_player_cannot_steal() {
        let mut world = World::new(Arena::phase2_minimal(), 64);
        let killer = world.alloc("killer").unwrap();
        let victim = world.alloc("victim").unwrap();
        let third = world.alloc("third").unwrap();
        let pos = Vec3::new(3.0, 0.0, 4.0);
        let confirmation_id = drop_test_confirmation(&mut world, killer, victim, pos);
        world.players[killer as usize].as_mut().unwrap().pos = Vec3::new(-20.0, 0.0, -20.0);
        world.players[third as usize].as_mut().unwrap().pos = pos;

        let untouched = world.advance();
        assert_eq!(untouched.confirmations.len(), 1);

        let victim_state = world.players[victim as usize].as_mut().unwrap();
        victim_state.respawn_at_pos(pos);
        let denied = world.advance();
        assert!(denied.events.iter().any(|event| matches!(
            event,
            GameEvent::KillDenied { confirmation_id: id, killer: k, victim: v }
                if *id == confirmation_id && *k == killer && *v == victim
        )));
        assert!(denied.confirmations.is_empty());
    }

    #[test]
    fn confirmations_expire_and_stay_bounded() {
        let mut world = World::new(Arena::phase2_minimal(), 64);
        let killer = world.alloc("killer").unwrap();
        let victim = world.alloc("victim").unwrap();
        world.players[killer as usize].as_mut().unwrap().pos = Vec3::new(-20.0, 0.0, -20.0);
        world.players[victim as usize].as_mut().unwrap().pos = Vec3::new(20.0, 0.0, 20.0);
        for index in 0..=MAX_CONFIRMATIONS {
            let mut events = vec![GameEvent::Kill {
                attacker: killer,
                victim,
            }];
            world.players[victim as usize].as_mut().unwrap().pos =
                Vec3::new(index as f32 * 0.01, 0.0, 0.0);
            world.drop_frag_confirmations(&mut events);
        }
        assert_eq!(world.confirmations.len(), MAX_CONFIRMATIONS);
        world.players[victim as usize].as_mut().unwrap().alive = false;
        world.confirmations[0].expires_at = world.tick;
        let expired_id = world.confirmations[0].id;

        let snapshot = world.advance();

        assert!(snapshot.events.iter().any(|event| matches!(
            event,
            GameEvent::ConfirmationExpired { confirmation_id, .. }
                if *confirmation_id == expired_id
        )));
        assert_eq!(snapshot.confirmations.len(), MAX_CONFIRMATIONS - 1);
    }

    #[test]
    fn scoring_boundary_discards_staging_confirmations() {
        let mut world = World::new(Arena::phase2_minimal(), 64);
        let killer = world.alloc("killer").unwrap();
        let victim = world.alloc("victim").unwrap();
        let pos = Vec3::new(3.0, 0.0, 4.0);
        drop_test_confirmation(&mut world, killer, victim, pos);
        world.players[killer as usize].as_mut().unwrap().pos = pos;

        assert_eq!(world.discard_pending_confirmations(), 1);
        let snapshot = world.advance();

        assert!(snapshot.confirmations.is_empty());
        assert!(!snapshot
            .events
            .iter()
            .any(|event| matches!(event, GameEvent::KillConfirmed { .. })));
    }

    #[test]
    fn quad_damage_multiplies_rail_damage() {
        let mut world = two_player_world_with_powerup(PowerupKind::QuadDamage);
        world.apply_input(
            0,
            Input {
                tick: 0,
                buttons: Buttons::FIRE,
                yaw: protocol::Fp::from_f32(0.0),
                pitch: protocol::Fp::from_f32(0.0),
            },
        );

        let snap = world.advance();

        assert!(snap.events.iter().any(|ev| matches!(
            ev,
            GameEvent::PowerupPickup {
                id: 0,
                kind: PowerupKind::QuadDamage,
            }
        )));
        assert!(snap.events.iter().any(|ev| matches!(
            ev,
            GameEvent::Damage {
                attacker: 0,
                victim: 1,
                amount,
            } if *amount == (RAIL_DAMAGE * 4) as u8
        )));
        assert!(snap.events.iter().any(|ev| matches!(
            ev,
            GameEvent::Kill {
                attacker: 0,
                victim: 1
            }
        )));
    }

    #[test]
    fn elevator_carries_grounded_rider() {
        let mut world = World::new(Arena::gru_lab_v3(), 64);
        let id = world.alloc("rider").unwrap();
        {
            let p = world.players[id as usize].as_mut().unwrap();
            p.pos = Vec3::new(0.0, 0.25, 4.5);
            p.vel = Vec3::ZERO;
            p.grounded = true;
        }
        // Ride through the bottom dwell and half the climb.
        for _ in 0..416 {
            world.advance();
        }
        let e = world.arena.elevators[0];
        let platform_top = e.top_y(world.tick, 64);
        let p = world.players[id as usize].as_ref().unwrap();
        assert!(
            (p.pos.y - platform_top).abs() < 0.05,
            "rider at y={} but platform top is {}",
            p.pos.y,
            platform_top
        );
        assert!(p.pos.y > 4.0, "rider should be well above the floor");
    }

    #[test]
    fn crouch_jump_mounts_a_crate() {
        let mut world = World::new(Arena::phase2_minimal(), 64);
        // a crate-sized box a plain jump (apex 0.625) can't mount
        world.arena.walls.push(Aabb::from_center_size(
            Vec3::new(2.0, 0.6, 0.0),
            Vec3::new(1.2, 1.2, 1.2),
        ));
        let id = world.alloc("hopper").unwrap();
        {
            let p = world.players[id as usize].as_mut().unwrap();
            p.pos = Vec3::new(0.6, 0.0, 0.0);
            p.vel = Vec3::ZERO;
            p.grounded = true;
        }
        let mut mounted = false;
        for t in 0..96u64 {
            // run at the crate, jump, then tuck mid-air (Source-style)
            let mut buttons = Buttons::FORWARD;
            buttons.insert(Buttons::JUMP);
            if t > 6 {
                buttons.insert(Buttons::CROUCH);
            }
            world.apply_input(
                id,
                Input {
                    tick: world.tick,
                    buttons,
                    yaw: protocol::Fp::from_f32(-std::f32::consts::FRAC_PI_2),
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            world.advance();
            let p = world.players[id as usize].as_ref().unwrap();
            if p.grounded && p.pos.y > 1.1 {
                mounted = true;
                break;
            }
        }
        assert!(mounted, "crouch-jump failed to mount a 1.2 m crate");
    }

    #[test]
    fn crouching_ducks_under_head_level_shots() {
        let mut world = two_player_world_with_powerup(PowerupKind::Haste);
        world.apply_input(
            1,
            Input {
                tick: 0,
                buttons: Buttons::CROUCH,
                yaw: protocol::Fp::from_f32(0.0),
                pitch: protocol::Fp::from_f32(0.0),
            },
        );
        world.advance();
        let shooter_pos = world.players[0].as_ref().unwrap().pos;
        let victim_pos = world.players[1].as_ref().unwrap().pos;
        assert!(world.players[1].as_ref().unwrap().crouching);
        let origin = Vec3::new(shooter_pos.x, shooter_pos.y + EYE_HEIGHT, shooter_pos.z);
        let aim = Vec3::new(
            victim_pos.x,
            victim_pos.y + PLAYER_HEIGHT - 0.1, // standing head height
            victim_pos.z,
        );
        let dir = (aim - origin).normalize();
        let hit = weapon::raycast_players(&world.arena, &[], &world.players, 0, origin, dir, 80.0);
        assert!(
            hit.is_none(),
            "shot at standing head height hit a crouched player"
        );
    }

    #[test]
    fn flamethrower_burns_cone_and_runs_dry() {
        let mut world = two_player_world_with_powerup(PowerupKind::Flamethrower);
        // a stands at origin (picks up the flamer), b is 5 m ahead (-z)
        world.players[1].as_mut().unwrap().pos = Vec3::new(0.0, 0.0, -5.0);
        let mut burned = false;
        for _ in 0..600 {
            world.apply_input(
                0,
                Input {
                    tick: world.tick,
                    buttons: Buttons::FIRE,
                    yaw: protocol::Fp::from_f32(0.0), // facing -z
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            let snap = world.advance();
            if snap.events.iter().any(|e| {
                matches!(
                    e,
                    GameEvent::Damage {
                        attacker: 0,
                        victim: 1,
                        ..
                    }
                )
            }) {
                burned = true;
            }
        }
        assert!(burned, "flame cone never damaged the target");
        let p = world.players[0].as_ref().unwrap();
        assert_eq!(p.ammo, 0, "fuel should be spent");
        assert_eq!(
            p.weapon,
            protocol::WeaponKind::Rail,
            "empty flamer must revert to rail"
        );
    }

    #[test]
    fn rocket_splash_damages_and_knocks_back() {
        let mut world = two_player_world_with_powerup(PowerupKind::RocketLauncher);
        world.players[1].as_mut().unwrap().pos = Vec3::new(0.0, 0.0, -8.0);
        let mut exploded = false;
        for t in 0..120u64 {
            let buttons = if t == 2 {
                Buttons::FIRE
            } else {
                Buttons::EMPTY
            };
            world.apply_input(
                0,
                Input {
                    tick: world.tick,
                    buttons,
                    yaw: protocol::Fp::from_f32(0.0),
                    pitch: protocol::Fp::from_f32(-0.12), // aim at their feet
                },
            );
            let snap = world.advance();
            if snap
                .events
                .iter()
                .any(|e| matches!(e, GameEvent::Explosion { .. }))
            {
                exploded = true;
            }
        }
        assert!(exploded, "rocket never detonated");
        let v = world.players[1].as_ref().unwrap();
        assert!(v.health < 100, "splash should damage the victim");
    }

    #[test]
    fn rocket_jump_boosts_self_for_half_damage() {
        let mut world = two_player_world_with_powerup(PowerupKind::RocketLauncher);
        let mut peak = 0.0f32;
        for t in 0..90u64 {
            let buttons = if t == 2 {
                Buttons::FIRE
            } else {
                Buttons::EMPTY
            };
            world.apply_input(
                0,
                Input {
                    tick: world.tick,
                    buttons,
                    yaw: protocol::Fp::from_f32(0.0),
                    pitch: protocol::Fp::from_f32(-1.45), // shoot the floor
                },
            );
            world.advance();
            let p = world.players[0].as_ref().unwrap();
            peak = peak.max(p.pos.y);
        }
        let p = world.players[0].as_ref().unwrap();
        assert!(
            peak > 1.2,
            "rocket jump should launch well above jump height (got {peak})"
        );
        assert!(
            p.health < 100 && p.health > 40,
            "self damage should be halved (hp {})",
            p.health
        );
    }

    #[test]
    fn shotgun_spends_shells_and_reverts() {
        let mut world = two_player_world_with_powerup(PowerupKind::Shotgun);
        world.players[1].as_mut().unwrap().pos = Vec3::new(0.0, 0.0, -6.0);
        for t in 0..(SHOTGUN_SHELLS as u64 * SHOTGUN_COOLDOWN_TICKS + 128) {
            let fire = t % SHOTGUN_COOLDOWN_TICKS == 0;
            world.apply_input(
                0,
                Input {
                    tick: world.tick,
                    buttons: if fire { Buttons::FIRE } else { Buttons::EMPTY },
                    yaw: protocol::Fp::from_f32(0.0),
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            world.advance();
        }
        let p = world.players[0].as_ref().unwrap();
        assert_eq!(p.ammo, 0);
        assert_eq!(p.weapon, protocol::WeaponKind::Rail);
    }

    #[test]
    fn armor_absorbs_two_thirds() {
        let mut p = Player::fresh("t", Vec3::ZERO);
        p.armor = 100;
        let died = inflict(&mut p, 60, 0);
        assert!(!died);
        assert_eq!(p.armor, 60); // absorbed 40
        assert_eq!(p.health, 80); // took 20
                                  // armor-limited absorption
        let mut q = Player::fresh("t", Vec3::ZERO);
        q.armor = 10;
        inflict(&mut q, 60, 0);
        assert_eq!(q.armor, 0);
        assert_eq!(q.health, 50);
    }

    #[test]
    fn overwritten_fire_press_survives_same_tick_input() {
        // two inputs in one tick window: the FIRE press in the first must
        // not vanish when the second (empty) overwrites pending
        let mut world = World::new(Arena::phase2_minimal(), 64);
        let s = world.alloc("shooter").unwrap();
        let mk = |fire: bool| Input {
            tick: 0,
            buttons: if fire { Buttons::FIRE } else { Buttons::EMPTY },
            yaw: protocol::Fp::from_f32(0.0),
            pitch: protocol::Fp::from_f32(0.0),
        };
        world.apply_input(s, mk(true));
        world.apply_input(s, mk(false)); // same-tick overwrite
        let snap = world.advance();
        assert!(
            snap.events
                .iter()
                .any(|e| matches!(e, GameEvent::Fire { .. })),
            "press merged into superseding input"
        );
    }

    #[test]
    fn fire_press_just_before_cooldown_end_is_buffered() {
        let mut world = World::new(Arena::phase2_minimal(), 64);
        let s = world.alloc("shooter").unwrap();
        let press = |w: &mut World, fire: bool| {
            w.apply_input(
                s,
                Input {
                    tick: w.tick,
                    buttons: if fire { Buttons::FIRE } else { Buttons::EMPTY },
                    yaw: protocol::Fp::from_f32(0.0),
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            w.advance()
                .events
                .iter()
                .any(|e| matches!(e, GameEvent::Fire { .. }))
        };
        assert!(press(&mut world, true), "first shot fires");
        // released through the cooldown, then press ONE TICK EARLY
        for _ in 0..FIRE_COOLDOWN_TICKS - 2 {
            assert!(!press(&mut world, false));
        }
        assert!(!press(&mut world, true), "one tick early: no shot yet");
        // no fresh edge here — the buffered press must release at ready
        assert!(press(&mut world, false), "buffered press fires at ready");
    }

    #[test]
    fn lag_comp_rewinds_hitscan_to_where_the_shooter_saw() {
        // victim strafes +x at full speed; shooter (delayed 8 ticks) aims
        // at the victim's 8-tick-old position. With lag comp that's a
        // hit; with no delay it must miss.
        for (delay, expect_hit) in [(8u8, true), (0u8, false)] {
            let mut world = World::new(Arena::phase2_minimal(), 64);
            let s = world.alloc("shooter").unwrap();
            let v = world.alloc("victim").unwrap();
            world.set_player_delay(s, delay);
            {
                let sp = world.players[s as usize].as_mut().unwrap();
                sp.pos = Vec3::new(0.0, 0.0, 0.0);
            }
            // build history while the victim strafes
            let mut past_pos = Vec3::ZERO;
            for step in 0..16u64 {
                {
                    let vp = world.players[v as usize].as_mut().unwrap();
                    vp.pos = Vec3::new(step as f32 * 0.11, 0.0, -10.0);
                    vp.vel = Vec3::ZERO;
                    if step == 15 - 8 {
                        past_pos = vp.pos; // where the shooter "sees" them
                    }
                }
                world.apply_input(
                    s,
                    Input {
                        tick: world.tick,
                        buttons: Buttons::EMPTY,
                        yaw: protocol::Fp::from_f32(0.0),
                        pitch: protocol::Fp::from_f32(0.0),
                    },
                );
                world.advance();
            }
            // aim at the centre of the 8-tick-old hull and fire
            let aim = past_pos + Vec3::new(0.0, 0.85, 0.0);
            let origin = Vec3::new(0.0, EYE_HEIGHT, 0.0);
            let dir = aim - origin;
            let yaw = (-dir.x).atan2(-dir.z);
            let pitch = (dir.y / dir.length()).asin();
            // view angles apply at end-of-tick: aim first, fire next tick
            let mut hit = false;
            for fire in [false, true] {
                {
                    let vp = world.players[v as usize].as_mut().unwrap();
                    vp.pos = Vec3::new(16.0 * 0.11, 0.0, -10.0);
                }
                world.apply_input(
                    s,
                    Input {
                        tick: world.tick,
                        buttons: if fire { Buttons::FIRE } else { Buttons::EMPTY },
                        yaw: protocol::Fp::from_f32(yaw),
                        pitch: protocol::Fp::from_f32(pitch),
                    },
                );
                let snap = world.advance();
                if snap.events.iter().any(|e| {
                    matches!(e, GameEvent::Damage { attacker, victim, .. }
                        if *attacker == s && *victim == v)
                }) {
                    hit = true;
                }
            }
            assert_eq!(hit, expect_hit, "delay={delay} expected hit={expect_hit}");
        }
    }

    #[test]
    fn serum_halves_incoming_damage() {
        let mut p = Player::fresh("t", Vec3::ZERO);
        p.serum_until = 100;
        inflict(&mut p, 60, 50); // berserk: takes 30
        assert_eq!(p.health, 70);
        inflict(&mut p, 60, 150); // expired: takes 60
        assert_eq!(p.health, 10);
    }

    #[test]
    fn serum_keeps_the_rail_firing() {
        let mut world = two_player_world_with_powerup(PowerupKind::Serum);
        world.players[1].as_mut().unwrap().pos = Vec3::new(0.0, 0.0, -10.0);
        let mut railed = false;
        for t in 0..16u64 {
            world.apply_input(
                0,
                Input {
                    tick: world.tick,
                    buttons: if t == 2 {
                        Buttons::FIRE
                    } else {
                        Buttons::EMPTY
                    },
                    yaw: protocol::Fp::from_f32(0.0),
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            let snap = world.advance();
            if snap.events.iter().any(|e| {
                matches!(e, GameEvent::Damage { attacker: 0, victim: 1, amount } if *amount == RAIL_DAMAGE as u8)
            }) {
                railed = true;
            }
        }
        assert!(railed, "serum must not replace the rail");
    }

    #[test]
    fn mega_health_rots_back_to_100() {
        let mut world = two_player_world_with_powerup(PowerupKind::MegaHealth);
        world.advance(); // pickup at origin
        assert_eq!(world.players[0].as_ref().unwrap().health, 200);
        for _ in 0..(64 * 12) {
            world.advance();
        }
        let hp = world.players[0].as_ref().unwrap().health;
        assert!(hp < 200 && hp >= 100, "overheal should rot (hp {hp})");
    }

    #[test]
    fn serum_does_not_hijack_pickup_weapons() {
        let mut world = two_player_world_with_powerup(PowerupKind::Serum);
        world.advance(); // pick up serum at origin
        {
            let p = world.players[0].as_mut().unwrap();
            p.weapon = protocol::WeaponKind::Flamethrower;
            p.ammo = 16;
        }
        world.players[1].as_mut().unwrap().pos = Vec3::new(0.0, 0.0, -4.0);
        let mut flamed = false;
        for _ in 0..32 {
            world.apply_input(
                0,
                Input {
                    tick: world.tick,
                    buttons: Buttons::FIRE,
                    yaw: protocol::Fp::from_f32(0.0),
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            let snap = world.advance();
            if snap
                .events
                .iter()
                .any(|e| matches!(e, GameEvent::Flame { .. }))
            {
                flamed = true;
            }
        }
        assert!(
            flamed,
            "flamethrower must keep firing while serum is active"
        );
    }

    #[test]
    fn no_spawn_point_intersects_static_geometry() {
        for arena in [Arena::gru_lab(), Arena::gru_lab_v3()] {
            for s in &arena.spawn_points {
                for w in &arena.walls {
                    let overlap = s.x + PLAYER_RADIUS > w.min.x
                        && s.x - PLAYER_RADIUS < w.max.x
                        && s.z + PLAYER_RADIUS > w.min.z
                        && s.z - PLAYER_RADIUS < w.max.z
                        && s.y + PLAYER_HEIGHT > w.min.y
                        && s.y < w.max.y;
                    assert!(!overlap, "spawn {s} intersects wall {w:?}");
                }
            }
        }
    }

    #[test]
    fn banana_pickup_applies_slip_impulse() {
        let mut world = two_player_world_with_powerup(PowerupKind::Banana);

        let snap = world.advance();
        let p = world.players[0].as_ref().unwrap();

        assert!(snap.events.iter().any(|ev| matches!(
            ev,
            GameEvent::PowerupPickup {
                id: 0,
                kind: PowerupKind::Banana,
            }
        )));
        assert!(!p.grounded);
        assert!(p.vel.z < -10.0);
        assert!(p.vel.y > 1.0);
    }

    #[test]
    fn kitchen_lava_scenario_damages_players_in_kitchen() {
        let mut world = World::new(Arena::gru_lab(), 64);
        let id = world.alloc("chef").unwrap();
        let p = world.players[id as usize].as_mut().unwrap();
        p.pos = Vec3::new(24.0, 0.0, -8.0);
        world.tick = KITCHEN_LAVA_START_TICKS;

        let snap = world.advance();

        assert!(snap.events.iter().any(|ev| matches!(
            ev,
            GameEvent::ScenarioState {
                kind: ScenarioKind::KitchenLava,
                active: true,
            }
        )));
        assert!(snap.events.iter().any(|ev| matches!(
            ev,
            GameEvent::ScenarioDamage {
                id: 0,
                kind: ScenarioKind::KitchenLava,
                amount: 8,
            }
        )));
        assert_eq!(snap.players[0].health, 92);
    }

    #[test]
    fn feature_flags_disable_powerups_and_lava() {
        let mut world = two_player_world_with_powerup(PowerupKind::QuadDamage);
        assert!(world.set_feature_flag("powerups", false));
        let snap = world.advance();
        assert!(!snap
            .events
            .iter()
            .any(|ev| matches!(ev, GameEvent::PowerupPickup { .. })));

        let mut world = World::new(Arena::gru_lab(), 64);
        let id = world.alloc("chef").unwrap();
        world.players[id as usize].as_mut().unwrap().pos = Vec3::new(24.0, 0.0, -8.0);
        world.tick = KITCHEN_LAVA_START_TICKS;
        assert!(world.set_feature_flag("kitchen_lava", false));
        let snap = world.advance();
        assert!(!snap
            .events
            .iter()
            .any(|ev| matches!(ev, GameEvent::ScenarioDamage { .. })));
    }
}
