use super::*;
use protocol::{ConfirmationState, ProjectileState, Vec3 as WireVec3};
use sim::arena::{Aabb, PowerupSpawn};

fn player(id: PlayerId, x: f32, z: f32) -> PlayerState {
    PlayerState {
        id,
        pos: wire_vec(x, 0.0, z),
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

fn wire_vec(x: f32, y: f32, z: f32) -> WireVec3 {
    WireVec3 {
        x: Fp::from_f32(x),
        y: Fp::from_f32(y),
        z: Fp::from_f32(z),
    }
}

fn arena(walls: Vec<Aabb>, powerups: Vec<PowerupSpawn>) -> sim::Arena {
    sim::Arena {
        floor_y: 0.0,
        ceiling_y: 8.0,
        walls,
        zones: vec![],
        spawn_points: vec![],
        jump_pads: vec![],
        powerups,
        elevators: vec![],
    }
}

fn snapshot(tick: u64, players: Vec<PlayerState>) -> Snapshot {
    Snapshot {
        tick,
        players,
        projectiles: vec![],
        confirmations: vec![],
        powerups_up: vec![],
        events: vec![],
    }
}

fn settle(bot: &mut Bot, me: PlayerState, others: &[PlayerState], first_tick: u64) {
    for tick in first_tick..=first_tick + NPC_STABILITY_HOLD_TICKS {
        let mut players = vec![me];
        players.extend_from_slice(others);
        let state = snapshot(tick, players);
        bot.update_entity_stability(&state);
        bot.update_perception(&me, &state.players, tick);
    }
}

#[test]
fn npc_backfill_uses_human_like_non_elite_profile() {
    assert!(!BotProfile::Npc.is_elite());
    for index in 0..7 {
        assert_eq!(profile_for(index, 7, 0), BotProfile::Npc);
    }
}

#[test]
fn npc_only_routes_to_confirmations_it_can_resolve() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 0);
    bot.id = Some(0);
    let me = player(0, 0.0, 0.0);
    let mut state = snapshot(100, vec![me, player(1, 4.0, 0.0), player(2, 1.0, 0.0)]);
    state.confirmations = vec![
        ConfirmationState {
            id: 1,
            killer: 1,
            victim: 2,
            pos: wire_vec(1.0, 0.0, 0.0),
            expires_in: 100,
        },
        ConfirmationState {
            id: 2,
            killer: 0,
            victim: 1,
            pos: wire_vec(4.0, 0.0, 0.0),
            expires_in: 200,
        },
    ];

    assert_eq!(
        bot.confirmation_goal(0, &me, &state),
        Some(Vec3::new(4.0, 0.0, 0.0))
    );

    state.confirmations[1].killer = 2;
    state.confirmations[1].victim = 0;
    assert_eq!(
        bot.confirmation_goal(0, &me, &state),
        Some(Vec3::new(4.0, 0.0, 0.0))
    );
}

#[test]
fn npc_personalities_are_varied_but_human_bounded() {
    let personalities: Vec<_> = (0..7).map(Personality::from_salt).collect();
    assert!(personalities
        .windows(2)
        .any(|pair| pair[0].reaction_ticks != pair[1].reaction_ticks));
    assert!(personalities
        .windows(2)
        .any(|pair| pair[0].preferred_range != pair[1].preferred_range));
    for personality in personalities {
        assert!((6..=9).contains(&personality.reaction_ticks));
        assert!((0.08..=0.10).contains(&personality.max_turn));
        assert!(personality.aim_error >= 0.017 && personality.aim_error <= 0.024);
        assert!(personality.memory_ticks >= 140 && personality.memory_ticks <= 165);
    }
}

