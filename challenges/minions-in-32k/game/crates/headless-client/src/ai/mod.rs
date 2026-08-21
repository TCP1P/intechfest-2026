mod navigation;

use common::FpExt;
use glam::Vec3;
use protocol::{
    Buttons, Fp, GameEvent, Input, PlayerId, PlayerState, PowerupKind, Snapshot, WeaponKind,
    MAX_PLAYERS,
};

const EYE_HEIGHT: f32 = 1.35;
const TARGET_HEIGHT: f32 = 1.0;
const ENGAGE_RANGE: f32 = 50.0;
const ELITE_MAX_TURN_PER_TICK: f32 = 0.19;
const ELITE_REACTION_TICKS: u64 = 4;
const ELITE_STABILITY_HOLD_TICKS: u64 = 28;
const NPC_STABILITY_HOLD_TICKS: u64 = 16;
const ELITE_BODY_OFFSET: f32 = 0.16;
const ELITE_FIRE_TOLERANCE: f32 = 0.12;
const ELITE_BACKPEDAL_RANGE: f32 = 10.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BotProfile {
    Npc,
    Elite,
    Mediocre,
}

impl BotProfile {
    fn is_elite(self) -> bool {
        matches!(self, Self::Elite)
    }
}

pub(crate) fn profile_for(index: usize, npc_count: usize, elite_count: usize) -> BotProfile {
    if index < npc_count {
        BotProfile::Npc
    } else if index < npc_count + elite_count {
        BotProfile::Elite
    } else {
        BotProfile::Mediocre
    }
}

#[derive(Clone, Copy, Debug)]
struct Personality {
    reaction_ticks: u64,
    max_turn: f32,
    turn_accel: f32,
    aim_error: f32,
    preferred_range: f32,
    memory_ticks: u64,
    fov_cos: f32,
    commitment_ticks: u64,
}

