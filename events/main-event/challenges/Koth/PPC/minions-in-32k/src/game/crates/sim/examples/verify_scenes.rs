//! Smoke-check: load `assets/scenes/` back and assert wall/pad/spawn counts
//! match `Arena::gru_lab_v3()`.

use common::FpExt;
use sim::Arena;

fn main() -> std::io::Result<()> {
    let baked = Arena::gru_lab_v3();
    let loaded = Arena::load_dir("assets/scenes")?;
    println!(
        "baked: walls={} pads={} powerups={} spawns={}",
        baked.walls.len(),
        baked.jump_pads.len(),
        baked.powerups.len(),
        baked.spawn_points.len()
    );
    println!(
        "loaded: walls={} pads={} powerups={} spawns={}",
        loaded.walls.len(),
        loaded.jump_pads.len(),
        loaded.powerups.len(),
        loaded.spawn_points.len()
    );
    assert_eq!(baked.walls.len(), loaded.walls.len(), "wall count mismatch");
    assert_eq!(
        baked.jump_pads.len(),
        loaded.jump_pads.len(),
        "pad count mismatch"
    );
    assert_eq!(
        baked.spawn_points.len(),
        loaded.spawn_points.len(),
        "spawn count mismatch"
    );
    assert_eq!(
        baked.powerups.len(),
        loaded.powerups.len(),
        "powerup count mismatch"
    );
    assert_eq!(
        baked.elevators.len(),
        loaded.elevators.len(),
        "elevator count mismatch"
    );

    // Demo regression: a player joining at any spawn must be able to move
    // (spawns flush against walls used to wedge players in --demo).
    let spawns: Vec<_> = loaded.spawn_points.clone();
    let mut world = sim::World::new(loaded, 64);
    let id = world.alloc("smoke").expect("slot");
    for (n, spawn) in spawns.iter().enumerate() {
        {
            let p = world.players[id as usize].as_mut().unwrap();
            p.pos = *spawn;
            p.vel = glam::Vec3::ZERO;
            p.grounded = true;
        }
        for yaw in [0.0f32, 1.57, 3.14, -1.57] {
            world.apply_input(
                id,
                protocol::Input {
                    tick: world.tick,
                    buttons: protocol::Buttons::FORWARD,
                    yaw: protocol::Fp::from_f32(yaw),
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            world.advance();
        }
        let p = world.players[id as usize].as_ref().unwrap();
        let moved = (p.pos - *spawn).length();
        assert!(
            moved > 0.05,
            "spawn {n} at {spawn} wedges the player (moved {moved:.3} m)"
        );
    }
    // Stair regression: walking into either light-well staircase must
    // carry the player to deck height (shallow treads used to defeat
    // try_step_up and the base was wedged against the internal wall).
    for (start, yaw, label) in [
        (glam::Vec3::new(-13.0, 0.0, -12.5), 1.5708f32, "west stairs"),
        (glam::Vec3::new(13.0, 0.0, 12.5), -1.5708f32, "east stairs"),
    ] {
        {
            let p = world.players[id as usize].as_mut().unwrap();
            p.pos = start;
            p.vel = glam::Vec3::ZERO;
            p.grounded = true;
        }
        let mut peak = 0.0f32;
        for _ in 0..256 {
            world.apply_input(
                id,
                protocol::Input {
                    tick: world.tick,
                    buttons: protocol::Buttons::FORWARD,
                    yaw: protocol::Fp::from_f32(yaw),
                    pitch: protocol::Fp::from_f32(0.0),
                },
            );
            world.advance();
            let p = world.players[id as usize].as_ref().unwrap();
            peak = peak.max(p.pos.y);
        }
        assert!(
            peak > 8.0,
            "{label}: player peaked at y={peak:.2} — stairs unclimbable"
        );
    }
    // Rung regression: from the north gantry, climb the tube maintenance
    // rungs (walk +z along x≈-3.5) then step east onto the quad platform.
    // The first version ran under the platform rim and head-blocked.
    {
        let p = world.players[id as usize].as_mut().unwrap();
        p.pos = glam::Vec3::new(-3.3, 12.55, -3.2);
        p.vel = glam::Vec3::ZERO;
        p.grounded = true;
    }
    let mut reached_top_rung = false;
    for _ in 0..64 {
        world.apply_input(
            id,
            protocol::Input {
                tick: world.tick,
                buttons: protocol::Buttons::FORWARD,
                yaw: protocol::Fp::from_f32(std::f32::consts::PI),
                pitch: protocol::Fp::from_f32(0.0),
            },
        );
        world.advance();
        if world.players[id as usize].as_ref().unwrap().pos.y >= 16.1 {
            reached_top_rung = true;
            break;
        }
    }
    assert!(reached_top_rung, "rungs: player never reached the top rung");
    for _ in 0..24 {
        world.apply_input(
            id,
            protocol::Input {
                tick: world.tick,
                buttons: protocol::Buttons::FORWARD,
                yaw: protocol::Fp::from_f32(-1.5708),
                pitch: protocol::Fp::from_f32(0.0),
            },
        );
        world.advance();
        let p = world.players[id as usize].as_ref().unwrap();
        if p.pos.y > 16.2 && p.pos.x > -2.8 {
            break;
        }
    }
    let p = world.players[id as usize].as_ref().unwrap();
    assert!(
        p.pos.y > 16.2 && p.pos.x > -2.8,
        "rungs: player ended at ({:.1},{:.2},{:.1}) — climb to the quad platform failed",
        p.pos.x,
        p.pos.y,
        p.pos.z
    );
    println!(
        "ok ({} spawns walkable, both stairs climbable, rungs climbable)",
        spawns.len()
    );
    Ok(())
}