#[test]
fn npc_never_perceives_a_server_probe_behind_full_cover() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 0);
    bot.id = Some(0);
    bot.arena = arena(
        vec![Aabb {
            min: Vec3::new(-1.0, 0.0, 2.0),
            max: Vec3::new(1.0, 2.0, 2.25),
        }],
        vec![],
    );
    let me = player(0, 0.0, 0.0);
    let hidden_probe = player(1, 0.0, 4.0);
    let visible = player(2, 0.0, -4.0);
    settle(&mut bot, me, &[hidden_probe, visible], 100);

    assert!(!bot.visible_now[hidden_probe.id as usize]);
    assert!(bot.visible_now[visible.id as usize]);
    assert_eq!(
        bot.choose_target(0, &me, &[me, hidden_probe, visible], 117)
            .map(|target| target.id),
        Some(visible.id)
    );
}

#[test]
fn npc_waits_out_new_and_reappearing_entities() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 0);
    bot.id = Some(0);
    bot.arena = arena(vec![], vec![]);
    let me = player(0, 0.0, 0.0);
    let target = player(1, 0.0, -7.0);

    for tick in 100..100 + NPC_STABILITY_HOLD_TICKS {
        let state = snapshot(tick, vec![me, target]);
        bot.update_entity_stability(&state);
        bot.update_perception(&me, &state.players, tick);
        assert!(!bot.visible_now[target.id as usize]);
    }

    let stable_tick = 100 + NPC_STABILITY_HOLD_TICKS;
    let stable = snapshot(stable_tick, vec![me, target]);
    bot.update_entity_stability(&stable);
    bot.update_perception(&me, &stable.players, stable_tick);
    assert!(bot.visible_now[target.id as usize]);

    let reappeared_tick = stable_tick + 4;
    let reappeared = snapshot(reappeared_tick, vec![me, target]);
    bot.update_entity_stability(&reappeared);
    bot.update_perception(&me, &reappeared.players, reappeared_tick);
    assert!(!bot.visible_now[target.id as usize]);
}

#[test]
fn npc_has_a_field_of_view_but_reacts_to_its_attacker() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 2);
    bot.id = Some(0);
    bot.arena = arena(vec![], vec![]);
    let me = player(0, 0.0, 0.0);
    let rear = player(1, 0.0, 5.0);
    settle(&mut bot, me, &[rear], 200);
    assert!(!bot.visible_now[rear.id as usize]);

    bot.observe_event(
        217,
        GameEvent::Damage {
            attacker: rear.id,
            victim: me.id,
            amount: 10,
        },
    );
    let state = snapshot(218, vec![me, rear]);
    bot.update_entity_stability(&state);
    bot.update_perception(&me, &state.players, 218);
    assert!(bot.visible_now[rear.id as usize]);
    assert_eq!(
        bot.choose_target(0, &me, &state.players, 218)
            .map(|target| target.id),
        Some(rear.id)
    );
}

#[test]
fn npc_aim_accelerates_instead_of_snapping() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 3);
    let desired = std::f32::consts::FRAC_PI_2;
    let (first, _) = bot.apply_aim_model(desired, 0.0);
    assert!(first > 0.0);
    assert!(first <= bot.personality.turn_accel + 1e-6);
    assert!(first < desired / 10.0);

    let mut previous = first;
    bot.last_yaw = first;
    for _ in 0..30 {
        let (yaw, _) = bot.apply_aim_model(desired, 0.0);
        assert!(angle_delta(previous, yaw).abs() <= bot.personality.max_turn + 1e-6);
        bot.last_yaw = yaw;
        previous = yaw;
    }
    assert!(angle_delta(previous, desired).abs() < 0.1);
}

#[test]
fn npc_reaction_delay_prevents_instant_fire_but_eventually_shoots() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 4);
    bot.id = Some(0);
    bot.arena = arena(vec![], vec![]);
    let me = player(0, 0.0, 0.0);
    let target = player(1, 0.0, -10.0);
    settle(&mut bot, me, &[target], 300);
    bot.update_target_lock(target.id, 317);
    let aim_point = bot.aim_point(&me, &target, 317);
    let origin = player_pos(&me) + Vec3::Y * EYE_HEIGHT;
    let delta = aim_point - origin;
    let flat = Vec3::new(delta.x, 0.0, delta.z);
    let aimed_yaw = (-flat.x).atan2(-flat.z);
    let aimed_pitch = delta.y.atan2(flat.length());
    bot.last_yaw = aimed_yaw;
    bot.last_pitch = aimed_pitch;
    let context = |tick| FireContext {
        tick,
        target_id: target.id,
        dist: 10.0,
        yaw: aimed_yaw,
        pitch: aimed_pitch,
        me: &me,
        aim_point,
    };
    assert!(!bot.should_fire(context(317)));
    assert!((317 + bot.personality.reaction_ticks..360).any(|tick| bot.should_fire(context(tick))));
}

