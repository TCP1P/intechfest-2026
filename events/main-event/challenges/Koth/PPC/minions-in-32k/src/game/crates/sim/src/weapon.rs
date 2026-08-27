//! Hitscan weapon utilities.

use crate::arena::{Aabb, Arena};
use crate::world::{player_height, Player, PLAYER_RADIUS};
use glam::Vec3;
use protocol::{PlayerId, MAX_PLAYERS};

/// Nearest wall intersection in `[0, max_dist]`, or `max_dist` if none.
pub fn raycast_walls(
    arena: &Arena,
    dyn_walls: &[Aabb],
    origin: Vec3,
    dir: Vec3,
    max_dist: f32,
) -> f32 {
    let mut best = max_dist;
    for wall in arena.walls.iter().chain(dyn_walls) {
        if let Some(t) = wall.ray(origin, dir, best) {
            if t < best {
                best = t;
            }
        }
    }
    best
}

/// Whether at least one representative point on a player hull is visible
/// from `eye`. This is shared by the server's anti-cheat view and honest
/// network bots so their definition of an occluded player cannot drift.
pub fn player_hull_visible(
    arena: &Arena,
    dyn_walls: &[Aabb],
    eye: Vec3,
    pos: Vec3,
    crouching: bool,
) -> bool {
    let height = player_height(crouching);
    let middle = pos + Vec3::new(0.0, height * 0.5, 0.0);
    let flat = Vec3::new(middle.x - eye.x, 0.0, middle.z - eye.z);
    let side = if flat.length_squared() > 1e-6 {
        Vec3::new(-flat.z, 0.0, flat.x).normalize() * PLAYER_RADIUS
    } else {
        Vec3::new(PLAYER_RADIUS, 0.0, 0.0)
    };
    let samples = [
        pos + Vec3::new(0.0, height - 0.1, 0.0),
        middle,
        pos + Vec3::new(0.0, 0.15, 0.0),
        middle + side,
        middle - side,
    ];

    samples.iter().any(|&point| {
        let to = point - eye;
        let distance = to.length();
        distance < 1e-4
            || raycast_walls(arena, dyn_walls, eye, to / distance, distance) >= distance - 0.2
    })
}

/// A hit-testable player hull at some tick: (feet position, crouching).
pub type Hull = Option<(Vec3, bool)>;

/// Like `raycast_players`, but tests the given hulls — used for lag
/// compensation, where the shooter's victims are tested where they stood
/// when the shooter actually saw them.
pub fn raycast_hulls(
    arena: &Arena,
    dyn_walls: &[Aabb],
    hulls: &[Hull; MAX_PLAYERS],
    shooter: PlayerId,
    origin: Vec3,
    dir: Vec3,
    max_dist: f32,
) -> Option<(PlayerId, f32, Vec3)> {
    let t_wall = raycast_walls(arena, dyn_walls, origin, dir, max_dist);
    nearest_hull_hit(t_wall, shooter, origin, dir, hulls.iter().copied())
}

/// Returns `(victim, t, hit_point)` for the nearest alive non-shooter player
/// within `max_dist`, or `None`. Walls that block the line of sight are
/// respected — the ray stops at the nearest wall.
pub fn raycast_players(
    arena: &Arena,
    dyn_walls: &[Aabb],
    players: &[Option<Player>; MAX_PLAYERS],
    shooter: PlayerId,
    origin: Vec3,
    dir: Vec3,
    max_dist: f32,
) -> Option<(PlayerId, f32, Vec3)> {
    let t_wall = raycast_walls(arena, dyn_walls, origin, dir, max_dist);
    // Crouching shrinks the target hull — ducking under a rail shot is
    // real, not cosmetic.
    nearest_hull_hit(
        t_wall,
        shooter,
        origin,
        dir,
        players
            .iter()
            .map(|s| s.as_ref().filter(|p| p.alive).map(|p| (p.pos, p.crouching))),
    )
}

/// Nearest player-AABB hit within `t_wall`, skipping the shooter and empty
/// hulls. Each hull is (feet position, crouching).
fn nearest_hull_hit(
    t_wall: f32,
    shooter: PlayerId,
    origin: Vec3,
    dir: Vec3,
    hulls: impl Iterator<Item = Hull>,
) -> Option<(PlayerId, f32, Vec3)> {
    let mut best: Option<(f32, PlayerId)> = None;
    for (i, hull) in hulls.enumerate() {
        let Some((pos, crouching)) = hull else {
            continue;
        };
        let id = i as PlayerId;
        if id == shooter {
            continue;
        }
        let bbox = Aabb {
            min: Vec3::new(pos.x - PLAYER_RADIUS, pos.y, pos.z - PLAYER_RADIUS),
            max: Vec3::new(
                pos.x + PLAYER_RADIUS,
                pos.y + player_height(crouching),
                pos.z + PLAYER_RADIUS,
            ),
        };
        if let Some(t) = bbox.ray(origin, dir, t_wall) {
            match best {
                Some((bt, _)) if bt <= t => {}
                _ => best = Some((t, id)),
            }
        }
    }
    best.map(|(t, id)| (id, t, origin + dir * t))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_arena(walls: Vec<Aabb>) -> Arena {
        Arena {
            floor_y: 0.0,
            ceiling_y: 8.0,
            walls,
            zones: vec![],
            spawn_points: vec![],
            jump_pads: vec![],
            powerups: vec![],
            elevators: vec![],
        }
    }

    #[test]
    fn player_visibility_checks_the_whole_hull() {
        let eye = Vec3::new(0.0, 1.35, 0.0);
        let target = Vec3::new(0.0, 0.0, 4.0);
        assert!(player_hull_visible(
            &empty_arena(vec![]),
            &[],
            eye,
            target,
            false
        ));

        let full_cover = Aabb {
            min: Vec3::new(-1.0, 0.0, 2.0),
            max: Vec3::new(1.0, 2.0, 2.25),
        };
        assert!(!player_hull_visible(
            &empty_arena(vec![full_cover]),
            &[],
            eye,
            target,
            false
        ));

        let low_cover = Aabb {
            min: Vec3::new(-1.0, 0.0, 2.0),
            max: Vec3::new(1.0, 0.55, 2.25),
        };
        assert!(player_hull_visible(
            &empty_arena(vec![low_cover]),
            &[],
            eye,
            target,
            false
        ));
    }
}
