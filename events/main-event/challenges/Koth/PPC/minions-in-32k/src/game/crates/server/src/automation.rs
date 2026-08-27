use common::FpExt;
use glam::Vec3;
use protocol::{Buttons, Input, PlayerId, PlayerState};

const MIN_FIRE_EDGES: u32 = 10;
const MIN_COMPLETED_PULSES: u32 = 8;
const MIN_SMOOTH_TRACKING_TICKS: u32 = 72;
const TARGET_AXIS_RADIUS: f32 = 0.38;
const MIN_TRACKING_RANGE: f32 = 8.0;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Evidence {
    pub fire_edges: u32,
    pub single_tick_pulses: u32,
    pub completed_pulses: u32,
    pub aligned_fire_edges: u32,
    pub smooth_tracking_ticks: u32,
}

#[derive(Debug, Default)]
pub(crate) struct Detector {
    flagged: bool,
    last_server_tick: Option<u64>,
    last_buttons: Buttons,
    fire_run: u16,
    last_yaw: Option<f32>,
    last_pitch: Option<f32>,
    last_yaw_step: Option<f32>,
    last_pitch_step: Option<f32>,
    evidence: Evidence,
}

impl Detector {
    pub(crate) fn observe(
        &mut self,
        server_tick: u64,
        player_id: PlayerId,
        input: Input,
        origin: Vec3,
        players: &[PlayerState],
    ) -> Option<Evidence> {
        if self.flagged || self.last_server_tick == Some(server_tick) {
            return None;
        }
        self.last_server_tick = Some(server_tick);

        let firing = input.buttons.contains(Buttons::FIRE);
        let was_firing = self.last_buttons.contains(Buttons::FIRE);
        if firing {
            self.fire_run = self.fire_run.saturating_add(1);
        } else if was_firing {
            self.evidence.completed_pulses = self.evidence.completed_pulses.saturating_add(1);
            if self.fire_run == 1 {
                self.evidence.single_tick_pulses =
                    self.evidence.single_tick_pulses.saturating_add(1);
            }
            self.fire_run = 0;
        }

        let yaw = input.yaw.to_f32();
        let pitch = input.pitch.to_f32();
        let yaw_step = self
            .last_yaw
            .map(|last| angle_delta(last, yaw))
            .unwrap_or_default();
        let pitch_step = self.last_pitch.map(|last| pitch - last).unwrap_or_default();
        let nearest = nearest_target_axis(origin, yaw, pitch, player_id, players);

        if firing && !was_firing {
            self.evidence.fire_edges = self.evidence.fire_edges.saturating_add(1);
            if nearest.is_some_and(|target| {
                target.range >= MIN_TRACKING_RANGE && target.distance <= TARGET_AXIS_RADIUS
            }) {
                self.evidence.aligned_fire_edges =
                    self.evidence.aligned_fire_edges.saturating_add(1);
            }
        }

        if nearest.is_some_and(|target| {
            target.range >= MIN_TRACKING_RANGE && target.distance <= TARGET_AXIS_RADIUS * 2.0
        }) && self.last_yaw_step.is_some_and(|last_yaw| {
            self.last_pitch_step.is_some_and(|last_pitch| {
                smooth_tracking_step(last_yaw, yaw_step, last_pitch, pitch_step)
            })
        }) {
            self.evidence.smooth_tracking_ticks =
                self.evidence.smooth_tracking_ticks.saturating_add(1);
        }

        self.last_buttons = input.buttons;
        self.last_yaw = Some(yaw);
        self.last_pitch = Some(pitch);
        self.last_yaw_step = Some(yaw_step);
        self.last_pitch_step = Some(pitch_step);

        let e = self.evidence;
        let automated = e.fire_edges >= MIN_FIRE_EDGES
            && e.completed_pulses >= MIN_COMPLETED_PULSES
            && e.single_tick_pulses.saturating_mul(10) >= e.completed_pulses.saturating_mul(9)
            && e.aligned_fire_edges.saturating_mul(5) >= e.fire_edges.saturating_mul(4)
            && e.smooth_tracking_ticks >= MIN_SMOOTH_TRACKING_TICKS;
        if automated {
            self.flagged = true;
            Some(e)
        } else {
            None
        }
    }
}

#[derive(Clone, Copy)]
struct TargetAxis {
    distance: f32,
    range: f32,
}

fn nearest_target_axis(
    origin: Vec3,
    yaw: f32,
    pitch: f32,
    player_id: PlayerId,
    players: &[PlayerState],
) -> Option<TargetAxis> {
    let direction = look_dir(yaw, pitch);
    players
        .iter()
        .filter(|player| player.id != player_id && player.respawn_in == 0)
        .flat_map(|player| {
            let position = player_pos(player);
            let heights = if player.crouching {
                [0.35, 0.65, 0.9]
            } else {
                [0.45, 0.95, 1.4]
            };
            heights.into_iter().filter_map(move |height| {
                let point = position + Vec3::Y * height;
                let to = point - origin;
                let along = to.dot(direction);
                (along > 0.0).then_some(TargetAxis {
                    distance: (origin + direction * along).distance(point),
                    range: to.length(),
                })
            })
        })
        .min_by(|left, right| left.distance.total_cmp(&right.distance))
}

