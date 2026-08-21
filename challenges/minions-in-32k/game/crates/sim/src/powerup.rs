use crate::world::Player;
use glam::Vec3;
use protocol::{PowerupKind, Tick};

pub fn respawn_ticks(kind: PowerupKind, tick_hz: u16) -> Tick {
    let s = tick_hz as Tick;
    match kind {
        PowerupKind::QuadDamage => s * 16,
        PowerupKind::Haste => s * 14,
        PowerupKind::Banana => s * 5,
        PowerupKind::Health => s * 15,
        PowerupKind::Flamethrower => s * 20,
        PowerupKind::Shotgun => s * 18,
        PowerupKind::RocketLauncher => s * 25,
        PowerupKind::Armor => s * 20,
        PowerupKind::MegaHealth => s * 35,
        PowerupKind::Serum => s * 30,
    }
}

pub fn apply(kind: PowerupKind, player: &mut Player, tick: Tick, tick_hz: u16) {
    match kind {
        PowerupKind::QuadDamage => {
            player.quad_until = tick + tick_hz as Tick * 10;
        }
        PowerupKind::Haste => {
            player.haste_until = tick + tick_hz as Tick * 8;
        }
        PowerupKind::Banana => {
            let horizontal = Vec3::new(player.vel.x, 0.0, player.vel.z);
            let dir = if horizontal.length_squared() > 0.05 {
                horizontal.normalize()
            } else {
                let (sy, cy) = common::trig::sin_cos(player.yaw);
                Vec3::new(-sy, 0.0, -cy)
            };
            player.vel = dir * 11.0 + Vec3::Y * 1.7;
            player.grounded = false;
        }
        PowerupKind::Health => {
            // Top up toward 100, but never REDUCE an over-healed player (MegaHealth
            // grants 200): touching a small pack at 200 must not drop you to 100.
            player.health = player.health.max((player.health + 65).min(100));
        }
        PowerupKind::Flamethrower => {
            player.weapon = protocol::WeaponKind::Flamethrower;
            player.ammo = crate::world::FLAME_FUEL;
        }
        PowerupKind::Shotgun => {
            player.weapon = protocol::WeaponKind::Shotgun;
            player.ammo = crate::world::SHOTGUN_SHELLS;
        }
        PowerupKind::RocketLauncher => {
            player.weapon = protocol::WeaponKind::RocketLauncher;
            player.ammo = crate::world::ROCKET_AMMO;
        }
        PowerupKind::Armor => {
            player.armor = (player.armor + crate::world::ARMOR_PICKUP).min(crate::world::ARMOR_MAX);
        }
        PowerupKind::MegaHealth => {
            player.health = (player.health + 100).min(200);
        }
        PowerupKind::Serum => {
            player.serum_until = tick + tick_hz as Tick * 8;
        }
    }
}
