use super::*;

fn player(id: PlayerId, x: f32, z: f32) -> PlayerState {
    PlayerState {
        id,
        pos: protocol::Vec3 {
            x: protocol::Fp::from_f32(x),
            y: protocol::Fp::from_f32(0.0),
            z: protocol::Fp::from_f32(z),
        },
        yaw: protocol::Fp::default(),
        pitch: protocol::Fp::default(),
        health: 100,
        respawn_in: 0,
        crouching: false,
        quad_ticks: 0,
        haste_ticks: 0,
        weapon: protocol::WeaponKind::Rail,
        ammo: 0,
        armor: 0,
        serum_ticks: 0,
    }
}

#[test]
fn randomized_probe_position_never_peeks_around_cover() {
    let arena = sim::Arena {
        floor_y: 0.0,
        ceiling_y: 8.0,
        walls: vec![sim::arena::Aabb {
            min: Vec3::new(-0.7, 0.0, 2.0),
            max: Vec3::new(0.7, 2.2, 2.25),
        }],
        zones: vec![],
        spawn_points: vec![],
        jump_pads: vec![],
        powerups: vec![],
        elevators: vec![],
    };
    let me = player(0, 0.0, 0.0);
    let target = player(1, 0.0, 4.0);
    let eye = player_pos(&me) + Vec3::new(0.0, EYE_HEIGHT, 0.0);
    assert!(!sim::weapon::player_hull_visible(
        &arena,
        &[],
        eye,
        player_pos(&target),
        false,
    ));

    for seed in 0..256 {
        let mut rng = common::DetRng::from_seed(seed);
        let position = concealed_probe_position(&arena, &me, &target, &mut rng);
        assert!(
            !sim::weapon::player_hull_visible(&arena, &[], eye, position, false),
            "seed {seed} produced a visible anti-cheat probe at {position:?}"
        );
    }
}

#[test]
fn reconnect_reuses_private_integrity_evidence() {
    let registry: AntiCheatRegistry = Arc::new(Mutex::new(HashMap::new()));
    let arena = Arc::new(sim::Arena {
        floor_y: 0.0,
        ceiling_y: 8.0,
        walls: vec![],
        zones: vec![],
        spawn_points: vec![],
        jump_pads: vec![],
        powerups: vec![],
        elevators: vec![],
    });
    let first = anti_cheat_for_player(&registry, 3, "first".into(), arena.clone(), 7);
    first.lock().unwrap().automated_combat_pending = true;
    let resumed = anti_cheat_for_player(&registry, 3, "second".into(), arena, 99);
    assert!(Arc::ptr_eq(&first, &resumed));
    assert!(resumed.lock().unwrap().automated_combat_pending);
}
