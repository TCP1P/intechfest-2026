//! Dump `Arena::gru_lab_v3()` as JSON on stdout so the Python preview
//! pipeline, editor, and per-zone exporter can work from a single source of
//! truth.
//!
//! Run: `cargo run --release -p sim --example dump_arena > /tmp/arena.json`

use sim::Arena;

fn main() {
    let a = Arena::gru_lab_v3();
    println!("{{");
    println!("  \"floor_y\": {:.3},", a.floor_y);
    println!("  \"ceiling_y\": {:.3},", a.ceiling_y);

    println!("  \"zones\": [");
    for (i, z) in a.zones.iter().enumerate() {
        let comma = if i + 1 < a.zones.len() { "," } else { "" };
        println!(
            "    {{\"name\":\"{}\",\"min\":[{:.3},{:.3},{:.3}],\"max\":[{:.3},{:.3},{:.3}]}}{}",
            z.name,
            z.bounds.min.x,
            z.bounds.min.y,
            z.bounds.min.z,
            z.bounds.max.x,
            z.bounds.max.y,
            z.bounds.max.z,
            comma
        );
    }
    println!("  ],");

    println!("  \"walls\": [");
    for (i, w) in a.walls.iter().enumerate() {
        let comma = if i + 1 < a.walls.len() { "," } else { "" };
        let zone = Arena::classify_wall(w);
        println!(
            "    {{\"zone\":\"{}\",\"min\":[{:.3},{:.3},{:.3}],\"max\":[{:.3},{:.3},{:.3}]}}{}",
            zone, w.min.x, w.min.y, w.min.z, w.max.x, w.max.y, w.max.z, comma
        );
    }
    println!("  ],");

    println!("  \"jump_pads\": [");
    for (i, p) in a.jump_pads.iter().enumerate() {
        let comma = if i + 1 < a.jump_pads.len() { "," } else { "" };
        println!(
            "    {{\"center\":[{:.3},{:.3},{:.3}],\"radius\":{:.3},\"velocity\":[{:.3},{:.3},{:.3}]}}{}",
            p.center.x, p.center.y, p.center.z, p.radius,
            p.velocity.x, p.velocity.y, p.velocity.z, comma
        );
    }
    println!("  ],");

    println!("  \"powerups\": [");
    for (i, p) in a.powerups.iter().enumerate() {
        let comma = if i + 1 < a.powerups.len() { "," } else { "" };
        let kind = match p.kind {
            protocol::PowerupKind::QuadDamage => "quad_damage",
            protocol::PowerupKind::Haste => "haste",
            protocol::PowerupKind::Banana => "banana",
            protocol::PowerupKind::Health => "health",
            protocol::PowerupKind::Flamethrower => "flamethrower",
            protocol::PowerupKind::Shotgun => "shotgun",
            protocol::PowerupKind::RocketLauncher => "rocket_launcher",
            protocol::PowerupKind::Armor => "armor",
            protocol::PowerupKind::MegaHealth => "mega_health",
            protocol::PowerupKind::Serum => "serum",
        };
        println!(
            "    {{\"kind\":\"{}\",\"center\":[{:.3},{:.3},{:.3}],\"radius\":{:.3}}}{}",
            kind, p.center.x, p.center.y, p.center.z, p.radius, comma
        );
    }
    println!("  ],");

    println!("  \"spawn_points\": [");
    for (i, s) in a.spawn_points.iter().enumerate() {
        let comma = if i + 1 < a.spawn_points.len() {
            ","
        } else {
            ""
        };
        println!("    [{:.3},{:.3},{:.3}]{}", s.x, s.y, s.z, comma);
    }
    println!("  ]");
    println!("}}");
}