#[test]
fn wounded_npc_prioritizes_a_health_route_over_nearby_quad() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 0);
    bot.arena = arena(
        vec![],
        vec![
            PowerupSpawn {
                kind: PowerupKind::QuadDamage,
                center: Vec3::new(1.0, 0.0, 0.0),
                radius: 0.75,
            },
            PowerupSpawn {
                kind: PowerupKind::Health,
                center: Vec3::new(12.0, 0.0, 0.0),
                radius: 0.75,
            },
        ],
    );
    let mut me = player(0, 0.0, 0.0);
    me.health = 25;
    let mut state = snapshot(1, vec![me]);
    state.powerups_up = vec![true, true];
    assert_eq!(
        bot.pickup_goal(&me, &state),
        Some(Vec3::new(12.0, 0.0, 0.0))
    );
}

#[test]
fn npc_only_pursues_recent_visual_memory() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 1);
    bot.lock_target = Some(2);
    bot.observations[2] = Some(Observation {
        pos: Vec3::new(3.0, 0.0, 4.0),
        velocity: Vec3::ZERO,
        seen_tick: 500,
    });
    assert_eq!(bot.pursuit_goal(501), Some(Vec3::new(3.0, 0.0, 4.0)));
    assert_eq!(
        bot.pursuit_goal(500 + bot.personality.memory_ticks),
        Some(Vec3::new(3.0, 0.0, 4.0))
    );
    assert_eq!(bot.pursuit_goal(501 + bot.personality.memory_ticks), None);
}

#[test]
fn npc_leads_rockets_but_not_hitscan() {
    let mut bot = Bot::new("NPC".into(), BotProfile::Npc, 0);
    let mut me = player(0, 0.0, 0.0);
    let target = player(1, 0.0, -22.0);
    bot.observations[1] = Some(Observation {
        pos: player_pos(&target),
        velocity: Vec3::new(4.0, 0.0, 0.0),
        seen_tick: 10,
    });
    me.weapon = WeaponKind::RocketLauncher;
    assert!(bot.aim_point(&me, &target, 10).x > 3.0);
    me.weapon = WeaponKind::Rail;
    assert!(bot.aim_point(&me, &target, 10).x.abs() <= 0.13);
}

#[test]
fn npc_aims_across_the_body_instead_of_holding_exact_center() {
    let me = player(0, 0.0, 0.0);
    let target = player(1, 0.0, -20.0);
    let exact_center = player_pos(&target) + Vec3::Y * TARGET_HEIGHT;
    for salt in 0..7 {
        let bot = Bot::new("NPC".into(), BotProfile::Npc, salt);
        for tick in [0, 95, 96, 191, 192, 287] {
            let point = bot.aim_point(&me, &target, tick);
            assert!(point.distance(exact_center) >= 0.30 - 1e-5);
            assert!((0.0..=sim::PLAYER_HEIGHT).contains(&(point.y - target.pos.y.to_f32())));
        }
    }
}

#[test]
fn npc_dodges_an_incoming_rocket() {
    let mut state = snapshot(10, vec![]);
    state.projectiles.push(ProjectileState {
        id: 1,
        pos: wire_vec(0.5, 0.8, -8.0),
        vel: wire_vec(0.0, 0.0, 22.0),
    });
    assert_eq!(incoming_projectile_dodge(&state, Vec3::ZERO, 0.0), Some(-1));
}