impl Personality {
    fn from_salt(salt: u64) -> Self {
        // Seven deterministic skill/personality bands: enough variation to
        // feel like a mixed public lobby, with every value kept in plausible
        // human bounds. Determinism keeps replays and regressions reproducible.
        const SKILL: [f32; 7] = [0.62, 0.78, 0.69, 0.86, 0.58, 0.74, 0.82];
        const RANGE: [f32; 7] = [14.0, 18.0, 11.0, 20.0, 9.0, 16.0, 13.0];
        let skill = SKILL[salt as usize % SKILL.len()];
        let half_fov = (68.0 + skill * 9.0).to_radians();
        Self {
            reaction_ticks: (13.0 - skill * 7.0).round() as u64,
            max_turn: 0.052 + skill * 0.050,
            turn_accel: 0.010 + skill * 0.010,
            aim_error: 0.034 - skill * 0.019,
            preferred_range: RANGE[salt as usize % RANGE.len()],
            memory_ticks: (96.0 + skill * 80.0).round() as u64,
            fov_cos: half_fov.cos(),
            commitment_ticks: (30.0 + skill * 24.0).round() as u64,
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct Observation {
    pos: Vec3,
    velocity: Vec3,
    seen_tick: u64,
}

pub(crate) struct Bot {
    pub(crate) name: String,
    pub(crate) profile: BotProfile,
    pub(crate) id: Option<PlayerId>,
    pub(crate) tick_rate_hz: u16,
    salt: u64,
    personality: Personality,
    next_fire_tick: u64,
    last_yaw: f32,
    last_pitch: f32,
    yaw_velocity: f32,
    pitch_velocity: f32,
    lock_target: Option<PlayerId>,
    lock_since: u64,
    target_commit_until: u64,
    raw_last_seen: [Option<(Vec3, u64)>; MAX_PLAYERS],
    unstable_until: [u64; MAX_PLAYERS],
    observations: [Option<Observation>; MAX_PLAYERS],
    visible_now: [bool; MAX_PLAYERS],
    threat: Option<(PlayerId, u64)>,
    strafe_until: u64,
    strafe_dir: i8,
    arena: sim::Arena,
    next_route_log_tick: u64,
    patrol_step: usize,
    progress_anchor: Option<(Vec3, u64)>,
    recovery_until: u64,
    recovery_dir: i8,
}

impl Bot {
    pub(crate) fn new(name: String, profile: BotProfile, salt: u64) -> Self {
        Self {
            name,
            profile,
            id: None,
            tick_rate_hz: common::DEFAULT_TICK_HZ,
            salt,
            personality: Personality::from_salt(salt),
            next_fire_tick: 0,
            last_yaw: 0.0,
            last_pitch: 0.0,
            yaw_velocity: 0.0,
            pitch_velocity: 0.0,
            lock_target: None,
            lock_since: 0,
            target_commit_until: 0,
            raw_last_seen: [None; MAX_PLAYERS],
            unstable_until: [0; MAX_PLAYERS],
            observations: [None; MAX_PLAYERS],
            visible_now: [false; MAX_PLAYERS],
            threat: None,
            strafe_until: 0,
            strafe_dir: 0,
            arena: sim::Arena::gru_lab_v3(),
            next_route_log_tick: 0,
            patrol_step: 0,
            progress_anchor: None,
            recovery_until: 0,
            recovery_dir: 1,
        }
    }

    pub(crate) fn observe_event(&mut self, tick: u64, event: GameEvent) {
        let Some(my_id) = self.id else { return };
        match event {
            GameEvent::Damage {
                attacker, victim, ..
            } if victim == my_id && attacker != my_id => {
                self.threat = Some((attacker, tick + self.tick_rate_hz as u64 * 3));
            }
            GameEvent::Kill { attacker, victim } => {
                if victim == my_id || self.lock_target == Some(victim) {
                    self.clear_target();
                }
                if victim == my_id && attacker != my_id {
                    self.threat = Some((attacker, tick + self.tick_rate_hz as u64 * 2));
                }
            }
            _ => {}
        }
    }

    pub(crate) fn think(&mut self, snapshot: &Snapshot) -> Option<Input> {
        let id = self.id?;
        let me = *snapshot.players.iter().find(|p| p.id == id)?;
        let tick = snapshot.tick;
        self.update_entity_stability(snapshot);
        self.update_perception(&me, &snapshot.players, tick);

        if me.respawn_in != 0 {
            self.progress_anchor = None;
            return Some(self.input(tick, Buttons::EMPTY, self.last_yaw, self.last_pitch));
        }

        if let Some(destination) = self.confirmation_goal(id, &me, snapshot) {
            self.clear_target();
            let me_pos = player_pos(&me);
            if tick >= self.next_route_log_tick {
                self.next_route_log_tick = tick + 256;
                tracing::info!(
                    tick,
                    x = me_pos.x,
                    z = me_pos.z,
                    waypoint_x = destination.x,
                    waypoint_z = destination.z,
                    "confirmation routing"
                );
            }
            let (mut buttons, yaw, pitch) = self.steer_to(me_pos, destination, tick);
            let phase = tick_mix(tick, self.salt) % 53;
            if phase == 0 {
                buttons.insert(Buttons::JUMP);
            } else if phase <= 14 {
                buttons.insert(Buttons::CROUCH);
            }
            let (buttons, yaw) = self.apply_stuck_recovery(me_pos, tick, buttons, yaw);
            return Some(self.input(tick, buttons, yaw, pitch));
        }

        if let Some(target) = self.choose_target(id, &me, &snapshot.players, tick) {
            return Some(self.combat_input(&me, &target, snapshot, tick));
        }

        if self.profile == BotProfile::Npc {
            if let Some(goal) = self.pursuit_goal(tick) {
                if navigation::flat_distance(player_pos(&me), goal) > 1.2 {
                    return Some(self.goal_input(&me, snapshot, goal, tick));
                }
                self.clear_target();
            }
            if let Some(goal) = self.pickup_goal(&me, snapshot) {
                return Some(self.goal_input(&me, snapshot, goal, tick));
            }
            let me_pos = player_pos(&me);
            let mut goal = navigation::patrol_goal(self.salt, self.patrol_step);
            if navigation::flat_distance(me_pos, goal) < 2.0 {
                self.patrol_step = self.patrol_step.wrapping_add(1);
                goal = navigation::patrol_goal(self.salt, self.patrol_step);
            }
            return Some(self.goal_input(&me, snapshot, goal, tick));
        }

        let mut buttons = Buttons::EMPTY;
        if tick % 128 < 64 {
            buttons.insert(Buttons::FORWARD);
        }
        self.add_strafe(&mut buttons, tick, snapshot, player_pos(&me));
        if self.should_jump(tick) {
            buttons.insert(Buttons::JUMP);
        }
        Some(self.input(tick, buttons, self.last_yaw, self.last_pitch))
    }

    fn input(&mut self, tick: u64, buttons: Buttons, yaw: f32, pitch: f32) -> Input {
        self.last_yaw = yaw;
        self.last_pitch = pitch;
        Input {
            tick,
            buttons,
            yaw: Fp::from_f32(yaw),
            pitch: Fp::from_f32(pitch),
        }
    }

    fn update_entity_stability(&mut self, snapshot: &Snapshot) {
        for player in snapshot
            .players
            .iter()
            .filter(|p| (p.id as usize) < MAX_PLAYERS)
        {
            let index = player.id as usize;
            let pos = player_pos(player);
            let discontinuous = match self.raw_last_seen[index] {
                Some((last_pos, last_tick)) => {
                    let dt = snapshot.tick.saturating_sub(last_tick);
                    (dt <= 2 && pos.distance(last_pos) > 0.85)
                        || (matches!(self.profile, BotProfile::Npc | BotProfile::Mediocre)
                            && dt > 2)
                }
                None => matches!(self.profile, BotProfile::Npc | BotProfile::Mediocre),
            };
            if discontinuous {
                let hold = if self.profile.is_elite() {
                    ELITE_STABILITY_HOLD_TICKS
                } else {
                    NPC_STABILITY_HOLD_TICKS
                };
                self.unstable_until[index] = snapshot.tick + hold;
            }
            self.raw_last_seen[index] = Some((pos, snapshot.tick));
        }
    }

    fn update_perception(&mut self, me: &PlayerState, players: &[PlayerState], tick: u64) {
        let previous_visibility = self.visible_now;
        self.visible_now.fill(false);
        let my_id = me.id;
        let me_pos = player_pos(me);
        let forward = flat_look(self.last_yaw);

        for player in players
            .iter()
            .filter(|p| p.id != my_id && (p.id as usize) < MAX_PLAYERS && p.respawn_in == 0)
        {
            let index = player.id as usize;
            if self.unstable_until[index] > tick || !self.target_visible(me, player) {
                continue;
            }
            let to = player_pos(player) - me_pos;
            let flat = Vec3::new(to.x, 0.0, to.z);
            let in_fov = flat.length_squared() < 0.01
                || flat.normalize().dot(forward) >= self.personality.fov_cos;
            let expected = self.lock_target == Some(player.id)
                || matches!(self.threat, Some((id, until)) if id == player.id && tick <= until);
            if self.profile == BotProfile::Npc && !in_fov && !expected {
                continue;
            }

            self.visible_now[index] = true;
            if !previous_visibility[index] && self.lock_target == Some(player.id) {
                self.lock_since = tick;
            }
            let pos = player_pos(player);
            let velocity = self.observations[index]
                .filter(|old| tick > old.seen_tick && tick - old.seen_tick <= 4)
                .map(|old| {
                    let seconds = (tick - old.seen_tick) as f32 / self.tick_rate_hz as f32;
                    ((pos - old.pos) / seconds.max(1.0 / 128.0)).clamp_length_max(12.0)
                })
                .unwrap_or(Vec3::ZERO);
            self.observations[index] = Some(Observation {
                pos,
                velocity,
                seen_tick: tick,
            });
        }
    }

    fn choose_target(
        &mut self,
        my_id: PlayerId,
        me: &PlayerState,
        players: &[PlayerState],
        tick: u64,
    ) -> Option<PlayerState> {
        let me_pos = player_pos(me);
        if self.profile.is_elite() {
            let target = players
                .iter()
                .filter(|p| {
                    p.id != my_id
                        && (p.id as usize) < MAX_PLAYERS
                        && p.respawn_in == 0
                        && self.unstable_until[p.id as usize] <= tick
                })
                .min_by(|a, b| {
                    let score = |p: &PlayerState| {
                        p.health as f32 / 100.0 + (player_pos(p) - me_pos).length_squared() / 2500.0
                    };
                    score(a).total_cmp(&score(b))
                })
                .copied();
            if let Some(target) = target {
                self.update_target_lock(target.id, tick);
            }
            return target;
        }

        let current_visible = self.lock_target.and_then(|id| {
            players
                .iter()
                .find(|p| p.id == id && self.visible_now[id as usize])
                .copied()
        });
        if self.profile == BotProfile::Mediocre {
            let target = players
                .iter()
                .filter(|p| p.id != my_id && (p.id as usize) < MAX_PLAYERS)
                .filter(|p| p.respawn_in == 0 && self.visible_now[p.id as usize])
                .min_by(|a, b| {
                    (player_pos(a) - me_pos)
                        .length_squared()
                        .total_cmp(&(player_pos(b) - me_pos).length_squared())
                })
                .copied();
            if let Some(target) = target {
                self.update_target_lock(target.id, tick);
            }
            return target;
        }

        if tick < self.target_commit_until && current_visible.is_some() {
            return current_visible;
        }
        let threat = self
            .threat
            .filter(|(_, until)| tick <= *until)
            .map(|(id, _)| id);
        let target = players
            .iter()
            .filter(|p| p.id != my_id && (p.id as usize) < MAX_PLAYERS)
            .filter(|p| p.respawn_in == 0 && self.visible_now[p.id as usize])
            .min_by(|a, b| {
                let score = |p: &PlayerState| {
                    let distance = navigation::flat_distance(me_pos, player_pos(p));
                    let mut value = distance / 28.0 + p.health as f32 / 180.0;
                    if self.lock_target == Some(p.id) {
                        value -= 0.55;
                    }
                    if threat == Some(p.id) {
                        value -= 0.85;
                    }
                    value
                };
                score(a).total_cmp(&score(b))
            })
            .copied();
        if let Some(target) = target {
            self.update_target_lock(target.id, tick);
        }
        target
    }

    fn update_target_lock(&mut self, target_id: PlayerId, tick: u64) {
        if self.lock_target != Some(target_id) {
            self.lock_target = Some(target_id);
            self.lock_since = tick;
            self.target_commit_until = tick + self.personality.commitment_ticks;
            self.yaw_velocity *= 0.4;
            self.pitch_velocity *= 0.4;
        }
    }

    fn clear_target(&mut self) {
        self.lock_target = None;
        self.target_commit_until = 0;
    }

    fn combat_input(
        &mut self,
        me: &PlayerState,
        target: &PlayerState,
        snapshot: &Snapshot,
        tick: u64,
    ) -> Input {
        let me_pos = player_pos(me);
        let target_pos = player_pos(target);
        let dist = navigation::flat_distance(me_pos, target_pos);
        let aim_point = self.aim_point(me, target, tick);
        let origin = me_pos + Vec3::new(0.0, EYE_HEIGHT, 0.0);
        let delta = aim_point - origin;
        let flat = Vec3::new(delta.x, 0.0, delta.z);
        let desired_yaw = (-flat.x).atan2(-flat.z);
        let desired_pitch = delta.y.atan2(flat.length().max(0.01));
        let error = self.aim_error(tick, dist);
        let (yaw, pitch) = self.apply_aim_model(
            desired_yaw + error.x,
            (desired_pitch + error.y).clamp(-1.2, 1.2),
        );

        let mut buttons = Buttons::EMPTY;
        let ideal = self.weapon_range(me.weapon);
        if self.profile.is_elite() && dist < ELITE_BACKPEDAL_RANGE {
            buttons.insert(Buttons::BACK);
        } else if self.profile == BotProfile::Npc && (me.health < 38 || dist < ideal * 0.55) {
            buttons.insert(Buttons::BACK);
        } else if dist > ideal * 1.12 {
            buttons.insert(Buttons::FORWARD);
        }
        self.add_strafe(&mut buttons, tick, snapshot, me_pos);
        if self.should_jump(tick) {
            buttons.insert(Buttons::JUMP);
        }
        if self.should_fire(FireContext {
            tick,
            target_id: target.id,
            dist,
            yaw,
            pitch,
            me,
            aim_point,
        }) {
            buttons.insert(Buttons::FIRE);
            self.next_fire_tick = tick + weapon_cooldown(me.weapon, self.tick_rate_hz);
        }
        let (buttons, yaw) = self.apply_stuck_recovery(me_pos, tick, buttons, yaw);
        self.input(tick, buttons, yaw, pitch)
    }

    fn goal_input(
        &mut self,
        me: &PlayerState,
        snapshot: &Snapshot,
        goal: Vec3,
        tick: u64,
    ) -> Input {
        let me_pos = player_pos(me);
        let waypoint = navigation::waypoint_to(&self.arena, me_pos, goal);
        let (mut buttons, yaw, pitch) = self.steer_to(me_pos, waypoint, tick);
        self.add_strafe(&mut buttons, tick, snapshot, me_pos);
        if self.should_jump(tick) {
            buttons.insert(Buttons::JUMP);
        }
        let (buttons, yaw) = self.apply_stuck_recovery(me_pos, tick, buttons, yaw);
        self.input(tick, buttons, yaw, pitch)
    }

    fn steer_to(&mut self, from: Vec3, destination: Vec3, _tick: u64) -> (Buttons, f32, f32) {
        let delta = destination - from;
        let flat = Vec3::new(delta.x, 0.0, delta.z);
        let mut buttons = Buttons::EMPTY;
        if flat.length_squared() <= 0.04 {
            return (buttons, self.last_yaw, self.last_pitch);
        }
        let desired_yaw = (-flat.x).atan2(-flat.z);
        let (yaw, pitch) = self.apply_aim_model(desired_yaw, 0.0);
        if angle_delta(yaw, desired_yaw).abs() < 0.78 {
            buttons.insert(Buttons::FORWARD);
        }
        (buttons, yaw, pitch)
    }

    fn pursuit_goal(&self, tick: u64) -> Option<Vec3> {
        let id = self.lock_target? as usize;
        self.observations[id]
            .filter(|seen| tick.saturating_sub(seen.seen_tick) <= self.personality.memory_ticks)
            .map(|seen| seen.pos)
    }

    fn confirmation_goal(
        &self,
        id: PlayerId,
        me: &PlayerState,
        snapshot: &Snapshot,
    ) -> Option<Vec3> {
        let from = player_pos(me);
        snapshot
            .confirmations
            .iter()
            .filter(|confirmation| confirmation.killer == id || confirmation.victim == id)
            .min_by(|a, b| {
                let urgency = |confirmation: &&protocol::ConfirmationState| {
                    let seconds = confirmation.expires_in as f32 / self.tick_rate_hz.max(1) as f32;
                    let travel = navigation::flat_distance(from, wire_pos(confirmation.pos)) / 7.0;
                    // The smallest remaining margin is the objective most at
                    // risk. Prefer confirming over denying on exact ties.
                    (seconds - travel, confirmation.killer != id, confirmation.id)
                };
                let (a_margin, a_denial, a_id) = urgency(a);
                let (b_margin, b_denial, b_id) = urgency(b);
                a_margin
                    .total_cmp(&b_margin)
                    .then(a_denial.cmp(&b_denial))
                    .then(a_id.cmp(&b_id))
            })
            .map(|confirmation| wire_pos(confirmation.pos))
    }

    fn pickup_goal(&self, me: &PlayerState, snapshot: &Snapshot) -> Option<Vec3> {
        self.arena
            .powerups
            .iter()
            .enumerate()
            .filter(|(i, powerup)| {
                powerup.center.y < 1.5
                    && snapshot.powerups_up.get(*i).copied().unwrap_or(false)
                    && powerup.kind != PowerupKind::Banana
            })
            .min_by(|(_, a), (_, b)| {
                let score = |powerup: &sim::arena::PowerupSpawn| {
                    navigation::flat_distance(player_pos(me), powerup.center)
                        + pickup_bias(me, powerup.kind)
                };
                score(a).total_cmp(&score(b))
            })
            .map(|(_, powerup)| powerup.center)
    }

    fn target_visible(&self, me: &PlayerState, target: &PlayerState) -> bool {
        sim::weapon::player_hull_visible(
            &self.arena,
            &[],
            player_pos(me) + Vec3::new(0.0, EYE_HEIGHT, 0.0),
            player_pos(target),
            target.crouching,
        )
    }

    fn aim_point(&self, me: &PlayerState, target: &PlayerState, tick: u64) -> Vec3 {
        let target_pos = player_pos(target);
        match self.profile {
            BotProfile::Elite => {
                let to_me = Vec3::new(
                    player_pos(me).x - target_pos.x,
                    0.0,
                    player_pos(me).z - target_pos.z,
                )
                .try_normalize()
                .unwrap_or(Vec3::new(0.0, 0.0, 1.0));
                let side = Vec3::new(-to_me.z, 0.0, to_me.x);
                let phase = tick / 96 + self.salt + target.id as u64;
                let side_sign = if phase.is_multiple_of(2) { 1.0 } else { -1.0 };
                let height = 0.88 + (phase % 3) as f32 * 0.12;
                target_pos + side * (ELITE_BODY_OFFSET * side_sign) + Vec3::Y * height
            }
            BotProfile::Npc => {
                let observed = self.observations[target.id as usize];
                if me.weapon == WeaponKind::RocketLauncher {
                    let lead = navigation::flat_distance(player_pos(me), target_pos) / 22.0;
                    let predicted = observed
                        .map(|seen| seen.pos + seen.velocity * lead.clamp(0.0, 0.8))
                        .unwrap_or(target_pos);
                    predicted + Vec3::Y * 0.18
                } else {
                    // Humans do not hold the exact mathematical centre of a
                    // model. Alternate between lower and upper torso, with a
                    // stable shoulder bias per engagement. Besides looking
                    // natural, this prevents a good NPC from accumulating the
                    // server's sub-centimetre aimbot precision heuristic.
                    let to_me = Vec3::new(
                        player_pos(me).x - target_pos.x,
                        0.0,
                        player_pos(me).z - target_pos.z,
                    )
                    .try_normalize()
                    .unwrap_or(Vec3::Z);
                    let side = Vec3::new(-to_me.z, 0.0, to_me.x);
                    let phase = tick / 96 + self.salt + target.id as u64;
                    let shoulder = if phase.is_multiple_of(2) { 0.12 } else { -0.12 };
                    let height = if target.crouching {
                        0.62
                    } else if (phase / 2).is_multiple_of(2) {
                        0.72
                    } else {
                        1.28
                    };
                    target_pos + side * shoulder + Vec3::Y * height
                }
            }
            BotProfile::Mediocre => target_pos + Vec3::Y * TARGET_HEIGHT,
        }
    }

    fn aim_error(&self, tick: u64, dist: f32) -> Vec3 {
        match self.profile {
            BotProfile::Elite => Vec3::ZERO,
            BotProfile::Npc => {
                let seconds = tick as f32 / self.tick_rate_hz.max(1) as f32;
                let phase = self.salt as f32 * 1.73;
                let range = (dist / 22.0).clamp(0.45, 1.45);
                Vec3::new(
                    ((seconds * 2.1 + phase).sin() * 0.72
                        + (seconds * 0.63 + phase * 0.4).sin() * 0.28)
                        * self.personality.aim_error
                        * range,
                    ((seconds * 1.4 + phase * 0.7).cos() * 0.75
                        + (seconds * 0.51 + phase).sin() * 0.25)
                        * self.personality.aim_error
                        * 0.55
                        * range,
                    0.0,
                )
            }
            BotProfile::Mediocre => {
                let t = tick as f32 + self.salt as f32 * 19.0;
                let range = (dist / 35.0).clamp(0.4, 1.6);
                Vec3::new(
                    (t * 0.071).sin() * 0.28 * range,
                    (t * 0.047).cos() * 0.16 * range,
                    0.0,
                )
            }
        }
    }

    fn apply_aim_model(&mut self, desired_yaw: f32, desired_pitch: f32) -> (f32, f32) {
        match self.profile {
            BotProfile::Elite => {
                let yaw_step = angle_delta(self.last_yaw, desired_yaw)
                    .clamp(-ELITE_MAX_TURN_PER_TICK, ELITE_MAX_TURN_PER_TICK);
                let pitch_step = (desired_pitch - self.last_pitch)
                    .clamp(-ELITE_MAX_TURN_PER_TICK, ELITE_MAX_TURN_PER_TICK);
                (
                    wrap_angle(self.last_yaw + yaw_step),
                    self.last_pitch + pitch_step,
                )
            }
            BotProfile::Npc => {
                let yaw_delta = angle_delta(self.last_yaw, desired_yaw);
                let pitch_delta = desired_pitch - self.last_pitch;
                self.yaw_velocity = accelerated_step(
                    yaw_delta,
                    self.yaw_velocity,
                    self.personality.max_turn,
                    self.personality.turn_accel,
                );
                self.pitch_velocity = accelerated_step(
                    pitch_delta,
                    self.pitch_velocity,
                    self.personality.max_turn * 0.75,
                    self.personality.turn_accel * 0.75,
                );
                (
                    wrap_angle(self.last_yaw + self.yaw_velocity),
                    (self.last_pitch + self.pitch_velocity).clamp(-1.2, 1.2),
                )
            }
            BotProfile::Mediocre => (desired_yaw, desired_pitch),
        }
    }

    fn should_fire(&self, ctx: FireContext<'_>) -> bool {
        if ctx.tick < self.next_fire_tick
            || ctx.dist >= self.weapon_max_range(ctx.me.weapon)
            || self.unstable_until[ctx.target_id as usize] > ctx.tick
        {
            return false;
        }
        if self.profile.is_elite() {
            return self.lock_target == Some(ctx.target_id)
                && ctx.tick.saturating_sub(self.lock_since) >= ELITE_REACTION_TICKS
                && aim_distance_to_point(
                    player_pos(ctx.me) + Vec3::Y * EYE_HEIGHT,
                    ctx.yaw,
                    ctx.pitch,
                    ctx.aim_point,
                )
                .unwrap_or(f32::INFINITY)
                    <= ELITE_FIRE_TOLERANCE;
        }
        if self.profile == BotProfile::Npc {
            if self.lock_target != Some(ctx.target_id)
                || !self.visible_now[ctx.target_id as usize]
                || ctx.tick.saturating_sub(self.lock_since) < self.personality.reaction_ticks
            {
                return false;
            }
            let miss = aim_distance_to_point(
                player_pos(ctx.me) + Vec3::Y * EYE_HEIGHT,
                ctx.yaw,
                ctx.pitch,
                ctx.aim_point,
            )
            .unwrap_or(f32::INFINITY);
            if miss > fire_tolerance(ctx.me.weapon) {
                return false;
            }
            // A small deterministic hesitation creates believable missed
            // opportunities without making behavior frame-rate dependent.
            let opportunity = ctx.tick / weapon_cooldown(ctx.me.weapon, self.tick_rate_hz).max(1);
            return !human_hash(opportunity, self.salt).is_multiple_of(13);
        }
        tick_mix(ctx.tick, self.salt * 11).is_multiple_of(4)
    }

    fn weapon_range(&self, weapon: WeaponKind) -> f32 {
        match weapon {
            WeaponKind::Flamethrower => 4.8,
            WeaponKind::Shotgun => 9.0,
            WeaponKind::RocketLauncher => 13.0,
            WeaponKind::Rail => self.personality.preferred_range,
        }
    }

    fn weapon_max_range(&self, weapon: WeaponKind) -> f32 {
        match weapon {
            WeaponKind::Flamethrower => 6.7,
            WeaponKind::Shotgun => 27.0,
            WeaponKind::RocketLauncher => 38.0,
            WeaponKind::Rail => ENGAGE_RANGE,
        }
    }

    fn add_strafe(&mut self, buttons: &mut Buttons, tick: u64, snapshot: &Snapshot, me_pos: Vec3) {
        if let Some(dodge) = incoming_projectile_dodge(snapshot, me_pos, self.last_yaw) {
            self.strafe_dir = dodge;
            self.strafe_until = tick + (self.tick_rate_hz as u64 / 3).max(1);
        } else if tick >= self.strafe_until {
            let choice = human_hash(tick / 17, self.salt * 7) % 5;
            self.strafe_dir = match choice {
                0 => 0,
                1 | 2 => -1,
                _ => 1,
            };
            let span = if self.profile == BotProfile::Npc {
                self.tick_rate_hz as u64 * (5 + human_hash(tick, self.salt) % 5) / 10
            } else if self.profile.is_elite() {
                self.tick_rate_hz as u64 / 3
            } else {
                self.tick_rate_hz as u64 * 3 / 4
            };
            self.strafe_until = tick + span.max(1);
        }
        match self.strafe_dir {
            d if d > 0 => buttons.insert(Buttons::RIGHT),
            d if d < 0 => buttons.insert(Buttons::LEFT),
            _ => {}
        }
    }

    fn should_jump(&self, tick: u64) -> bool {
        let period = if self.profile.is_elite() { 41 } else { 113 };
        human_hash(tick / 3, self.salt + 31).is_multiple_of(period)
    }

    fn apply_stuck_recovery(
        &mut self,
        me_pos: Vec3,
        tick: u64,
        mut buttons: Buttons,
        mut yaw: f32,
    ) -> (Buttons, f32) {
        let wants_move = [
            Buttons::FORWARD,
            Buttons::BACK,
            Buttons::LEFT,
            Buttons::RIGHT,
        ]
        .iter()
        .any(|button| buttons.contains(*button));
        if wants_move {
            match self.progress_anchor {
                Some((anchor, since))
                    if tick.saturating_sub(since) >= self.tick_rate_hz as u64 * 3 / 2 =>
                {
                    if navigation::flat_distance(anchor, me_pos) < 0.55 {
                        self.recovery_dir = if human_hash(tick, self.salt).is_multiple_of(2) {
                            1
                        } else {
                            -1
                        };
                        self.recovery_until = tick + self.tick_rate_hz as u64 / 2;
                    }
                    self.progress_anchor = Some((me_pos, tick));
                }
                None => self.progress_anchor = Some((me_pos, tick)),
                _ => {}
            }
        } else {
            self.progress_anchor = None;
        }

        if tick < self.recovery_until {
            let keep_fire = buttons.contains(Buttons::FIRE);
            buttons = Buttons::EMPTY;
            if keep_fire {
                buttons.insert(Buttons::FIRE);
            }
            buttons.insert(Buttons::BACK);
            buttons.insert(if self.recovery_dir > 0 {
                Buttons::RIGHT
            } else {
                Buttons::LEFT
            });
            buttons.insert(Buttons::JUMP);
            yaw = wrap_angle(yaw + self.recovery_dir as f32 * 0.035);
        }
        (buttons, yaw)
    }
}

struct FireContext<'a> {
    tick: u64,
    target_id: PlayerId,
    dist: f32,
    yaw: f32,
    pitch: f32,
    me: &'a PlayerState,
    aim_point: Vec3,
}

fn player_pos(player: &PlayerState) -> Vec3 {
    wire_pos(player.pos)
}

fn wire_pos(pos: protocol::Vec3) -> Vec3 {
    Vec3::new(pos.x.to_f32(), pos.y.to_f32(), pos.z.to_f32())
}

fn flat_look(yaw: f32) -> Vec3 {
    let (sin, cos) = yaw.sin_cos();
    Vec3::new(-sin, 0.0, -cos)
}

fn accelerated_step(delta: f32, velocity: f32, max_speed: f32, accel: f32) -> f32 {
    let desired = (delta * 0.42).clamp(-max_speed, max_speed);
    let change = (desired - velocity).clamp(-accel, accel);
    let next = velocity + change;
    if next.abs() > delta.abs() && next.signum() == delta.signum() {
        delta
    } else {
        next
    }
}

fn pickup_bias(me: &PlayerState, kind: PowerupKind) -> f32 {
    match kind {
        PowerupKind::MegaHealth if me.health < 80 => -70.0,
        PowerupKind::Health if me.health < 60 => -55.0,
        PowerupKind::Armor if me.armor < 40 => -38.0,
        PowerupKind::Serum if me.health < 70 => -30.0,
        PowerupKind::QuadDamage => -26.0,
        PowerupKind::RocketLauncher if me.weapon == WeaponKind::Rail => -18.0,
        PowerupKind::Shotgun if me.weapon == WeaponKind::Rail => -14.0,
        PowerupKind::Flamethrower if me.weapon == WeaponKind::Rail => -12.0,
        PowerupKind::Haste => -10.0,
        _ => 4.0,
    }
}

fn incoming_projectile_dodge(snapshot: &Snapshot, me_pos: Vec3, yaw: f32) -> Option<i8> {
    let right = {
        let forward = flat_look(yaw);
        Vec3::new(-forward.z, 0.0, forward.x)
    };
    snapshot.projectiles.iter().find_map(|projectile| {
        let pos = Vec3::new(
            projectile.pos.x.to_f32(),
            projectile.pos.y.to_f32(),
            projectile.pos.z.to_f32(),
        );
        let velocity = Vec3::new(
            projectile.vel.x.to_f32(),
            projectile.vel.y.to_f32(),
            projectile.vel.z.to_f32(),
        );
        let speed_sq = velocity.length_squared();
        if speed_sq < 1.0 {
            return None;
        }
        let relative = pos - (me_pos + Vec3::Y * 0.8);
        let approach = -relative.dot(velocity) / speed_sq;
        if !(0.0..=0.85).contains(&approach) {
            return None;
        }
        let closest = relative + velocity * approach;
        if closest.length() > 2.4 {
            return None;
        }
        Some(if relative.dot(right) >= 0.0 { -1 } else { 1 })
    })
}

fn fire_tolerance(weapon: WeaponKind) -> f32 {
    match weapon {
        WeaponKind::Rail => 0.20,
        WeaponKind::Flamethrower => 0.85,
        WeaponKind::Shotgun => 0.22,
        WeaponKind::RocketLauncher => 0.72,
    }
}

fn weapon_cooldown(weapon: WeaponKind, tick_rate_hz: u16) -> u64 {
    let base = match weapon {
        WeaponKind::Rail => 64,
        WeaponKind::Flamethrower => 1,
        WeaponKind::Shotgun => 51,
        WeaponKind::RocketLauncher => 58,
    };
    (base * tick_rate_hz as u64 / common::DEFAULT_TICK_HZ as u64).max(1)
}

fn aim_distance_to_point(origin: Vec3, yaw: f32, pitch: f32, point: Vec3) -> Option<f32> {
    let direction = look_dir(yaw, pitch);
    let to_point = point - origin;
    let distance = to_point.dot(direction);
    if !(0.0..=ENGAGE_RANGE).contains(&distance) {
        return None;
    }
    Some((origin + direction * distance).distance(point))
}

fn look_dir(yaw: f32, pitch: f32) -> Vec3 {
    let (sin_yaw, cos_yaw) = yaw.sin_cos();
    let (sin_pitch, cos_pitch) = pitch.sin_cos();
    Vec3::new(-sin_yaw * cos_pitch, sin_pitch, -cos_yaw * cos_pitch).normalize()
}

fn angle_delta(from: f32, to: f32) -> f32 {
    wrap_angle(to - from)
}

fn wrap_angle(angle: f32) -> f32 {
    (angle + std::f32::consts::PI).rem_euclid(std::f32::consts::TAU) - std::f32::consts::PI
}

fn tick_mix(tick: u64, salt: u64) -> u64 {
    tick.wrapping_add(salt)
}

fn human_hash(value: u64, salt: u64) -> u64 {
    let mut mixed = value ^ salt.wrapping_mul(0x9e37_79b9_7f4a_7c15);
    mixed ^= mixed >> 30;
    mixed = mixed.wrapping_mul(0xbf58_476d_1ce4_e5b9);
    mixed ^= mixed >> 27;
    mixed = mixed.wrapping_mul(0x94d0_49bb_1331_11eb);
    mixed ^ (mixed >> 31)
}

#[cfg(test)]
mod tests;