fn player_pos(player: &PlayerState) -> Vec3 {
    Vec3::new(
        player.pos.x.to_f32(),
        player.pos.y.to_f32(),
        player.pos.z.to_f32(),
    )
}

fn look_dir(yaw: f32, pitch: f32) -> Vec3 {
    let (sy, cy) = yaw.sin_cos();
    let (sp, cp) = pitch.sin_cos();
    Vec3::new(-sy * cp, sp, -cy * cp).normalize()
}

fn angle_delta(from: f32, to: f32) -> f32 {
    ((to - from + std::f32::consts::PI).rem_euclid(std::f32::consts::TAU)) - std::f32::consts::PI
}

fn smooth_tracking_step(
    previous_yaw: f32,
    current_yaw: f32,
    previous_pitch: f32,
    current_pitch: f32,
) -> bool {
    current_yaw.hypot(current_pitch) >= 0.0005
        && (current_yaw - previous_yaw).abs() <= 0.018
        && (current_pitch - previous_pitch).abs() <= 0.018
}

#[cfg(test)]
mod tests {
    use super::*;
    use protocol::{Fp, WeaponKind};

    fn player(id: PlayerId, position: Vec3) -> PlayerState {
        PlayerState {
            id,
            pos: protocol::Vec3 {
                x: Fp::from_f32(position.x),
                y: Fp::from_f32(position.y),
                z: Fp::from_f32(position.z),
            },
            yaw: Fp::default(),
            pitch: Fp::default(),
            health: 100,
            respawn_in: 0,
            crouching: false,
            quad_ticks: 0,
            haste_ticks: 0,
            weapon: WeaponKind::Rail,
            ammo: 0,
            armor: 0,
            serum_ticks: 0,
        }
    }

    fn aim_at(origin: Vec3, target: Vec3) -> (f32, f32) {
        let delta = target - origin;
        let flat = Vec3::new(delta.x, 0.0, delta.z);
        ((-flat.x).atan2(-flat.z), delta.y.atan2(flat.length()))
    }

    #[test]
    fn advice_without_automated_fire_never_flags() {
        let mut detector = Detector::default();
        let origin = Vec3::new(0.0, 1.35, 0.0);
        for tick in 1..2_000 {
            let target = player(1, Vec3::new((tick as f32 / 80.0).sin() * 4.0, 0.0, -18.0));
            let (yaw, pitch) = aim_at(origin, player_pos(&target) + Vec3::Y);
            assert!(detector
                .observe(
                    tick,
                    0,
                    Input {
                        tick,
                        buttons: Buttons::FORWARD,
                        yaw: Fp::from_f32(yaw),
                        pitch: Fp::from_f32(pitch),
                    },
                    origin,
                    &[target],
                )
                .is_none());
        }
    }

    #[test]
    fn ordinary_held_clicks_do_not_look_like_one_tick_macros() {
        let mut detector = Detector::default();
        let origin = Vec3::new(0.0, 1.35, 0.0);
        for tick in 1..2_000 {
            let target = player(1, Vec3::new((tick as f32 / 91.0).sin() * 5.0, 0.0, -20.0));
            let (yaw, pitch) = aim_at(origin, player_pos(&target) + Vec3::Y);
            let mut buttons = Buttons::FORWARD;
            if tick % 64 < 4 {
                buttons |= Buttons::FIRE;
            }
            assert!(detector
                .observe(
                    tick,
                    0,
                    Input {
                        tick,
                        buttons,
                        yaw: Fp::from_f32(yaw),
                        pitch: Fp::from_f32(pitch),
                    },
                    origin,
                    &[target],
                )
                .is_none());
        }
    }

    #[test]
    fn unattended_tracking_and_one_tick_fire_is_flagged() {
        let mut detector = Detector::default();
        let origin = Vec3::new(0.0, 1.35, 0.0);
        let mut flagged = None;
        for tick in 1..2_000 {
            let target = player(1, Vec3::new((tick as f32 / 90.0).sin() * 5.0, 0.0, -20.0));
            let (yaw, pitch) = aim_at(origin, player_pos(&target) + Vec3::Y);
            let buttons = if tick % 64 == 1 {
                Buttons::FORWARD | Buttons::FIRE
            } else {
                Buttons::FORWARD
            };
            flagged = flagged.or_else(|| {
                detector.observe(
                    tick,
                    0,
                    Input {
                        tick,
                        buttons,
                        yaw: Fp::from_f32(yaw),
                        pitch: Fp::from_f32(pitch),
                    },
                    origin,
                    &[target],
                )
            });
        }
        let evidence = flagged.expect("unattended combat was not classified");
        assert!(evidence.fire_edges >= MIN_FIRE_EDGES);
        assert!(evidence.single_tick_pulses >= MIN_COMPLETED_PULSES);
    }
}
